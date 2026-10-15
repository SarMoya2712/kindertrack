"""Attendance API behavior against real PostgreSQL constraints and locks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import select, func, inspect
from sqlalchemy.exc import IntegrityError
from database import db
from database.models import Attendance, Child, Scan
from conftest import add_child, csrf, image_bytes, child_form


def scan(client):
    return client.post('/api/scan', data={'csrf_token':csrf(client), 'photo':(image_bytes(), 'scan.jpg')})


def count(model):
    return db.session.scalar(select(func.count()).select_from(model))


def test_first_repeat_cooldown_boundary_and_later_same_day(authorized, app, fake_embedding, monkeypatch):
    add_child(authorized, price='12345.67')
    moment = datetime(2026,10,13,10,0,tzinfo=ZoneInfo('Asia/Tashkent'))
    monkeypatch.setattr('services.attendance.now', lambda:moment)
    assert scan(authorized).json['status'] == 'success'
    for elapsed, expected_events in [(0,1),(899,1),(900,2),(901,2),(3600,3)]:
        moment = datetime(2026,10,13,10,0,tzinfo=ZoneInfo('Asia/Tashkent')) + timedelta(seconds=elapsed)
        response = scan(authorized)
        assert response.json['status'] == 'already'
        assert response.json['message'] == 'Ребёнок уже отмечен сегодня'
        with app.app_context():
            assert count(Attendance) == 1
            assert count(Scan) == expected_events
            row = db.session.scalar(select(Attendance))
            assert row.amount == Decimal('12345.67')
            assert row.attendance_date.isoformat() == '2026-10-13'


def test_tariff_snapshot_and_midnight_within_cooldown(authorized, app, fake_embedding, monkeypatch):
    add_child(authorized, price='10000.15')
    moment = datetime(2026,10,13,23,59,tzinfo=ZoneInfo('Asia/Tashkent'))
    monkeypatch.setattr('services.attendance.now', lambda:moment)
    assert scan(authorized).json['status'] == 'success'
    form = dict(child_form('20000.29'), csrf_token=csrf(authorized))
    assert authorized.post('/children/1/edit', data=form).status_code == 302
    assert scan(authorized).json['status'] == 'already'
    moment += timedelta(minutes=2)
    assert scan(authorized).json['status'] == 'success'
    assert scan(authorized).json['status'] == 'already'
    with app.app_context():
        rows = db.session.scalars(select(Attendance).order_by(Attendance.attendance_date)).all()
        assert [(r.attendance_date.isoformat(), r.amount) for r in rows] == [
            ('2026-10-13',Decimal('10000.15')), ('2026-10-14',Decimal('20000.29'))]
        assert count(Scan) == 2
        assert db.session.get(Child,1).attendance_price == Decimal('20000.29')


def test_simultaneous_successful_scans_only_charge_once(authorized, app, fake_embedding):
    add_child(authorized, price='0.10')
    token = csrf(authorized)
    cookie = authorized.get_cookie('session').value
    def request(_):
        with app.test_client() as client:
            client.set_cookie('session',cookie)
            response = client.post('/api/scan', data={'csrf_token':token,'photo':(image_bytes(),'scan.jpg')})
            assert response.status_code == 200
            return response.json['status']
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(request, range(8)))
    assert statuses.count('success') == 1 and statuses.count('already') == 7
    with app.app_context():
        assert count(Attendance) == count(Scan) == 1
        assert db.session.scalar(select(Attendance.amount)) == Decimal('0.10')


def test_postgres_unique_constraint_rejects_direct_duplicate(authorized, app, fake_embedding):
    add_child(authorized)
    assert scan(authorized).json['status'] == 'success'
    with app.app_context():
        constraints = inspect(db.engine).get_unique_constraints('attendance')
        assert any(c['name']=='uq_attendance_child_date' and c['column_names']==['child_id','attendance_date'] for c in constraints)
        row = db.session.scalar(select(Attendance))
        original_amount = row.amount
        db.session.add(Attendance(child_id=row.child_id, attendance_date=row.attendance_date, amount=Decimal('999.99')))
        with pytest.raises(IntegrityError) as error:
            db.session.commit()
        assert error.value.orig.diag.constraint_name == 'uq_attendance_child_date'
        db.session.rollback()
        assert count(Attendance) == 1
        assert db.session.scalar(select(Attendance.amount)) == original_amount


def test_attendance_and_scan_roll_back_together(authorized, app, fake_embedding, monkeypatch):
    add_child(authorized)
    token = csrf(authorized)
    def fail(): raise RuntimeError('simulated commit failure')
    monkeypatch.setattr(db.session, 'commit', fail)
    assert authorized.post('/api/scan', data={'csrf_token':token,'photo':(image_bytes(),'scan.jpg')}).status_code == 500
    with app.app_context(): assert count(Attendance) == count(Scan) == 0


def test_inactive_and_low_confidence_never_accrue(authorized, app, fake_embedding):
    add_child(authorized)
    authorized.post('/children/1/status',data={'csrf_token':csrf(authorized),'status':'inactive'})
    assert scan(authorized).json['status'] == 'not_found'
    with app.app_context(): assert count(Attendance) == 0
