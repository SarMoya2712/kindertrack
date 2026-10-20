import threading
from pathlib import Path
import cv2
import numpy as np
from flask import current_app, g, has_request_context
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
        data = photo_storage.prepare(upload, diagnostics=True)
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

    def registration_faces(self, image):
        """Run under the model lock; map all detections to stored-image coordinates.

        YuNet's training scale is roughly 10–300px per face. Full-size portraits
        can exceed it. Keep the full-resolution pass for small/background faces,
        and also downsample the whole frame (never crop out another person).
        """
        height, width = image.shape[:2]
        candidates, ambiguous = [], False
        sizes = [(width, height)]
        for bound in (640, 320):
            scale = min(1., bound / max(width, height))
            size = (max(1, round(width * scale)), max(1, round(height * scale)))
            if size not in sizes:
                sizes.append(size)
        for size in sizes:
            frame = image if size == (width, height) else cv2.resize(
                image, size, interpolation=cv2.INTER_AREA)
            self.detector.setInputSize(size)
            _, faces = self.detector.detect(frame)
            diagnostics = []
            for raw in ([] if faces is None else faces):
                face = np.asarray(raw, dtype=np.float32).copy()
                finite = face.shape == (15,) and np.isfinite(face).all()
                if finite:
                    face[:14:2] *= width / size[0]
                    face[1:14:2] *= height / size[1]
                    x, y, w, h = face[:4]
                    points = face[4:14].reshape(5, 2)
                    intersection = max(0., min(width, x+w)-max(0., x)) * max(
                        0., min(height, y+h)-max(0., y))
                    valid = (w > 0 and h > 0 and intersection >= .9 * w * h
                             and .85 <= face[-1] <= 1
                             and (points >= 0).all()
                             and (points[:, 0] < width).all()
                             and (points[:, 1] < height).all()
                             and np.linalg.norm(points[0]-points[1]) > 1)
                else:
                    valid = False
                diagnostics.append(dict(
                    confidence=round(float(face[-1]), 5) if finite else None,
                    box=[round(float(v), 2) for v in face[:4]] if finite else None,
                    valid=bool(valid)))
                if valid:
                    candidates.append((face, size))
                else:
                    # Do not silently discard a questionable second face.
                    ambiguous = True
            photo_storage.diagnostic('registration_detection', input_size=size,
                stored_size=(width, height), color='BGR_uint8', normalization='none',
                detector='yunet-2023mar', opencv=cv2.__version__,
                resize='none' if size == (width, height) else 'INTER_AREA',
                threshold=.85, count=len(diagnostics), detections=diagnostics)

        # Cross-scale duplicates only: retain separate people, including small
        # faces. NMS inside each detector pass has already removed duplicates.
        kept, passes = [], []
        for face, size in sorted(candidates, key=lambda row: float(row[0][-1]), reverse=True):
            duplicate = False
            for previous, seen in zip(kept, passes):
                if size in seen:
                    continue
                left = np.maximum(face[:2], previous[:2])
                right = np.minimum(face[:2]+face[2:4], previous[:2]+previous[2:4])
                intersection = float(np.prod(np.maximum(0, right-left)))
                union = float(face[2]*face[3]+previous[2]*previous[3])-intersection
                if union > 0 and intersection / union > .5:
                    seen.add(size)
                    duplicate = True
                    break
            if not duplicate:
                kept.append(face)
                passes.append({size})
        photo_storage.diagnostic('registration_detection_result', count=len(kept),
                                 ambiguous=ambiguous)
        if len(kept) > 1:
            raise FaceError('registration_multiple_faces')
        if ambiguous:
            raise FaceError('registration_retry')
        if not kept:
            raise FaceError('registration_no_face')
        return np.asarray(kept)

    def analyze(self, image, scanning=True):
        """Detect, check passive liveness, then embed under the model lock."""
        from services.liveness import LivenessResult
        with self.lock:
            self.load()
            if scanning:
                self.detector.setInputSize((image.shape[1], image.shape[0]))
                _, faces = self.detector.detect(image)
            else:
                faces = self.registration_faces(image)
            if scanning and has_request_context():
                from services.frame_quality import evaluate
                g.scan_quality = evaluate(image, faces)
            if faces is None or len(faces) == 0: raise FaceError('no_face')
            if len(faces) != 1: raise FaceError('multiple_faces')
            face = faces[0]
            if min(face[2:4]) < 60: raise FaceError('closer')
            if not scanning:
                from services.frame_quality import evaluate
                quality = evaluate(image, faces)
                photo_storage.diagnostic('registration_quality',
                    sharpness=quality.get('sharpness'), state=quality['state'])
                # Reuse the existing blur measurement; no mouth/expression or
                # neutral-pose requirement is imposed on registration.
                if quality.get('sharpness', 0) < 35:
                    raise FaceError('registration_retry')
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

