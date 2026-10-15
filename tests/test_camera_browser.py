"""Optional Chrome integration with simulated camera; no physical camera access."""
import os
import time
from pathlib import Path
from threading import Thread
import pytest
from werkzeug.serving import make_server

playwright = pytest.importorskip('playwright.sync_api')


def wait_js(page, expression):
    # Poll through CDP without Playwright's string-eval helper (CSP forbids eval).
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if page.evaluate('() => (' + expression + ')'):
            return
        page.wait_for_timeout(50)
    raise AssertionError(expression)


@pytest.fixture
def camera_page(app, authorized):
    executable = os.getenv('TEST_CHROME_PATH', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    if not Path(executable).exists():
        pytest.skip('Set TEST_CHROME_PATH to a local Chromium browser')
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    try:
        with playwright.sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=executable, headless=True,
                args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'])
            context = browser.new_context(viewport={'width':390, 'height':844}, is_mobile=True,
                has_touch=True, permissions=['camera'])
            context.add_cookies([{'name':'session', 'value':authorized.get_cookie('session').value, 'url':origin}])
            page = context.new_page()
            yield page, origin
            browser.close()
    finally:
        server.shutdown();thread.join(timeout=5)


def test_mobile_camera_preview_capture_switch_stop(camera_page):
    page, origin = camera_page
    errors=[];page.on('pageerror', lambda error:errors.append(str(error)))
    page.add_init_script('''
        window.cameraRequests=[];
        const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
        navigator.mediaDevices.getUserMedia=async constraints=>{
          window.cameraRequests.push(constraints);
          const stream=await original(constraints);window.lastStream=stream;return stream;
        };
    ''')
    page.goto(origin+'/scanner')
    assert page.locator('html').get_attribute('lang') == 'ru'
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.locator('#start-camera').click()
    wait_js(page, "document.querySelector('#camera').videoWidth > 0 && !document.querySelector('#stop-camera').hidden")
    assert page.evaluate('cameraRequests[0].video.facingMode.ideal') == 'environment'
    assert page.evaluate('cameraRequests[0].audio') is False
    hits=[]
    def response(route):
        hits.append(route.request)
        route.fulfill(json={'status':'success','message':'Посещение добавлено','name':'Азиз Алиев',
            'child_url':'/children/1','group':'Средняя группа','amount':'25000.00','pause_ms':2500,'confidence':.92,'threshold':.50,'liveness':'unavailable'})
    page.route('**/api/live/frame', response)
    playwright.expect(page.locator('#scan-result')).to_contain_text('92.0%')
    playwright.expect(page.locator('#scan-result a')).to_have_text('Азиз Алиев')
    if os.getenv('SCANNER_SCREENSHOT_DIR'):
        directory = Path(os.environ['SCANNER_SCREENSHOT_DIR'])
        directory.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(directory / 'scanner-mobile.png'), full_page=True)
    assert len(hits)==1 and hits[0].headers['x-csrf-token']
    assert b'image/jpeg' in hits[0].post_data_buffer
    playwright.expect(page.locator('#scan-result')).to_contain_text('Средняя группа')
    playwright.expect(page.locator('#scan-result')).to_contain_text('25000.00')
    # Live sampling resumes without another click and keeps the video track alive.
    page.wait_for_timeout(2900)
    assert len(hits) >= 2
    assert page.evaluate('lastStream.getVideoTracks()[0].readyState') == 'live'
    page.locator('#switch-camera').click()
    wait_js(page, 'cameraRequests.length === 2 && !document.querySelector("#switch-camera").disabled')
    assert page.evaluate('cameraRequests[1].video.facingMode.ideal') == 'user'
    page.locator('#stop-camera').click()
    assert page.evaluate('lastStream.getTracks().every(track=>track.readyState==="ended")')
    assert page.locator('#start-camera').is_visible()
    for width,height in [(768,1024),(1440,900)]:
        page.set_viewport_size({'width':width,'height':height})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert errors == []


def test_permission_denial_and_late_permission_cleanup(camera_page):
    page, origin = camera_page
    page.goto(origin+'/scanner')
    page.evaluate('''() => {window.originalCamera=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia=()=>Promise.reject(new DOMException('denied','NotAllowedError'));}''')
    page.locator('#start-camera').click()
    playwright.expect(page.locator('#scan-result')).to_contain_text('Доступ к камере запрещён')
    playwright.expect(page.locator('#start-camera')).to_be_enabled()
    page.evaluate('''() => {navigator.mediaDevices.getUserMedia=async options=>{
        window.lateStream=await window.originalCamera(options);
        return new Promise(resolve=>window.finishPermission=()=>resolve(window.lateStream));
    };}''')
    page.locator('#start-camera').click()
    wait_js(page, 'Boolean(window.finishPermission)')
    page.locator('#stop-camera').click()
    page.evaluate('finishPermission()')
    wait_js(page, 'lateStream.getTracks().every(track=>track.readyState==="ended")')
    assert page.locator('#start-camera').is_visible()
    assert page.evaluate('document.querySelector("#camera").srcObject === null')


@pytest.mark.parametrize('error_name,message', [
    ('NotFoundError','Камера не найдена'),
    ('NotReadableError','Камера недоступна'),
])
def test_camera_unavailable_has_recoverable_message(camera_page,error_name,message):
    page,origin=camera_page
    page.goto(origin+'/scanner')
    page.evaluate('''name=>{navigator.mediaDevices.getUserMedia=()=>Promise.reject(new DOMException('test',name));}''',error_name)
    page.locator('#start-camera').click()
    playwright.expect(page.locator('#scan-result')).to_contain_text(message)
    playwright.expect(page.locator('#start-camera')).to_be_enabled()
    assert page.locator('#start-camera').is_visible()


def test_live_slow_network_single_request_and_stale_reply(camera_page):
    page,origin=camera_page
    requests=[]
    # Hold the first frame response. Several sample intervals must elapse without
    # another request being sent; video playback and Stop must remain responsive.
    page.route('**/api/live/frame',lambda route:requests.append(route))
    page.goto(origin+'/scanner');page.locator('#start-camera').click()
    deadline=time.monotonic()+5
    while not requests and time.monotonic()<deadline: page.wait_for_timeout(50)
    assert len(requests)==1
    video_time=page.locator('#camera').evaluate('(video)=>video.currentTime')
    page.wait_for_timeout(1800)
    assert len(requests)==1
    assert page.locator('#camera').evaluate('(video)=>video.currentTime')>video_time
    raw=requests[0].request.post_data_buffer
    from PIL import Image
    from io import BytesIO
    start=raw.index(b'\xff\xd8');end=raw.index(b'\xff\xd9',start)+2
    with Image.open(BytesIO(raw[start:end])) as sampled: assert max(sampled.size)<=720
    page.locator('#stop-camera').click()
    requests[0].fulfill(json={'status':'success','message':'STALE RESULT','name':'STALE NAME'})
    page.wait_for_timeout(800)
    assert 'STALE' not in page.locator('#scan-result').inner_text()
    assert len(requests)==1 and page.locator('#start-camera').is_visible()
