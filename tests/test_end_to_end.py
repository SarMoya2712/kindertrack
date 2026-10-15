"""Fresh PostgreSQL -> real Chrome UI -> real YuNet/SFace -> logout.

Only the camera hardware is simulated. Recognition, HTTP, migrations, authentication,
audit, attendance, files and reports use the real application and PostgreSQL.
"""
import base64
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
from threading import Thread
import cv2
import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.engine import URL
from flask_migrate import upgrade
from werkzeug.serving import make_server
from app import create_app
from database import db
from database.models import Attendance, Photo, UnknownFace, Audit

playwright=pytest.importorskip('playwright.sync_api')
ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def fresh_application(tmp_path):
    initdb,pg_ctl=shutil.which('initdb'),shutil.which('pg_ctl')
    if not initdb or not pg_ctl: pytest.skip('Local PostgreSQL tools required for fresh-start E2E')
    password=secrets.token_urlsafe(24)
    password_file=tmp_path/'pg-password';password_file.write_text(password);password_file.chmod(0o600)
    cluster=tmp_path/'postgres'
    subprocess.run([initdb,'-D',str(cluster),'--auth=scram-sha-256','--username=e2e_admin','--pwfile='+str(password_file),'--encoding=UTF8','--no-locale'],check=True,capture_output=True)
    password_file.unlink()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    subprocess.run([pg_ctl,'-D',str(cluster),'-l',str(tmp_path/'postgres.log'),'-o',f'-h 127.0.0.1 -p {port} -k ""','-w','start'],check=True,capture_output=True)
    app=None
    try:
        url=URL.create('postgresql+psycopg',username='e2e_admin',password=password,host='127.0.0.1',port=port,database='postgres')
        engine=create_engine(url,isolation_level='AUTOCOMMIT')
        with engine.connect() as connection: connection.exec_driver_sql('CREATE DATABASE kindscan_e2e_test')
        engine.dispose()
        app=create_app({'TESTING':True,'SECRET_KEY':secrets.token_hex(32),'SESSION_COOKIE_SECURE':False,
            'DATA_DIR':tmp_path/'private','SQLALCHEMY_DATABASE_URI':url.set(database='kindscan_e2e_test'),
            'REQUIRE_LIVENESS':False})
        with app.app_context(): upgrade(directory=str(ROOT/'migrations'))
        admin_password=secrets.token_urlsafe(24)
        result=app.test_cli_runner().invoke(args=['create-admin','--email','e2e@example.test'],input=admin_password+'\n'+admin_password+'\n')
        assert result.exit_code==0
        yield app,admin_password
    finally:
        if app:
            with app.app_context(): db.session.remove();db.engine.dispose()
        subprocess.run([pg_ctl,'-D',str(cluster),'-w','stop','-m','fast'],check=True,capture_output=True)


