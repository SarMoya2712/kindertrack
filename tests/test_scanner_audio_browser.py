"""Real browser/Web Audio scheduling; camera and result responses are simulated."""
from test_camera_browser import camera_page, wait_js  # noqa: F401


def instrument(page):
    page.add_init_script('''
      window.tones=[];window.audioTimings=[];window.clips=[];
      const bufferSource=AudioContext.prototype.createBufferSource;
      AudioContext.prototype.createBufferSource=function(){
        const node=bufferSource.call(this), start=node.start.bind(node);
        node.start=(...args)=>{
          if(node.buffer?.length>1)window.clips.push({duration:node.buffer.duration,state:document.querySelector('#scan-result').className});
          return start(...args);
        };
        return node;
      };
      const original=AudioContext.prototype.createOscillator;
      AudioContext.prototype.createOscillator=function(){
        const node=original.call(this), start=node.start.bind(node), stop=node.stop.bind(node);
        node.start=(...args)=>{window.tones.push(node.frequency.value);
          window.audioTimings.push({start:args[0], state:document.querySelector('#scan-result').className});
          return start(...args);};
        node.stop=(...args)=>{window.audioTimings[window.audioTimings.length-1].stop=args[0];return stop(...args);};
        return node;
      };
    ''')


def test_confirmed_sounds_cooldowns_and_silent_frames(camera_page):
    page, origin = camera_page
    instrument(page)
    results=[
        {'status':'checking'}, {'status':'no_face'}, {'status':'multiple_faces'},
        {'status':'success','child_id':15,'pause_ms':300},
        {'status':'success','child_id':15,'pause_ms':300},
        {'status':'already','child_id':15,'pause_ms':300},
        {'status':'already','child_id':15,'pause_ms':300},
        {'status':'not_found','saved':True,'pause_ms':300},
        {'status':'not_found','saved':False,'pause_ms':300},
        {'status':'liveness_failed','fatal':True},
    ]
    hits=[]
    assets=[]
    page.on('request',lambda request:assets.append(request.url) if '/static/audio/' in request.url else None)
    def respond(route):
        data=results.pop(0) if results else {'status':'checking'}
        hits.append(data['status'])
        route.fulfill(json={**data,'message':data['status']})
    page.route('**/api/live/frame',respond)
    page.goto(origin+'/scanner')
    assert page.locator('#sound-toggle').get_attribute('title')=='Звук: Вкл'
    page.locator('#start-camera').click()
    wait_js(page,"document.querySelector('#scan-result').textContent.includes('liveness_failed')")
    assert len(hits)==10
    clips=page.evaluate('clips')
    assert [clip['state'] for clip in clips]==['scan-result success','scan-result not_found']
    assert all(clip['duration']>0 for clip in clips)
    assert sorted(url.rsplit('/',1)[1] for url in assets)==['applepay.mp3','error_CDOxCYm.mp3']
    assert page.evaluate('tones')==[440,220,164.80999755859375]
    assert page.locator('#start-camera').is_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_mute_persists_and_reenable_unlocks(camera_page):
    page, origin = camera_page
    instrument(page)
    page.route('**/api/live/frame',lambda route:route.fulfill(json={
        'status':'success','child_id':22,'pause_ms':300,'message':'success'}))
    page.goto(origin+'/scanner')
    page.locator('#sound-toggle').click()
    assert page.locator('#sound-toggle').get_attribute('aria-pressed')=='false'
    page.reload()
    assert page.locator('#sound-toggle').get_attribute('title')=='Звук: Выкл'
    page.locator('#start-camera').click()
    wait_js(page,"document.querySelector('#scan-result').classList.contains('success')")
    assert page.evaluate('tones')==[]
    assert page.evaluate('clips')==[]
    page.locator('#sound-toggle').click()
    wait_js(page,'clips.length === 1')
    assert page.evaluate('tones')==[]
    page.locator('#sound-toggle').click()
    page.wait_for_timeout(1000)
    assert page.evaluate('clips.length')==1


def test_camera_error_once_and_audio_failure_nonblocking(camera_page):
    page, origin = camera_page
    instrument(page)
    page.goto(origin+'/scanner')
    page.evaluate("() => {navigator.mediaDevices.getUserMedia=()=>Promise.reject(new DOMException('denied','NotAllowedError'));}")
    page.locator('#start-camera').click()
    wait_js(page,'tones.length === 2')
    page.locator('#start-camera').click()
    page.wait_for_timeout(200)
    assert page.evaluate('tones')==[220,164.80999755859375]
    page.reload()
    page.evaluate("() => {window.AudioContext=function(){throw new Error('unavailable')};}")
    page.route('**/api/live/frame',lambda route:route.fulfill(json={
        'status':'success','child_id':23,'pause_ms':2500,'message':'success'}))
    page.locator('#start-camera').click()
    wait_js(page,"document.querySelector('#scan-result').classList.contains('success')")
    assert page.evaluate("document.querySelector('#camera').srcObject.getVideoTracks()[0].readyState")=='live'
    assert page.evaluate('tones')==[]


def test_missing_mp3_falls_back_without_interrupting_scanner(camera_page):
    page, origin = camera_page
    instrument(page)
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.route('**/static/audio/*.mp3',lambda route:route.fulfill(status=404,body='missing'))
    page.route('**/api/live/frame',lambda route:route.fulfill(json={
        'status':'success','child_id':25,'pause_ms':2500,'message':'success'}))
    page.goto(origin+'/scanner')
    page.locator('#start-camera').click()
    wait_js(page,'tones.length === 4')
    assert page.evaluate('tones')==[660,1320,880,1760]
    assert page.evaluate('clips')==[]
    assert page.evaluate("document.querySelector('#camera').srcObject.getVideoTracks()[0].readyState")=='live'
    assert errors==[]
