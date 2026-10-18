"""Private registration photos; deliberately independent of face recognition."""
import io
import os
import warnings
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
from flask import current_app

MAX_BYTES = 15 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_PIXELS = 50_000_000


class PhotoError(ValueError):
    pass


def prepare(upload):
    data = upload.stream.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        raise PhotoError('image_invalid')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {'JPEG', 'PNG', 'WEBP'} or source.width * source.height > MAX_PIXELS or getattr(source, 'n_frames', 1) != 1:
                    raise PhotoError('image_invalid')
                source.verify()
            with Image.open(io.BytesIO(data)) as source:
                source.load()  # Decode before accepting; truncated files must fail.
                image = ImageOps.exif_transpose(source).convert('RGB')
                image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                image.info.clear()
                output = io.BytesIO()
                image.save(output, format='JPEG', quality=90)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
            Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise PhotoError('image_invalid') from error


def photo_path(filename):
    folder = (Path(current_app.config['DATA_DIR']) / 'photos').resolve()
    if (not filename or filename in {'.', '..'} or Path(filename).name != filename
        or '\\' in filename or '\x00' in filename):
        raise PhotoError('image_invalid')
    path = folder / filename
    if path.is_symlink() or path.resolve().parent != folder:
        raise PhotoError('image_invalid')
    return path


def save(filename, data):
    path = photo_path(filename)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as output:
        output.write(data)


def remove(filename):
    try:
        photo_path(filename).unlink(missing_ok=True)
    except (OSError, PhotoError):
        current_app.logger.error('Could not remove replaced child photo')


def validate_batch(uploads):
    """Bound the entire batch before decoding or generating any embeddings."""
    total = 0
    for upload in uploads:
        upload.stream.seek(0, 2)
        size = upload.stream.tell()
        upload.stream.seek(0)
        if not 0 < size <= MAX_BYTES:
            raise PhotoError('image_invalid')
        total += size
    if total > MAX_TOTAL_BYTES:
        raise PhotoError('photo_total')
