from test_camera_browser import camera_page, wait_js  # noqa: F401
from test_unknown_deletion import seed
from database import db
from database.models import UnknownFace
from sqlalchemy import select


def test_confirm_cancel_bulk_and_mobile(camera_page,app):
    page,origin=camera_page
    ids=seed(app,(0,0,0))
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(origin+'/unknown')
    page.locator(f'[data-unknown-id="{ids[0]}"] .unknown-delete button').click()
    assert page.locator('#unknown-confirm').is_visible()
    assert page.locator('#unknown-confirm-text').inner_text()=='Удалить этот снимок неизвестного лица?'
    page.locator('#unknown-cancel').click()
    assert page.locator('[data-unknown-id]').count()==3
    with app.app_context(): assert db.session.get(UnknownFace,ids[0])
    page.locator(f'[data-unknown-id="{ids[0]}"] .unknown-delete button').click()
    page.locator('#unknown-confirm-delete').click()
    wait_js(page,'document.querySelectorAll("[data-unknown-id]").length === 2')
    assert 'Снимок удалён' in page.locator('body').inner_text()
    page.locator('#unknown-select-all').check()
    assert page.locator('[name="face_ids"]:checked').count()==2
    page.locator('#delete-selected').click()
    page.locator('#unknown-confirm-delete').click()
    wait_js(page,'document.querySelectorAll("[data-unknown-id]").length === 0')
    with app.app_context(): assert db.session.scalars(select(UnknownFace.id)).all()==[]
    for width,height in [(390,844),(768,1024),(1440,900)]:
        page.set_viewport_size({'width':width,'height':height})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#cleanup-period').select_option('custom')
    assert page.locator('[name="cleanup_start"]').is_visible()
    page.locator('[name="cleanup_start"]').fill('2026-01-01')
    page.locator('[name="cleanup_end"]').fill('2026-01-02')
    page.locator('#unknown-cleanup button').click()
    assert page.locator('#unknown-confirm').is_visible()
    page.locator('#unknown-cancel').click()
    assert errors==[]
