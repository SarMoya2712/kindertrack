'use strict';
(() => {
 const dialog=document.getElementById('unknown-confirm');
 let pending=null;
 document.querySelectorAll('form[data-confirm]').forEach(form=>form.addEventListener('submit',event=>{
   event.preventDefault();pending=form;
   document.getElementById('unknown-confirm-text').textContent=form.dataset.confirm;
   dialog.showModal();document.getElementById('unknown-cancel').focus();
 }));
 document.getElementById('unknown-cancel').addEventListener('click',()=>dialog.close());
 dialog.addEventListener('close',()=>{pending=null;});
 document.getElementById('unknown-confirm-delete').addEventListener('click',()=>{
   if(!pending)return;
   const form=pending;pending=null;
   const confirmed=document.createElement('input');confirmed.type='hidden';confirmed.name='confirmed';confirmed.value='yes';form.append(confirmed);
   document.getElementById('unknown-confirm-delete').disabled=true;
   HTMLFormElement.prototype.submit.call(form);
 });
 const boxes=[...document.querySelectorAll('[name="face_ids"]')],all=document.getElementById('unknown-select-all');
 function selection(){if(!all)return;const count=boxes.filter(box=>box.checked).length;all.checked=count===boxes.length;all.indeterminate=count>0&&count<boxes.length;document.getElementById('delete-selected').disabled=!count;}
 all?.addEventListener('change',()=>{boxes.forEach(box=>{box.checked=all.checked;});selection();});
 boxes.forEach(box=>box.addEventListener('change',selection));selection();
 const period=document.getElementById('cleanup-period'),dates=document.getElementById('cleanup-dates');
 function range(){const custom=period.value==='custom';dates.hidden=!custom;dates.querySelectorAll('input').forEach(input=>{input.required=custom;input.disabled=!custom;});}
 period.addEventListener('change',range);range();
})();
