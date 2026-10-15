"""Group membership and rollups from children.group_id, without copied rosters."""
from datetime import date
from decimal import Decimal
from sqlalchemy import select, func, or_
from database import db
from database.models import Group, Child, Attendance, Photo
from services.audit import now

CHILD_FILTERS = ('all', 'active', 'inactive', 'present', 'absent')


def present_today():
    return select(Attendance.id).where(Attendance.child_id == Child.id,
        Attendance.attendance_date == now().date()).exists()


def child_period_totals(start, end):
    return select(Attendance.child_id, func.count(Attendance.id).label('visits'),
        func.sum(Attendance.amount).label('accrued')).where(
        Attendance.attendance_date.between(date.fromisoformat(start), date.fromisoformat(end)))\
        .group_by(Attendance.child_id).subquery()


def group_statistics(start, end, group_id=None):
    # Aggregate membership independently of attendance so repeated visits cannot
    # multiply roster counts. EXISTS is one boolean per current child.
    roster = select(Child.group_id, func.count(Child.id).label('total'),
        func.count(Child.id).filter(Child.status == 'active').label('active'),
        func.count(Child.id).filter(present_today()).label('present')).group_by(Child.group_id).subquery()
    visits = select(Child.group_id, func.count(Attendance.id).label('visits'),
        func.count(func.distinct(Attendance.child_id)).label('unique'),
        func.sum(Attendance.amount).label('accrued')).join(Attendance, Attendance.child_id == Child.id)\
        .where(Attendance.attendance_date.between(date.fromisoformat(start), date.fromisoformat(end)))\
        .group_by(Child.group_id).subquery()
    query = select(Group, func.coalesce(roster.c.total, 0), func.coalesce(roster.c.active, 0),
        func.coalesce(roster.c.present, 0), func.coalesce(visits.c.visits, 0),
        func.coalesce(visits.c.unique, 0), func.coalesce(visits.c.accrued, Decimal('0.00')))\
        .outerjoin(roster, roster.c.group_id == Group.id).outerjoin(visits, visits.c.group_id == Group.id)
    if group_id is not None: query = query.where(Group.id == group_id)
    return [dict(group=group, total=total, active=active, present=present, absent=total-present,
        visits=count, unique=unique, accrued=amount,
        percentage=(Decimal(present)*100/total).quantize(Decimal('.1')) if total else Decimal('0.0'))
        for group,total,active,present,count,unique,amount in db.session.execute(query.order_by(Group.name, Group.id))]


def group_children(group_id, start, end, query='', status='all', page=1):
    conditions = [Child.group_id == group_id]
    if query:
        pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        conditions.append(or_(func.concat_ws(' ', Child.last_name, Child.first_name, Child.middle_name).ilike(pattern, escape='\\'),
            func.concat_ws(' ', Child.first_name, Child.last_name).ilike(pattern, escape='\\'), Child.id.cast(db.String) == query))
    if status in ('active', 'inactive'): conditions.append(Child.status == status)
    elif status == 'present': conditions.append(present_today())
    elif status == 'absent': conditions.append(~present_today())
    totals = child_period_totals(start, end)
    photo = select(Photo.id).where(Photo.child_id == Child.id).order_by(Photo.id).limit(1).correlate(Child).scalar_subquery()
    count = db.session.scalar(select(func.count(Child.id)).where(*conditions))
    rows = db.session.execute(select(Child, photo.label('photo_id'), func.coalesce(totals.c.visits, 0).label('visits'))
        .outerjoin(totals, totals.c.child_id == Child.id).where(*conditions)
        .order_by(Child.last_name, Child.first_name, Child.id).limit(25).offset((page-1)*25)).all()
    return rows, count


def children_report(start, end, group_id=None):
    totals = child_period_totals(start, end)
    query = select(Child, Group.name, func.coalesce(totals.c.visits, 0),
        func.coalesce(totals.c.accrued, Decimal('0.00'))).join(Group, Child.group_id == Group.id)\
        .outerjoin(totals, totals.c.child_id == Child.id)
    if group_id is not None: query = query.where(Child.group_id == group_id)
    return db.session.execute(query.order_by(Child.last_name, Child.first_name, Child.id)).all()
