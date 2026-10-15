import secrets
import os
from sqlalchemy.exc import SQLAlchemyError, OperationalError
from werkzeug.exceptions import HTTPException
from pathlib import Path
from flask import Flask, request, render_template
from config import Config
from database import init_app as init_database, db
from services.security import init_app as init_security, csrf_token
from commands import init_app as init_commands
from decimal import Decimal
from datetime import date, datetime
from zoneinfo import ZoneInfo
from services.localization import t, catalog, language, LANGUAGES
from services.audit import now, error_event
from services.face_recognition import FaceService
from services.liveness import UnavailableLiveness

def create_app(test_config=None):
    app=Flask(__name__)
    app.config.from_object(Config)
    if test_config: app.config.update(test_config)
    data=Path(app.config['DATA_DIR'])
    (data/'photos').mkdir(parents=True,exist_ok=True,mode=0o700)
    (data/'photos').chmod(0o700)
    if not app.config['SECRET_KEY']:
        secret_file=data/'session.key'
        try:
            with os.fdopen(os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as file:
                file.write(secrets.token_hex(32))
        except FileExistsError: pass
        app.config['SECRET_KEY']=secret_file.read_text().strip()
    init_database(app)
    init_security(app)
    init_commands(app)
    app.extensions['face']=FaceService(app.config['MODEL_DIR'])
    app.extensions['liveness']=UnavailableLiveness()
    from routes import auth,main,children,scanner,groups
    for module in (auth,main,children,scanner,groups): app.register_blueprint(module.bp)

    @app.context_processor
    def context():
        code = language()
        return dict(t=t, translations=catalog(), lang=code, html_lang=LANGUAGES[code][1],
                    languages=LANGUAGES, csrf_token=csrf_token, now=now())

    @app.template_filter('money')
    def money(value):
        result=f'{Decimal(str(value)):,.2f}'.replace(',',' ')
        return result[:-3] if result.endswith('.00') else result

    @app.template_filter('dt')
    def dt(value):
        if isinstance(value, datetime): value = value.astimezone(ZoneInfo(app.config['TIMEZONE']))
        if isinstance(value, date): return value.strftime('%d.%m.%Y')
        return '.'.join(str(value)[:10].split('-')[::-1])

    @app.template_filter('time')
    def local_time(value):
        return value.astimezone(ZoneInfo(app.config['TIMEZONE'])).strftime('%H:%M')

    def failure(key, status):
        if request.path.startswith('/api/'):
            return {'message':t(key), 'status':'error'}, status
        return render_template('error.html', title=key, code=status), status

    @app.errorhandler(SQLAlchemyError)
    def database_error(error):
        db.session.rollback()
        # SQLAlchemy exceptions can contain SQL parameters or connection strings.
        # Never log their message/traceback or retry the unavailable database here.
        current_code = 'database_unavailable' if isinstance(error, OperationalError) else 'server_error'
        app.logger.error('Database operation failed (%s)', type(error).__name__)
        return failure(current_code, 503 if current_code == 'database_unavailable' else 500)

    @app.errorhandler(Exception)
    def unexpected_error(error):
        if isinstance(error, HTTPException): return error
        app.logger.error('Request failed (%s)', type(error).__name__)
        error_event('system_error', 'storage_error' if isinstance(error, OSError) else 'unexpected_error')
        return failure('server_error', 500)

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(405)
    @app.errorhandler(413)
    @app.errorhandler(500)
    def error(error):
        key={400:'csrf_error',404:'not_found_page',405:'method_not_allowed',413:'request_large',500:'server_error'}[error.code]
        if request.path.startswith('/api/'): return {'message':t(key),'status':'error'},error.code
        return render_template('error.html',title=key,code=error.code),error.code

    return app

if __name__=='__main__':
    create_app().run(host='127.0.0.1',port=5001)
