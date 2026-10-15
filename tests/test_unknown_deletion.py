from datetime import timedelta, datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import pytest
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError
from database import db
from database.models import UnknownFace, Photo, Child, Attendance, Scan, Audit
from services.audit import now
from services.photo_storage import save, photo_path
from conftest import csrf, add_child, image_bytes


def seed(app, ages=(0,), missing=False):
    with app.app_context():
        ids=[]
        for index, age in enumerate(ages):
            row=UnknownFace(filename=f'unknown-{index}.jpg',created_at=now()-timedelta(days=age))
            db.session.add(row);db.session.flush();ids.append(row.id)
            if not missing: save(row.filename,image_bytes().getvalue())
        db.session.commit()
        return ids


def post(client, path, **data):
    return client.post(path,data={'csrf_token':csrf(client),'confirmed':'yes',**data},follow_redirects=True)


@pytest.mark.parametrize('missing',[False,True])
def test_single_and_missing(app,authorized,missing):
    face=seed(app,missing=missing)[0]
    response=post(authorized,f'/unknown/{face}/delete')
    assert response.status_code==200 and 'Снимок удалён' in response.text
    with app.app_context():
        assert db.session.get(UnknownFace,face) is None
        assert not photo_path('unknown-0.jpg').exists()
        audit=db.session.scalar(select(Audit).where(Audit.action=='unknown_deleted'))
        assert audit.entity_id==face and audit.old_value['snapshot_ids']==[face]


def test_bulk_preserves_registered_data(app,authorized,fake_embedding):
    add_child(authorized)
    ids=seed(app,(0,0,0))
    with app.app_context():
        db.session.add(Attendance(child_id=1,attendance_date=now().date(),amount=25000))
        db.session.add(Scan(child_id=1,status='success'))
        db.session.add(Scan(status='not_found'))
        db.session.commit()
        filename=db.session.scalar(select(Photo.filename))
    post(authorized,'/unknown/delete',face_ids=[ids[0],ids[2]])
    with app.app_context():
        assert db.session.scalars(select(UnknownFace.id)).all()==[ids[1]]
        assert not photo_path('unknown-0.jpg').exists() and not photo_path('unknown-2.jpg').exists()
        assert photo_path(filename).exists()
        for model,count in [(Child,1),(Photo,1),(Attendance,1),(Scan,2)]:
            assert db.session.scalar(select(func.count()).select_from(model))==count
        assert db.session.scalar(select(Audit.id).where(Audit.action=='unknown_bulk_deleted'))


@pytest.mark.parametrize('days',[7,30,90])
def test_cleanup_age(app,authorized,days):
    ids=seed(app,(days+1,days-1,0))
    post(authorized,'/unknown/cleanup',cleanup_period=str(days))
    with app.app_context():
        assert db.session.scalars(select(UnknownFace.id).order_by(UnknownFace.id)).all()==ids[1:]
        assert not photo_path('unknown-0.jpg').exists()
        assert db.session.scalar(select(Audit.id).where(Audit.action=='unknown_cleanup'))


def test_custom_local_inclusive_range(app,authorized):
    ids=seed(app,(0,0,0,0))
    zone=ZoneInfo('Asia/Tashkent')
    with app.app_context():
        for id_,stamp in zip(ids,['2026-10-01T23:59:59','2026-10-02T00:00:00','2026-10-02T23:59:59','2026-10-03T00:00:00']):
            db.session.get(UnknownFace,id_).created_at=datetime.fromisoformat(stamp).replace(tzinfo=zone)
        db.session.commit()
    post(authorized,'/unknown/cleanup',cleanup_period='custom',cleanup_start='2026-10-02',cleanup_end='2026-10-02')
    with app.app_context():
        assert db.session.scalars(select(UnknownFace.id).order_by(UnknownFace.id)).all()==[ids[0],ids[3]]


def test_security_validation_and_photo_alias(app,authorized,fake_embedding):
    id_=seed(app)[0]
    anon=app.test_client()
    assert anon.post(f'/unknown/{id_}/delete',data={'csrf_token':csrf(anon),'confirmed':'yes'}).status_code==302
    assert authorized.post(f'/unknown/{id_}/delete').status_code==400
    assert authorized.get(f'/unknown/{id_}/delete').status_code==405
    for data in [{'confirmed':'no'},{'face_ids':['bad']},{'face_ids':['999999999999999999']}]:
        assert 'Не удалось удалить снимок' in post(authorized,'/unknown/delete',**data).text
    assert 'Не удалось удалить снимок' in post(authorized,'/unknown/cleanup',cleanup_period='custom',cleanup_start='bad',cleanup_end='bad').text
    add_child(authorized)
    with app.app_context():
        filename=db.session.scalar(select(Photo.filename))
        db.session.get(UnknownFace,id_).filename=filename;db.session.commit()
    assert 'Не удалось удалить снимок' in post(authorized,f'/unknown/{id_}/delete').text
    with app.app_context():
        assert photo_path(filename).exists() and db.session.get(UnknownFace,id_)


def test_commit_failure_restores_files(app,authorized,monkeypatch):
    id_=seed(app)[0]
    token=csrf(authorized)
    with app.app_context():
        def fail(): raise SQLAlchemyError('test failure')
        monkeypatch.setattr(db.session,'commit',fail)
        response=authorized.post(f'/unknown/{id_}/delete',data={'csrf_token':token,'confirmed':'yes'})
        assert response.status_code==302
        assert db.session.get(UnknownFace,id_) is not None
        assert photo_path('unknown-0.jpg').exists()
        assert not db.session.scalar(select(Audit.id).where(Audit.action=='unknown_deleted'))


@pytest.mark.parametrize('unsafe',['../outside.jpg','symlink.jpg'])
def test_unsafe_file_refused(app,authorized,unsafe):
    id_=seed(app)[0]
    outside=Path(app.config['DATA_DIR'])/'outside.jpg';outside.write_bytes(b'protected')
    with app.app_context():
        if unsafe=='symlink.jpg': photo_path(unsafe).symlink_to(outside)
        db.session.get(UnknownFace,id_).filename=unsafe;db.session.commit()
    assert 'Не удалось удалить снимок' in post(authorized,f'/unknown/{id_}/delete').text
    assert outside.read_bytes()==b'protected'
    with app.app_context(): assert db.session.get(UnknownFace,id_)


def test_file_failure_rolls_back_entire_selection(app,authorized,monkeypatch):
    ids=seed(app,(0,0))
    original=Path.unlink
    def fail_second(path,*args,**kwargs):
        if path.name=='unknown-1.jpg': raise PermissionError('test failure')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',fail_second)
    response=post(authorized,'/unknown/delete',face_ids=ids)
    assert 'Не удалось удалить снимок' in response.text
    with app.app_context():
        assert db.session.scalars(select(UnknownFace.id).order_by(UnknownFace.id)).all()==ids
        assert photo_path('unknown-0.jpg').exists() and photo_path('unknown-1.jpg').exists()


def test_deletion_labels_in_all_languages(app,authorized):
    from services.localization import CATALOGS
    from html import unescape
    seed(app)
    for language in ('ru','uz_lat','uz_cyr'):
        authorized.post('/language/'+language,data={'csrf_token':csrf(authorized),'next':'/unknown'})
        page=unescape(post(authorized,'/unknown/delete',face_ids=['invalid']).text)
        for key in ('delete_snapshot','confirm_snapshot_delete','snapshot_delete_failed','delete_selected','cleanup_snapshots'):
            # Check rendered labels and the actual localized error flash.
            assert CATALOGS[language][key] in page
