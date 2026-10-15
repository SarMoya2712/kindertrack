"""Live recognition confirmation, with PostgreSQL locks and bounded session state.

A feed is bound to the authenticated Admin session. Starting another feed in the
same session supersedes the previous one. Unknown fingerprints expire after the
cooldown, never become registered references, and disappear on logout.
"""
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4
import numpy as np
from flask import current_app, session
from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert
from database import db
from database.models import LiveScannerState, UnknownFace
from services.admin_auth import _token_hash
from services.audit import now, record
from services.face_recognition import FaceError
from services.scanner import recognize
from services import photo_storage


def settings():
    config = current_app.config
    return dict(interval_ms=max(300, min(800, config['LIVE_SCAN_INTERVAL_MS'])),
        stable_frames=max(2, min(3, config['LIVE_SCAN_STABLE_FRAMES'])),
        window_seconds=max(2, min(15, config['LIVE_SCAN_WINDOW_SECONDS'])),
        pause_ms=max(2000, min(3000, config['LIVE_SCAN_PAUSE_MS'])),
        unknown_cooldown=max(10, min(300, config['LIVE_UNKNOWN_COOLDOWN_SECONDS'])),
        unknown_similarity=max(.3, min(.95, config['LIVE_UNKNOWN_SIMILARITY'])))


def begin():
    moment = now()
    # No accumulating frame history: one bounded row per authenticated session.
    db.session.execute(delete(LiveScannerState).where(LiveScannerState.updated_at < moment-timedelta(minutes=15)))
    key = _token_hash(session['auth_token'])
    db.session.execute(insert(LiveScannerState).values(session_hash=key, state={}, updated_at=moment)
        .on_conflict_do_nothing(index_elements=[LiveScannerState.session_hash]))
    row = db.session.scalar(select(LiveScannerState).where(LiveScannerState.session_hash == key).with_for_update())
    opts = settings()
    recent = [r for r in row.state.get('recent_unknown', []) if r['until'] > moment.timestamp()]
    feed = uuid4().hex
    row.state = dict(feed=feed, recent_unknown=recent[-32:], candidate=None)
    row.updated_at = moment
    db.session.commit()
    return dict(feed_id=feed, interval_ms=opts['interval_ms'], pause_ms=opts['pause_ms'], stable_frames=opts['stable_frames'])


def similarity(a, b):
    return float(np.dot(np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)))


def frame(upload, feed_id):
    # A concurrent frame skips instead of waiting in a PostgreSQL request queue.
    key = _token_hash(session['auth_token'])
    row = db.session.scalar(select(LiveScannerState).where(LiveScannerState.session_hash == key)
        .with_for_update(skip_locked=True))
    if row is None:
        db.session.rollback()
        if db.session.get(LiveScannerState, key) is None:
            return dict(status='live_expired', fatal=True)
        return dict(status='checking', retry_ms=800)
    moment = now()
    stamp = moment.timestamp()
    if row.state.get('feed') != feed_id or row.updated_at < moment-timedelta(minutes=15):
        db.session.rollback()
        return dict(status='live_expired', fatal=True)
    state = deepcopy(row.state)
    opts = settings()
    wait = max(state.get('pause_until', 0), state.get('last_frame', 0)+opts['interval_ms']/1000)-stamp
    if wait > 0:
        db.session.rollback()
        return dict(status='checking', retry_ms=int(wait*1000)+1)
    state['last_frame'] = stamp
    state['recent_unknown'] = [r for r in state.get('recent_unknown', []) if r['until'] > stamp]

    def gate(result, feature):
        child_id = result['child_id']
        candidate = state.get('candidate')
        stable = (candidate is not None and stamp-candidate['started'] <= opts['window_seconds']
            and candidate['child_id'] == child_id)
        if stable and child_id is None:
            # Compare with the original candidate, avoiding drift across different people.
            stable = similarity(candidate['feature'], feature) >= opts['unknown_similarity']
        if not stable:
            candidate = dict(child_id=child_id, started=stamp, count=0, feature=feature.tolist() if child_id is None else None)
        candidate['count'] += 1
        state['candidate'] = candidate
        if candidate['count'] < opts['stable_frames']:
            return dict(status='checking', stable_count=candidate['count'], stable_required=opts['stable_frames'])
        state['candidate'] = None
        state['pause_until'] = stamp+opts['pause_ms']/1000
        if child_id is None:
            if any(similarity(r['feature'], feature) >= opts['unknown_similarity'] for r in state['recent_unknown']):
                return dict(status=result['status'], confidence=result['confidence'], saved=False, pause_ms=opts['pause_ms'])
            state['recent_unknown'].append(dict(feature=feature.tolist(), until=stamp+opts['unknown_cooldown']))
            state['recent_unknown'] = state['recent_unknown'][-32:]
        return None

    created_filename = None
    try:
        result = recognize(upload, gate=gate, commit=False)
        state['last_error'] = None
        if result['status'] in ('success', 'already', 'not_found', 'low_confidence'):
            # Start the pause after slow inference/storage, not when sampling began.
            state['pause_until'] = now().timestamp()+opts['pause_ms']/1000
            result['pause_ms'] = opts['pause_ms']
        if result.get('unknown_id'):
            created_filename = db.session.get(UnknownFace, result['unknown_id']).filename
            result['saved'] = True
        row.state, row.updated_at = state, now()
        db.session.commit()
        return result
    except FaceError as error:
        code = str(error)
        state['candidate'] = None
        if code in ('models_missing', 'liveness_failed') and state.get('last_error') != code:
            record('scanner_error', entity_type='scanner', details={'code':code})
        state['last_error'] = code
        row.state, row.updated_at = state, now()
        db.session.commit()
        return dict(status=code, fatal=code in ('models_missing', 'liveness_failed'))
    except Exception:
        db.session.rollback()
        if created_filename: photo_storage.remove(created_filename)
        raise
