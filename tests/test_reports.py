"""Real PostgreSQL report totals, local-date filters, localization and private media."""
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from flask import template_rendered
import pytest
from database import db
from database.models import Child, Attendance, UnknownFace, Scan
from services.localization import CATALOGS
from conftest import csrf, image_bytes

DAY=date(2026,10,13)
ZONE=ZoneInfo('Asia/Tashkent')
ATTENDANCES=[(1,'2026-10-13','10.10'),(2,'2026-10-13','20.20'),(1,'2026-10-12','10.10'),
             (3,'2026-10-07','5.00'),(3,'2026-09-30','100.00'),(3,'2026-01-01','1.00')]


@pytest.fixture
def report_data(app, monkeypatch):
    monkeypatch.setattr('services.statistics.now', lambda:datetime(2026,10,13,12,tzinfo=ZONE))
    with app.app_context():
        for i in range(1,4):
            db.session.add(Child(group_id=1, id=i,first_name=f'Ребёнок {i}',last_name='Тест',registration_date=date(2026,1,1),
                attendance_price=Decimal('999999.99'),status='inactive' if i==3 else 'active'))
        db.session.flush()
        for child,day,amount in ATTENDANCES:
            db.session.add(Attendance(child_id=child,attendance_date=date.fromisoformat(day),amount=Decimal(amount)))
        for i,stamp in enumerate(['2026-10-12T23:59:59','2026-10-13T00:00:00','2026-10-13T23:59:59',
                                   '2026-10-14T00:00:00','2026-10-07T00:00:00','2026-09-30T12:00:00'],1):
            name=f'unknown-{i}.jpg';(app.config['DATA_DIR']/'photos'/name).write_bytes(image_bytes().getvalue())
            db.session.add(UnknownFace(filename=name,created_at=datetime.fromisoformat(stamp).replace(tzinfo=ZONE),
                confidence=None if i==1 else .25, reason='low_confidence' if i==2 else 'not_found'))
        for i,child,status in [(8,1,'success'),(9,1,'already'),(10,2,'recognized')]:
            db.session.add(Scan(child_id=child,status=status,created_at=datetime(2026,10,13,i,tzinfo=ZONE)))
        db.session.add(Scan(status='low_confidence',created_at=datetime(2026,10,13,11,tzinfo=ZONE)))
        db.session.commit()
    return ATTENDANCES


@contextmanager
def contexts(app):
    captured=[]
    def receive(sender, template, context, **extra): captured.append(context)
    template_rendered.connect(receive,app)
    try: yield captured
    finally: template_rendered.disconnect(receive,app)


def test_dashboard_cards_and_recent_sections(authorized, app, report_data):
    with contexts(app) as rendered:
        response=authorized.get('/?period=7days')
    assert response.status_code==200
    data=rendered[-1];stats=data['stats']
    assert stats['today']['count']==2
    assert stats['today']['amount']==Decimal('30.30')
    assert stats['total']==3  # Includes inactive registered children.
    assert stats['selected']['count']==4
    assert stats['selected']['amount']==Decimal('45.40')
    assert stats['unknown']==2  # Local midnight boundaries, not UTC date().
    for metric in ['attended_today','total_children','attendance_count','accrued_today','accrued_period','unknown_today']:
        assert f'data-metric="{metric}"' in response.text
    assert [row[0].id for row in data['recognized']]==[2,1]
    assert len(data['scans'])==4
    assert [row.id for row in data['unknown_preview']]==[3,2,1]
    assert '999 999.99' not in response.text


@pytest.mark.parametrize('period,start,end',[
 ('today','2026-10-13','2026-10-13'),('yesterday','2026-10-12','2026-10-12'),
 ('7days','2026-10-07','2026-10-13'),('30days','2026-09-14','2026-10-13'),
 ('month','2026-10-01','2026-10-13'),('previous_month','2026-09-01','2026-09-30'),
 ('year','2026-01-01','2026-10-13'),('custom','2026-09-30','2026-10-12')])
