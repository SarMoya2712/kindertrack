"""Regression coverage for the October logic review."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import numpy as np
import pytest
from sqlalchemy import func, select

from conftest import add_child, child_form, csrf, image_bytes
from database import db
from database.models import Attendance, Audit, LoginAttempt, Photo
from services.audit import now


def test_parallel_successful_logins_do_not_lose_attempt_row(app):
    with app.app_context():
        db.session.add(LoginAttempt(key='127.0.0.1', failures=1, last_attempt_at=now()))
        db.session.commit()
    ready = Barrier(8)

    def attempt(_):
        client = app.test_client()
        token = csrf(client)
        ready.wait(timeout=10)
        return client.post('/login', data={'csrf_token': token,
            'email': 'admin@example.com', 'password': 'a-test-password-123'}).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(attempt, range(8))) == [302] * 8


@pytest.mark.parametrize('price', ['0', '10'])
def test_zero_tariff_is_preserved_in_edit_audit(authorized, app, fake_embedding, price):
    assert add_child(authorized, price='0').status_code == 302
    form = dict(child_form(price), csrf_token=csrf(authorized))
    assert authorized.post('/children/1/edit', data=form).status_code == 302
    with app.app_context():
        entry = db.session.scalar(select(Audit).where(Audit.action == 'child_edited'))
        if price == '0':
            assert 'attendance_price' not in entry.old_value
            assert 'attendance_price' not in entry.new_value
        else:
            assert entry.old_value['attendance_price'] == '0.00'
            assert entry.new_value['attendance_price'] == '10.00'


@pytest.mark.parametrize('bad', [b'bad', np.full(128, np.nan, dtype=np.float32).tobytes(),
    np.full(128, np.inf, dtype=np.float32).tobytes(), np.zeros(128, dtype=np.float32).tobytes(),
    (np.eye(1, 128, dtype=np.float32)[0] * 2).tobytes()],
    ids=['truncated', 'nan', 'infinity', 'zero', 'non_unit'])
def test_corrupt_reference_cannot_break_or_override_valid_match(authorized, app, fake_embedding, bad):
    assert add_child(authorized).status_code == 302
    assert add_child(authorized).status_code == 302
    with app.app_context():
        photo = db.session.scalar(select(Photo).where(Photo.child_id == 2))
        photo.embedding = bad
        db.session.commit()
        result = app.extensions['face'].compare(fake_embedding)
        assert result['child_id'] == 1
        assert result['confidence'] == pytest.approx(1)
        db.session.scalar(select(Photo).where(Photo.child_id == 1)).embedding = bad
        db.session.commit()
        assert app.extensions['face'].compare(fake_embedding) == {
            'child_id': None, 'confidence': 0.0, 'status': 'not_found'}


def test_replaced_reference_is_rechecked_before_attendance(authorized, app, fake_embedding, monkeypatch):
    assert add_child(authorized).status_code == 302
    service = app.extensions['face']
    original_compare = service.compare
    replaced = False

    def compare_then_replace(feature):
        nonlocal replaced
        result = original_compare(feature)
        if not replaced:
            replaced = True
            # A separate committed edit between matching and the scanner's lock.
            with app.app_context():
                vector = np.zeros(128, dtype=np.float32)
                vector[1] = 1
                photo = db.session.scalar(select(Photo).where(Photo.child_id == 1))
                photo.embedding = vector.tobytes()
                db.session.commit()
        return result

    monkeypatch.setattr(service, 'compare', compare_then_replace)
    response = authorized.post('/api/scan', data={'csrf_token': csrf(authorized),
        'photo': (image_bytes(), 'frame.jpg')})
    assert response.status_code == 200
    assert response.json['child_id'] is None
    assert 'name' not in response.json
    with app.app_context():
        assert db.session.scalar(select(func.count(Attendance.id))) == 0
