(function(){
  const finePointer=window.matchMedia('(hover:hover) and (pointer:fine)');
  const reducedMotion=window.matchMedia('(prefers-reduced-motion:reduce)');
  function revealCards(root){
    const cards=root.querySelectorAll('.card');
    cards.forEach((card,index)=>{
      card.style.setProperty('--glass-enter-index',String(index));
      if(reducedMotion.matches)return;
      card.classList.add('glass-card-enter');
      card.addEventListener('animationend',()=>card.classList.remove('glass-card-enter'),{once:true});
    });
  }
  function observeCatalog(){
    const grid=document.getElementById('tests-grid')||document.querySelector('.grid');
    if(!grid)return;
    revealCards(grid);
    new MutationObserver(mutations=>{
      if(mutations.some(mutation=>mutation.addedNodes.length))revealCards(grid);
    }).observe(grid,{childList:true});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',observeCatalog,{once:true});
  else observeCatalog();
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
