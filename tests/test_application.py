import io
from datetime import timedelta, datetime
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
import numpy as np
import pytest
from flask import url_for
from sqlalchemy import select, func, inspect
from sqlalchemy.exc import IntegrityError
from database import db
from database.models import Admin, Child, Photo, Attendance, Scan, UnknownFace, Audit
from services.attendance import register
from services.statistics import date_range, statistics
from services.localization import CATALOGS, LANGUAGES
from conftest import csrf, add_child, image_bytes, child_form


def count(model): return db.session.scalar(select(func.count()).select_from(model))


def scan(client):
    return client.post('/api/scan',data={'csrf_token':csrf(client),'photo':(image_bytes(),'scan.jpg')},content_type='multipart/form-data')


def test_auth_csrf_and_rate_limit(client, app):
    assert client.get('/children').status_code==302
    assert client.get('/media/1').status_code==302
    assert client.post('/login',data={'email':'admin@example.com','password':'a-test-password-123'}).status_code==400
    for _ in range(5):
        assert client.post('/login',data={'csrf_token':csrf(client),'email':'admin@example.com','password':'wrong'}).status_code==200
    assert client.post('/login',data={'csrf_token':csrf(client),'email':'admin@example.com','password':'wrong'}).status_code==429
    with app.app_context(): assert db.session.get(Admin,1).password.startswith('scrypt:')


def test_three_languages_and_persistence(authorized, app, fake_embedding):
    assert set(CATALOGS)=={'ru','uz_lat','uz_cyr'}
    assert all(set(catalog)==set(CATALOGS['ru']) for catalog in CATALOGS.values())
    assert '<html lang="ru">' in authorized.get('/').text
    add_child(authorized)
    for language in ('ru','uz_lat','uz_cyr'):
        response=authorized.post('/language/'+language,data={'csrf_token':csrf(authorized),'next':'/children'})
        assert response.status_code==302
        for path in ['/','/statistics','/children','/children/new','/children/1','/children/1/edit','/scanner','/unknown','/audit','/missing']:
            page=authorized.get(path)
            assert page.status_code==(404 if path=='/missing' else 200),path
            assert f'<html lang="{LANGUAGES[language][1]}">' in page.text
            assert '/language/en' not in page.text
            assert 'RU' in page.text and 'UZ' in page.text and 'ЎЗ' in page.text
        assert CATALOGS[language]['children'] in authorized.get('/children').text
        first=scan(authorized)
        assert first.json['message']==CATALOGS[language]['success' if language=='ru' else 'already']
        assert scan(authorized).json['message']==CATALOGS[language]['already']
        with authorized.session_transaction() as session: assert session['lang']==language
    # A previous English cookie cannot keep English enabled.
    with authorized.session_transaction() as session: session['lang']='en'
    assert '<html lang="ru">' in authorized.get('/').text
    authorized.post('/language/en',data={'csrf_token':csrf(authorized)})
    with authorized.session_transaction() as session: assert session['lang']=='ru'


def test_full_flow_parent_fields_and_decimal_snapshot(authorized, app, fake_embedding):
    client=authorized
    assert add_child(client,price='25000.15').status_code==302
    with app.app_context():
        assert count(Photo)==1
        assert len(db.session.get(Photo,1).embedding) == 512
        child=db.session.get(Child,1)
        assert child.middle_name=='Рустамович'
        assert child.guardians[0].full_name=='Алиев Рустам Каримович'
        assert child.attendance_price==Decimal('25000.15')
    assert client.get('/media/1').status_code==200
    assert scan(client).json['status']=='success'
    assert scan(client).json['status']=='already'
    with app.app_context():
        assert count(Attendance)==1 and count(Scan)==1
        assert db.session.get(Attendance,1).amount==Decimal('25000.15')
    edit=dict(child_form('30000.29'),csrf_token=csrf(client),guardian_0_first_name='Рустамжон',middle_name='Рустамжонович')
    assert client.post('/children/1/edit',data=edit).status_code==302
    with app.app_context():
        assert db.session.get(Attendance,1).amount==Decimal('25000.15')
        assert db.session.get(Child,1).guardians[0].first_name=='Рустамжон'
        entry=db.session.scalar(select(Audit).where(Audit.action=='tariff_changed'))
        assert entry.details=={'old':'25000.15','new':'30000.29'}
    assert 'Рустамжон' in client.get('/children/1').text
    assert 'Рустамжонович' in client.get('/children').text
    for period in ['today','yesterday','7days','30days','month','previous_month','year','custom&start=2026-01-01&end=2026-10-13']:
        assert client.get('/?period='+period).status_code==200
    assert client.post('/children/1/status',data={'csrf_token':csrf(client), 'status':'inactive'}).status_code==302
    assert scan(client).json['status']=='not_found'
    assert client.get('/unknown').status_code==200
    assert client.get('/unknown/media/1').status_code==200
    assert client.post('/unknown/1/review',data={'csrf_token':csrf(client)}).status_code==302
    with app.app_context():
        assert db.session.get(UnknownFace,1).reviewed
        assert count(Child)==1 and count(Attendance)==1
    client.post('/logout',data={'csrf_token':csrf(client)})
    assert client.get('/unknown/media/1').status_code==302


