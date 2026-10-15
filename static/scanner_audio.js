'use strict';
// Optional audio: recognition never depends on browser audio support or permission.
window.createScannerAudio = function(button) {
 const preference='kindscan.scanner.sound';
 let enabled=true, context=null;
 const recent=new Map(), playing=new Set(), clips=new Map();
 let loading=null;
 function preload(){
   if(loading||!context)return;
   loading=Promise.all(Object.entries({success:'/static/audio/applepay.mp3',warning:'/static/audio/error_CDOxCYm.mp3'}).map(async([kind,url])=>{
     try {
       const response=await fetch(url,{credentials:'same-origin'});
       if(!response.ok)return;
       const buffer=await context.decodeAudioData(await response.arrayBuffer());
       clips.set(kind,buffer);
     } catch (_) {} // Missing/unsupported files retain the synthesized fallback.
   }));
 }
 try {enabled=localStorage.getItem(preference)!=='off';} catch (_) {}
 function render(){button.textContent=tr('sound')+': '+tr(enabled?'sound_on':'sound_off');button.setAttribute('aria-pressed',String(enabled));}
 function unlock(){
   try {
     const Audio=window.AudioContext||window.webkitAudioContext;
     if(!Audio)return;
     if(!context)context=new Audio();
     preload();
     if(!enabled)return;
     // Called directly from a user gesture, including on iOS Safari.
     context.resume().catch(()=>{});
     const source=context.createBufferSource();source.buffer=context.createBuffer(1,1,context.sampleRate);
     source.connect(context.destination);source.start();
   } catch (_) {}
 }
 function silence(){for(const node of playing){try{node.stop();}catch(_){} }playing.clear();}
 function play(kind,key,cooldown){
   const now=Date.now();
   for(const [id,expires] of recent)if(expires<=now)recent.delete(id);
   if(!enabled||!context||context.state!=='running'||document.hidden||recent.has(key))return;
   recent.set(key,now+cooldown);
   try {
     silence();
     const clip=clips.get(kind);
     if(clip){
       const source=context.createBufferSource(), gain=context.createGain();
       source.buffer=clip;gain.gain.value=.35;
       source.connect(gain);gain.connect(context.destination);playing.add(source);
       source.onended=()=>{playing.delete(source);source.disconnect();gain.disconnect();};
       source.start();
       return;
     }
     // Original two-stage chime: a rising fourth with quiet octave partials.
     // Both bells overlap softly; the complete success cue ends after 570 ms.
     // Tuple: frequency, onset, decay duration, peak gain (no external assets).
     const notes={
       success:[[660,0,.28,.045],[1320,0,.19,.009],[880,.18,.36,.040],[1760,.18,.24,.007]],
       info:[[440,0,.18,.030]],
       warning:[[392,0,.12,.032],[349.23,.14,.16,.026]],
       error:[[220,0,.15,.035],[164.81,.12,.20,.030]]
     }[kind];
     const began=context.currentTime+.01;
     for(const [frequency,offset,duration,volume] of notes){
       const oscillator=context.createOscillator(), gain=context.createGain(), at=began+offset;
       oscillator.type='sine';oscillator.frequency.value=frequency;
       gain.gain.setValueAtTime(0,at);gain.gain.linearRampToValueAtTime(volume,at+.012);
       gain.gain.exponentialRampToValueAtTime(.0001,at+duration);gain.gain.setValueAtTime(0,at+duration+.01);
       oscillator.connect(gain);gain.connect(context.destination);playing.add(oscillator);
       oscillator.onended=()=>{playing.delete(oscillator);oscillator.disconnect();gain.disconnect();};
       oscillator.start(at);oscillator.stop(at+duration+.02);
     }
   } catch (_) {silence();}
 }
 function result(data){
   if(data.pause_ms && (data.status==='success'||data.status==='already')){
     const child=data.child_id||data.child_url;
     if(child)play(data.status==='success'?'success':'info',data.status+':'+child,15*60*1000);
   }else if(data.pause_ms && ['not_found','low_confidence'].includes(data.status) && data.saved!==false){
     play('warning','unknown',3000);
   }
 }
 button.addEventListener('click',()=>{
   enabled=!enabled;try{localStorage.setItem(preference,enabled?'on':'off');}catch(_){}
   render();if(enabled)unlock();else silence();
 });
 document.addEventListener('visibilitychange',()=>{if(document.hidden)silence();});
 window.addEventListener('pagehide',silence);
 render();
 return {unlock,result,error:()=>play('error','error',15000)};
};
