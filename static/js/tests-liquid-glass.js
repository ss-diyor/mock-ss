(function(){
  const finePointer=window.matchMedia('(hover:hover) and (pointer:fine)');
  document.addEventListener('pointermove',event=>{
    if(!finePointer.matches)return;
    const card=event.target.closest('.card');
    if(!card)return;
    const rect=card.getBoundingClientRect();
    const x=Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width));
    const y=Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height));
    const tiltX=card.classList.contains('soon')?2.2:3.6;
    const tiltY=card.classList.contains('soon')?2.6:4.2;
    card.style.setProperty('--glass-tilt-x',`${((.5-y)*tiltX).toFixed(2)}deg`);
    card.style.setProperty('--glass-tilt-y',`${((x-.5)*tiltY).toFixed(2)}deg`);
  },{passive:true});
  document.addEventListener('pointerout',event=>{
    const card=event.target.closest?.('.card');
    if(!card||card.contains(event.relatedTarget))return;
    card.style.setProperty('--glass-tilt-x','0deg');
    card.style.setProperty('--glass-tilt-y','0deg');
  });
})();
