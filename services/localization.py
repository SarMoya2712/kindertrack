"""Central catalogs shared by Jinja, validation and scanner JavaScript."""
import json
from pathlib import Path
from flask import session

LANGUAGES = {'ru': ('RU', 'ru'), 'uz_lat': ('UZ', 'uz-Latn'), 'uz_cyr': ('ЎЗ', 'uz-Cyrl')}
ROOT = Path(__file__).resolve().parents[1] / 'translations'
CATALOGS = {code: json.loads((ROOT / f'{code}.json').read_text(encoding='utf-8')) for code in LANGUAGES}
if any(set(items) != set(CATALOGS['ru']) for items in CATALOGS.values()):
    raise RuntimeError('All translation catalogs must have identical keys.')


def language():
    code = session.get('lang', 'ru')
    # Old sessions with English selected gracefully return to Russian.
    return code if code in LANGUAGES else 'ru'


def t(key):
    return CATALOGS[language()].get(key, key)


def catalog():
    return CATALOGS[language()]
