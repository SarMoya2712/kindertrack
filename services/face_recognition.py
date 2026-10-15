import threading
from pathlib import Path
import cv2
import numpy as np
from flask import current_app
from sqlalchemy import select
from database import db
from database.models import Photo, Child
from services import photo_storage

MODEL = 'sface-2021dec'
class FaceError(ValueError): pass

class FaceService:
    def __init__(self, model_dir):
        self.model_dir = Path(model_dir)
        self.lock = threading.Lock()
        self.detector = self.recognizer = None

    def load(self):
        if self.detector is not None: return
        detector = self.model_dir / 'face_detection_yunet_2023mar.onnx'
        recognizer = self.model_dir / 'face_recognition_sface_2021dec.onnx'
        if not detector.exists() or not recognizer.exists(): raise FaceError('models_missing')
        try:
            loaded_detector = cv2.FaceDetectorYN.create(str(detector), '', (320,320), 0.85)
            loaded_recognizer = cv2.FaceRecognizerSF.create(str(recognizer), '')
        except cv2.error as error:
            raise FaceError('models_missing') from error
        self.detector, self.recognizer = loaded_detector, loaded_recognizer

    def reference(self, upload):
        """Validate and enroll the exact normalized image that will be stored."""
        data = photo_storage.prepare(upload)
        feature = self.embedding_from_bytes(data)
        return data, feature.tobytes(), MODEL

    def embedding_from_bytes(self, data):
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise FaceError('image_invalid')
        try:
            return self.extract(image)
        except cv2.error as error:
            raise FaceError('face_invalid') from error

    def enroll_existing(self):
        """Backfill only unenrolled photos, preserving files and existing vectors.

        Commit each photo independently so an invalid legacy image does not prevent
        other children from being enrolled. No attendance or scanning is involved.
        """
        ids = list(db.session.scalars(select(Photo.id).where(Photo.embedding.is_(None))))
        enrolled, rejected = 0, []
        for photo_id in ids:
            photo = db.session.scalar(select(Photo).where(Photo.id == photo_id).with_for_update())
            if photo is None or photo.embedding is not None:
                db.session.rollback()
                continue
            try:
                with photo_storage.photo_path(photo.filename).open('rb') as stream:
                    from werkzeug.datastructures import FileStorage
                    _, embedding, model = self.reference(FileStorage(stream=stream))
                photo.embedding, photo.model = embedding, model
                db.session.commit()
                enrolled += 1
            except (FaceError, photo_storage.PhotoError, OSError) as error:
                db.session.rollback()
                rejected.append((photo_id, str(error) if isinstance(error, ValueError) else 'image_invalid'))
        return enrolled, rejected

    def decode(self, upload):
        try:
            data = photo_storage.prepare(upload)
        except photo_storage.PhotoError as error:
            raise FaceError(str(error)) from error
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise FaceError('image_invalid')
        return image

    def extract(self, image, scanning=False):
        return self.analyze(image, scanning=scanning)[0]

    def analyze(self, image, scanning=True):
        """Detect, check passive liveness, then embed under the model lock."""
        from services.liveness import LivenessResult
        with self.lock:
            self.load()
            self.detector.setInputSize((image.shape[1], image.shape[0]))
            _, faces = self.detector.detect(image)
            if faces is None or len(faces) == 0: raise FaceError('no_face' if scanning else 'one_face')
            if len(faces) != 1: raise FaceError('multiple_faces' if scanning else 'one_face')
            face = faces[0]
            if min(face[2:4]) < 60: raise FaceError('closer')
            result = LivenessResult('unavailable')
            if scanning:
                result = current_app.extensions['liveness'].check(image, face)
                if result.status == 'failed' or (result.status != 'passed' and current_app.config['REQUIRE_LIVENESS']):
                    raise FaceError('liveness_failed')
            feature = self.recognizer.feature(self.recognizer.alignCrop(image, face)).flatten()
            norm = np.linalg.norm(feature)
            if feature.shape != (128,) or not np.isfinite(feature).all() or not np.isfinite(norm) or norm <= 0:
                raise FaceError('face_invalid')
            return (feature / norm).astype(np.float32), result

    def match(self, feature):
        result = self.compare(feature)
        return result['child_id'], result['confidence']

    def compare(self, feature):
        rows = db.session.execute(select(Photo.child_id, Photo.embedding).join(Child, Child.id == Photo.child_id)
            .where(Child.status == 'active', Photo.model == MODEL, Photo.embedding.is_not(None))).mappings().all()
        valid_rows, vectors = [], []
        for row in rows:
            # Legacy/corrupt database values must not poison all comparisons or
            # let a non-unit vector inflate similarity and bypass the threshold.
            if len(row['embedding']) != 128 * np.dtype(np.float32).itemsize:
                continue
            vector = np.frombuffer(row['embedding'], dtype=np.float32)
            if not np.isfinite(vector).all():
                continue
            norm = np.linalg.norm(vector.astype(np.float64))
            if not np.isclose(norm, 1.0, rtol=1e-5, atol=1e-6):
                continue
            valid_rows.append(row)
            vectors.append(vector)
        if not vectors: return dict(child_id=None, confidence=0.0, status='not_found')
        scores = np.clip(np.stack(vectors) @ feature, -1.0, 1.0)
        by_child = {}
        for row, score in zip(valid_rows, scores):
            by_child[row['child_id']] = max(float(score), by_child.get(row['child_id'], -1))
        ranked = sorted(by_child.items(), key=lambda item:item[1], reverse=True)
        child, score = ranked[0]
        runner_up = ranked[1][1] if len(ranked)>1 else -1
        accepted = score >= current_app.config['FACE_THRESHOLD'] and score-runner_up >= current_app.config['FACE_MARGIN']
        return dict(child_id=child if accepted else None, confidence=score,
                    status='recognized' if accepted else 'low_confidence')

