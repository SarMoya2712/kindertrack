"""Registration detection regressions using shipped production ONNX files."""
import io
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from services.face_recognition import FaceError, FaceService, MODEL
from conftest import add_child

ROOT = Path(__file__).resolve().parents[1]


def portrait():
    return cv2.imread(str(ROOT / 'tests/fixtures/lena.jpg'))


def upload(image):
    return FileStorage(stream=io.BytesIO(cv2.imencode('.jpg', image)[1].tobytes()))


def test_large_portrait_original_zero_now_embeds(caplog):
    service = FaceService(ROOT / 'models')
    image = cv2.resize(portrait(), (1600, 1600))
    service.load()
    service.detector.setInputSize((1600, 1600))
    _, original = service.detector.detect(image)
    assert original is None or len(original) == 0
    data, embedding, model = service.reference(upload(image))
    vector = np.frombuffer(embedding, dtype=np.float32)
    assert vector.shape == (128,) and np.isfinite(vector).all()
    assert np.linalg.norm(vector) == pytest.approx(1, abs=1e-6)
    assert model == MODEL and data
    records = [r.message for r in caplog.records]
    passes = [json.loads(r.split(' ', 1)[1]) for r in records
              if r.startswith('registration_detection ')]
    assert passes[0]['count'] == 0
    assert any(p['count'] == 1 for p in passes[1:])
    assert all(p['threshold'] == .85 for p in passes)
    assert all('confidence' in d and 'box' in d for p in passes for d in p['detections'])
    assert not any('embedding' in r or 'landmark' in r for r in records)


@pytest.mark.parametrize('angle', [-20, 0, 20])
def test_real_moderate_rotation(angle):
    image = cv2.warpAffine(portrait(), cv2.getRotationMatrix2D((256, 256), angle, 1),
                           (512, 512))
    _, embedding, _ = FaceService(ROOT / 'models').reference(upload(image))
    assert len(embedding) == 512


def test_distinct_people_rejected_and_second_portrait_alone_embeds():
    other = cv2.imread(str(ROOT / 'tests/fixtures/messi5.jpg'))[60:180, 200:310]
    other = cv2.resize(other, (512, 512))
    service = FaceService(ROOT / 'models')
    assert len(service.reference(upload(other))[1]) == 512
    with pytest.raises(FaceError, match='registration_multiple_faces'):
        service.reference(upload(np.concatenate([portrait(), other], axis=1)))


@pytest.mark.parametrize('kind,code', [
    ('blank', 'registration_no_face'), ('multiple', 'registration_multiple_faces'),
    ('blur', 'registration_no_face'), ('small', 'closer'),
])
def test_real_invalid_images(kind, code):
    image = portrait()
    image = {
        'blank': np.zeros_like(image),
        'multiple': np.concatenate([image, image], axis=1),
        'blur': cv2.GaussianBlur(image, (51, 51), 15),
        'small': cv2.resize(image, (96, 96)),
    }[kind]
    with pytest.raises(FaceError, match=f'^{code}$'):
        FaceService(ROOT / 'models').reference(upload(image))


def test_exif_rotated_photo_embeds_and_logs_orientation(caplog):
    image = Image.fromarray(cv2.cvtColor(portrait(), cv2.COLOR_BGR2RGB))
    image = image.transpose(Image.Transpose.ROTATE_90)
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = 'DO_NOT_LOG_PRIVATE_METADATA'
    data = io.BytesIO()
    image.save(data, 'JPEG', exif=exif)
    data.seek(0)
    stored, embedding, _ = FaceService(ROOT / 'models').reference(FileStorage(stream=data))
    assert len(embedding) == 512
    assert not Image.open(io.BytesIO(stored)).getexif()
    assert '"exif_orientation": 6' in caplog.text
    assert 'DO_NOT_LOG_PRIVATE_METADATA' not in caplog.text


def box(x=100, y=100, w=150, h=180):
    return np.array([x, y, w, h, x+40, y+50, x+110, y+50,
                     x+75, y+85, x+50, y+130, x+100, y+130, .95], np.float32)


class Detector:
    def __init__(self, outputs):
        self.outputs = iter(outputs)

    def setInputSize(self, size):
        pass

    def detect(self, image):
        return None, next(self.outputs)


def detect(outputs):
    service = FaceService(ROOT / 'models')
    service.detector = Detector(outputs)
    return service.registration_faces(np.zeros((1000, 1000, 3), np.uint8))


def scaled(face, scale):
    face = face.copy()
    face[:14] *= scale
    return face


def test_cross_scale_duplicates_merge_but_second_background_face_is_retained():
    face = box()
    assert len(detect([[face], [scaled(face, .64)], [scaled(face, .32)]])) == 1
    with pytest.raises(FaceError, match='registration_multiple_faces'):
        detect([[face, box(700, 700, 50, 60)], [scaled(face, .64)], None])


def test_same_pass_overlapping_people_are_not_merged():
    with pytest.raises(FaceError, match='registration_multiple_faces'):
        detect([[box(), box(120)], None, None])


@pytest.mark.parametrize('invalid', [
    box(-500), np.full(15, np.nan), box(w=-20), box(980),
])
def test_invalid_candidate_requires_retry_instead_of_silently_ignoring_it(invalid):
    with pytest.raises(FaceError, match='registration_retry'):
        detect([[box(), invalid], None, None])


def test_blurry_single_detection_requires_retry(monkeypatch):
    service = FaceService(ROOT / 'models')
    service.load()
    monkeypatch.setattr(service, 'registration_faces', lambda image: np.array([box()]))
    with pytest.raises(FaceError, match='registration_retry'):
        service.extract(np.full((512, 512, 3), 128, np.uint8))


@pytest.mark.parametrize('multiple,message', [
    (False, 'Лицо не обнаружено. Попробуйте другую фотографию.'),
    (True, 'Обнаружено несколько лиц. Загрузите фотографию одного ребёнка.'),
])
def test_registration_displays_specific_error(authorized, multiple, message):
    image = np.concatenate([portrait(), portrait()], axis=1) if multiple else np.zeros(
        (512, 512, 3), np.uint8)
    response = add_child(authorized, photos=[(upload(image).stream, 'test.jpg')])
    assert response.status_code == 200
    assert message in response.text
    assert 'В кадре должно быть ровно одно лицо' not in response.text


def test_large_single_face_registration_succeeds(authorized):
    image = cv2.resize(portrait(), (1600, 1600))
    assert add_child(authorized, photos=[(upload(image).stream, 'large.jpg')]).status_code == 302
