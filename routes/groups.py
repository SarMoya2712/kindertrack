from flask import Blueprint, request, render_template, redirect, url_for, flash, abort
from sqlalchemy import select
from database import db
from database.models import Group
from routes.auth import login_required
from services.audit import record
from services.guardians import clean_text
from services.localization import t
from routes.main import filters
from services.statistics import PERIODS
from services.groups import group_statistics, group_children, CHILD_FILTERS

bp = Blueprint('groups', __name__)


@bp.get('/groups')
@login_required
def index():
    period, start, end = filters()
    return render_template('groups.html', title='groups', rows=group_statistics(start, end),
        period=period, start=start, end=end, periods=PERIODS)


@bp.get('/groups/<int:group_id>')
@login_required
def detail(group_id):
    group = db.get_or_404(Group, group_id)
    period, start, end = filters()
    q = request.args.get('q', '').strip()[:100]
    status = request.args.get('status', 'all')
    if status not in CHILD_FILTERS: status = 'all'
    page = min(1000000, max(1, request.args.get('page', 1, type=int)))
    rows, count = group_children(group_id, start, end, q, status, page)
    summary = group_statistics(start, end, group_id)[0]
    return render_template('group_detail.html', title='groups', group=group, rows=rows, count=count,
        summary=summary, q=q, status=status, child_filters=CHILD_FILTERS, page=page,
        period=period, start=start, end=end, periods=PERIODS)


@bp.route('/groups/new', methods=['GET', 'POST'])
@bp.route('/groups/<int:group_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(group_id=None):
    group = db.get_or_404(Group, group_id) if group_id else None
    if request.method == 'POST':
        try:
            name = clean_text(request.form.get('name', ''), 120, required=True)
            description = clean_text(request.form.get('description', ''), 1000)
            if group:
                group = db.session.scalar(select(Group).where(Group.id == group_id).with_for_update())
                old = {'name':group.name, 'description':group.description}
                group.name, group.description = name, description
                action = 'group_edited'
            else:
                old = None
                group = Group(name=name, description=description, is_active=True)
                db.session.add(group)
                db.session.flush()
                action = 'group_created'
            record(action, entity_type='group', entity_id=group.id, old_value=old,
                   new_value={'name':name, 'description':description})
            db.session.commit()
            flash(t('saved'), 'success')
            return redirect(url_for('groups.index'))
        except ValueError:
            db.session.rollback()
            flash(t('invalid_form'), 'error')
    return render_template('group_form.html', title='edit_group' if group_id else 'add_group', group=group)


@bp.post('/groups/<int:group_id>/status')
@login_required
def status(group_id):
    target = request.form.get('status')
    if target not in ('active', 'inactive'): abort(400)
    group = db.session.scalar(select(Group).where(Group.id == group_id).with_for_update())
    if group is None: abort(404)
    active = target == 'active'
    if group.is_active != active:
        record('group_activated' if active else 'group_deactivated', entity_type='group', entity_id=group.id,
               old_value='active' if group.is_active else 'inactive', new_value=target)
        group.is_active = active
    db.session.commit()
    return redirect(url_for('groups.index'))
