"""Phase 2/3 coverage: PostgreSQL configuration and single-admin authentication."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from sqlalchemy import delete, func, select
from werkzeug.security import check_password_hash
from app import create_app
from config import Config
from database import db
from database.models import Admin, AdminSession, LoginAttempt
from services.audit import now
from conftest import csrf, login


def test_first_admin_cli_hashes_password_and_prevents_second_admin(app):
    with app.app_context():
        db.session.execute(delete(Admin))
        db.session.commit()
    runner = app.test_cli_runner()
    invalid = runner.invoke(args=['create-admin', '--email', 'bad-email'], input='short\nshort\n')
    assert invalid.exit_code != 0
    password = 'new-admin-password-123'
    created = runner.invoke(args=['create-admin', '--email', ' Admin@Example.COM '], input=password+'\n'+password+'\n')
    assert created.exit_code == 0, created.output
    assert password not in created.output
    with app.app_context():
        admin = db.session.get(Admin, 1)
        assert admin.email == 'admin@example.com'
        assert admin.password.startswith('scrypt:') and check_password_hash(admin.password, password)
    duplicate = runner.invoke(args=['create-admin', '--email', 'other@example.com'], input=password+'\n'+password+'\n')
    assert duplicate.exit_code != 0
    with app.app_context(): assert db.session.scalar(select(func.count(Admin.id))) == 1


def test_authenticated_session_rotates_csrf_and_logout_revokes_cookie(client, app):
    old_csrf = csrf(client)
    response = client.post('/login', data={'csrf_token':old_csrf,'email':'ADMIN@example.com','password':'a-test-password-123'})
    assert response.status_code == 302
    client.get('/')
    with client.session_transaction() as state:
        new_csrf, token = state['csrf'], state['auth_token']
        assert state['admin_id'] == 1
    assert old_csrf != new_csrf
    with app.app_context():
        record = db.session.get(AdminSession, hashlib.sha256(token.encode()).hexdigest())
        assert record and record.expires_at > now()
        assert record.token_hash != token
    saved_cookie = client.get_cookie('session').value
    assert client.post('/logout', data={'csrf_token':old_csrf}).status_code == 400
    assert client.post('/logout', data={'csrf_token':new_csrf}).status_code == 302
    assert client.get('/children').status_code == 302
    replay = app.test_client()
    replay.set_cookie('session', saved_cookie)
    assert replay.get('/children').status_code == 302
    with app.app_context(): assert db.session.scalar(select(func.count(AdminSession.token_hash))) == 0


def test_expired_sessions_and_legacy_cookies_are_rejected(client, app):
    login(client)
    with app.app_context():
        record = db.session.scalar(select(AdminSession))
        record.expires_at = now() - timedelta(seconds=1)
        db.session.commit()
    assert client.get('/children').status_code == 302
    with client.session_transaction() as state:
        assert 'admin_id' not in state and 'auth_token' not in state
        state.update(admin_id=1,email='admin@example.com')
    assert client.get('/children').status_code == 302


def test_unicode_missing_oversized_and_wrong_csrf_return_400(client, app):
    csrf(client)
    for token in ['', 'токен', 'notoʻgʻri', 'x'*129, 'wrong']:
        response = client.post('/login', data={'csrf_token':token,'email':'admin@example.com','password':'anything'})
        assert response.status_code == 400
    with app.app_context(): assert db.session.scalar(select(func.count(LoginAttempt.key))) == 0


def test_existing_routes_remain_protected_without_executing_features(client):
    for path in ['/', '/statistics', '/children', '/children/new', '/children/1', '/children/1/edit',
                 '/scanner', '/unknown', '/audit', '/media/1', '/unknown/media/1']:
        response = client.get(path)
        assert response.status_code == 302 and response.location.endswith('/login'), path
    token = csrf(client)
    for path in ['/logout','/children/new','/children/1/edit','/children/1/status','/unknown/1/review']:
        assert client.post(path,data={'csrf_token':token}).status_code == 302
    assert client.post('/api/scan',headers={'X-CSRF-Token':token}).status_code == 401


def test_parallel_failed_logins_are_rate_limited(app):
    def attempt(_):
        client = app.test_client()
        return client.post('/login',data={'csrf_token':csrf(client),'email':'admin@example.com','password':'wrong'}).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(attempt,range(8)))
    assert statuses.count(200) == 5 and statuses.count(429) == 3
    with app.app_context(): assert db.session.get(LoginAttempt, '127.0.0.1').failures == 5


def test_login_lockout_expires_and_success_clears_attempts(client, app):
    for _ in range(5):
        client.post('/login',data={'csrf_token':csrf(client),'email':'admin@example.com','password':'wrong'})
    with app.app_context():
        record = db.session.get(LoginAttempt, '127.0.0.1')
        record.last_attempt_at = now() - timedelta(minutes=16)
        db.session.commit()
    login(client)
    with app.app_context(): assert db.session.get(LoginAttempt,'127.0.0.1') is None


def test_cookie_flags_security_headers_and_localization(client, app):
    app.config['SESSION_COOKIE_SECURE'] = True
    response = client.get('/login', base_url='https://localhost')
    cookie = response.headers['Set-Cookie']
    assert 'Secure;' in cookie and 'HttpOnly;' in cookie and 'SameSite=Lax' in cookie
    assert response.headers['Cache-Control'] == 'no-store, private'
    assert response.headers['X-Frame-Options'] == 'DENY'
    assert response.headers['Strict-Transport-Security'] == 'max-age=31536000'
    assert Config.SESSION_REFRESH_EACH_REQUEST is False
    app.config['SESSION_COOKIE_SECURE'] = False
    for code, label in [('ru','Вход в систему'),('uz_lat','Tizimga kirish'),('uz_cyr','Тизимга кириш')]:
        client.post('/language/'+code, data={'csrf_token':csrf(client),'next':'/login'})
        assert label in client.get('/login').text
    login(client)
    token = csrf(client)
    client.post('/logout',data={'csrf_token':token})
    with client.session_transaction() as state: assert state['lang'] == 'uz_cyr'


def test_standard_postgres_url_uses_installed_psycopg_driver(app, tmp_path):
    standard = app.config['SQLALCHEMY_DATABASE_URI'].set(drivername='postgresql')
    normalized = create_app({'DATA_DIR':tmp_path,'SECRET_KEY':'test','SQLALCHEMY_DATABASE_URI':standard})
    with normalized.app_context():
        assert db.engine.dialect.name == 'postgresql'
        assert db.engine.dialect.driver == 'psycopg'
        db.engine.dispose()
