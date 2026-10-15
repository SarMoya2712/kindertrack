"""PostgreSQL migration compatibility and preservation of historical values."""
from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest
from sqlalchemy import select, text, inspect
from flask_migrate import check, downgrade, upgrade
from database import db
from database.models import Child, Photo, Attendance, Audit
from app import create_app

ROOT=Path(__file__).resolve().parents[1]


def test_audit_migration_preserves_old_events_and_history(app):
    with app.app_context():
        downgrade(directory=str(ROOT/'migrations'), revision='8c14297ad613')
        child_id = db.session.scalar(text("""INSERT INTO children(first_name,last_name,registration_date,attendance_price,status,created_at)
            VALUES ('Азиз','Алиев','2026-01-01',30000.29,'active',CURRENT_TIMESTAMP) RETURNING id"""))
        db.session.add(Photo(child_id=child_id,filename='preserved.jpg',embedding=b'original-vector',model='legacy'))
        db.session.add(Attendance(child_id=child_id,attendance_date=date(2026,10,12),amount=Decimal('25000.15')))
        db.session.execute(text("INSERT INTO audit(admin_id,action,child_id,details,created_at) VALUES(1,'tariff_changed',:id,CAST(:details AS jsonb),CURRENT_TIMESTAMP)"),
            {'id':child_id,'details':'{"old":"25000.15","new":"30000.29"}'})
        db.session.commit()
        upgrade(directory=str(ROOT/'migrations'))
        row=db.session.scalar(select(Audit))
        assert row.entity_type=='child' and row.entity_id==child_id
        assert row.old_value=='25000.15' and row.new_value=='30000.29'
        assert db.session.scalar(select(Photo.embedding))==b'original-vector'
        assert db.session.scalar(select(Attendance.amount))==Decimal('25000.15')
        assert db.session.scalar(select(Child.attendance_price))==Decimal('30000.29')


def test_migrations_match_models(app):
    with app.app_context():
        check(directory=str(ROOT/'migrations'))
        schema=inspect(db.engine)
        for table in schema.get_table_names():
            for column in schema.get_columns(table):
                if column['name'] in {'created_at','expires_at','last_attempt_at'}:
                    assert column['type'].timezone is True
        assert any(c['name']=='ck_unknown_faces_valid_reason' for c in schema.get_check_constraints('unknown_faces'))
        assert {'ix_audit_action','ix_audit_entity','ix_audit_created_at'} <= {i['name'] for i in schema.get_indexes('audit')}


def test_non_postgres_configuration_is_rejected(tmp_path):
    for uri in (None,'sqlite:///:memory:'):
        with pytest.raises(RuntimeError,match='PostgreSQL'):
            create_app({'DATA_DIR':tmp_path,'SECRET_KEY':'test','SQLALCHEMY_DATABASE_URI':uri})
