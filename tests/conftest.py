import io
import os
from pathlib import Path
from uuid import uuid4
import numpy as np
import pytest
from PIL import Image
from werkzeug.security import generate_password_hash
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from flask_migrate import upgrade
from app import create_app
from database import db
from database.models import Admin, Group

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app(tmp_path):
    url = os.getenv('TEST_DATABASE_URL')
    if not url or make_url(url).get_backend_name() != 'postgresql' or not make_url(url).database.endswith('_test'):
        raise pytest.UsageError('Set TEST_DATABASE_URL to a separate PostgreSQL database ending in _test.')
    production = os.getenv('DATABASE_URL')
    if production:
        prod, test = make_url(production), make_url(url)
        if (prod.host,prod.port,prod.database)==(test.host,test.port,test.database):
            raise pytest.UsageError('Tests must not use the application database.')
    schema = 'test_' + uuid4().hex
    engine = create_engine(url)
    with engine.begin() as conn: conn.execute(text(f'CREATE SCHEMA {schema}'))
    app = create_app({'TESTING':True, 'SECRET_KEY':'test-key', 'SESSION_COOKIE_SECURE':False,
        'DATA_DIR':tmp_path, 'SQLALCHEMY_DATABASE_URI':url,
        'SQLALCHEMY_ENGINE_OPTIONS':{'connect_args':{'options':f'-csearch_path={schema}'}}})
    try:
        with app.app_context():
            upgrade(directory=str(ROOT/'migrations'))
            db.session.add(Admin(id=1,email='admin@example.com',password=generate_password_hash('a-test-password-123')))
            db.session.add(Group(name='Тестовая группа', is_active=True))
            db.session.commit()
        yield app
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
        with engine.begin() as conn: conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        engine.dispose()


@pytest.fixture
def client(app): return app.test_client()


def csrf(client):
    client.get('/login', follow_redirects=True)
    with client.session_transaction() as session: return session['csrf']


def login(client):
    response=client.post('/login',data={'csrf_token':csrf(client),'email':'admin@example.com','password':'a-test-password-123'})
    assert response.status_code==302
    return csrf(client)


def image_bytes(color='red'):
    output=io.BytesIO();Image.new('RGB',(200,200),color).save(output,'JPEG');output.seek(0);return output


@pytest.fixture
def authorized(client):
    login(client)
    return client


@pytest.fixture
def fake_embedding(app,monkeypatch):
    feature=np.zeros(128,dtype=np.float32);feature[0]=1
    from services.liveness import LivenessResult
    monkeypatch.setattr(app.extensions['face'],'analyze',lambda image,scanning=True:(feature, LivenessResult('unavailable')))
    monkeypatch.setattr(app.extensions['face'],'extract',lambda image,scanning=False:feature)
    return feature


def child_form(price='25000'):
    return {'group_id':'1', 'guardian_index':['0'], 'guardian_0_relation_type':'father', 'guardian_0_is_primary':'on', 'first_name':'Азиз','last_name':'Алиев','middle_name':'Рустамович',
        'guardian_0_first_name':'Рустам','guardian_0_last_name':'Алиев','guardian_0_middle_name':'Каримович',
        'guardian_0_phone':'+998901234567','registration_date':'2026-01-01','attendance_price':price,'status':'active'}


def add_child(client,price='25000',photos=None,overrides=None):
    data=dict(child_form(price),csrf_token=csrf(client),photos=photos if photos is not None else [(image_bytes(),'portrait.jpg')])
    data.update(overrides or {})
    return client.post('/children/new',data=data,content_type='multipart/form-data')

