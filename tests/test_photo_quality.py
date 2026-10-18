import io
import struct
import zlib
import numpy as np
import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage
from services import photo_storage
from services.frame_quality import evaluate
from sqlalchemy import select
from database import db
from database.models import Photo
from conftest import add_child, image_bytes
from test_children import edit


def test_ten_photos_add_remove_replace_preserves_vectors(authorized, app, fake_embedding):
    assert add_child(authorized, photos=[(image_bytes(),str(i)+'.jpg') for i in range(10)]).status_code == 302
    with app.app_context():
        before = [(p.id,p.child_id,p.embedding,p.filename) for p in db.session.scalars(select(Photo).order_by(Photo.id))]
    assert len(before)==10 and all(row[1]==1 and len(row[2])==512 for row in before)
    assert edit(authorized, photos=[(image_bytes(),'extra.jpg')]).status_code == 200
    assert edit(authorized, remove_photos=['1'],photos=[(image_bytes(),'replacement.jpg')]).status_code == 302
    with app.app_context():
        after = [(p.id,p.child_id,p.embedding,p.filename) for p in db.session.scalars(select(Photo).order_by(Photo.id))]
    assert after[:9] == before[1:]
    assert edit(authorized, remove_photos=['2']).status_code == 302
    assert edit(authorized, remove_photos=['999']).status_code == 200
    assert edit(authorized, remove_photos=[str(row[0]) for row in after[1:]]).status_code == 200


def test_batch_bytes_limits_before_decode():
    class SizedStream(io.BytesIO):
        def seek(self, offset, whence=0):
            self.end=whence==2
            return 0
        def tell(self): return 15*1024*1024 if self.end else 0
    with pytest.raises(photo_storage.PhotoError,match='photo_total'):
        photo_storage.validate_batch([FileStorage(stream=SizedStream()) for _ in range(7)])
    with pytest.raises(photo_storage.PhotoError):
        photo_storage.prepare(FileStorage(stream=io.BytesIO(b'x'*(photo_storage.MAX_BYTES+1))))


@pytest.mark.parametrize('size',[(10000,5001),(100000,100000)])
def test_bomb_header_rejected_without_pixel_allocation(size):
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
    raw=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',*size,8,2,0,0,0))+chunk(b'IEND',b'')
    with pytest.raises(photo_storage.PhotoError):
        photo_storage.prepare(FileStorage(stream=io.BytesIO(raw)))


def test_orientation_metadata_resize():
    source=Image.new('RGB',(2400,1200),'red');exif=Image.Exif();exif[274]=6;exif[270]='private'
    data=io.BytesIO();source.save(data,'JPEG',exif=exif);data.seek(0)
    output=photo_storage.prepare(FileStorage(stream=data))
    with Image.open(io.BytesIO(output)) as image:
        assert image.size==(800,1600) and not image.getexif()


def face(x=60,y=60,w=130,h=150):
    return np.array([[x,y,w,h,90,100,150,100,120,130,100,160,140,160,.99]],dtype=np.float32)


def test_quality_measured_advisory_states():
    rng=np.random.default_rng(1)
    good=rng.integers(70,200,(300,300,3),dtype=np.uint8)
    assert evaluate(good,None)['state']=='camera_ready'
    assert evaluate(good,face())['state']=='face_detected'
    assert evaluate(good,face(w=20,h=20))['state']=='quality_far'
    assert evaluate(good,face(w=45,h=45))['state']=='closer'
    assert evaluate(good,face(x=-10))['state']=='quality_position'
    assert evaluate(np.zeros_like(good),face())['state']=='quality_dark'
    assert evaluate(np.full_like(good,128),face())['state']=='quality_blur'
    assert evaluate(good,np.concatenate([face(),face()]))['state']=='multiple_faces'
    angled=face();angled[0,8]=158
    assert evaluate(good,angled)['state']=='face_detected' # moderate asymmetry allowed
    angled[0,8]=190
    assert evaluate(good,angled)['state']=='quality_pose'


def test_slow_sampling_preserves_confirmation_window(app):
    from services.live_scanner import settings
    with app.app_context():
        app.config['LIVE_SCAN_INTERVAL_MS']=5000
        config=settings()
        assert config['window_seconds']>(config['stable_frames']-1)*config['interval_ms']/1000


def test_railway_rejects_ephemeral_paths(monkeypatch,tmp_path):
    from app import create_app
    monkeypatch.setenv('RAILWAY_ENVIRONMENT_ID','test-environment')
    monkeypatch.setenv('RAILWAY_VOLUME_MOUNT_PATH',str(tmp_path/'volume'))
    with pytest.raises(RuntimeError,match='persistent volume'):
        create_app({'TESTING':False,'DATA_DIR':tmp_path/'ephemeral','MODEL_DIR':tmp_path/'models'})


def test_private_storage_survives_app_restart(app):
    from app import create_app
    with app.app_context(): photo_storage.save('restart.jpg',b'persistent bytes')
    restarted=create_app(dict(app.config))
    with restarted.app_context():
        assert photo_storage.photo_path('restart.jpg').read_bytes()==b'persistent bytes'


def test_total_upload_limit_through_registration(authorized,app,fake_embedding):
    # 7 individually legal 14.5 MiB files exceed the 100 MiB batch budget,
    # but fit below the HTTP envelope limit so batch validation is exercised.
    raw=image_bytes().getvalue()
    padded=raw+b'\0'*(int(14.5*1024*1024)-len(raw))
    response=add_child(authorized,photos=[(io.BytesIO(padded),f'{i}.jpg') for i in range(7)])
    assert response.status_code==200
    assert '100 МБ' in response.text
    with app.app_context(): assert list(db.session.scalars(select(Photo)))==[]
