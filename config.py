import os
from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)
class Config:
    SECRET_KEY = os.getenv('SECRET_KEY')
    DATA_DIR = ROOT / 'instance'
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {'pool_pre_ping': True, 'hide_parameters': True,
        'connect_args': {'connect_timeout': 5}, 'pool_timeout': 10}
    MODEL_DIR = ROOT / 'models'
    TIMEZONE = os.getenv('TIMEZONE', 'Asia/Tashkent')
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_REFRESH_EACH_REQUEST = False
    SESSION_COOKIE_SECURE = os.getenv('COOKIE_SECURE', 'true').lower() == 'true'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    MAX_FORM_MEMORY_SIZE = 128 * 1024
    MAX_FORM_PARTS = 128
    TRUSTED_HOSTS = [host.strip() for host in os.getenv('TRUSTED_HOSTS', '').split(',') if host.strip()] or None
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024
    FACE_THRESHOLD = float(os.getenv('FACE_THRESHOLD', '0.50'))
    FACE_MARGIN = float(os.getenv('FACE_MARGIN', '0.08'))
    REQUIRE_LIVENESS = os.getenv('REQUIRE_LIVENESS', 'false').lower() == 'true'

    LIVE_SCAN_INTERVAL_MS = int(os.getenv('LIVE_SCAN_INTERVAL_MS', '600'))
    LIVE_SCAN_STABLE_FRAMES = int(os.getenv('LIVE_SCAN_STABLE_FRAMES', '3'))
    LIVE_SCAN_WINDOW_SECONDS = float(os.getenv('LIVE_SCAN_WINDOW_SECONDS', '5'))
    LIVE_SCAN_PAUSE_MS = int(os.getenv('LIVE_SCAN_PAUSE_MS', '2500'))
    LIVE_UNKNOWN_COOLDOWN_SECONDS = int(os.getenv('LIVE_UNKNOWN_COOLDOWN_SECONDS', '60'))
    LIVE_UNKNOWN_SIMILARITY = float(os.getenv('LIVE_UNKNOWN_SIMILARITY', '0.65'))
