"""Recognize faces and delegate accepted matches to the attendance service."""
from uuid import uuid4
import cv2
from flask import current_app
from sqlalchemy import select
from database import db
from database.models import Child, Scan, UnknownFace, Photo, Attendance
from services.audit import now
from services.attendance import register
from services.face_recognition import FaceError
from services import photo_storage


def recognize(upload, *, gate=None, commit=True):
    face = current_app.extensions['face']
    try:
        image = face.decode(upload)
        feature, liveness = face.analyze(image, scanning=True)
        result = face.compare(feature)
    except cv2.error as error:
        raise FaceError('face_invalid') from error
    filename = None
    moment = now()
    try:
        child = None
        if result['child_id'] is not None:
            child = db.session.scalar(select(Child).where(Child.id == result['child_id'])
                .with_for_update().execution_options(populate_existing=True))
            if child is None or child.status != 'active':
                child = None
                result.update(child_id=None, status='not_found')
            else:
                # Photo replacements take the same child lock. Recheck the
                # stored vectors now so an outdated match cannot create a visit.
                result = face.compare(feature)
                if result['child_id'] != child.id:
                    child = None
                    if result['child_id'] is not None:
                        # A different candidate has not been locked/validated;
                        # reject this frame and let the administrator rescan.
                        result.update(child_id=None, status='low_confidence')
        if gate is not None:
            pending = gate(result, feature)
            if pending is not None:
                return dict(pending, liveness=liveness.status, threshold=current_app.config['FACE_THRESHOLD'], captured_at=moment.isoformat())
        if child is None:
            filename = uuid4().hex + '.jpg'
            ok, data = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if not ok:
                raise FaceError('image_invalid')
            photo_storage.save(filename, data.tobytes())
            unknown = UnknownFace(filename=filename, created_at=moment, confidence=result['confidence'], reason=result['status'])
            db.session.add(unknown)
            db.session.flush()
            result['unknown_id'] = unknown.id
        else:
            # The attendance service commits attendance and its scan event together.
            # It also suppresses duplicate events inside the cooldown.
            name, child_url = child.full_name, f'/children/{child.id}'
            group_name = child.group.name
            photo_id = db.session.scalar(select(Photo.id).where(Photo.child_id == child.id).order_by(Photo.id).limit(1))
            outcome = register(child.id, result['confidence'], commit=commit)
            amount = db.session.scalar(select(Attendance.amount).where(Attendance.child_id == child.id)
                .order_by(Attendance.id.desc()).limit(1)) if outcome == 'success' else None
            result.update(status=outcome, name=name, child_url=child_url, group=group_name,
                photo_url=f'/media/{photo_id}' if photo_id else None,
                amount=str(amount) if amount is not None else None)
        if child is None:
            db.session.add(Scan(created_at=moment, status=result['status'], confidence=result['confidence']))
            if commit: db.session.commit()
            else: db.session.flush()
    except Exception:
        if commit: db.session.rollback()
        if filename:
            photo_storage.remove(filename)
        raise
    result.update(liveness=liveness.status, threshold=current_app.config['FACE_THRESHOLD'],
                  captured_at=moment.isoformat())
    return result
