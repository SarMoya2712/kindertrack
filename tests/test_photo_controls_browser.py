"""Photo-overlay confirmation, individual removal and compact responsive layout."""
from pathlib import Path
from sqlalchemy import select
from database import db
from database.models import Photo
from conftest import add_child, image_bytes
from test_camera_browser import camera_page, playwright  # noqa: F401


def test_photo_overlay_confirmation_and_save(camera_page, authorized, app, fake_embedding):
    assert add_child(authorized,photos=[(image_bytes(),f'{i}.jpg') for i in range(3)]).status_code==302
    with app.app_context():
        before=[(p.id,p.filename,p.embedding) for p in db.session.scalars(select(Photo).order_by(Photo.id))]
    page,origin=camera_page
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(origin+'/children/1/edit')
    cards=page.locator('.existing-photos .reference-photo:not([hidden])')
    page.once('dialog',lambda dialog:dialog.dismiss())
    cards.nth(1).locator('button').click()
    assert cards.count()==3
    page.once('dialog',lambda dialog:dialog.accept())
    cards.nth(1).locator('button').click()
    assert cards.count()==2
    assert page.locator('[name=remove_photos]:checked').input_value()==str(before[1][0])
    with app.app_context(): assert len(list(db.session.scalars(select(Photo))))==3
    reference=str(Path(__file__).parent/'fixtures/lena.jpg')
    page.locator('#photo-upload').set_input_files([reference,reference])
    previews=page.locator('#photo-previews .reference-photo')
    page.once('dialog',lambda dialog:dialog.dismiss())
    previews.first.locator('button').click()
    assert previews.count()==2
    page.once('dialog',lambda dialog:dialog.accept())
    previews.first.locator('button').click()
    assert previews.count()==1
    assert page.locator('#photo-upload').evaluate('(input)=>input.files.length')==1
    for width,height in [(390,844),(1440,1000)]:
        page.set_viewport_size({'width':width,'height':height})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        for card in [cards.first,previews.first]:
            box=card.bounding_box();img=card.locator('img').bounding_box();button=card.locator('button').bounding_box()
            assert box['height']==img['height']==100
            assert button['width']==button['height']==32
            assert button['x']>img['x'] and button['y']==img['y']+4
            assert button['x']+button['width']<=img['x']+img['width']
    page.set_viewport_size({'width':390,'height':844})
    page.locator('.existing-photos').screenshot(path='/tmp/kindscan-photo-overlays-mobile.png')
    page.locator('.form-footer button[type=submit]').click()
    playwright.expect(page).to_have_url(origin+'/children/1')
    with app.app_context():
        after=list(db.session.scalars(select(Photo).order_by(Photo.id)))
        assert len(after)==3
        assert [(p.id,p.filename,p.embedding) for p in after[:2]]==[before[0],before[2]]
        assert not (app.config['DATA_DIR']/'photos'/before[1][1]).exists()
        assert all((app.config['DATA_DIR']/'photos'/p.filename).exists() for p in after)
    assert not errors
