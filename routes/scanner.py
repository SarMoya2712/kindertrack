from flask import Blueprint, request, render_template
from routes.auth import login_required
from services.face_recognition import FaceError
from services.localization import t
from services.scanner import recognize
from services.audit import error_event

bp = Blueprint('scanner', __name__)


@bp.get('/scanner')
@login_required
def scanner():
    return render_template('scanner.html', title='scanner')


@bp.post('/api/scan')
@login_required
def scan():
    upload = request.files.get('photo')
    if not upload:
        return {'message':t('image_invalid'), 'status':'error'}, 400
    try:
        result = recognize(upload)
    except FaceError as error:
        if str(error) in {'models_missing', 'liveness_failed'}:
            error_event('scanner_error', str(error))
        return {'message':t(str(error)), 'status':'error'}, 422
    result['message'] = t(result['status'])
    return result


@bp.post('/api/live/start')
@login_required
def live_start():
    from services.live_scanner import begin
    return begin()


@bp.post('/api/live/frame')
@login_required
def live_frame():
    from services.live_scanner import frame
    upload = request.files.get('photo')
    feed_id = request.form.get('feed_id', '')
    if not upload or len(feed_id) != 32:
        return {'message':t('image_invalid'), 'status':'error'}, 400
    result = frame(upload, feed_id)
    result['message'] = t(result['status'])
    return result
