"""Delete only unknown snapshots. Never cascade into scans or attendance."""
from datetime import timedelta
import os
from tempfile import TemporaryDirectory
from pathlib import Path
from flask import current_app
from sqlalchemy import select
from database import db
from database.models import UnknownFace, Photo
from services.audit import now, record
from services.photo_storage import photo_path
from services.statistics import date_range, period_bounds


def cleanup_conditions(form):
    mode = form.get('cleanup_period')
    if mode in ('7', '30', '90'):
        cutoff = now() - timedelta(days=int(mode))
        return [UnknownFace.created_at < cutoff], {'before': cutoff.isoformat()}
    if mode != 'custom':
        raise ValueError('invalid period')
    _, start, end = date_range({'period':'custom', 'start':form.get('cleanup_start', ''),
                               'end':form.get('cleanup_end', '')})
    lower, upper = period_bounds(start, end)
    return [UnknownFace.created_at >= lower, UnknownFace.created_at < upper], {'from':start, 'to':end}


def delete_snapshots(conditions, action, details=None):
    try:
        rows = db.session.scalars(select(UnknownFace).where(*conditions)
            .order_by(UnknownFace.id).with_for_update()).all()
        paths = []
        for row in rows:
            # Photos currently share a private directory. Explicitly refuse a
            # corrupt/legacy alias to a registered child's image.
            if db.session.scalar(select(Photo.id).where(Photo.filename == row.filename).limit(1)):
                raise ValueError('protected photo')
            paths.append(photo_path(row.filename))
        ids = [row.id for row in rows]
        for row in rows:
            db.session.delete(row)
        record(action, entity_type='unknown_face', entity_id=ids[0] if action=='unknown_deleted' and ids else None,
               old_value={'snapshot_ids':ids, 'snapshot_count':len(ids), **(details or {})},
               new_value={'snapshot_count':0})
        # Surface database constraint errors before touching files. Missing files
        # are harmless; other filesystem errors abort and roll back database work.
        db.session.flush()
        # Private temporary hard links allow ordinary filesystem/commit failures
        # to restore files without loading an entire cleanup into memory.
        with TemporaryDirectory(prefix='.unknown-delete-', dir=Path(current_app.config['DATA_DIR'])/'photos') as temporary:
            backups = []
            try:
                for index, path in enumerate(paths):
                    backup = Path(temporary)/str(index)
                    try:
                        os.link(path, backup, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    backups.append((path, backup))
                for path, _ in backups:
                    path.unlink(missing_ok=True)
                db.session.commit()
            except Exception:
                for path, backup in backups:
                    os.replace(backup, path)
                raise
        return len(ids)
    except Exception:
        db.session.rollback()
        raise
