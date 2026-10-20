"""Integration tests use actual YuNet/SFace inference, never mocked embeddings."""
import io
from pathlib import Path
import cv2
import numpy as np
import pytest
from services.liveness import LivenessResult
from services.face_recognition import FaceError
from database import db
from database.models import Attendance, Scan, UnknownFace
from sqlalchemy import select, func
from decimal import Decimal
from conftest import csrf,add_child

FIXTURES=Path(__file__).parent/'fixtures'

def encode(image):
    return io.BytesIO(cv2.imencode('.jpg',image)[1].tobytes())

def test_real_recognition_end_to_end(authorized,app,monkeypatch):
    if not (app.config['MODEL_DIR']/'face_recognition_sface_2021dec.onnx').exists():
        pytest.skip('Run scripts/download_models.py for actual-model integration tests')
    client=authorized
    original=cv2.imread(str(FIXTURES/'lena.jpg'))
    response=add_child(client,photos=[(encode(original),'reference.jpg')])
    assert response.status_code==302,response.text
    transformed=cv2.convertScaleAbs(cv2.resize(original,(620,620)),alpha=.95,beta=8)
    service=app.extensions['face']
    original_extract=service.analyze
    calls=[]
    def extract(image,scanning=False):
        calls.append(scanning)
        return original_extract(image,scanning)
    monkeypatch.setattr(service,'analyze',extract)
    def scan(image):
        return client.post('/api/scan',data={'csrf_token':csrf(client),'photo':(encode(image),'scan.jpg')})
    response=scan(transformed)
    assert response.json['status']=='success',response.json
    assert scan(transformed).json['status']=='already'
    assert calls==[True,True] # Enrollment embeddings are never recalculated.
    other=cv2.imread(str(FIXTURES/'messi5.jpg'))
    other=cv2.resize(other[60:180,200:310],(550,600))
    response=scan(other)
    assert response.json['status']=='low_confidence',response.json
    with app.app_context():
        assert db.session.scalar(select(func.count(Attendance.id)))==1
        assert db.session.scalar(select(func.sum(Attendance.amount))) == Decimal('25000.00')
        assert db.session.scalar(select(func.count(UnknownFace.id)))==1
        assert db.session.scalar(select(func.count(Scan.id)))==2
    assert client.get('/unknown').status_code==200
    assert client.get('/?period=today').status_code==200
    # Liveness enforcement must fail closed when no provider is available.
    app.config['REQUIRE_LIVENESS']=True
    assert scan(transformed).status_code==422
    class Reject:
        def check(self,image,face):return LivenessResult('failed',0.1)
    app.config['REQUIRE_LIVENESS']=False
    app.extensions['liveness']=Reject()
    assert scan(transformed).status_code==422
    # Blank and multi-face frames must never create attendance.
    app.config['REQUIRE_LIVENESS']=False
    with app.app_context():
        with pytest.raises(FaceError,match='registration_no_face'): service.extract(np.zeros((512,512,3),np.uint8))
        with pytest.raises(FaceError,match='registration_multiple_faces'): service.extract(np.concatenate([original,original],axis=1))