def test_every_statistics_filter(authorized, app, report_data, period,start,end):
    selected=[(child,Decimal(amount)) for child,day,amount in ATTENDANCES if start<=day<=end]
    for path in ['/','/statistics']:
        with contexts(app) as rendered:
            response=authorized.get(path,query_string={'period':period,'start':start,'end':end})
        assert response.status_code==200
        data=rendered[-1];stats=data['stats']
        assert (data['start'],data['end'])==(start,end)
        assert stats['selected']['count']==len(selected)
        assert stats['selected']['unique']==len({child for child,_ in selected})
        assert stats['selected']['amount']==sum((amount for _,amount in selected),Decimal(0))
        days=(date.fromisoformat(end)-date.fromisoformat(start)).days+1
        assert len(stats['chart'])==days
        assert stats['average']==(Decimal(len(selected))/days).quantize(Decimal('.01'))
        assert sum(row['count'] for row in stats['chart'])==len(selected)
        assert sum(Decimal(row['amount']) for row in stats['chart'])==stats['selected']['amount']
        assert stats['chart'][0]['day']==start and stats['chart'][-1]['day']==end


@pytest.mark.parametrize('period,expected', [('today',[3,2]),('7days',[3,2,1,5]),('30days',[3,2,1,5,6]),('custom',[1,5,6])])
def test_unknown_filters_confidence_reason_and_protection(authorized, app, report_data, period,expected):
    with contexts(app) as rendered:
        response=authorized.get('/unknown',query_string={'period':period,'start':'2026-09-30','end':'2026-10-12'})
    assert response.status_code==200
    assert [row.id for row in rendered[-1]['rows']]==expected
    assert rendered[-1]['count']==len(expected)
    if period=='today':
        assert '25.0%' in response.text and CATALOGS['ru']['reason_low_confidence'] in response.text
    assert authorized.get('/unknown/media/2').status_code==200
    assert app.test_client().get('/unknown/media/2').status_code==302
    assert authorized.get('/unknown/media/2').headers['Cache-Control']=='no-store, private'


def test_unknown_review_preserves_filter(authorized, app, report_data):
    response=authorized.post('/unknown/1/review',data={'csrf_token':csrf(authorized),'period':'custom',
        'start':'2026-10-12','end':'2026-10-12','page':1},follow_redirects=True)
    assert response.status_code==200 and 'data-unknown-id="1"' in response.text
    assert CATALOGS['ru']['reviewed'] in response.text
    with app.app_context(): assert db.session.get(UnknownFace,1).reviewed


@pytest.mark.parametrize('query', [
 'period=custom&start=bad&end=2026-10-13', 'period=custom&start=2026-10-14&end=2026-10-13',
 'period=custom&start=2020-01-01&end=2026-10-13', 'period=custom',
 'period=custom&start=9999-12-31&end=9999-12-31'])
def test_invalid_ranges_show_error_and_safe_default(authorized, app, query):
    for path in ['/','/statistics','/unknown']:
        response=authorized.get(path+'?'+query)
        assert response.status_code==200 and CATALOGS['ru']['date_invalid'] in response.text


def test_empty_period_zeros_and_three_languages(authorized, app, report_data):
    for language in CATALOGS:
        authorized.post('/language/'+language,data={'csrf_token':csrf(authorized)})
        for path in ['/','/statistics','/unknown']:
            with contexts(app) as rendered:
                response=authorized.get(path+'?period=custom&start=2025-12-01&end=2025-12-03')
            assert response.status_code==200
            if path!='/unknown':
                stats=rendered[-1]['stats']
                assert stats['selected']['count']==0 and stats['selected']['amount']==0
                assert stats['selected']['unique']==0 and stats['average']==0
                assert all(row['count']==0 and Decimal(row['amount'])==0 for row in stats['chart'])
                assert CATALOGS[language]['no_period_attendance'] in response.text
            else: assert CATALOGS[language]['no_unknown_period'] in response.text
            assert '/language/en' not in response.text
    assert all(set(catalog)==set(CATALOGS['ru']) for catalog in CATALOGS.values())


def test_unknown_pagination_preserves_range(authorized, app, report_data):
    with app.app_context():
        for i in range(30):
            db.session.add(UnknownFace(filename=f'extra-{i}.jpg', created_at=datetime(2026,10,13,15,tzinfo=ZONE)))
        db.session.commit()
    with contexts(app) as rendered:
        response=authorized.get('/unknown?period=custom&start=2026-10-13&end=2026-10-13&page=2')
    assert response.status_code==200
    assert rendered[-1]['count']==32 and len(rendered[-1]['rows'])==8
    assert 'start=2026-10-13' in response.text and 'page=1' in response.text
