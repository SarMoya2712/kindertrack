"""Server-side stability, bounded unknown capture, atomic attendance and workers."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
import threading
from zoneinfo import ZoneInfo
import numpy as np
import pytest
from sqlalchemy import select, func
from database import db
from database.models import Attendance, Scan, UnknownFace, LiveScannerState, AdminSession
from services.face_recognition import FaceError
from services.liveness import LivenessResult
from conftest import add_child, csrf, image_bytes


@pytest.fixture
def live(authorized, app, monkeypatch):
    moment=[datetime(2026,10,14,10,tzinfo=ZoneInfo('Asia/Tashkent'))]
    for module in ('live_scanner','scanner','attendance'):
        monkeypatch.setattr('services.'+module+'.now', lambda:moment[0])
    token=csrf(authorized)
    started=authorized.post('/api/live/start',headers={'X-CSRF-Token':token})
    assert started.status_code==200
    feed=started.json['feed_id']
    def capture(seconds=.6, client=authorized, feed_id=None):
        moment[0]+=timedelta(seconds=seconds)
        return client.post('/api/live/frame',data={'feed_id':feed_id or feed,'photo':(image_bytes(),'frame.jpg')},headers={'X-CSRF-Token':token})
    return capture,moment,feed


def counts():
    return tuple(db.session.scalar(select(func.count()).select_from(model)) for model in (Attendance,Scan,UnknownFace))


def test_live_stable_child_pause_resume_and_tariff(authorized, app, fake_embedding, live):
    add_child(authorized,price='25000')
    capture,moment,feed=live
    for expected in (1,2):
        data=capture().json
        assert data['status']=='checking' and data['stable_count']==expected
        assert 'name' not in data
        with app.app_context(): assert counts()==(0,0,0)
    data=capture().json
    assert data['status']=='success' and data['amount']=='25000.00'
    assert data['name']=='Алиев Азиз Рустамович' and data['group']=='Тестовая группа'
    assert data['photo_url']=='/media/1' and data['pause_ms']==2500
    with app.app_context():
        assert counts()==(1,1,0)
        assert db.session.scalar(select(Attendance.amount))==Decimal('25000')
    assert capture().json['retry_ms'] > 0
    for seconds in (3,.6): assert capture(seconds).json['status']=='checking'
    assert capture().json['message']=='Ребёнок уже отмечен сегодня'
    with app.app_context(): assert counts()==(1,1,0)


def test_unknown_stability_cooldown_different_faces_and_restart(authorized, app, fake_embedding, live, monkeypatch):
    capture,moment,feed=live
    assert capture().json['status']=='checking'
    assert capture().json['status']=='checking'
    first=capture().json
    assert first['saved'] and first['unknown_id']
    for seconds in (3,.6): assert capture(seconds).json['status']=='checking'
    repeat=capture().json
    assert repeat['status']=='not_found' and repeat['saved'] is False
    with app.app_context(): assert counts()==(0,1,1)
    # Camera restart keeps recent unknown fingerprints in this Admin session.
    new_feed=authorized.post('/api/live/start',headers={'X-CSRF-Token':csrf(authorized)}).json['feed_id']
    for i in range(3): result=capture(feed_id=new_feed).json
    assert result['saved'] is False
    vector=np.zeros(128,dtype=np.float32);vector[1]=1
    monkeypatch.setattr(app.extensions['face'],'analyze',lambda image,scanning=True:(vector,LivenessResult('unavailable')))
    for seconds in (3,.6,.6): result=capture(seconds,feed_id=new_feed).json
    assert result['saved'] and result['unknown_id']!=first['unknown_id']
    with app.app_context(): assert counts()==(0,2,2)
    for seconds in (61,.6,.6): result=capture(seconds,feed_id=new_feed).json
    assert result['saved']
    with app.app_context(): assert counts()==(0,3,3)


def test_inconsistent_absent_or_expired_candidates_never_confirm(authorized, app, fake_embedding, live, monkeypatch):
    add_child(authorized);add_child(authorized)
    chosen=[1]
    monkeypatch.setattr(app.extensions['face'],'compare',lambda feature:dict(child_id=chosen[0],status='recognized',confidence=.95))
    capture,moment,feed=live
    for child in (1,2,1,2):
        chosen[0]=child
        assert capture().json['stable_count']==1
    assert capture(6).json['stable_count']==1
    original=app.extensions['face'].analyze
    def no_face(*args,**kwargs): raise FaceError('no_face')
    monkeypatch.setattr(app.extensions['face'],'analyze',no_face)
    assert capture().json['message']=='Лицо не видно'
    monkeypatch.setattr(app.extensions['face'],'analyze',original)
    assert capture().json['stable_count']==1
    with app.app_context(): assert counts()==(0,0,0)


def test_unknown_different_embeddings_do_not_accumulate(app, fake_embedding, live, monkeypatch):
    capture,moment,feed=live
    for i in (0,1,0,1,0):
        vector=np.zeros(128,dtype=np.float32);vector[i]=1
        monkeypatch.setattr(app.extensions['face'],'analyze',lambda image,scanning=True:(vector,LivenessResult('unavailable')))
        assert capture().json['stable_count']==1
    with app.app_context(): assert counts()==(0,0,0)


def test_live_min_interval_and_concurrent_request_skip(authorized, app, fake_embedding, live):
    capture,moment,feed=live
    assert capture().json['stable_count']==1
    assert capture(.01).json['retry_ms']>0
    acquired=threading.Event();release=threading.Event()
    def hold():
        with app.app_context():
            db.session.scalar(select(LiveScannerState).with_for_update())
            acquired.set();release.wait(5);db.session.rollback()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(hold);assert acquired.wait(2)
        try:
            assert capture().json=={'status':'checking','retry_ms':800,'message':'Проверка...'}
        finally: release.set();future.result()
    assert capture().json['stable_count']==2


def test_feed_ownership_csrf_and_logout_cleanup(authorized, app, fake_embedding, live):
    capture,moment,feed=live
    assert app.test_client().post('/api/live/start').status_code==400
    assert authorized.post('/api/live/start').status_code==400
    assert capture(feed_id='f'*32).json['fatal']
    assert authorized.post('/api/live/frame',data={'csrf_token':csrf(authorized)}).status_code==400
    authorized.post('/logout',data={'csrf_token':csrf(authorized)})
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(LiveScannerState))==0
        assert db.session.scalar(select(func.count()).select_from(AdminSession))==0


def test_unknown_file_and_state_rollback_on_commit_failure(app, fake_embedding, live, monkeypatch):
    capture,moment,feed=live
    capture();capture()
    def fail(): raise RuntimeError('commit failed')
    monkeypatch.setattr(db.session,'commit',fail)
    assert capture().status_code==500
    with app.app_context(): assert counts()==(0,0,0)
    assert not list((app.config['DATA_DIR']/'photos').glob('*.jpg'))


def test_live_detection_states_and_liveness_fail_closed(app, fake_embedding, live, monkeypatch):
    capture,moment,feed=live
    for code in ('no_face','multiple_faces','closer','liveness_failed'):
        def reject(*args,**kwargs): raise FaceError(code)
        monkeypatch.setattr(app.extensions['face'],'analyze',reject)
        data=capture().json
        assert data['status']==code and data['fatal']==(code=='liveness_failed')
    with app.app_context(): assert counts()==(0,0,0)
