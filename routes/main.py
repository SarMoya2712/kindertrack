from flask import Blueprint, render_template, request, send_file, redirect, url_for, flash, abort
from sqlalchemy import select, func
from database import db
from database.models import Child, Photo, UnknownFace, Audit, Group
from routes.auth import login_required
from services.statistics import date_range, statistics, recent_activity, period_bounds, PERIODS, UNKNOWN_PERIODS
from services.localization import t
from services.audit import ACTIONS
from services.photo_storage import photo_path, PhotoError

bp = Blueprint('main', __name__)


def filters(periods=None, default='7days'):
    try: return date_range(request.args, periods, default)
    except ValueError:
        flash(t('date_invalid'), 'error')
        return date_range({}, periods, default)


@bp.get('/')
@bp.get('/statistics', endpoint='statistics')
@login_required
def dashboard():
    period, start, end = filters()
    is_statistics = request.path == '/statistics'
    group = selected_group() if is_statistics else None
    return render_template('statistics.html' if is_statistics else 'dashboard.html',
        title='statistics' if is_statistics else 'dashboard', stats=statistics(start, end, group.id if group else None),
        **(dict(stat_groups=db.session.scalars(select(Group).order_by(Group.name, Group.id)).all(),
                group_id=group.id if group else None) if is_statistics else recent_activity(start, end)),
        period=period, start=start, end=end, periods=PERIODS)


def private_photo(filename):
    from flask import abort
    try:
        path = photo_path(filename)
    except PhotoError:
        abort(404)
    if not path.is_file(): abort(404)
    return send_file(path, mimetype='image/jpeg')


@bp.get('/media/<int:photo_id>')
@login_required
def media(photo_id):
    row = db.get_or_404(Photo, photo_id)
    return private_photo(row.filename)


@bp.get('/unknown/media/<int:face_id>')
@login_required
def unknown_media(face_id):
    row = db.get_or_404(UnknownFace, face_id)
    return private_photo(row.filename)


@bp.get('/unknown')
@login_required
def unknown():
    period, start, end = filters(UNKNOWN_PERIODS, 'today')
    lower, upper = period_bounds(start, end)
    conditions = (UnknownFace.created_at >= lower, UnknownFace.created_at < upper)
    page = min(1000000, max(1, request.args.get('page', 1, type=int)))
    count = db.session.scalar(select(func.count(UnknownFace.id)).where(*conditions))
    rows = db.session.scalars(select(UnknownFace).where(*conditions)
        .order_by(UnknownFace.created_at.desc(), UnknownFace.id.desc()).limit(24).offset((page-1)*24)).all()
    return render_template('unknown.html', title='unknown', rows=rows, page=page, count=count,
        period=period, start=start, end=end, periods=UNKNOWN_PERIODS)


@bp.post('/unknown/<int:face_id>/review')
@login_required
def review(face_id):
    row = db.get_or_404(UnknownFace, face_id)
    row.reviewed = True
    db.session.commit()
    try:
        period, start, end = date_range(request.form, UNKNOWN_PERIODS, 'today')
    except ValueError:
        period, start, end = date_range({}, UNKNOWN_PERIODS, 'today')
    return redirect(url_for('main.unknown', period=period, start=start, end=end,
                            page=max(1, request.form.get('page', 1, type=int))))


@bp.get('/audit')
@login_required
def audit():
    period, start, end = filters(UNKNOWN_PERIODS)
    lower, upper = period_bounds(start, end)
    action = request.args.get('action', 'all')
    if action not in ACTIONS: action = 'all'
    conditions = [Audit.created_at >= lower, Audit.created_at < upper]
    if action != 'all': conditions.append(Audit.action == action)
    page = min(1000000, max(1, request.args.get('page', 1, type=int)))
    count = db.session.scalar(select(func.count(Audit.id)).where(*conditions))
    results = db.session.execute(select(Audit, Child).outerjoin(Child, Audit.child_id == Child.id)
        .where(*conditions).order_by(Audit.created_at.desc(), Audit.id.desc())
        .limit(50).offset((page-1)*50)).all()
    return render_template('audit.html', title='audit', rows=results, page=page, count=count,
        period=period, start=start, end=end, periods=UNKNOWN_PERIODS, audit_actions=ACTIONS, action=action)



def selected_group():
    raw = request.args.get('group_id', '')
    if not raw: return None
    if not raw.isascii() or not raw.isdigit() or len(raw) > 10: abort(404)
    return db.get_or_404(Group, int(raw))


@bp.get('/statistics/export.xlsx')
@login_required
def export_statistics():
    from services.excel_export import build_workbook
    # Auth has already performed a read. Begin a fresh read-only snapshot so all
    # sheets agree even if a camera records attendance during the export.
    db.session.rollback()
    db.session.connection(execution_options={'isolation_level':'REPEATABLE READ'})
    db.session.execute(db.text('SET TRANSACTION READ ONLY'))
    group = selected_group()
    period, start, end = filters()
    output = build_workbook(start, end, group)
    suffix = f'_group_{group.id}' if group else ''
    db.session.rollback()
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True, download_name=f'statistics{suffix}_{start}_{end}.xlsx')


@bp.post('/unknown/<int:face_id>/delete')
@bp.post('/unknown/delete', defaults={'face_id':None})
@bp.post('/unknown/cleanup', defaults={'face_id':None}, endpoint='cleanup_unknown')
@login_required
def delete_unknown(face_id):
    from services.unknown_faces import delete_snapshots, cleanup_conditions
    from sqlalchemy.exc import SQLAlchemyError
    try:
        if request.form.get('confirmed') != 'yes':
            raise ValueError('confirmation required')
        details = None
        if request.endpoint == 'main.cleanup_unknown':
            conditions, details = cleanup_conditions(request.form)
            action = 'unknown_cleanup'
        else:
            raw = [str(face_id)] if face_id is not None else request.form.getlist('face_ids')
            if not raw or len(raw)>1000 or any(not item.isascii() or not item.isdigit() or len(item)>10 or int(item)>2147483647 for item in raw):
                raise ValueError('invalid selection')
            conditions = [UnknownFace.id.in_({int(item) for item in raw})]
            action = 'unknown_deleted' if face_id is not None else 'unknown_bulk_deleted'
        delete_snapshots(conditions, action, details)
        flash(t('snapshot_deleted'), 'success')
    except (ValueError, OSError, SQLAlchemyError):
        db.session.rollback()
        flash(t('snapshot_delete_failed'), 'error')
    try:
        period, start, end = date_range(request.form, UNKNOWN_PERIODS, 'today')
    except ValueError:
        period, start, end = date_range({}, UNKNOWN_PERIODS, 'today')
    return redirect(url_for('main.unknown', period=period, start=start, end=end))