def test_invalid_uploads_and_required_parent_names(authorized, app, fake_embedding):
    for photos in [[(io.BytesIO(b'not image'),'bad.jpg')],[(image_bytes(),f'{i}.jpg') for i in range(4)]]:
        assert add_child(authorized,photos=photos).status_code==200
    for price in ['NaN','-1','123.456','Infinity','99999999999999']:
        assert add_child(authorized,price=price).status_code==200
    for overrides in [{'guardian_0_relation_type':'invalid'},{'group_id':''},{'middle_name':'x'*81},{'status':'other'}]:
        assert add_child(authorized,overrides=overrides).status_code==200
    with app.app_context(): assert count(Child)==0
    assert not list((app.config['DATA_DIR']/'photos').glob('*.jpg'))
    assert add_child(authorized,overrides={'middle_name':'','guardian_0_middle_name':'','guardian_0_phone':''}).status_code==302


def test_unknown_and_ambiguous_do_not_accrue(authorized, app, fake_embedding, monkeypatch):
    add_child(authorized); add_child(authorized)
    assert scan(authorized).json['status']=='low_confidence'
    with app.app_context(): assert count(Attendance)==0 and count(UnknownFace)==1
    feature=np.zeros(128,dtype=np.float32); feature[1]=1
    monkeypatch.setattr(app.extensions['face'],'extract',lambda image,scanning=False:feature)
    for language in ['uz_lat','uz_cyr']:
        authorized.post('/language/'+language,data={'csrf_token':csrf(authorized)})
        assert scan(authorized).json['message']==CATALOGS[language]['low_confidence']
    with app.app_context(): assert count(Attendance)==0


def test_concurrency_cooldown_and_new_day(authorized, app, fake_embedding, monkeypatch):
    add_child(authorized)
    fixed=datetime(2026,10,13,23,58,tzinfo=ZoneInfo('Asia/Tashkent'))
    monkeypatch.setattr('services.attendance.now',lambda:fixed)
    def record():
        with app.app_context(): return register(1,.9)
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes=list(pool.map(lambda _:record(),range(8)))
    assert outcomes.count('success')==1
    fixed+=timedelta(minutes=1)
    assert record()=='already'
    fixed+=timedelta(minutes=2)
    assert record()=='success'
    fixed+=timedelta(minutes=16)
    assert record()=='already'
    with app.app_context(): assert count(Attendance)==2 and count(Scan)==3


def test_postgres_constraints_indexes_and_single_admin(authorized, app, fake_embedding):
    add_child(authorized)
    with app.app_context():
        assert db.engine.dialect.name=='postgresql'
        inspector=inspect(db.engine)
        constraints=inspector.get_unique_constraints('attendance')
        assert any(c['column_names']==['child_id','attendance_date'] for c in constraints)
        for table,columns in [('children',{'status','created_at'}),('attendance',{'child_id','attendance_date','created_at'}),('scans',{'child_id','created_at','status'})]:
            indexed={col for idx in inspector.get_indexes(table) for col in idx['column_names']}
            assert columns<=indexed
        for table,column in [('children','attendance_price'),('attendance','amount')]:
            sqltype=next(c['type'] for c in inspector.get_columns(table) if c['name']==column)
            assert sqltype.precision==14 and sqltype.scale==2
        register(1,.95)
        row=db.session.scalar(select(Attendance))
        db.session.add(Attendance(child_id=1,attendance_date=row.attendance_date,amount=Decimal('0.10')))
        with pytest.raises(IntegrityError): db.session.commit()
        db.session.rollback()
        db.session.add(Admin(id=2,email='second@example.com',password='hash'))
        with pytest.raises(IntegrityError): db.session.commit()
        db.session.rollback()
        child=db.session.get(Child,1)
        db.session.delete(child)
        with pytest.raises(IntegrityError): db.session.commit()
        db.session.rollback()
        assert count(Attendance)==1 and count(Child)==1


def test_filters_timezone_decimal_totals_and_security(authorized, app, fake_embedding, monkeypatch):
    add_child(authorized,price='0.10')
    moment=datetime(2026,10,13,0,1,tzinfo=ZoneInfo('Asia/Tashkent'))
    monkeypatch.setattr('services.attendance.now',lambda:moment)
    monkeypatch.setattr('services.statistics.now',lambda:moment)
    with app.test_request_context():
        assert url_for('main.dashboard')=='/'
        assert url_for('main.statistics')=='/statistics'
    with app.app_context():
        register(1,.9)
        data=statistics('2026-10-13','2026-10-13')
        assert data['today']['amount']==Decimal('0.10')
        assert data['chart']==[{'day':'2026-10-13','count':1,'amount':'0.10'}]
        assert date_range({'period':'previous_month'})==('previous_month','2026-09-01','2026-09-30')
        with pytest.raises(ValueError): date_range({'period':'custom','start':'2026-10-13','end':'2026-01-01'})
        with pytest.raises(ValueError): date_range({'period':'custom','start':'2010-01-01','end':'2026-01-01'})
    response=authorized.get('/')
    assert response.headers['Cache-Control']=='no-store, private'
    assert response.headers['X-Frame-Options']=='DENY'
    assert 'frame-ancestors' in response.headers['Content-Security-Policy']
    form=authorized.get('/children/new').text
    for label in ['Фамилия ребёнка','Имя ребёнка','Отчество ребёнка','Группа','Родители и опекуны','Основной контакт','Телефон родителя','Дата регистрации','Тариф посещения','Статус','Фотографии ребёнка']:
        assert label in form
    assert 'Алиев' in authorized.get('/children?q=алиев').text
    assert authorized.get('/children?page=2').status_code==200
