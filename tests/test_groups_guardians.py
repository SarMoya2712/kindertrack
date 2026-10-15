"""Groups/guardians and scan-to-report regression against isolated PostgreSQL."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from flask_migrate import downgrade, upgrade
from sqlalchemy import select, func, text, inspect
from database import db
from database.models import Child, Guardian, Attendance, Scan, UnknownFace, Photo
from services.statistics import statistics
from services.guardians import snapshot
from services.localization import CATALOGS
from conftest import add_child, csrf, child_form
from test_attendance import scan
from test_reports import contexts


def family_form():
    return dict(child_form(), guardian_index=['0','1'], guardian_1_relation_type='mother',
        guardian_1_first_name='Малика', guardian_1_last_name='Алиева',
        guardian_1_middle_name='', guardian_1_phone='+998909876543')


def test_groups_create_edit_deactivate_filter_and_localization(authorized, app, fake_embedding):
    client = authorized
    assert client.post('/groups/new', data={'csrf_token':csrf(client), 'name':'Младшая группа', 'description':'Возраст 3–4 года'}).status_code == 302
    assert client.post('/groups/2/edit', data={'csrf_token':csrf(client), 'name':'Средняя группа', 'description':'Возраст 4–5 лет'}).status_code == 302
    assert add_child(client, overrides={'group_id':'2'}).status_code == 302
    assert 'Алиев' in client.get('/children?group_id=2').text
    assert 'Алиев' not in client.get('/children?group_id=1').text
    with app.app_context(): original_photo = db.session.scalar(select(Photo.filename))
    assert client.post('/groups/2/status', data={'csrf_token':csrf(client),'status':'inactive'}).status_code == 302
    # No new assignment to an inactive/missing group. Existing membership remains editable.
    assert add_child(client, overrides={'group_id':'2'}).status_code == 200
    assert add_child(client, overrides={'group_id':'999'}).status_code == 200
    assert add_child(client, overrides={'group_id':''}).status_code == 200
    assert 'Средняя группа' not in client.get('/children/new').text
    assert 'Средняя группа' in client.get('/children/1/edit').text
    assert client.post('/children/1/edit', data=dict(child_form(), group_id='2', first_name='Бек', csrf_token=csrf(client))).status_code == 302
    with app.app_context():
        assert db.session.get(Child,1).group_id == 2
        assert db.session.scalar(select(Photo.filename)) == original_photo
        assert db.session.scalar(select(func.count(Child.id))) == 1
    assert client.post('/groups/2/status', data={'csrf_token':csrf(client),'status':'active'}).status_code == 302
    assert add_child(client, overrides={'group_id':'2'}).status_code == 302
    assert client.post('/groups/new', data={'csrf_token':csrf(client),'name':' '}).status_code == 200
    assert client.post('/groups/2/status', data={'status':'inactive'}).status_code == 400
    for language in CATALOGS:
        client.post('/language/'+language, data={'csrf_token':csrf(client)})
        for path, key in [('/groups','groups'),('/groups/new','group_name'),('/children/1/edit','add_guardian'),('/children/1','guardians')]:
            response = client.get(path)
            assert response.status_code == 200 and CATALOGS[language][key] in response.text
    for path in ('/groups','/groups/new','/groups/1/edit'):
        assert app.test_client().get(path).status_code == 302


def test_guardians_multiple_update_remove_primary_validation(authorized, app, fake_embedding):
    client = authorized
    assert add_child(client, overrides=family_form()).status_code == 302
    with app.app_context():
        child = db.session.get(Child,1)
        assert [g.relation_type for g in child.guardians] == ['father','mother']
        assert [g.is_primary for g in child.guardians] == [True,False]
        original = snapshot(child.guardians)
        columns = {c['name'] for c in inspect(db.engine).get_columns('children')}
        assert not any(c.startswith('parent_') for c in columns)
    assert 'Малика' in client.get('/children/1').text
    bad = dict(family_form(), guardian_1_is_primary='on', csrf_token=csrf(client))
    assert client.post('/children/1/edit', data=bad).status_code == 200
    with app.app_context(): assert snapshot(db.session.get(Child,1).guardians) == original
    form = dict(family_form(), csrf_token=csrf(client), guardian_0_is_primary='', guardian_1_is_primary='on', guardian_1_phone='+998901111111')
    assert client.post('/children/1/edit', data=form).status_code == 302
    with app.app_context(): assert db.session.get(Child,1).guardians[1].is_primary
    # Removing father preserves the explicitly selected mother, without index shifting.
    form['guardian_index'] = ['1']
    assert client.post('/children/1/edit', data=form).status_code == 302
    with app.app_context():
        rows = db.session.scalars(select(Guardian)).all()
        assert len(rows) == 1 and rows[0].first_name == 'Малика'
    form['guardian_index'] = []
    assert client.post('/children/1/edit', data=form).status_code == 302
    with app.app_context(): assert not db.session.get(Child,1).guardians
    # Invalid indices, relationship types and excessive rows are all rejected.
    for change in [{'guardian_index':['0','0']}, {'guardian_index':['../0']},
                   {'guardian_index':[str(i) for i in range(11)]}, {'guardian_0_relation_type':'uncle'},
                   {'guardian_0_phone':'<script>'}]:
        assert add_child(client, overrides=change).status_code == 200
    assert add_child(client, overrides={'guardian_index':[]}).status_code == 302


def test_scan_statistics_exact_flow_and_legacy_cooldown(authorized, app, fake_embedding, monkeypatch):
    # Local Oct 14, even though PostgreSQL can represent the same instant as UTC Oct 13.
    moment = datetime(2026,10,14,0,2,tzinfo=ZoneInfo('Asia/Tashkent'))
    for service in ('attendance','scanner','statistics'):
        monkeypatch.setattr('services.'+service+'.now',lambda:moment)
    client = authorized
    assert add_child(client, price='25000', overrides=family_form()).status_code == 302
    with app.app_context():
        # Reproduce production's legacy recognition-only event within the cooldown.
        db.session.add(Scan(child_id=1, status='recognized', created_at=(moment-timedelta(minutes=1)).astimezone(timezone.utc)))
        db.session.commit()
        before = statistics('2026-10-14','2026-10-14')
        assert before['missing_attendance'] == 1 and before['today']['count'] == 0
    assert 'без записи посещения' in client.get('/?period=today').text
    assert scan(client).json['status'] == 'success'
    with app.app_context():
        row = db.session.scalar(select(Attendance))
        assert row.attendance_date.isoformat() == '2026-10-14' and row.amount == Decimal('25000')
        assert db.session.scalar(select(func.count(Attendance.id))) == 1
        assert db.session.get(Child,1).group_id == 1 and len(db.session.get(Child,1).guardians) == 2
    with contexts(app) as rendered:
        assert client.get('/?period=today').status_code == 200
    stats = rendered[-1]['stats']
    assert stats['today']['unique'] - before['today']['unique'] == 1
    assert stats['today']['amount'] - before['today']['amount'] == Decimal('25000')
    assert stats['selected']['count'] == 1 and stats['selected']['amount'] == Decimal('25000')
    assert stats['average'] == Decimal('1.00') and stats['missing_attendance'] == 0
    response = scan(client)
    assert response.json['message'] == 'Ребёнок уже отмечен сегодня'
    with app.app_context(): assert statistics('2026-10-14','2026-10-14') == stats
    for period in ('today','7days','month','custom&start=2026-10-14&end=2026-10-14'):
        with contexts(app) as rendered:
            assert client.get('/?period='+period).status_code == 200
        selected = rendered[-1]['stats']
        assert selected['selected']['count'] == 1
        assert selected['selected']['amount'] == Decimal('25000')
        assert sum(row['count'] for row in selected['chart']) == 1
        assert sum(Decimal(row['amount']) for row in selected['chart']) == Decimal('25000')
    # Unknown image affects only unknown count, never attendance or accrued amounts.
    monkeypatch.setattr(app.extensions['face'], 'compare', lambda feature:dict(child_id=None, status='not_found', confidence=.1))
    assert scan(client).json['status'] == 'not_found'
    with app.app_context():
        after = statistics('2026-10-14','2026-10-14')
        assert after['unknown'] == stats['unknown']+1
        assert after['today'] == stats['today'] and after['selected'] == stats['selected']
        assert db.session.scalar(select(func.count(UnknownFace.id))) == 1
        assert db.session.scalar(select(func.count(Attendance.id))) == 1


def test_legacy_missing_attendance_is_reported_never_backfilled(authorized, app, fake_embedding):
    add_child(authorized)
    with app.app_context():
        db.session.add(Scan(child_id=1,status='recognized',created_at=datetime(2026,10,13,20,tzinfo=timezone.utc)))
        db.session.commit()
        stats = statistics('2026-10-14','2026-10-14')
        assert stats['missing_attendance'] == 1 and stats['selected']['amount'] == 0
    result = app.test_cli_runner().invoke(args=['check-attendance'])
    assert result.exit_code == 0 and 'local date 2026-10-14' in result.output
    with app.app_context(): assert db.session.scalar(select(func.count(Attendance.id))) == 0


def test_migration_preserves_parents_photos_history_and_missing_contacts(app):
    migrations = str(Path(__file__).resolve().parents[1]/'migrations')
    with app.app_context():
        downgrade(directory=migrations, revision='a047eced711a')
        db.session.execute(text("""INSERT INTO children(id,first_name,last_name,registration_date,attendance_price,status,created_at,
            parent_first_name,parent_last_name,parent_middle_name,parent_phone) VALUES
            (11,'А','Б','2026-01-01',25000,'active',CURRENT_TIMESTAMP,'О','П','Р','+998901234567'),
            (12,'В','Г','2026-01-01',0,'inactive',CURRENT_TIMESTAMP,NULL,NULL,NULL,NULL),
            (13,'Д','Е','2026-01-01',10,'active',CURRENT_TIMESTAMP,NULL,NULL,NULL,'+998902222222')"""))
        db.session.execute(text("INSERT INTO photos(child_id,filename,embedding,model) VALUES(11,'preserved.jpg',decode('010203','hex'),'legacy')"))
        db.session.execute(text("INSERT INTO attendance(child_id,attendance_date,amount,created_at) VALUES(11,'2026-01-02',123.45,CURRENT_TIMESTAMP)"))
        db.session.commit()
        upgrade(directory=migrations)
        children = db.session.scalars(select(Child).order_by(Child.id)).all()
        assert len({c.group_id for c in children}) == 1
        assert children[0].group.name == 'Без группы (перенесено)'
        assert children[0].guardians[0].full_name == 'П О Р'
        assert children[0].guardians[0].relation_type == 'other'
        assert not children[1].guardians
        assert children[2].guardians[0].phone == '+998902222222'
        assert db.session.scalar(select(Photo.embedding)) == b'\x01\x02\x03'
        assert db.session.scalar(select(Attendance.amount)) == Decimal('123.45')
