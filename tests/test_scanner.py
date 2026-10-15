"""Attendance-enabled scanning API safety and rejected-match snapshot lifecycle."""
import io
from datetime import datetime
import numpy as np
import pytest
from sqlalchemy import select, func
from database import db
from database.models import Attendance, Scan, UnknownFace
from services.liveness import LivenessResult
from conftest import add_child, csrf, image_bytes


def scan(client):
    return client.post('/api/scan', data={'csrf_token':csrf(client), 'photo':(image_bytes(), 'frame.jpg')})


def test_known_and_repeated_scans_create_one_attendance(authorized, app, fake_embedding):
    add_child(authorized)
    for status in ['success', 'already']:
        data = scan(authorized).json
        assert data['status'] == status
        assert data['name'] == 'Алиев Азиз Рустамович'
        assert data['confidence'] == pytest.approx(1)
        assert data['liveness'] == 'unavailable'
        assert datetime.fromisoformat(data['captured_at']).tzinfo is not None
    with app.app_context():
        assert db.session.scalar(select(func.count(Attendance.id))) == 1
        assert db.session.scalar(select(func.count(Scan.id))) == 1
        assert db.session.scalar(select(func.count(UnknownFace.id))) == 0


def test_unknown_snapshots_are_not_suppressed(authorized, app, fake_embedding):
    ids = []
    for _ in range(2):
        data = scan(authorized).json
        assert data['status'] == 'not_found'
        assert data['message'] == 'Ребёнок не найден в базе'
        ids.append(data['unknown_id'])
    assert len(set(ids)) == 2
    with app.app_context():
        rows = db.session.scalars(select(UnknownFace)).all()
        assert len(rows) == 2
        for row in rows:
            file = app.config['DATA_DIR'] / 'photos' / row.filename
            assert file.exists() and file.stat().st_mode & 0o777 == 0o600
            assert row.created_at.tzinfo is not None
            assert row.reason == 'not_found'
        assert db.session.scalar(select(func.count(Attendance.id))) == 0
    assert authorized.get(f'/unknown/media/{ids[0]}').status_code == 200
    assert app.test_client().get(f'/unknown/media/{ids[0]}').status_code == 302


def test_low_confidence_and_ambiguous_matches_hide_identity(authorized, app, fake_embedding, monkeypatch):
    add_child(authorized)
    vector = np.zeros(128,dtype=np.float32); vector[0]=.3; vector[1]=np.sqrt(1-.3**2)
    monkeypatch.setattr(app.extensions['face'], 'analyze', lambda image, scanning=True:(vector,LivenessResult('passed')))
    data = scan(authorized).json
    assert data['status'] == 'low_confidence'
    assert data['confidence'] == pytest.approx(.3)
    assert data['child_id'] is None and 'name' not in data and 'child_url' not in data
    assert data['unknown_id']
    with app.app_context(): assert db.session.get(UnknownFace, data['unknown_id']).reason == 'low_confidence'
    # Two children with indistinguishable references must not be assigned arbitrarily.
    add_child(authorized)
    monkeypatch.setattr(app.extensions['face'], 'analyze', lambda image, scanning=True:(fake_embedding,LivenessResult('passed')))
    data = scan(authorized).json
    assert data['status'] == 'low_confidence' and data['confidence'] == pytest.approx(1)
    with app.app_context(): assert db.session.scalar(select(func.count(Attendance.id))) == 0


def test_invalid_images_do_not_create_unknowns(authorized, app):
    for raw in [b'not a jpeg', image_bytes().getvalue()]:
        response = authorized.post('/api/scan', data={'csrf_token':csrf(authorized),'photo':(io.BytesIO(raw),'frame.jpg')})
        assert response.status_code == 422
    with app.app_context():
        assert db.session.scalar(select(func.count(UnknownFace.id))) == 0
        assert db.session.scalar(select(func.count(Scan.id))) == 0


def test_failed_snapshot_write_rolls_back(authorized, app, fake_embedding, monkeypatch):
    from services import photo_storage
    original = photo_storage.save
    def fail(filename, data):
        original(filename, data)
        raise OSError('test write failure')
    monkeypatch.setattr(photo_storage, 'save', fail)
    assert scan(authorized).status_code == 500
    assert not list((app.config['DATA_DIR'] / 'photos').glob('*.jpg'))
    with app.app_context(): assert db.session.scalar(select(func.count(UnknownFace.id))) == 0
