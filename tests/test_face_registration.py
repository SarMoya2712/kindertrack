"""Real-model enrollment, invalid portraits, preserved files and stored-vector matching."""
import io
from pathlib import Path
from datetime import date
import cv2
import numpy as np
import pytest
from sqlalchemy import select, func
from werkzeug.datastructures import FileStorage
from database import db
from database.models import Child, Photo, Attendance, Scan
from services.face_recognition import MODEL
from services import photo_storage
from conftest import add_child, child_form, csrf, image_bytes

FIXTURES = Path(__file__).parent / 'fixtures'


def portrait():
    return io.BytesIO((FIXTURES / 'lena.jpg').read_bytes())


def test_real_enrollment_update_preserves_vectors_without_upload(authorized, app, monkeypatch):
    service = app.extensions['face']
    extract = service.extract
    calls = []
    def track(image, scanning=False):
        calls.append(scanning)
        return extract(image, scanning)
    monkeypatch.setattr(service, 'extract', track)
    assert add_child(authorized, photos=[(portrait(), f'{i}.jpg') for i in range(3)]).status_code == 302
    assert calls == [False] * 3
    with app.app_context():
        photos = db.session.scalars(select(Photo).order_by(Photo.id)).all()
        before = [(p.filename, p.embedding, p.model) for p in photos]
        for p in photos:
            vector = np.frombuffer(p.embedding, dtype=np.float32)
            assert vector.shape == (128,) and np.isfinite(vector).all()
            assert np.linalg.norm(vector) == pytest.approx(1, abs=1e-6)
            assert p.model == MODEL
        assert db.session.scalar(select(func.count(Attendance.id))) == 0
        assert db.session.scalar(select(func.count(Scan.id))) == 0
    form = dict(child_form(), csrf_token=csrf(authorized), first_name='Сардор')
    assert authorized.post('/children/1/edit', data=form).status_code == 302
    assert len(calls) == 3
    with app.app_context():
        after = [(p.filename, p.embedding, p.model) for p in db.session.scalars(select(Photo).order_by(Photo.id))]
        assert before == after
        assert all(photo_storage.photo_path(name).exists() for name, _, _ in before)
        # Matching reads saved vectors and does not run extraction for references.
        assert service.match(np.frombuffer(before[0][1], dtype=np.float32))[0] == 1
    assert len(calls) == 3
    form.update(csrf_token=csrf(authorized), photos=[(portrait(), 'replacement.jpg')])
    assert authorized.post('/children/1/edit', data=form).status_code == 302
    assert len(calls) == 4
    with app.app_context():
        assert db.session.scalar(select(func.count(Photo.id))) == 1
        assert db.session.scalar(select(Photo)).embedding is not None


@pytest.mark.parametrize('kind', ['blank', 'multiple', 'small', 'corrupt'])
def test_invalid_face_does_not_replace_existing_photo(authorized, app, kind):
    assert add_child(authorized, photos=[(portrait(), 'good.jpg')]).status_code == 302
    with app.app_context():
        p = db.session.scalar(select(Photo))
        before = (p.id, p.filename, p.embedding)
        original_file = photo_storage.photo_path(p.filename).read_bytes()
    original = cv2.imread(str(FIXTURES / 'lena.jpg'))
    if kind == 'corrupt':
        raw = b'fake jpg'
    else:
        image = {'blank':np.zeros((512,512,3), np.uint8),
                 'multiple':np.concatenate([original, original], axis=1),
                 'small':cv2.resize(original, (96,96))}[kind]
        raw = cv2.imencode('.jpg', image)[1].tobytes()
    form = dict(child_form(), csrf_token=csrf(authorized), first_name='Changed',
                photos=[(portrait(), 'good.jpg'), (io.BytesIO(raw), 'bad.jpg')])
    response = authorized.post('/children/1/edit', data=form)
    assert response.status_code == 200
    with app.app_context():
        p = db.session.scalar(select(Photo))
        assert (p.id, p.filename, p.embedding) == before
        assert photo_storage.photo_path(p.filename).read_bytes() == original_file
        assert db.session.get(Child, 1).first_name == 'Азиз'
    assert len(list((app.config['DATA_DIR'] / 'photos').glob('*.jpg'))) == 1


def test_missing_models_is_controlled_error(authorized, app, tmp_path):
    app.extensions['face'].model_dir = tmp_path / 'missing-models'
    response = add_child(authorized, photos=[(portrait(), 'valid.jpg')])
    assert response.status_code == 200
    assert 'Модели распознавания не установлены' in response.text
    with app.app_context(): assert db.session.scalar(select(func.count(Child.id))) == 0


def test_backfill_preserves_files_existing_embeddings_and_invalid_photos(authorized, app):
    assert add_child(authorized, photos=[(portrait(), 'valid.jpg')]).status_code == 302
    with app.app_context():
        original = db.session.scalar(select(Photo))
        embedding = original.embedding
        files = {}
        for name, upload in [('legacy.jpg', portrait()), ('blank.jpg', image_bytes())]:
            data = photo_storage.prepare(FileStorage(stream=upload))
            photo_storage.save(name, data)
            files[name] = data
            db.session.add(Photo(child_id=1, filename=name))
        db.session.commit()
    result = app.test_cli_runner().invoke(args=['enroll-photos'])
    assert result.exit_code == 1 and 'Enrolled: 1; rejected: 1' in result.output
    with app.app_context():
        assert db.session.get(Photo, 1).embedding == embedding
        assert db.session.scalar(select(Photo).where(Photo.filename == 'legacy.jpg')).embedding is not None
        assert db.session.scalar(select(Photo).where(Photo.filename == 'blank.jpg')).embedding is None
        for name, data in files.items(): assert photo_storage.photo_path(name).read_bytes() == data
    result = app.test_cli_runner().invoke(args=['enroll-photos'])
    assert 'Enrolled: 0; rejected: 1' in result.output


def test_500_children_match_uses_only_stored_vectors(app, monkeypatch):
    rng = np.random.default_rng(42)
    vectors = rng.normal(size=(1500,128)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    with app.app_context():
        for child_id in range(1, 501):
            db.session.add(Child(group_id=1, id=child_id, first_name='Test', last_name=str(child_id),
                registration_date=date(2026,1,1), attendance_price=1, status='active'))
        db.session.flush()
        for i, vector in enumerate(vectors):
            db.session.add(Photo(child_id=i//3+1, filename=f'{i}.jpg', embedding=vector.tobytes(), model=MODEL))
        db.session.commit()
        def forbidden(*args, **kwargs): raise AssertionError('Reference photos must not be reprocessed')
        monkeypatch.setattr(app.extensions['face'], 'extract', forbidden)
        monkeypatch.setattr(app.extensions['face'], 'load', forbidden)
        monkeypatch.setattr(photo_storage, 'photo_path', forbidden)
        assert app.extensions['face'].match(vectors[750])[0] == 251
        candidate = vectors[750] * .6 + vectors[751] * .8
        candidate /= np.linalg.norm(candidate)
        app.config['FACE_THRESHOLD'] = .99
        assert app.extensions['face'].match(candidate)[0] is None
        app.config['FACE_THRESHOLD'] = .50
        assert app.extensions['face'].match(candidate)[0] == 251
