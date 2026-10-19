'use strict';
(() => {
 const video=document.getElementById('camera'), start=document.getElementById('start-camera'), stop=document.getElementById('stop-camera'), flip=document.getElementById('switch-camera'), result=document.getElementById('scan-result');
 document.querySelector('meta[name="viewport"]').content='width=device-width, initial-scale=1, viewport-fit=cover';
 const audio=window.createScannerAudio(document.getElementById('sound-toggle'));
 const canvas=document.createElement('canvas');
 let stream=null, facing='environment', busy=false, starting=false, generation=0, controller=null, timer=null, feed=null, interval=600;
 const panel=document.querySelector('.camera-panel'),stage=document.querySelector('.camera-stage');
 const overlay=document.getElementById('face-overlay'),qualityLabel=document.getElementById('frame-quality');
 let quality=null,qualityTimer=null,lastFingerprint=null,lastSubmitted=0,confirming=false;
 function drawQuality(value){
   quality=value;
   const w=stage.clientWidth,h=stage.clientHeight;
   overlay.width=w;overlay.height=h;
   const ctx=overlay.getContext('2d');ctx.clearRect(0,0,w,h);
   if(!value){qualityLabel.textContent='';return;}
   const scale=Math.max(w/value.width,h/value.height),dx=(w-value.width*scale)/2,dy=(h-value.height*scale)/2;
   let cropped=false;
   ctx.strokeStyle='#E1D4C2';ctx.lineWidth=3;
   for(const [x,y,bw,bh] of value.boxes){
     let left=x*scale+dx,top=y*scale+dy;
     if(video.classList.contains('mirrored'))left=w-left-bw*scale;
     if(left<0||top<0||left+bw*scale>w||top+bh*scale>h)cropped=true;
     ctx.strokeRect(left,top,bw*scale,bh*scale);
   }
   qualityLabel.textContent=tr(cropped?'quality_position':value.state);
 }
 new ResizeObserver(()=>drawQuality(quality)).observe(stage);
 const fullscreen=document.getElementById('fullscreen-scanner');
 function immersive(active){
   panel.classList.toggle('immersive',active);document.body.classList.toggle('scanner-immersive',active);
   const label=tr(active?'exit_fullscreen':'fullscreen');
   fullscreen.setAttribute('aria-label',label);fullscreen.title=label;
   fullscreen.querySelector('[data-fullscreen-enter]').hidden=active;
   fullscreen.querySelector('[data-fullscreen-exit]').hidden=!active;
 }
 fullscreen.addEventListener('click',async()=>{
   if(panel.classList.contains('immersive')){
     try{if(document.fullscreenElement)await document.exitFullscreen();}catch(_){return;}
     immersive(false);return;
   }
   immersive(true);
   try{if(panel.requestFullscreen)await panel.requestFullscreen();}catch(_){/* CSS fallback remains usable. */}
 });
 document.addEventListener('fullscreenchange',()=>{if(!document.fullscreenElement)immersive(false);});
 document.addEventListener('keydown',event=>{if(event.key==='Escape')immersive(false);});
 let lastMessage='';
 function message(text,status='',data={}) {
   if(status==='error')audio.error();
   const signature=JSON.stringify([text,status,data]);
   if(signature===lastMessage)return;lastMessage=signature;
   result.className='scan-result '+status;result.replaceChildren();
   const heading=document.createElement('strong');heading.textContent=text;result.append(heading);
   if(data.status==='success'||data.status==='already'){
     const card=document.createElement('div');card.className='live-child';
     if(data.photo_url){const photo=document.createElement('img');photo.src=data.photo_url;photo.alt=data.name||'';card.append(photo);}
     const details=document.createElement('div');
     if(data.name && data.child_url){const link=document.createElement('a');link.textContent=data.name;link.href=data.child_url;details.append(link);}
     if(data.group){const group=document.createElement('span');group.textContent=tr('group')+': '+data.group;details.append(group);}
     const attendance=document.createElement('span');attendance.textContent=data.status==='success'?tr('success'):tr('already');details.append(attendance);
     if(data.status==='success'&&data.amount!=null){const amount=document.createElement('strong');amount.textContent=tr('live_accrued')+': '+data.amount+' '+tr('sum');details.append(amount);}
     card.append(details);result.append(card);
   }
   if(data.stable_count){const progress=document.createElement('span');progress.textContent=data.stable_count+' / '+data.stable_required;result.append(progress);}
   if(data.liveness){const note=document.createElement('small');note.textContent=tr('liveness_'+data.liveness);result.append(note);}
   if(data.pause_ms){const note=document.createElement('small');note.textContent=tr('live_resuming');result.append(note);}
 }
 function controls(){
   start.hidden=Boolean(stream);start.disabled=starting;
   stop.hidden=!stream&&!starting;flip.hidden=!stream;flip.disabled=starting;
   document.getElementById('camera-placeholder').hidden=Boolean(stream);
   document.getElementById('camera-state').textContent=tr(stream?'camera_ready':'camera_off');
   result.setAttribute('aria-busy',busy?'true':'false');
 }
 function stopCamera(){
   generation++;clearTimeout(qualityTimer);drawQuality(null);lastFingerprint=null;lastSubmitted=0;confirming=false;clearTimeout(timer);timer=null;controller?.abort();controller=null;feed=null;
   const old=stream;stream=null;if(old)old.getTracks().forEach(track=>track.stop());
   video.srcObject=null;starting=false;busy=false;controls();
 }
 const headers=()=>({'X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content});
 function schedule(delay=interval){clearTimeout(timer);if(stream&&!document.hidden)timer=setTimeout(capture,delay);}
 async function startCamera(){
   if(starting||stream)return;
   audio.unlock();
   const ticket=++generation;starting=true;controls();message(tr('camera_starting'));
   let candidate=null;
   const requestController=new AbortController();controller=requestController;
   let timeout=null;
   try{
     if(!window.isSecureContext)throw Object.assign(new Error(),{name:'InsecureContext'});
     if(!navigator.mediaDevices?.getUserMedia)throw Object.assign(new Error(),{name:'NotSupportedError'});
     try{
       candidate=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:facing},width:{ideal:1280},height:{ideal:960}},audio:false});
     }catch(error){
       if(ticket!==generation)return;
       if(!['OverconstrainedError','NotFoundError'].includes(error.name))throw error;
       candidate=await navigator.mediaDevices.getUserMedia({video:true,audio:false});
     }
     if(ticket!==generation||document.hidden){candidate.getTracks().forEach(track=>track.stop());return;}
     stream=candidate;video.srcObject=stream;await video.play();
     if(ticket!==generation)return;
     const track=stream.getVideoTracks()[0];facing=track.getSettings?.().facingMode||facing;
     video.classList.toggle('mirrored',facing==='user');
     track.addEventListener('ended',()=>{if(ticket===generation){stopCamera();message(tr('camera_ended'),'error');}},{once:true});
     timeout=setTimeout(()=>requestController.abort(),12000);
     const response=await fetch('/api/live/start',{method:'POST',credentials:'same-origin',headers:headers(),signal:requestController.signal});
     if(ticket!==generation)return;
     if(response.status===401){stopCamera();window.location.assign('/login');return;}
     if(!response.ok)throw Object.assign(new Error(),{name:'LiveStartError'});
     const data=await response.json();if(ticket!==generation)return;
     feed=data.feed_id;interval=Math.max(300,Math.min(5000,data.interval_ms));
     message(tr('camera_ready'));schedule(interval);
   }catch(error){
     candidate?.getTracks().forEach(track=>track.stop());if(ticket!==generation)return;
     stopCamera();
     const key={NotAllowedError:'camera_denied',NotFoundError:'camera_missing',NotReadableError:'camera_busy',InsecureContext:'camera_https',NotSupportedError:'camera_unsupported',AbortError:'scan_timeout',LiveStartError:'server_error'}[error.name]||'camera_error';
     message(tr(key),'error');
   }finally{clearTimeout(timeout);if(ticket===generation){starting=false;controller=null;controls();}}
 }
 async function capture(){
   // A single self-scheduling worker: no interval callback can build a queue.
   if(busy||starting||!stream||!feed||document.hidden)return;
   if(!video.videoWidth||!video.videoHeight){message(tr('searching'));schedule();return;}
   const ticket=generation, began=performance.now();let delay=interval;
   busy=true;controls();
   const requestController=new AbortController();controller=requestController;
   const timeout=setTimeout(()=>requestController.abort(),12000);
   try{
     const scale=Math.min(1,720/Math.max(video.videoWidth,video.videoHeight));
     canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);
     canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
     // Hash a tiny sample: suppress exact duplicate frames without requiring stillness.
     const pixels=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
     let fingerprint=2166136261;
     for(let i=0;i<pixels.length;i+=64)fingerprint=Math.imul(fingerprint^pixels[i],16777619);
     if(fingerprint===lastFingerprint && began-lastSubmitted<(confirming?1500:10000)){delay=interval;return;}
     const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.82));
     if(ticket!==generation)return;if(!blob)throw new Error('capture');
     const form=new FormData();form.append('photo',blob,'frame.jpg');form.append('feed_id',feed);
     message(tr('checking'));
     const response=await fetch('/api/live/frame',{method:'POST',body:form,credentials:'same-origin',headers:headers(),signal:requestController.signal});
     if(ticket!==generation)return;
     if(response.status===401){stopCamera();window.location.assign('/login');return;}
     const data=await response.json();if(ticket!==generation)return;
     if(!response.ok){if(response.status===400||response.status===403||response.status===413)stopCamera();message(data.message||tr('server_error'),'error');delay=3000;return;}
     if(data.fatal){stopCamera();message(data.message,'error');return;}
     lastFingerprint=fingerprint;lastSubmitted=began;confirming=Boolean(data.stable_count);
     if(data.quality){
       clearTimeout(qualityTimer);
       // Ignore geometry if the device rotated while the request was in flight.
       if(Math.abs(data.quality.width/data.quality.height-video.videoWidth/video.videoHeight)<.05)drawQuality(data.quality);
       else drawQuality(null);
       qualityTimer=setTimeout(()=>drawQuality(null),Math.max(1500,interval*2));
     }
     message(data.status==='success'?tr('child_recognized'):data.message,data.status,data);
     audio.result(data);
     if(['face_invalid','image_invalid'].includes(data.status))audio.error();
     delay=data.pause_ms||data.retry_ms||Math.max(0,interval-(performance.now()-began));
   }catch(error){if(ticket===generation){message(tr(error.name==='AbortError'?'scan_timeout':'server_error'),'error');delay=3000;}}
   finally{
     clearTimeout(timeout);
     if(ticket===generation){controller=null;busy=false;controls();schedule(delay);}
   }
 }
 start.addEventListener('click',startCamera);
 stop.addEventListener('click',()=>{stopCamera();message(tr('camera_off'));});
 flip.addEventListener('click',async()=>{if(starting)return;facing=facing==='environment'?'user':'environment';stopCamera();await startCamera();});
 window.addEventListener('pagehide',stopCamera);
 document.addEventListener('visibilitychange',()=>{if(document.hidden){stopCamera();message(tr('camera_off'));}});
 controls();
})();
