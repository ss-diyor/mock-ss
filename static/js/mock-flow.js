(function(){
  const params=new URLSearchParams(location.search),mode=params.get('mode')||sessionStorage.getItem('mock_mode');
  if(mode!=='full')return;
  sessionStorage.setItem('mock_mode','full');
  const path=location.pathname.toLowerCase(),section=path.includes('listening')?'listening':path.includes('reading')?'reading':path.includes('writing')?'writing':path.includes('speaking')?'speaking':null;
  if(!section)return;
  const next={listening:['Reading','/reading-demo?mode=full'],reading:['Writing','/writing-demo?mode=full'],writing:['Speaking','/speaking-demo?mode=full'],speaking:['Umumiy natija','/mock-result']}[section];
  const originalFetch=window.fetch.bind(window);let shown=false;
  window.fetch=async function(input,options){const response=await originalFetch(input,options),url=typeof input==='string'?input:(input&&input.url)||'';if(response.ok&&(url.includes('/api/submit')||url.includes('/api/submit-speaking')))setTimeout(showNext,300);return response};
  if(section==='speaking'){const save=document.getElementById('saveBtn');if(save)save.addEventListener('click',()=>setTimeout(showNext,400))}
  function showNext(){if(shown)return;shown=true;let completed=[];try{completed=JSON.parse(sessionStorage.getItem('mock_completed')||'[]')}catch{}if(!completed.includes(section))completed.push(section);sessionStorage.setItem('mock_completed',JSON.stringify(completed));const box=document.createElement('div');box.innerHTML=`<div style="position:fixed;inset:0;background:rgba(11,23,51,.65);z-index:2147483646;display:grid;place-items:center;padding:20px"><div style="width:min(440px,100%);background:#fff;border:1px solid #c9d8ff;border-radius:14px;padding:26px;font-family:Inter,Arial,sans-serif;color:#0b1733;box-shadow:0 20px 60px rgba(0,0,0,.25)"><div style="font:600 10px monospace;color:#1a56e8;margin-bottom:8px">$ full-mock --continue</div><h2 style="margin:0 0 8px">${section[0].toUpperCase()+section.slice(1)} yakunlandi</h2><p style="color:#4a5978;line-height:1.5">Full mock tartibi bo‘yicha keyingi qadam: <b>${next[0]}</b>.</p><button id="mock-next-section" style="width:100%;padding:12px;border:0;border-radius:8px;background:#1a56e8;color:#fff;font:700 12px monospace;cursor:pointer">${next[0]}ga o‘tish →</button></div></div>`;document.body.appendChild(box);box.querySelector('#mock-next-section').onclick=()=>location.href=next[1]}
})();
