"""Children management integration; inference is mocked separately from enrollment."""
import io
from decimal import Decimal
from PIL import Image
import pytest
from sqlalchemy import select, func
from database import db
from database.models import Child, Photo, Audit, Attendance
from conftest import add_child, child_form, csrf, image_bytes


def edit(client, **changes):
    data = dict(child_form(), csrf_token=csrf(client))
    data.update(changes)
    return client.post('/children/1/edit', data=data, content_type='multipart/form-data')


def test_create_edit_status_and_private_photos(authorized, app, fake_embedding):
    assert add_child(authorized, price='12500.25').status_code == 302
    with app.app_context():
        photo = db.session.scalar(select(Photo))
        filename = photo.filename
        assert len(photo.embedding) == 512 and photo.model == 'sface-2021dec'
        assert db.session.get(Child, 1).attendance_price == Decimal('12500.25')
    path = app.config['DATA_DIR'] / 'photos' / filename
    assert path.exists() and path.stat().st_mode & 0o777 == 0o600
    assert authorized.get('/media/1').mimetype == 'image/jpeg'
    assert authorized.get('/media/1').headers['Cache-Control'] == 'no-store, private'
    assert app.test_client().get('/media/1').status_code == 302
    assert authorized.get('/static/' + filename).status_code == 404
    assert authorized.get('/instance/photos/' + filename).status_code == 404
    assert 'existing-photos' in authorized.get('/children/1/edit').text
    assert edit(authorized, first_name='Сардор', guardian_0_first_name='Отабек', attendance_price='18000.50').status_code == 302
    assert 'Сардор' in authorized.get('/children?q=Сардор').text
    assert 'Отабек' in authorized.get('/children/1').text
    assert path.exists()
    for target in ['inactive', 'inactive', 'active', 'active']:
        assert authorized.post('/children/1/status', data={'csrf_token':csrf(authorized), 'status':target}).status_code == 302
        with app.app_context(): assert db.session.get(Child, 1).status == target
    with app.app_context():
        actions = list(db.session.scalars(select(Audit.action)))
        assert actions.count('child_deactivated') == actions.count('child_activated') == 1
        assert 'child_created' in actions and 'child_edited' in actions and 'tariff_changed' in actions
        assert db.session.scalar(select(func.count(Attendance.id))) == 0
    assert authorized.post('/children/1/status', data={'csrf_token':csrf(authorized), 'status':'invalid'}).status_code == 400
    assert authorized.post('/children/1/status', data={'status':'inactive'}).status_code == 400
    assert authorized.get('/children/999').status_code == 404


def test_three_formats_replacement_and_metadata(authorized, app, fake_embedding):
    photos = []
    for fmt in ['JPEG', 'PNG', 'WEBP']:
        buffer = io.BytesIO()
        image = Image.new('RGB', (200, 200), 'green')
        exif = Image.Exif(); exif[270] = 'private metadata'
        image.save(buffer, fmt, exif=exif); buffer.seek(0)
        photos.append((buffer, '../../portrait.' + fmt.lower()))
    assert add_child(authorized, photos=photos).status_code == 302
    with app.app_context(): filenames = list(db.session.scalars(select(Photo.filename)))
    assert len(filenames) == 3
    for filename in filenames:
        assert '/' not in filename
        with Image.open(app.config['DATA_DIR'] / 'photos' / filename) as image:
            assert image.format == 'JPEG' and not image.getexif()
    assert edit(authorized, photos=[(image_bytes('blue'), 'new.jpg')]).status_code == 302
    assert all(not (app.config['DATA_DIR'] / 'photos' / name).exists() for name in filenames)
    with app.app_context(): assert db.session.scalar(select(func.count(Photo.id))) == 1
    assert len(list((app.config['DATA_DIR'] / 'photos').glob('*.jpg'))) == 1


@pytest.mark.parametrize('kind', ['missing', 'too_many', 'fake', 'oversized', 'svg', 'truncated'])
def test_invalid_photos_leave_no_records(authorized, app, kind):
    uploads = {
        'missing': [],
        'too_many': [(image_bytes(), f'{i}.jpg') for i in range(4)],
        'fake': [(io.BytesIO(b'not an image'), 'image.jpg')],
        'oversized': [(io.BytesIO(b'x' * (6 * 1024 * 1024 + 1)), 'image.jpg')],
        'svg': [(io.BytesIO(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'), 'image.svg')],
        'truncated': [(io.BytesIO(image_bytes().getvalue()[:100]), 'image.jpg')],
    }
    assert add_child(authorized, photos=uploads[kind]).status_code == 200
    with app.app_context():
        assert db.session.scalar(select(func.count(Child.id))) == 0
        assert db.session.scalar(select(func.count(Photo.id))) == 0
        assert db.session.scalar(select(func.count(Audit.id)).where(Audit.entity_type == 'child')) == 0
    assert not list((app.config['DATA_DIR'] / 'photos').glob('*.jpg'))


def test_failed_replacement_preserves_child_and_files(authorized, app, monkeypatch, fake_embedding):
    assert add_child(authorized).status_code == 302
    with app.app_context(): original = db.session.scalar(select(Photo.filename))
    assert edit(authorized, first_name='Changed', photos=[(io.BytesIO(b'bad'), 'bad.jpg')]).status_code == 200
    with app.app_context(): assert db.session.get(Child, 1).first_name == 'Азиз'
    from services import photo_storage
    real_save = photo_storage.save
    def fail_after_write(filename, data):
        real_save(filename, data)
        raise OSError('simulated storage failure')
    monkeypatch.setattr(photo_storage, 'save', fail_after_write)
    assert edit(authorized, photos=[(image_bytes(), 'new.jpg')]).status_code == 500
    with app.app_context(): assert db.session.scalar(select(Photo.filename)) == original
    assert [p.name for p in (app.config['DATA_DIR'] / 'photos').glob('*.jpg')] == [original]
