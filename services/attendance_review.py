"""Read-only reconciliation. A recognition is not proof of a historical charge."""
from sqlalchemy import select, func, cast, Date
from flask import current_app
from database.models import Scan, Attendance


def missing_attendance_statement(start=None, end=None):
    local_date = cast(func.timezone(current_app.config['TIMEZONE'], Scan.created_at), Date)
    has_attendance = select(Attendance.id).where(
        Attendance.child_id == Scan.child_id, Attendance.attendance_date == local_date).exists()
    query = select(Scan.id, Scan.child_id, Scan.status, Scan.created_at, local_date.label('local_date')).where(
        Scan.status.in_(('recognized', 'success', 'already')), Scan.child_id.is_not(None), ~has_attendance)
    if start is not None: query = query.where(local_date >= start)
    if end is not None: query = query.where(local_date <= end)
    return query
