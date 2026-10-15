from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from database import db
from database.models import Child, Attendance, Scan
from services.audit import now


def register(child_id, confidence, *, commit=True):
    """Serialize scans for one child without blocking scans of other children."""
    try:
        # This lock is also taken by edits/deactivation; refresh any cached ORM row.
        child = db.session.execute(select(Child).where(Child.id == child_id)
            .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        if child is None or child.status != 'active':
            if commit: db.session.rollback()
            return 'not_found'
        # Determine the local calendar day after acquiring the lock, including
        # requests that waited across midnight. Cooldown never replaces this check.
        moment = now()
        existing = db.session.scalar(select(Attendance.id).where(
            Attendance.child_id == child_id, Attendance.attendance_date == moment.date()))
        previous = db.session.scalar(select(Scan.created_at).where(Scan.child_id == child_id)
            .order_by(Scan.created_at.desc(), Scan.id.desc()).limit(1))
        if existing and previous and previous > moment - timedelta(minutes=15):
            if commit: db.session.rollback()
            return 'already'
        if not existing:
            inserted = db.session.scalar(insert(Attendance).values(child_id=child_id,
                attendance_date=moment.date(), created_at=moment, amount=child.attendance_price)
                .on_conflict_do_nothing(constraint='uq_attendance_child_date').returning(Attendance.id))
            outcome = 'success' if inserted else 'already'
        else:
            outcome = 'already'
        db.session.add(Scan(child_id=child_id, created_at=moment, status=outcome, confidence=confidence))
        if commit: db.session.commit()
        else: db.session.flush()
        return outcome
    except Exception:
        db.session.rollback()
        raise
