(function(){
  const esc=value=>String(value??'').replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
  const initials=value=>String(value||'?').trim().split(/\s+/).slice(0,2).map(part=>part.charAt(0)).join('').toUpperCase()||'?';
  const rgb=color=>{const safe=/^#[0-9a-f]{6}$/i.test(color||'')?color:'#1a56e8';return[1,3,5].map(index=>parseInt(safe.slice(index,index+2),16)).join(',')};
  function imagePair(url,name){if(!url)return esc(initials(name));const src=esc(url),alt=esc(name);return `<img class="rp-logo-fill" src="${src}" alt="" aria-hidden="true"><img class="rp-logo-main" src="${src}" alt="${alt} logosi">`}
  function render(target,options={}){
    const root=typeof target==='string'?document.querySelector(target):target;if(!root)return null;
    const user=options.user||{},context=options.context||{},accent=options.accent||'#1a56e8',stats=options.stats||[];
    const avatar=user.avatar_url?`<img src="${esc(user.avatar_url)}${String(user.avatar_url).includes('?')?'&':'?'}t=${Date.now()}" alt="${esc(user.full_name||'Profil')}">`:esc(initials(user.full_name));
    const verified=user.email_verified===true?'<span class="rp-badge success">✓ Email tasdiqlangan</span>':'<span class="rp-badge">Email tasdiqlanmagan</span>';
    const meta=(context.meta||[]).slice(0,2).map(item=>`<div><span>${esc(item.label)}</span><b>${esc(item.value)}</b></div>`).join('');
    const actions=(context.actions||[]).map((item,index)=>`<a class="${item.primary||index===0?'primary':''}" href="${esc(item.href||'#')}">${esc(item.label||'Ochish')} ${item.external?'↗':'→'}</a>`).join('');
    root.className='rp-shell';root.style.setProperty('--rp-accent',accent);root.style.setProperty('--rp-accent-rgb',rgb(accent));root.style.setProperty('--rp-stat-columns',String(Math.min(4,Math.max(1,stats.length||1))));
    root.innerHTML=`<section class="rp-hero"><div class="rp-identity"><div class="rp-avatar-wrap"><div class="rp-avatar" id="${esc(options.avatarId||'rp-avatar')}">${avatar}</div>${options.onAvatarEdit?'<button class="rp-avatar-edit" type="button" aria-label="Avatarni o‘zgartirish">✎</button>':''}</div><div class="rp-copy"><div class="rp-badges"><span class="rp-badge">${esc(user.role_label||'Foydalanuvchi')}</span>${verified}</div><h1>${esc(user.full_name||'Profil')}</h1><div class="rp-username">@${esc(user.username||'—')}</div><div class="rp-email">${esc(user.email||'—')}</div><div class="rp-bio">${esc(user.bio||'Professional bio hali yozilmagan.')}</div></div></div><aside class="rp-context"><div class="rp-context-head"><div class="rp-logo">${imagePair(context.logo_url,context.title||context.kicker)}</div><div><div class="rp-context-kicker">${esc(context.kicker||'IELTS Mock SS')}</div><h2 class="rp-context-title">${esc(context.title||'Shaxsiy kabinet')}</h2></div></div><div class="rp-context-meta">${meta}</div>${actions?`<div class="rp-context-actions">${actions}</div>`:''}</aside></section><div class="rp-stats" id="${esc(options.statsId||'rp-stats')}">${stats.map((item,index)=>`<article class="rp-stat" data-index="${String(index+1).padStart(2,'0')}"><div class="rp-stat-index">${String(index+1).padStart(2,'0')}</div><b>${esc(item.value??'—')}</b><span>${esc(item.label||'Ko‘rsatkich')}</span></article>`).join('')}</div>`;
    if(options.onAvatarEdit)root.querySelector('.rp-avatar-edit')?.addEventListener('click',options.onAvatarEdit);
    return root;
  }
  window.RoleProfileUI={render};
})();
