"""PostgreSQL persistence. Schema changes are applied only through migrations."""
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from sqlalchemy import MetaData

NAMING = {
    'ix': 'ix_%(table_name)s_%(column_0_name)s',
    'uq': 'uq_%(table_name)s_%(column_0_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}
db = SQLAlchemy(metadata=MetaData(naming_convention=NAMING))
migrate = Migrate()


def init_app(app):
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError
    uri = app.config.get('SQLALCHEMY_DATABASE_URI')
    try:
        url = make_url(uri) if uri else None
    except ArgumentError:
        raise RuntimeError('Invalid DATABASE_URL. Supply a PostgreSQL connection URL.') from None
    if url is None or url.get_backend_name() != 'postgresql':
        raise RuntimeError('Set DATABASE_URL to a PostgreSQL connection URL. No SQLite fallback is supported.')
    if url.drivername == 'postgresql':
        url = url.set(drivername='postgresql+psycopg')
    if url.drivername != 'postgresql+psycopg':
        raise RuntimeError('DATABASE_URL must use postgresql:// or postgresql+psycopg://.')
    app.config['SQLALCHEMY_DATABASE_URI'] = url
    db.init_app(app)
    from database import models  # noqa: F401 -- register model metadata for Alembic
    migrate.init_app(app, db, render_as_batch=False)