def test_complete_fresh_start_workflow(fresh_application,tmp_path):
    app,password=fresh_application
    executable=os.getenv('TEST_CHROME_PATH','/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    if not Path(executable).exists(): pytest.skip('Local Chrome required')
    frame=cv2.imread(str(ROOT/'tests/fixtures/lena.jpg'))
    yuv=cv2.cvtColor(frame,cv2.COLOR_BGR2YUV_I420).tobytes()
    clip=tmp_path/'camera.y4m'
    clip.write_bytes(b'YUV4MPEG2 W512 H512 F30:1 Ip A1:1 C420jpeg\n'+(b'FRAME\n'+yuv)*30)
    server=make_server('127.0.0.1',0,app,threaded=True)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    try:
        with playwright.sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=executable,headless=True,args=[
                '--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream',
                '--use-file-for-fake-video-capture='+str(clip)])
            context=browser.new_context(viewport={'width':1440,'height':1000},permissions=['camera'])
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(origin+'/login')
            page.locator('[name=email]').fill('e2e@example.test')
            page.locator('[name=password]').fill(password)
            page.locator('.login-card button[type=submit], .login-card .button.full').click()
            playwright.expect(page).to_have_url(origin+'/')
            page.goto(origin+'/groups/new')
            page.locator('[name=name]').fill('Средняя группа')
            page.locator('[name=description]').fill('Проверка регистрации')
            page.locator('.form-footer button').click()
            playwright.expect(page).to_have_url(origin+'/groups')
            page.goto(origin+'/children/new')
            page.locator('[name=group_id]').select_option(label='Средняя группа')
            page.locator('#add-guardian').click()
            page.locator('[name=guardian_0_relation_type]').select_option('father')
            page.locator('[name=guardian_0_is_primary]').check()
            page.locator('#add-guardian').click()
            page.locator('[name=guardian_1_relation_type]').select_option('mother')
            page.locator('[name=guardian_1_first_name]').fill('Малика')
            page.locator('[name=guardian_1_last_name]').fill('Алиева')
            page.locator('[name=guardian_1_phone]').fill('+998909876543')
            # Removing an unsaved extra contact must not disturb the two contacts.
            page.locator('#add-guardian').click()
            page.locator('.remove-guardian').last.click()

            fields={'last_name':'Алиев','first_name':'Азиз','middle_name':'Рустамович',
                    'guardian_0_last_name':'Алиев','guardian_0_first_name':'Рустам','guardian_0_middle_name':'Каримович',
                    'guardian_0_phone':'+998901234567','registration_date':date.today().isoformat(),'attendance_price':'12500.15'}
            for key,value in fields.items(): page.locator('[name='+key+']').fill(value)
            reference=str(ROOT/'tests/fixtures/lena.jpg')
            page.locator('#photo-upload').set_input_files([reference,reference,reference])
            page.locator('.form-footer button').click()
            playwright.expect(page).to_have_url(origin+'/children/1')
            with app.app_context():
                photos=db.session.scalars(select(Photo)).all()
                assert len(photos)==3 and all(len(p.embedding)==512 for p in photos)
            page.goto(origin+'/children/1/edit')
            page.locator('[name=first_name]').fill('Сардор')
            page.locator('[name=attendance_price]').fill('25000')
            page.locator('#photo-upload').set_input_files(reference)
            page.locator('.form-footer button').click()
            playwright.expect(page.locator('.profile-info h2')).to_contain_text('Сардор')
            page.set_viewport_size({'width':390,'height':844})
            page.goto(origin+'/scanner');page.locator('#start-camera').click()
            playwright.expect(page.locator('#stop-camera')).to_be_visible()
            playwright.expect(page.locator('#scan-result')).to_contain_text('Посещение добавлено',timeout=15000)
            with app.app_context():
                row=db.session.scalar(select(Attendance))
                assert row.amount==Decimal('25000')
            playwright.expect(page.locator('#scan-result')).to_contain_text('Ребёнок уже отмечен сегодня',timeout=15000)
            with app.app_context(): assert db.session.scalar(select(func.count(Attendance.id)))==1
            # Replace only simulated video input with a different real portrait.
            other=cv2.imread(str(ROOT/'tests/fixtures/messi5.jpg'))
            other=cv2.resize(other[60:180,200:310],(550,600))
            image_url='data:image/jpeg;base64,'+base64.b64encode(cv2.imencode('.jpg',other)[1]).decode()
            page.evaluate('''async source=>{
                const image=new Image();image.src=source;await image.decode();
                const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;
                canvas.getContext('2d').drawImage(image,0,0);
                const video=document.querySelector('#camera');video.srcObject=canvas.captureStream(10);await video.play();
            }''',image_url)
            playwright.expect(page.locator('#scan-result')).to_contain_text('Ребёнок не найден в базе',timeout=15000)
            page.locator('#stop-camera').click()
            with app.app_context():
                unknown=db.session.scalar(select(UnknownFace))
                assert unknown and (app.config['DATA_DIR']/'photos'/unknown.filename).is_file()
                assert unknown.created_at.tzinfo is not None
            page.goto(origin+'/unknown');playwright.expect(page.locator('[data-unknown-id]')).to_have_count(1)
            page.goto(origin+'/?period=today')
            for metric,expected in [('attended_today','1'),('total_children','1'),('attendance_count','1'),('accrued_today','25 000'),('accrued_period','25 000'),('unknown_today','1')]:
                playwright.expect(page.locator(f'[data-metric={metric}] strong')).to_have_text(expected)
            for chart in page.locator('canvas.chart').all():
                rows=json.loads(chart.get_attribute('data-chart'))
                assert sum(row['count'] for row in rows)==1
                assert sum(Decimal(row['amount']) for row in rows)==Decimal('25000')
            page.goto(origin+'/statistics')
            for period in ['today','yesterday','7days','30days','month','previous_month','year','custom']:
                page.locator('#period-select').select_option(period)
                if period=='custom':
                    page.locator('[name=start]').fill(date.today().isoformat());page.locator('[name=end]').fill(date.today().isoformat())
                page.locator('.period-filter button').click()
                playwright.expect(page.locator('canvas.chart')).to_have_count(2)
            for language,label in [('uz_lat','UZ'),('uz_cyr','ЎЗ'),('ru','RU')]:
                page.get_by_role('button',name=label,exact=True).click()
                assert page.locator('html').get_attribute('lang')=={'ru':'ru','uz_lat':'uz-Latn','uz_cyr':'uz-Cyrl'}[language]
            page.goto(origin+'/children/1')
            page.locator('.status-form button').click();playwright.expect(page.locator('.profile-meta .badge')).to_have_text('Неактивен')
            page.locator('.status-form button').click();playwright.expect(page.locator('.profile-meta .badge')).to_have_text('Активен')
            page.goto(origin+'/audit')
            playwright.expect(page.locator('main')).to_contain_text('Фотографии ребёнка обновлены')
            for width,height in [(390,844),(768,1024),(1440,1000)]:
                page.set_viewport_size({'width':width,'height':height})
                for path in ['/','/statistics','/children','/children/1','/children/1/edit','/scanner','/unknown','/audit','/groups','/groups/1/edit']:
                    page.goto(origin+path)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),(width,path)
                    if path=='/audit':
                        assert page.locator('.audit-values').evaluate_all('(nodes)=>nodes.every(n=>n.scrollWidth <= n.clientWidth+1)')
                    if os.getenv('E2E_ARTIFACT_DIR'):
                        folder=Path(os.environ['E2E_ARTIFACT_DIR']);folder.mkdir(parents=True,exist_ok=True)
                        page.screenshot(path=str(folder/(str(width)+'-'+(path.strip('/').replace('/','-') or 'dashboard')+'.png')),full_page=True)
            page.locator('form[action="/logout"] button').click()
            playwright.expect(page).to_have_url(origin+'/login')
            for path in ['/','/statistics','/children','/children/1','/scanner','/unknown','/audit','/media/4','/unknown/media/1']:
                page.goto(origin+path);playwright.expect(page).to_have_url(origin+'/login')
            with app.app_context():
                actions=set(db.session.scalars(select(Audit.action)))
                assert {'admin_login','admin_logout','child_created','child_edited','tariff_changed','child_photos_updated','child_deactivated','child_activated'}<=actions
                assert db.session.scalar(select(Attendance.amount))==Decimal('25000')
            assert errors==[]
            browser.close()
    finally:
        server.shutdown();thread.join(timeout=5)
