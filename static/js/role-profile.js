(function(){
  const esc=value=>String(value??'').replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
  const initials=value=>String(value||'?').trim().split(/\s+/).slice(0,2).map(part=>part.charAt(0)).join('').toUpperCase()||'?';
  const rgb=color=>{const safe=/^#[0-9a-f]{6}$/i.test(color||'')?color:'#1a56e8';return[1,3,5].map(index=>parseInt(safe.slice(index,index+2),16)).join(',')};
  function imagePair(url,name){if(!url)return esc(initials(name));const src=esc(url),alt=esc(name);return `<img class="rp-logo-fill" src="${src}" alt="" aria-hidden="true"><img class="rp-logo-main" src="${src}" alt="${alt} logosi">`}
  function initializeContextCard(root){
    const stage=root.querySelector('.rp-context-stage'),rotator=root.querySelector('.rp-context-rotator');
    if(!stage||!rotator)return;
    let rotation=0,axis='y',pointerId=null,startX=0,startY=0,startRotation=0,currentRotation=0,dragAxis=null,turning=false;
    const timers=[];
    const cancelIntro=()=>{while(timers.length)clearTimeout(timers.pop())};
    const setRotation=(degrees,nextAxis=axis,animate=true)=>{
      rotation=degrees;axis=nextAxis;
      rotator.classList.toggle('axis-x',axis==='x');
      rotator.classList.toggle('dragging',!animate);
      rotator.style.setProperty('--rp-card-rotation-x',axis==='x'?`${degrees}deg`:'0deg');
      rotator.style.setProperty('--rp-card-rotation-y',axis==='y'?`${degrees}deg`:'0deg');
      const normalized=((degrees%360)+360)%360,isBack=normalized>=90&&normalized<270;
      rotator.querySelector('.rp-context-front')?.setAttribute('aria-hidden',String(isBack));
      rotator.querySelector('.rp-context-back')?.setAttribute('aria-hidden',String(!isBack));
    };
    rotator.addEventListener('pointerdown',event=>{
      if(event.target.closest('a,button,input,textarea'))return;
      cancelIntro();pointerId=event.pointerId;startX=event.clientX;startY=event.clientY;startRotation=rotation;currentRotation=startRotation;dragAxis=null;turning=false;
      rotator.setPointerCapture(pointerId);
    });
    rotator.addEventListener('pointermove',event=>{
      if(pointerId!==event.pointerId)return;
      const deltaX=event.clientX-startX,deltaY=event.clientY-startY;
      if(!turning){
        if(Math.max(Math.abs(deltaX),Math.abs(deltaY))<8)return;
        dragAxis=Math.abs(deltaX)>=Math.abs(deltaY)?'y':'x';
        const normalized=((rotation%360)+360)%360,isBack=normalized>=90&&normalized<270;
        startRotation=dragAxis===axis?rotation:(isBack?180:0);turning=true;
        rotator.classList.add('dragging');rotator.classList.toggle('axis-x',dragAxis==='x');
      }
      currentRotation=startRotation+(dragAxis==='y'?deltaX:-deltaY)*.72;
      rotator.style.setProperty('--rp-card-rotation-x',dragAxis==='x'?`${currentRotation}deg`:'0deg');
      rotator.style.setProperty('--rp-card-rotation-y',dragAxis==='y'?`${currentRotation}deg`:'0deg');
      if(event.cancelable)event.preventDefault();
    });
    const finish=event=>{
      if(pointerId!==event.pointerId)return;
      if(rotator.hasPointerCapture(pointerId))rotator.releasePointerCapture(pointerId);
      pointerId=null;rotator.classList.remove('dragging');
      if(!turning)return;
      setRotation(Math.round(currentRotation/180)*180,dragAxis,true);turning=false;
    };
    rotator.addEventListener('pointerup',finish);rotator.addEventListener('pointercancel',finish);
    stage.addEventListener('keydown',event=>{
      if(event.target!==stage||!['Enter',' '].includes(event.key))return;
      event.preventDefault();cancelIntro();setRotation(rotation+180,axis,true);
    });
    setRotation(0,'y',true);
    if(!window.matchMedia('(prefers-reduced-motion: reduce)').matches){
      const later=(delay,callback)=>timers.push(setTimeout(callback,delay));
      later(480,()=>{rotator.classList.add('intro-spin');setRotation(360,'y',true)});
      later(1700,()=>{rotator.classList.remove('intro-spin');timers.length=0});
    }
    root.__rpCardCleanup=cancelIntro;
  }
  function render(target,options={}){
    const root=typeof target==='string'?document.querySelector(target):target;if(!root)return null;
    root.__rpCardCleanup?.();
    const user=options.user||{},context=options.context||{},accent=options.accent||'#1a56e8',stats=options.stats||[];
    const avatar=user.avatar_url?`<img src="${esc(user.avatar_url)}${String(user.avatar_url).includes('?')?'&':'?'}t=${Date.now()}" alt="${esc(user.full_name||'Profil')}">`:esc(initials(user.full_name));
    const verified=user.email_verified===true?'<span class="rp-badge success">✓ Email tasdiqlangan</span>':'<span class="rp-badge">Email tasdiqlanmagan</span>';
    const meta=(context.meta||[]).slice(0,2).map(item=>`<div><span>${esc(item.label)}</span><b>${esc(item.value)}</b></div>`).join('');
    const actions=(context.actions||[]).map((item,index)=>`<a class="${item.primary||index===0?'primary':''}" href="${esc(item.href||'#')}">${esc(item.label||'Ochish')} ${item.external?'↗':'→'}</a>`).join('');
    root.className='rp-shell';root.style.setProperty('--rp-accent',accent);root.style.setProperty('--rp-accent-rgb',rgb(accent));root.style.setProperty('--rp-stat-columns',String(Math.min(4,Math.max(1,stats.length||1))));
    const isLeader=['director','head_teacher'].includes(String(user.role||'').toLowerCase());
    const bioFallback=isLeader?'Professional bio hali yozilmagan.':'Bio hali yozilmagan.';
    const contextName=esc(context.title||'Shaxsiy kabinet'),contextLogo=imagePair(context.logo_url,context.title||context.kicker);
    root.innerHTML=`<section class="rp-hero"><div class="rp-identity"><div class="rp-avatar-wrap"><div class="rp-avatar" id="${esc(options.avatarId||'rp-avatar')}">${avatar}</div>${options.onAvatarEdit?'<button class="rp-avatar-edit" type="button" aria-label="Avatarni o‘zgartirish">✎</button>':''}</div><div class="rp-copy"><div class="rp-badges"><span class="rp-badge">${esc(user.role_label||'Foydalanuvchi')}</span>${verified}</div><h1>${esc(user.full_name||'Profil')}</h1><div class="rp-username">@${esc(user.username||'—')}</div><div class="rp-email">${esc(user.email||'—')}</div><div class="rp-bio">${esc(user.bio||bioFallback)}</div></div></div><div class="rp-context-stage" tabindex="0" role="group" aria-label="Profil kartasi. X yoki Y o‘qida aylantirish uchun ushlab torting"><div class="rp-context-rotator"><aside class="rp-context rp-context-face rp-context-front"><div class="rp-context-head"><div class="rp-logo">${contextLogo}</div><div><div class="rp-context-kicker">${esc(context.kicker||'IELTS Mock SS')}</div><h2 class="rp-context-title">${contextName}</h2></div></div><div class="rp-context-meta">${meta}</div>${actions?`<div class="rp-context-actions">${actions}</div>`:''}<span class="rp-card-hint">↔ ↕ Ushlab aylantiring</span></aside><aside class="rp-context rp-context-face rp-context-back" aria-hidden="true"><div class="rp-context-back-content"><div class="rp-context-watermark">${contextLogo}</div><h3>${contextName}</h3><p>${esc(context.kicker||'IELTS Mock SS')} · IELTS Mock SS</p></div><span class="rp-card-hint">↔ ↕ Orqaga aylantiring</span></aside></div></div></section><div class="rp-stats" id="${esc(options.statsId||'rp-stats')}">${stats.map((item,index)=>`<article class="rp-stat" data-index="${String(index+1).padStart(2,'0')}"><div class="rp-stat-index">${String(index+1).padStart(2,'0')}</div><b>${esc(item.value??'—')}</b><span>${esc(item.label||'Ko‘rsatkich')}</span></article>`).join('')}</div>`;
    if(options.onAvatarEdit)root.querySelector('.rp-avatar-edit')?.addEventListener('click',options.onAvatarEdit);
    initializeContextCard(root);
    return root;
  }
  window.RoleProfileUI={render};
})();
