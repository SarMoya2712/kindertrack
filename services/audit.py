"""Transactional, structured audit events; never accept credentials or raw errors."""
from flask import session, current_app, has_request_context
from datetime import datetime
from zoneinfo import ZoneInfo
from database import db
from database.models import Audit

ACTIONS = ('admin_login', 'admin_logout', 'child_created', 'child_edited',
           'child_activated', 'child_deactivated', 'tariff_changed',
           'child_photos_updated', 'scanner_error', 'system_error', 'guardians_updated',
           'group_created', 'group_edited', 'group_activated', 'group_deactivated',
           'unknown_deleted', 'unknown_bulk_deleted', 'unknown_cleanup')


def now():
    return datetime.now(ZoneInfo(current_app.config['TIMEZONE']))


def record(action, child_id=None, details=None, *, entity_type=None, entity_id=None,
           old_value=None, new_value=None, admin_id=None):
    if action not in ACTIONS:
        raise ValueError('Unsupported audit action')
    if admin_id is None and has_request_context():
        admin_id = session.get('admin_id')
    if action == 'tariff_changed' and details:
        old_value, new_value = details.get('old'), details.get('new')
    db.session.add(Audit(admin_id=admin_id, action=action, child_id=child_id,
        entity_type=entity_type or ('child' if child_id is not None else 'system'),
        entity_id=entity_id if entity_id is not None else child_id,
        old_value=old_value, new_value=new_value, details=details or {}, created_at=now()))


def error_event(action, code):
    """Best-effort isolated error audit; code must be a fixed, non-sensitive key."""
    if code not in {'models_missing', 'liveness_failed', 'storage_error', 'unexpected_error'}:
        code = 'unexpected_error'
    db.session.rollback()
    try:
        record(action, entity_type='scanner' if action=='scanner_error' else 'system', details={'code':code})
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.error('Audit error could not be persisted')
