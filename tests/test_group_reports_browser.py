"""Group pages and real Excel download in desktop/tablet/mobile Chrome."""
import os
from pathlib import Path
from openpyxl import load_workbook
import pytest
pytest.importorskip('playwright.sync_api')
from playwright.sync_api import expect
from test_camera_browser import camera_page  # noqa: F401 -- pytest fixture
from test_group_reports_excel import group_data  # noqa: F401 -- pytest fixture


def test_group_navigation_filters_and_excel_download(camera_page,group_data,tmp_path):
    page,origin=camera_page
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    for width,height in [(390,844),(768,1024),(1440,1000)]:
        page.set_viewport_size({'width':width,'height':height})
        for path in ['/groups?period=today','/groups/2?period=today','/statistics?period=today&group_id=2']:
            page.goto(origin+path)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),path
            if os.getenv('GROUP_REPORT_ARTIFACT_DIR'):
                folder=Path(os.environ['GROUP_REPORT_ARTIFACT_DIR']);folder.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(folder/(str(width)+'-'+path.split('?')[0].strip('/').replace('/','-')+'.png')),full_page=True)
    page.goto(origin+'/groups?period=today')
    page.locator('[data-group-id="2"]').get_by_role('link',name='Открыть группу').click()
    expect(page.locator('[data-child-id]')).to_have_count(2)
    page.locator('select[name=status]').select_option('present')
    page.locator('.table-toolbar button').click()
    expect(page.locator('[data-child-id]')).to_have_count(1)
    page.locator('#period-select').select_option('7days')
    page.locator('.period-filter button').click()
    expect(page.locator('select[name=status]')).to_have_value('present')
    page.goto(origin+'/statistics?period=today')
    page.locator('[name=group_id]').select_option('2')
    page.locator('.period-filter button').click()
    expect(page.locator('[data-metric=attendance_count] strong')).to_have_text('1')
    expect(page.locator('[data-metric=accrued_period] strong')).to_have_text('25 000')
    with page.expect_download() as download_info: page.locator('#download-excel').click()
    download=download_info.value
    assert download.suggested_filename=='statistics_group_2_2026-10-14_2026-10-14.xlsx'
    path=tmp_path/download.suggested_filename;download.save_as(path)
    report=load_workbook(path)
    assert report['Посещения']['F2'].value=='Группа А'
    assert report['Посещения']['H2'].value==25000
    for label in ('UZ','ЎЗ','RU'):
        page.get_by_role('button',name=label,exact=True).click()
        expect(page.locator('[name=group_id]')).to_have_value('2')
        assert page.locator('#download-excel').get_attribute('href').endswith('group_id=2')
    assert errors==[]
