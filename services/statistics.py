from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select, func
from database import db
from database.models import Attendance, Child, UnknownFace, Photo, Scan
from services.audit import now
from services.attendance_review import missing_attendance_statement

PERIODS = ['today','yesterday','7days','30days','month','previous_month','year','custom']
UNKNOWN_PERIODS = ['today', '7days', '30days', 'custom']


def date_range(args, periods=None, default='7days'):
    today = now().date()
    period = args.get('period', default)
    if period not in (periods or PERIODS): period = default
    end = today
    if period == 'today': start = today
    elif period == 'yesterday': start = end = today-timedelta(days=1)
    elif period == '30days': start = today-timedelta(days=29)
    elif period == 'month': start = today.replace(day=1)
    elif period == 'previous_month':
        end = today.replace(day=1)-timedelta(days=1)
        start = end.replace(day=1)
    elif period == 'year': start = today.replace(month=1,day=1)
    elif period == 'custom':
        start = date.fromisoformat(args.get('start',''))
        end = date.fromisoformat(args.get('end',''))
        if start>end or end==date.max or (end-start).days>730: raise ValueError('date_invalid')
    else: period, start = '7days', today-timedelta(days=6)
    return period, start.isoformat(), end.isoformat()


def totals(*conditions):
    return db.session.execute(select(func.count(Attendance.id).label('count'),
        func.count(func.distinct(Attendance.child_id)).label('unique'),
        func.coalesce(func.sum(Attendance.amount), Decimal('0.00')).label('amount'))
        .where(*conditions)).mappings().one()


def statistics(start, end, group_id=None):
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    moment = now()
    midnight = datetime.combine(moment.date(), time.min, moment.tzinfo)
    scope = [Attendance.child_id.in_(select(Child.id).where(Child.group_id == group_id))] if group_id is not None else []
    child_scope = [Child.group_id == group_id] if group_id is not None else []
    missing = missing_attendance_statement(start, end)
    if group_id is not None: missing = missing.where(Scan.child_id.in_(select(Child.id).where(Child.group_id == group_id)))
    daily = totals(*scope, Attendance.attendance_date == moment.date())
    selected = totals(*scope, Attendance.attendance_date.between(start, end))
    rows = db.session.execute(select(Attendance.attendance_date, func.count(Attendance.id),
        func.sum(Attendance.amount)).where(*scope, Attendance.attendance_date.between(start, end))
        .group_by(Attendance.attendance_date)).all()
    indexed = {row[0]: {'day': row[0].isoformat(), 'count': row[1], 'amount': str(row[2])} for row in rows}
    dates = [start+timedelta(days=i) for i in range((end-start).days+1)]
    chart = [indexed.get(day, {'day': day.isoformat(), 'count': 0, 'amount': '0.00'}) for day in dates]
    return dict(today=daily, selected=selected,
        missing_attendance=db.session.scalar(select(func.count()).select_from(missing.subquery())),
        total=db.session.scalar(select(func.count(Child.id)).where(*child_scope)),
        unknown=db.session.scalar(select(func.count(UnknownFace.id)).where(
            UnknownFace.created_at >= midnight, UnknownFace.created_at < midnight+timedelta(days=1))) if group_id is None else None,
        chart=chart, days=len(dates),
        average=(Decimal(selected['count']) / len(dates)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def period_bounds(start, end):
    """Inclusive local dates represented as an exclusive timestamp interval."""
    zone = now().tzinfo
    return (datetime.combine(date.fromisoformat(start), time.min, zone),
            datetime.combine(date.fromisoformat(end) + timedelta(days=1), time.min, zone))


def recent_activity(start, end):
    lower, upper = period_bounds(start, end)
    photo_id = select(Photo.id).where(Photo.child_id == Child.id).order_by(Photo.id).limit(1).correlate(Child).scalar_subquery()
    results = db.session.execute(select(Scan, Child, photo_id).outerjoin(Child, Scan.child_id == Child.id)
        .where(Scan.created_at >= lower, Scan.created_at < upper)
        .order_by(Scan.created_at.desc(), Scan.id.desc()).limit(8)).all()
    scans = [dict(child_id=scan.child_id, created_at=scan.created_at, status=scan.status,
                  full_name=child.full_name if child else '', photo_id=photo) for scan, child, photo in results]
    # Show each recognized child once, including historical inactive children.
    latest = select(Scan.child_id, func.max(Scan.created_at).label('seen_at')).where(
        Scan.created_at >= lower, Scan.created_at < upper,
        Scan.status.in_(['success', 'already', 'recognized']), Scan.child_id.is_not(None)
    ).group_by(Scan.child_id).subquery()
    recognized = db.session.execute(select(Child, latest.c.seen_at, photo_id)
        .join(latest, latest.c.child_id == Child.id)
        .order_by(latest.c.seen_at.desc(), Child.id.desc()).limit(6)).all()
    unknown = db.session.scalars(select(UnknownFace).where(UnknownFace.created_at >= lower,
        UnknownFace.created_at < upper).order_by(UnknownFace.created_at.desc(), UnknownFace.id.desc()).limit(3)).all()
    return dict(scans=scans, recognized=recognized, unknown_preview=unknown)
