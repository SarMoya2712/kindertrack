"""Structured audit, filters, safe errors and authenticated private-file boundaries."""
from datetime import datetime, timedelta
import json
import pytest
from sqlalchemy import select, func
from sqlalchemy.exc import OperationalError
from database import db
from database.models import Audit, Photo, UnknownFace, AdminSession
from services.localization import CATALOGS
from services.photo_storage import photo_path, PhotoError
from conftest import csrf, add_child, child_form, image_bytes
from test_reports import contexts


def test_audit_actions_values_filters_and_logout(authorized, app, fake_embedding):
    assert add_child(authorized).status_code==302
    data=dict(child_form('30000.25'),csrf_token=csrf(authorized),first_name='Сардор',photos=[(image_bytes(),'new.jpg')])
    assert authorized.post('/children/1/edit',data=data).status_code==302
    for status in ['inactive','active']:
        authorized.post('/children/1/status',data={'csrf_token':csrf(authorized),'status':status})
    with app.app_context():
        entries=db.session.scalars(select(Audit).order_by(Audit.id)).all()
        by_action={row.action:row for row in entries}
        assert {'admin_login','child_created','child_edited','tariff_changed','child_photos_updated','child_activated','child_deactivated'}<=set(by_action)
        assert by_action['admin_login'].entity_type=='admin' and by_action['admin_login'].entity_id==1
        assert by_action['guardians_updated'].new_value['guardians'][0]['first_name']=='Рустам'
        assert by_action['child_edited'].old_value['first_name']=='Азиз'
        assert by_action['child_edited'].new_value['first_name']=='Сардор'
        assert by_action['tariff_changed'].old_value=='25000.00'
        assert by_action['tariff_changed'].new_value=='30000.25'
        assert by_action['child_photos_updated'].old_value=={'photo_count':1}
        assert by_action['child_photos_updated'].new_value=={'photo_count':2}
        assert by_action['child_deactivated'].old_value=='active'
        assert by_action['child_activated'].new_value=='active'
        assert all(row.created_at.tzinfo is not None for row in entries)
        serialized=json.dumps([dict(details=row.details,old=row.old_value,new=row.new_value) for row in entries])
        assert 'password' not in serialized and 'auth_token' not in serialized
    for period in ['today','7days','30days','custom']:
        with contexts(app) as rendered:
            response=authorized.get('/audit',query_string={'period':period,'action':'tariff_changed',
                'start':'2000-01-01' if period!='custom' else datetime.now().date().isoformat(),
                'end':datetime.now().date().isoformat()})
        assert response.status_code==200
        assert all(row.action=='tariff_changed' for row,child in rendered[-1]['rows'])
        if period!='custom': assert rendered[-1]['count']==1
    with contexts(app) as rendered:
        response=authorized.get('/audit?action=%27%20OR%201=1&period=custom&start=bad')
    assert response.status_code==200 and rendered[-1]['action']=='all'
    for lang in CATALOGS:
        authorized.post('/language/'+lang,data={'csrf_token':csrf(authorized)})
        page=authorized.get('/audit').text
        assert CATALOGS[lang]['child_photos_updated'] in page
        assert CATALOGS[lang]['old_value'] in page
    saved_cookie=authorized.get_cookie('session').value
    authorized.post('/logout',data={'csrf_token':csrf(authorized)})
    with app.app_context():
        row=db.session.scalar(select(Audit).where(Audit.action=='admin_logout'))
        assert row and row.entity_type=='admin'
        assert db.session.scalar(select(func.count(AdminSession.token_hash)))==0
    for path in ['/','/audit','/statistics','/unknown','/scanner','/children','/children/1','/media/1']:
        assert authorized.get(path).status_code==302
    replay=app.test_client();replay.set_cookie('session',saved_cookie)
    assert replay.get('/audit').status_code==302


def test_audit_period_boundaries(authorized, app, monkeypatch):
    from zoneinfo import ZoneInfo
    fixed=datetime(2026,10,13,12,tzinfo=ZoneInfo('Asia/Tashkent'))
    monkeypatch.setattr('services.statistics.now',lambda:fixed)
    with app.app_context():
        for days in [0,6,7,29,30]:
            db.session.add(Audit(action='scanner_error',entity_type='scanner',details={'code':'models_missing'},created_at=fixed-timedelta(days=days)))
        db.session.commit()
    for period,expected in [('today',1),('7days',2),('30days',4),('custom',3)]:
        with contexts(app) as rendered:
            authorized.get('/audit',query_string={'period':period,'action':'scanner_error','start':'2026-10-06','end':'2026-10-13'})
        assert rendered[-1]['count']==expected


def test_private_photos_reject_traversal_and_symlinks(authorized, app, fake_embedding, tmp_path):
    add_child(authorized)
    outside=tmp_path/'secret.txt';outside.write_text('private-secret')
    link=app.config['DATA_DIR']/'photos'/'link.jpg';link.symlink_to(outside)
    with app.app_context():
        for value in ['../secret.txt','/etc/passwd','..\\secret.txt','link.jpg','bad\x00.jpg']:
            with pytest.raises(PhotoError): photo_path(value)
        photo=db.session.get(Photo,1);photo.filename='../secret.txt';db.session.commit()
    response=authorized.get('/media/1')
    assert response.status_code==404 and 'private-secret' not in response.text
    with app.app_context():
        db.session.add(UnknownFace(filename='link.jpg'))
        db.session.commit()
    assert authorized.get('/unknown/media/1').status_code==404
    for path in ['/.env','/instance/session.key','/instance/photos/link.jpg','/static/../.env']:
        response=authorized.get(path)
        assert response.status_code==404


