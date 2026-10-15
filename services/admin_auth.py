"""Single-admin credentials and revocable, time-limited sessions."""
import hashlib
import re
import secrets
from datetime import timedelta
from flask import current_app, g, session
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash
from database import db
from database.models import Admin, AdminSession, LoginAttempt
from services.audit import now, record
from services.localization import language

DUMMY_HASH = generate_password_hash('not-a-real-password')
MAX_PASSWORD_LENGTH = 1024


def create_admin(email, password):
    email = email.strip().lower()
    if len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise ValueError('Enter a valid email address.')
    if not 12 <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ValueError('Use a password between 12 and 1024 characters.')
    if db.session.get(Admin, 1):
        raise ValueError('The single administrator already exists.')
    admin = Admin(id=1, email=email, password=generate_password_hash(password, method='scrypt'))
    db.session.add(admin)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ValueError('The single administrator already exists.') from None
    return admin


def _token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def clear_session():
    selected_language = language()
    session.clear()
    session['lang'] = selected_language
    g.current_admin = None


def current_admin():
    if 'current_admin' in g:
        return g.current_admin
    g.current_admin = None
    token = session.get('auth_token')
    admin_id = session.get('admin_id')
    if not isinstance(token, str) or len(token) != 64 or admin_id != 1:
        if token or admin_id:
            clear_session()
        return None
    admin = db.session.scalar(select(Admin).join(AdminSession, AdminSession.admin_id == Admin.id).where(
        Admin.id == admin_id, AdminSession.token_hash == _token_hash(token), AdminSession.expires_at > now()))
    if admin is None:
        clear_session()
        return None
    g.current_admin = admin
    return admin


def authenticate(email, password, remote_address):
    """Return success/invalid_login/rate_limit without disclosing account existence."""
    moment = now()
    key = (remote_address or 'local')[:64]
    # Serialize attempts from one address, including the password check. Parallel
    # requests cannot all pass an earlier read of the same failed-attempt count.
    # Acquire the row lock in the upsert itself. DO NOTHING followed by SELECT
    # can lose the row when a concurrent successful login deletes it in between.
    db.session.execute(insert(LoginAttempt).values(key=key, failures=0, last_attempt_at=moment)
        .on_conflict_do_update(index_elements=[LoginAttempt.key], set_={'key': key}))
    attempt = db.session.scalar(select(LoginAttempt).where(LoginAttempt.key == key).with_for_update())
    moment = now()
    if moment - attempt.last_attempt_at >= timedelta(minutes=15):
        attempt.failures = 0
    if attempt.failures >= 5:
        db.session.rollback()
        return 'rate_limit'
    admin = db.session.scalar(select(Admin).where(Admin.email == email.strip().lower()))
    valid = len(password) <= MAX_PASSWORD_LENGTH and check_password_hash(
        admin.password if admin else DUMMY_HASH, password)
    if not admin or not valid:
        attempt.failures += 1
        attempt.last_attempt_at = moment
        db.session.commit()
        return 'invalid_login'

    token = secrets.token_hex(32)
    db.session.execute(delete(LoginAttempt).where(LoginAttempt.key == key))
    db.session.execute(delete(AdminSession).where(AdminSession.expires_at <= moment))
    db.session.add(AdminSession(token_hash=_token_hash(token), admin_id=admin.id, created_at=moment,
        expires_at=moment + current_app.config['PERMANENT_SESSION_LIFETIME']))
    record('admin_login', entity_type='admin', entity_id=admin.id, admin_id=admin.id)
    db.session.commit()
    clear_session()
    session.update(admin_id=admin.id, email=admin.email, auth_token=token)
    session.permanent = True
    g.current_admin = admin
    return 'success'


def logout_admin():
    token = session.get('auth_token')
    if isinstance(token, str):
        db.session.execute(delete(AdminSession).where(AdminSession.token_hash == _token_hash(token)))
        record('admin_logout', entity_type='admin', entity_id=session.get('admin_id'))
        db.session.commit()
    clear_session()
