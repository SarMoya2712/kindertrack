from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import uuid4
import unicodedata
from flask import Blueprint, request, render_template, redirect, url_for, current_app, flash, abort
from sqlalchemy import select, func, or_
from database import db
from database.models import Child, Photo, Attendance, Group, Guardian
from routes.auth import login_required
from routes.main import filters
from services.audit import record, now
from services import photo_storage
from services.photo_storage import PhotoError
from services.face_recognition import FaceError
from services.localization import t
from services.statistics import PERIODS
from services.guardians import form_rows, validate, snapshot, RELATIONS

bp = Blueprint('children', __name__)
NAME_FIELDS = ('last_name', 'first_name', 'middle_name')


@bp.get('/children')
@login_required
def index():
    period, start, end = filters()
    q = request.args.get('q', '').strip()[:100]
    status = request.args.get('status', 'all')
    page = min(1000000, max(1, request.args.get('page', 1, type=int)))
    group_id = request.args.get('group_id', type=int)
    conditions = []
    if group_id is not None: conditions.append(Child.group_id == group_id)
    if q:
        # Treat user-entered % and _ as text, not wildcard operators.
        pattern = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        conditions.append(or_(func.concat_ws(' ', Child.last_name, Child.first_name, Child.middle_name).ilike(pattern, escape='\\'),
            func.concat_ws(' ', Child.first_name, Child.last_name).ilike(pattern, escape='\\'),
            Child.id.cast(db.String) == q))
    if status in ('active', 'inactive'): conditions.append(Child.status == status)
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    totals = select(Attendance.child_id, func.count(Attendance.id).label('visits'), func.sum(Attendance.amount).label('accrued'))\
        .where(Attendance.attendance_date.between(start_date, end_date)).group_by(Attendance.child_id).subquery()
    photo_id = select(Photo.id).where(Photo.child_id == Child.id).order_by(Photo.id).limit(1).correlate(Child).scalar_subquery()
    count = db.session.scalar(select(func.count(Child.id)).where(*conditions))
    results = db.session.execute(select(Child, photo_id, func.coalesce(totals.c.visits, 0),
        func.coalesce(totals.c.accrued, Decimal('0.00'))).outerjoin(totals, totals.c.child_id == Child.id)
        .where(*conditions).order_by(Child.id.desc()).limit(25).offset((page-1)*25)).all()
    rows = []
    for child, photo, visits, accrued in results:
        child.photo_id, child.visits, child.accrued = photo, visits, accrued
        rows.append(child)
    return render_template('children.html', title='children', rows=rows, q=q, status=status, page=page,
        count=count, period=period, start=start, end=end, periods=PERIODS, group_id=group_id,
        groups=db.session.scalars(select(Group).order_by(Group.name)).all())


def child_or_404(child_id, lock=False):
    statement = select(Child).where(Child.id == child_id)
    if lock: statement = statement.with_for_update().execution_options(populate_existing=True)
    child = db.session.scalar(statement)
    if child is None: abort(404)
    return child


