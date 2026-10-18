"""Download official OpenCV Zoo models; no runtime external inference calls."""
from pathlib import Path
from urllib.request import urlretrieve
import hashlib
import os

ROOT=Path(os.getenv('MODEL_DIR') or (Path(os.getenv('DATA_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH', '/data')) / 'models' if os.getenv('RAILWAY_ENVIRONMENT_ID') else Path(__file__).resolve().parents[1]/'models'))
MODELS={
 'face_detection_yunet_2023mar.onnx':('face_detection_yunet',232589,'8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4'),
 'face_recognition_sface_2021dec.onnx':('face_recognition_sface',38696353,'0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79'),
}
if __name__=='__main__':
    ROOT.mkdir(parents=True, exist_ok=True)
    for name,(folder,size,digest) in MODELS.items():
        path=ROOT/name
        if path.exists() and path.stat().st_size==size and hashlib.sha256(path.read_bytes()).hexdigest()==digest:
            print(f'{name}: already installed')
            continue
        url=f'https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/{folder}/{name}'
        tmp=path.with_suffix('.download')
        try:
            urlretrieve(url,tmp)
            if tmp.stat().st_size!=size or hashlib.sha256(tmp.read_bytes()).hexdigest()!=digest: raise RuntimeError(f'Unexpected model size for {name}; check upstream.')
            tmp.replace(path)
            print(f'{name}: installed; sha256={hashlib.sha256(path.read_bytes()).hexdigest()}')
        finally: tmp.unlink(missing_ok=True)
