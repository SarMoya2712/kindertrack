from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from database import db


def utcnow():
    return datetime.now(timezone.utc)


class Admin(db.Model):
    __tablename__ = 'admins'
    __table_args__ = (CheckConstraint('id = 1', name='single_admin'),)
    id = db.Column(db.Integer, primary_key=True, default=1, autoincrement=False)
    email = db.Column(db.String(254), nullable=False, unique=True)
    password = db.Column(db.Text, nullable=False)


class AdminSession(db.Model):
    __tablename__ = 'admin_sessions'
    token_hash = db.Column(db.String(64), primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey('admins.id', ondelete='CASCADE'), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)


class Child(db.Model):
    __tablename__ = 'children'
    __table_args__ = (
        CheckConstraint("status IN ('active', 'inactive')", name='valid_status'),
        CheckConstraint('attendance_price >= 0 AND attendance_price <= 100000000', name='valid_price'),
    )
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    middle_name = db.Column(db.String(80))
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id', ondelete='RESTRICT'), nullable=False, index=True)
    group = db.relationship('Group')
    guardians = db.relationship('Guardian', cascade='all, delete-orphan', order_by='Guardian.id')
    registration_date = db.Column(db.Date, nullable=False)
    attendance_price = db.Column(db.Numeric(14, 2), nullable=False)
    status = db.Column(db.String(8), nullable=False, default='active', index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    @property
    def full_name(self):
        return ' '.join(filter(None, (self.last_name, self.first_name, self.middle_name)))



class Group(db.Model):
    __tablename__ = 'groups'
    __table_args__ = (CheckConstraint("length(trim(name)) > 0", name='group_name_required'),)
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(1000))
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class Guardian(db.Model):
    __tablename__ = 'guardians'
    __table_args__ = (
        CheckConstraint("relation_type IN ('father','mother','grandmother','grandfather','guardian','other')", name='guardian_relation'),
        Index('uq_guardians_primary_child', 'child_id', unique=True, postgresql_where=db.text('is_primary')),
    )
    id = db.Column(db.Integer, primary_key=True)
    child_id = db.Column(db.Integer, db.ForeignKey('children.id', ondelete='CASCADE'), nullable=False, index=True)
    relation_type = db.Column(db.String(16), nullable=False)
    # Legacy records may contain only a phone or one name; preserve them intact.
    first_name = db.Column(db.String(80))
    last_name = db.Column(db.String(80))
    middle_name = db.Column(db.String(80))
    phone = db.Column(db.String(30))
    is_primary = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    @property
    def full_name(self):
        return ' '.join(filter(None, (self.last_name, self.first_name, self.middle_name)))


class Photo(db.Model):
    __tablename__ = 'photos'
    id = db.Column(db.Integer, primary_key=True)
    child_id = db.Column(db.Integer, db.ForeignKey('children.id', ondelete='RESTRICT'), nullable=False, index=True)
    filename = db.Column(db.String(80), nullable=False, unique=True)
    # Nullable for preserved legacy photos that have not yet been enrolled.
    embedding = db.Column(db.LargeBinary, nullable=True)
    model = db.Column(db.String(80), nullable=True)


class Attendance(db.Model):
    __tablename__ = 'attendance'
    __table_args__ = (
        UniqueConstraint('child_id', 'attendance_date', name='uq_attendance_child_date'),
        CheckConstraint('amount >= 0', name='nonnegative_amount'),
    )
    id = db.Column(db.Integer, primary_key=True)
    child_id = db.Column(db.Integer, db.ForeignKey('children.id', ondelete='RESTRICT'), nullable=False, index=True)
    attendance_date = db.Column(db.Date, nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    amount = db.Column(db.Numeric(14, 2), nullable=False)


class Scan(db.Model):
    __tablename__ = 'scans'
    __table_args__ = (Index('ix_scans_child_timestamp', 'child_id', 'created_at'),)
    id = db.Column(db.Integer, primary_key=True)
    child_id = db.Column(db.Integer, db.ForeignKey('children.id', ondelete='RESTRICT'), index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    status = db.Column(db.String(24), nullable=False, index=True)
    confidence = db.Column(db.Float)


class UnknownFace(db.Model):
    __tablename__ = 'unknown_faces'
    __table_args__ = (CheckConstraint("reason IN ('not_found', 'low_confidence', 'unspecified')", name='valid_reason'),)
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(80), nullable=False, unique=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    confidence = db.Column(db.Float)
    reviewed = db.Column(db.Boolean, nullable=False, default=False)
    reason = db.Column(db.String(24), nullable=False, server_default='unspecified')


class Audit(db.Model):
    __tablename__ = 'audit'
    __table_args__ = (Index('ix_audit_entity', 'entity_type', 'entity_id'),)
    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey('admins.id', ondelete='RESTRICT'))
    action = db.Column(db.String(40), nullable=False, index=True)
    entity_type = db.Column(db.String(24), nullable=False, server_default='system')
    entity_id = db.Column(db.Integer)
    old_value = db.Column(JSONB)
    new_value = db.Column(JSONB)
    child_id = db.Column(db.Integer, db.ForeignKey('children.id', ondelete='RESTRICT'), index=True)
    details = db.Column(JSONB, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class LoginAttempt(db.Model):
    __tablename__ = 'login_attempts'
    key = db.Column(db.String(64), primary_key=True)
    failures = db.Column(db.Integer, nullable=False, default=0)
    last_attempt_at = db.Column(db.DateTime(timezone=True), nullable=False)


class LiveScannerState(db.Model):
    """Short-lived recognition state, shared safely across server workers."""
    __tablename__ = 'live_scanner_states'
    session_hash = db.Column(db.String(64), db.ForeignKey('admin_sessions.token_hash', ondelete='CASCADE'), primary_key=True)
    state = db.Column(JSONB, nullable=False, default=dict)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
