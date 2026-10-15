"""Rendered report charts, filters, languages and responsive layouts in Chrome."""
import json
import os
from pathlib import Path
import pytest
pytest.importorskip('playwright.sync_api')
from playwright.sync_api import expect
from test_camera_browser import camera_page, wait_js  # noqa: F401 -- pytest fixture
from test_reports import report_data  # noqa: F401 -- pytest fixture
from services.localization import CATALOGS


def test_report_layouts_charts_and_localization(camera_page, report_data):
    page,origin=camera_page
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    for width,height,label in [(1440,1000,'desktop'),(768,1024,'tablet'),(390,844,'mobile')]:
        page.set_viewport_size({'width':width,'height':height})
        for path in ['/','/statistics','/unknown']:
            page.goto(origin+path+'?period=7days')
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            if path!='/unknown':
                wait_js(page, 'Array.from(document.querySelectorAll("canvas.chart")).every(c=>c.width>0 && c.getAttribute("aria-label").includes("2026-10-07"))')
                charts=page.locator('canvas.chart')
                assert charts.count()==2
                data=json.loads(charts.first.get_attribute('data-chart'))
                assert sum(row['count'] for row in data)==4
                assert charts.first.evaluate('(c)=>Array.from(c.getContext("2d").getImageData(0,0,c.width,c.height).data).some(v=>v>0)')
            if os.getenv('REPORT_SCREENSHOT_DIR'):
                folder=Path(os.environ['REPORT_SCREENSHOT_DIR']);folder.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(folder/(('dashboard' if path=='/' else path[1:])+'-'+label+'.png')),full_page=True)
    page.goto(origin+'/statistics')
    page.locator('#period-select').select_option('custom')
    expect(page.locator('#custom-dates')).to_be_visible()
    page.locator('input[name=start]').fill('2026-10-11')
    page.locator('input[name=end]').fill('2026-10-13')
    page.locator('.period-filter button').click()
    expect(page.locator('[data-metric=attendance_count] strong')).to_have_text('3')
    expect(page.locator('[data-metric=unique_children] strong')).to_have_text('2')
    expect(page.locator('[data-metric=average_attendance] strong')).to_have_text('1.00')
    assert len(json.loads(page.locator('canvas.chart').first.get_attribute('data-chart')))==3
    for language,label in [('uz_lat','UZ'),('uz_cyr','ЎЗ'),('ru','RU')]:
        page.get_by_role('button',name=label,exact=True).click()
        for path,key in [('/','recent_recognized'),('/statistics','unique_children'),('/unknown','reason')]:
            page.goto(origin+path+'?period=7days')
            expect(page.locator('main')).to_contain_text(CATALOGS[language][key])
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert errors==[]