@bp.route('/children/new', methods=['GET', 'POST'])
@bp.route('/children/<int:child_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(child_id=None):
    child = child_or_404(child_id) if child_id else None
    editing = child is not None
    guardians = snapshot(child.guardians) if child else []
    if request.method == 'POST':
        paths = []
        try:
            guardians = form_rows(request.form)
            values = {name: request.form.get(name, '').strip() for name in NAME_FIELDS}
            required = ('first_name', 'last_name')
            if (any(not values[name] for name in required) or any(len(value)>80 for value in values.values())
                or any(any(unicodedata.category(char).startswith('C') for char in value) for value in values.values())): raise ValueError()
            validate(guardians)
            values['group_id'] = int(request.form.get('group_id', ''))
            values['registration_date'] = date.fromisoformat(request.form.get('registration_date', ''))
            values['attendance_price'] = Decimal(request.form.get('attendance_price', ''))
            values['status'] = request.form.get('status', 'active')
            price = values['attendance_price']
            if (values['status'] not in ('active','inactive')
                or not price.is_finite() or price<0 or price>100000000
                or price.as_tuple().exponent < -2 or values['registration_date']>now().date()): raise ValueError()
            values['attendance_price'] = price.quantize(Decimal('0.01'))
            uploads = [f for f in request.files.getlist('photos') if f.filename]
            remove_ids = {int(value) for value in request.form.getlist('remove_photos')}
            if (not editing and (not uploads or remove_ids)) or len(uploads)>10: raise PhotoError('photo_count')
            photo_storage.validate_batch(uploads)
            prepared = [(uuid4().hex + '.jpg', *current_app.extensions['face'].reference(upload)) for upload in uploads]
            # Serialize against group deactivation. Keep an existing assignment
            # to an inactive group, but never assign new children to it.
            group = db.session.scalar(select(Group).where(Group.id == values['group_id']).with_for_update())
            if group is None: raise ValueError('invalid_form')
            old_photos = []
            if editing:
                # Serialize concurrent edits after validating uploads.
                child = child_or_404(child_id, lock=True)
                existing = list(db.session.scalars(select(Photo).where(Photo.child_id == child_id)))
                if not remove_ids.issubset({photo.id for photo in existing}): raise PhotoError('image_invalid')
                if not 1 <= len(existing) - len(remove_ids) + len(prepared) <= 10:
                    raise PhotoError('photo_count')
                if not group.is_active and child.group_id != group.id: raise ValueError('invalid_form')
                old_guardians = snapshot(child.guardians)
                old_data = {}
                for key in values:
                    value = getattr(child, key)
                    old_data[key] = str(value) if value is not None else ''
                if child.attendance_price != price:
                    record('tariff_changed', child_id, {'old': str(child.attendance_price), 'new': str(price)})
                if child.status != values['status']:
                    record('child_activated' if values['status']=='active' else 'child_deactivated', child_id, old_value=child.status, new_value=values['status'])
                for key, value in values.items(): setattr(child, key, value)
                record('child_edited', child_id,
                    old_value={key:old_data[key] for key in values if old_data[key] != str(values[key])},
                    new_value={key:str(values[key]) for key in values if old_data[key] != str(values[key])})
            else:
                if not group.is_active: raise ValueError('invalid_form')
                old_guardians = []
                child = Child(**values, created_at=now())
                db.session.add(child)
                db.session.flush()
                child_id = child.id
                record('child_created', child_id, new_value={key:str(value) for key,value in values.items()})
            if old_guardians != guardians:
                record('guardians_updated', child_id, old_value={'guardians':old_guardians}, new_value={'guardians':guardians})
                # Delete first so the partial unique primary-contact index cannot
                # conflict with the replacement rows in this same transaction.
                child.guardians.clear()
                db.session.flush()
                child.guardians.extend(Guardian(**row) for row in guardians)
            if prepared or remove_ids:
                removed = [photo for photo in existing if photo.id in remove_ids] if editing else []
                old_photos = [photo.filename for photo in removed]
                for photo in removed: db.session.delete(photo)
                if editing:
                    record('child_photos_updated', child_id,
                        old_value={'photo_count':len(existing)}, new_value={'photo_count':len(existing)-len(removed)+len(prepared)})
                for filename, image, embedding, model in prepared:
                    paths.append(filename)
                    photo_storage.save(filename, image)
                    db.session.add(Photo(child_id=child_id, filename=filename, embedding=embedding, model=model))
            db.session.commit()
            paths.clear()
            for filename in old_photos:
                photo_storage.remove(filename)
            flash(t('saved' if editing else 'created'), 'success')
            return redirect(url_for('children.detail', child_id=child_id))
        except (ValueError, InvalidOperation) as error:
            db.session.rollback()
            for filename in paths: photo_storage.remove(filename)
            flash(t(str(error) if isinstance(error, (PhotoError, FaceError)) or str(error)=='one_primary' else 'invalid_form'), 'error')
            if not editing: child = None
        except Exception:
            db.session.rollback()
            for filename in paths: photo_storage.remove(filename)
            raise
    return render_template('child_form.html', title='edit_child' if editing else 'add_child', child=child,
        today=now().date().isoformat(), name_fields=NAME_FIELDS, guardians=guardians, relations=RELATIONS,
        groups=db.session.scalars(select(Group).where(or_(Group.is_active.is_(True), Group.id == (child.group_id if child else None))).order_by(Group.name)).all(),
        photos=db.session.scalars(select(Photo).where(Photo.child_id == child_id).order_by(Photo.id)).all() if editing else [])


@bp.get('/children/<int:child_id>')
@login_required
def detail(child_id):
    child = child_or_404(child_id)
    period, start, end = filters()
    page = min(1000000, max(1, request.args.get('page', 1, type=int)))
    conditions = (Attendance.child_id==child_id,
                  Attendance.attendance_date.between(date.fromisoformat(start), date.fromisoformat(end)))
    photos = db.session.scalars(select(Photo).where(Photo.child_id==child_id).order_by(Photo.id)).all()
    history = db.session.scalars(select(Attendance).where(*conditions).order_by(Attendance.attendance_date.desc())
        .limit(50).offset((page-1)*50)).all()
    summary = db.session.execute(select(func.count(Attendance.id).label('visits'),
        func.coalesce(func.sum(Attendance.amount), Decimal('0.00')).label('accrued')).where(*conditions)).mappings().one()
    return render_template('child_detail.html', title='details', child=child, photos=photos, history=history,
        summary=summary, period=period, start=start, end=end, periods=PERIODS, page=page)


@bp.post('/children/<int:child_id>/status')
@login_required
def status(child_id):
    child = child_or_404(child_id, lock=True)
    target = request.form.get('status')
    if target not in ('active', 'inactive'):
        abort(400)
    if child.status != target:
        previous = child.status
        child.status = target
        record('child_activated' if target == 'active' else 'child_deactivated', child_id, old_value=previous, new_value=target)
    db.session.commit()
    flash(t('saved'), 'success')
    return redirect(url_for('children.detail', child_id=child_id))
