'use strict';
const messages = JSON.parse(document.getElementById('i18n').dataset.catalog);
const tr = key => messages[key] || key;
const period = document.getElementById('period-select');
if (period) period.addEventListener('change', () => {document.getElementById('custom-dates').hidden = period.value !== 'custom';});
const upload = document.getElementById('photo-upload');
if (upload) upload.addEventListener('change', () => {
  const tooLarge = [...upload.files].some(file => file.size > 6 * 1024 * 1024);
  upload.setCustomValidity(upload.files.length > 3 ? tr('photo_count') : tooLarge ? tr('image_invalid') : '');
  document.getElementById('photo-count').textContent = tr('files_selected') + ': ' + upload.files.length;
  const previews = document.getElementById('photo-previews');
  previews.replaceChildren();
  [...upload.files].slice(0,3).forEach(file => {
    const img = document.createElement('img');
    const url = URL.createObjectURL(file);
    img.src = url; img.alt = file.name;
    img.onload = img.onerror = () => URL.revokeObjectURL(url);
    previews.append(img);
  });
});
function drawChart(canvas) {
  const raw = JSON.parse(canvas.dataset.chart);
  const kind = canvas.dataset.kind;
  // Keep one point per calendar day, including zero-attendance days.
  const rows = raw.map(row=>({day:row.day,value:Number(row[kind])}));
  const style = getComputedStyle(canvas);
  const width = Math.max(160,canvas.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)), height = 220, dpr=window.devicePixelRatio||1;
  canvas.width = width*dpr; canvas.height = height*dpr;
  const ctx=canvas.getContext('2d'); ctx.scale(dpr,dpr);
  const left=47,right=12,top=15,bottom=33,w=width-left-right,h=height-top-bottom;
  const maximum=Math.max(...rows.map(r=>r.value),kind==='amount'?1:4);
  const max=Math.ceil(maximum/4)*4;
  const compact=v=>v>=1000000?(v/1000000).toFixed(1)+' '+tr('million'):v>=1000?(v/1000).toFixed(v%1000?1:0)+' '+tr('thousand'):String(v);
  ctx.font='9px Inter, sans-serif'; ctx.fillStyle='#a38c75'; ctx.textAlign='right';
  for(let i=0;i<=4;i++){const y=top+h-i*h/4;ctx.beginPath();ctx.strokeStyle='#eee5da';ctx.moveTo(left,y);ctx.lineTo(width-right,y);ctx.stroke();ctx.fillText(compact(max*i/4),left-10,y+3);}
  const band=w/Math.max(rows.length,1), bar=Math.min(32,band*.5);
  const gradient=ctx.createLinearGradient(0,top,0,top+h);gradient.addColorStop(0,'#e4d3bd');gradient.addColorStop(1,'#a78d78');
  rows.forEach((row,i)=>{const x=left+band*(i+.5), y=top+h-(row.value/max)*h;
    if(kind==='count'){ctx.fillStyle=gradient;ctx.beginPath();ctx.roundRect(x-bar/2,y,bar,Math.max(row.value?2:0,top+h-y),[3,3,0,0]);ctx.fill();}
    if(rows.length<=8||i%Math.ceil(rows.length/7)===0){ctx.fillStyle='#a38c75';ctx.textAlign='center';const date=new Date(row.day+'T12:00:00');ctx.fillText(date.getDate()+' '+messages.month_names[date.getMonth()],x,height-12);}
  });
  if(kind==='amount'&&rows.length){
    const points=rows.map((r,i)=>[left+band*(i+.5),top+h-r.value/max*h]);
    const fill=ctx.createLinearGradient(0,top,0,top+h);fill.addColorStop(0,'#c2a48670');fill.addColorStop(1,'#c2a48600');
    ctx.beginPath();ctx.moveTo(points[0][0],top+h);points.forEach(p=>ctx.lineTo(...p));ctx.lineTo(points.at(-1)[0],top+h);ctx.closePath();ctx.fillStyle=fill;ctx.fill();
    ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));ctx.strokeStyle='#947055';ctx.lineWidth=1.8;ctx.stroke();
    if(rows.length<=31) points.forEach(p=>{ctx.beginPath();ctx.arc(...p,2.5,0,Math.PI*2);ctx.fillStyle='#947055';ctx.fill();});
  }
  canvas.setAttribute('aria-label',tr(kind==='count'?'attendance_chart':'revenue_chart')+': '+raw.map(r=>r.day+' '+r[kind]).join('; '));
}
const charts=document.querySelectorAll('canvas.chart');
if(charts.length){const resize=new ResizeObserver(entries=>entries.forEach(entry=>drawChart(entry.target)));charts.forEach(c=>resize.observe(c));}

// Indexed field names keep each guardian's checkbox attached to its own row.
const guardians = document.getElementById('guardians');
const addGuardian = document.getElementById('add-guardian');
if (guardians && addGuardian) {
  let nextIndex = guardians.children.length;
  const updateLimit = () => { addGuardian.disabled = guardians.children.length >= 10; };
  addGuardian.addEventListener('click', () => {
    if (guardians.children.length >= 10) return;
    const fragment = document.getElementById('guardian-template').content.cloneNode(true);
    fragment.querySelectorAll('[name]').forEach(input => {input.name = input.name.replace('__INDEX__', String(nextIndex));});
    fragment.querySelector('[name="guardian_index"]').value = String(nextIndex++);
    guardians.append(fragment);
    guardians.lastElementChild.querySelector('select').focus();
    updateLimit();
  });
  guardians.addEventListener('click', event => {
    if (event.target.closest('.remove-guardian')) {event.target.closest('.guardian-fields').remove(); updateLimit();}
  });
  guardians.addEventListener('change', event => {
    if (event.target.type === 'checkbox' && event.target.checked) {
      guardians.querySelectorAll('input[type="checkbox"]').forEach(input => {if (input !== event.target) input.checked = false;});
    }
  });
  updateLimit();
}