def test_input_validation_and_escaping(authorized, app, fake_embedding):
    for changes in [{'first_name':''},{'guardian_0_relation_type':'invalid'},{'guardian_0_phone':'<script>alert(1)</script>'},
                    {'first_name':'name\x00'},{'attendance_price':'NaN'},{'status':'invalid'},
                    {'registration_date':'2099-01-01'}]:
        assert add_child(authorized,overrides=changes).status_code==200
    assert add_child(authorized,overrides={'first_name':'<script>alert(1)</script>'}).status_code==302
    page=authorized.get('/children/1').text
    assert '<script>alert(1)</script>' not in page and '&lt;script&gt;' in page
    assert authorized.get('/children?q=%27%20OR%201=1').status_code==200
    assert authorized.get('/audit?page='+('9'*100)).status_code==200


def test_database_errors_are_safe_and_do_not_log_credentials(authorized, app, monkeypatch, caplog):
    token=csrf(authorized)
    credential='a-database-password-that-must-not-be-logged'
    def fail(*args,**kwargs):
        raise OperationalError('SELECT sensitive', {'password':credential}, Exception('postgresql://user:'+credential+'@host/db'))
    monkeypatch.setattr(db.session,'scalar',fail)
    response=authorized.get('/children')
    assert response.status_code==503
    assert CATALOGS['ru']['database_unavailable'] in response.text
    assert credential not in response.text and credential not in caplog.text
    response=authorized.post('/api/scan',headers={'X-CSRF-Token':token})
    assert response.status_code==503 and response.json['status']=='error'
    assert credential not in response.text


def test_actual_unreachable_postgres_connection(tmp_path, caplog):
    import socket
    from app import create_app
    # Reserve a port without listening: no unrelated running database is touched.
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1',0));port=reserved.getsockname()[1]
        app=create_app({'TESTING':True,'SECRET_KEY':'test-only','DATA_DIR':tmp_path,
            'SQLALCHEMY_DATABASE_URI':f'postgresql+psycopg://test:no-real-secret@127.0.0.1:{port}/unavailable',
            'SESSION_COOKIE_SECURE':False})
        client=app.test_client()
        response=client.post('/login',data={'csrf_token':csrf(client),'email':'admin@example.com','password':'do-not-log-this-password'})
        assert response.status_code==503
        assert 'no-real-secret' not in caplog.text and 'do-not-log-this-password' not in caplog.text
        with app.app_context(): db.engine.dispose()


def test_scanner_errors_audited_without_saving_invalid_frames(authorized, app, monkeypatch):
    from services.face_recognition import FaceError
    def fail(*args,**kwargs): raise FaceError('models_missing')
    monkeypatch.setattr(app.extensions['face'],'analyze',fail)
    response=authorized.post('/api/scan',data={'csrf_token':csrf(authorized),'photo':(image_bytes(),'frame.jpg')})
    assert response.status_code==422
    with app.app_context():
        row=db.session.scalar(select(Audit).where(Audit.action=='scanner_error'))
        assert row.details=={'code':'models_missing'} and row.entity_type=='scanner'
        assert db.session.scalar(select(func.count(UnknownFace.id)))==0


def test_localization_keys_and_safe_configuration_files(app):
    from jinja2 import nodes
    from services.localization import CATALOGS, LANGUAGES
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    assert set(LANGUAGES)=={'ru','uz_lat','uz_cyr'}
    for template in (root/'templates').glob('*.html'):
        tree=app.jinja_env.parse(template.read_text())
        for call in tree.find_all(nodes.Call):
            if isinstance(call.node,nodes.Name) and call.node.name=='t' and call.args and isinstance(call.args[0],nodes.Const):
                assert call.args[0].value in CATALOGS['ru'],(template.name,call.args[0].value)
    for script in (root/'static').glob('*.js'):
        import re
        for key in re.findall(r"tr\('([^']+)'\)",script.read_text()):
            assert key in CATALOGS['ru'],(script.name,key)
    example=(root/'.env.example').read_text()
    assert 'YOUR_PASSWORD' in example and 'SECRET_KEY=\n' in example
    assert 'COOKIE_SECURE=true' in example
    assert '.env' in (root/'.gitignore').read_text().splitlines()
    assert not (root/'translations/en.json').exists()
    assert not (root/'database/legacy_import.py').exists()


def test_oversized_request_is_localized(authorized, app):
    app.config['MAX_CONTENT_LENGTH']=128
    token=csrf(authorized)
    response=authorized.post('/children/new',data={'csrf_token':token,'padding':'x'*300})
    assert response.status_code==413 and CATALOGS['ru']['request_large'] in response.text
    assert authorized.get('/logout').status_code==405
