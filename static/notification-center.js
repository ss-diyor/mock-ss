(function () {
  'use strict';

  let active = null;
  const icons = {
    info: 'i',
    success: '✓',
    warning: '!',
    task: '→'
  };

  function addStyles() {
    if (document.getElementById('nc-styles')) return;
    const style = document.createElement('style');
    style.id = 'nc-styles';
    style.textContent = `
      .nc-wrap{position:relative;display:inline-flex;align-items:center;flex:0 0 auto}
      .nc-bell{position:relative;width:38px;height:38px;display:grid;place-items:center;border:1px solid var(--border,#c9d8ff);border-radius:9px;background:var(--surface,#fff);color:var(--text,#0b1733);cursor:pointer;transition:.18s transform,.18s background,.18s border-color}
      .nc-bell:hover{transform:translateY(-1px);background:var(--blue-light,#eef3ff);border-color:var(--blue,#1a56e8)}
      .nc-bell svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
      .nc-count{position:absolute;top:-6px;right:-6px;min-width:19px;height:19px;padding:0 5px;display:none;align-items:center;justify-content:center;border:2px solid var(--surface,#fff);border-radius:999px;background:#e53935;color:#fff;font:700 9px ui-monospace,SFMono-Regular,Consolas,monospace}
      .nc-count.show{display:flex}.nc-count.pulse{animation:ncPulse .5s ease}
      @keyframes ncPulse{50%{transform:scale(1.2)}}
      .nc-backdrop{position:fixed;inset:0;z-index:9997;background:rgba(10,28,68,.26);opacity:0;pointer-events:none;transition:opacity .2s}
      .nc-backdrop.open{opacity:1;pointer-events:auto}
      .nc-panel{position:fixed;z-index:9998;top:76px;right:18px;width:min(390px,calc(100vw - 28px));max-height:min(620px,calc(100vh - 96px));display:flex;flex-direction:column;border:1px solid var(--border,#c9d8ff);border-radius:16px;background:rgba(255,255,255,.96);box-shadow:0 24px 70px rgba(10,28,68,.22);backdrop-filter:blur(18px);transform:translateY(-10px) scale(.98);transform-origin:top right;opacity:0;pointer-events:none;transition:.2s transform,.2s opacity;overflow:hidden;color:#0b1733}
      .nc-panel.open{transform:none;opacity:1;pointer-events:auto}
      .nc-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:17px 18px;border-bottom:1px solid #dce6fb;background:linear-gradient(135deg,#f7f9ff,#eef4ff)}
      .nc-title{font:800 15px Inter,Arial,sans-serif}.nc-subtitle{margin-top:3px;color:#657493;font:600 10px ui-monospace,SFMono-Regular,Consolas,monospace}
      .nc-read-all{border:0;background:none;color:var(--blue,#1a56e8);cursor:pointer;font:700 10px ui-monospace,SFMono-Regular,Consolas,monospace;padding:7px}.nc-read-all:disabled{opacity:.45;cursor:default}
      .nc-list{overflow:auto;overscroll-behavior:contain;padding:8px}
      .nc-item{width:100%;display:grid;grid-template-columns:34px 1fr 8px;gap:11px;align-items:start;text-align:left;padding:12px;border:0;border-radius:11px;background:transparent;color:inherit;cursor:pointer;transition:background .15s}
      .nc-item:hover{background:#f1f5ff}.nc-item+.nc-item{margin-top:2px}.nc-item.unread{background:#f6f8ff}.nc-item.unread:hover{background:#edf3ff}
      .nc-icon{width:34px;height:34px;display:grid;place-items:center;border-radius:9px;background:#edf3ff;color:#1a56e8;font:800 13px ui-monospace,SFMono-Regular,Consolas,monospace}
      .nc-item[data-kind="success"] .nc-icon{background:#e8f8ef;color:#159455}.nc-item[data-kind="warning"] .nc-icon{background:#fff4df;color:#c57400}.nc-item[data-kind="task"] .nc-icon{background:#edeaff;color:#6149d8}
      .nc-item-title,.nc-item-message,.nc-item-time{display:block}.nc-item-title{font:750 12px Inter,Arial,sans-serif;line-height:1.35}.nc-item-message{margin-top:4px;color:#596987;font:400 11px/1.5 Inter,Arial,sans-serif}.nc-item-time{margin-top:6px;color:#8491aa;font:600 9px ui-monospace,SFMono-Regular,Consolas,monospace}
      .nc-dot{width:7px;height:7px;margin-top:5px;border-radius:50%;background:transparent}.nc-item.unread .nc-dot{background:#1a56e8;box-shadow:0 0 0 4px #e9f0ff}
      .nc-empty,.nc-loading{padding:45px 24px;text-align:center;color:#657493;font:500 12px/1.6 Inter,Arial,sans-serif}.nc-empty-mark{width:44px;height:44px;margin:0 auto 12px;display:grid;place-items:center;border:1px solid #d5e0f8;border-radius:13px;background:#f4f7ff;color:#1a56e8;font-size:20px}
      .nc-error{margin:10px;padding:10px;border-radius:8px;background:#fff1f1;color:#b42318;font:600 11px/1.4 Inter,Arial,sans-serif}
      @media(max-width:600px){.nc-panel{top:auto;right:0;bottom:0;width:100%;max-height:78vh;border-radius:20px 20px 0 0;transform:translateY(24px)}.nc-panel.open{transform:none}.nc-bell{width:40px;height:40px}}
      @media(prefers-reduced-motion:reduce){.nc-panel,.nc-backdrop,.nc-bell{transition:none}.nc-count.pulse{animation:none}}
    `;
    document.head.appendChild(style);
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>'"]/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    })[char]);
  }

  function relativeTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
    if (seconds < 45) return 'hozir';
    if (seconds < 3600) return `${Math.floor(seconds / 60)} daqiqa oldin`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} soat oldin`;
    if (seconds < 604800) return `${Math.floor(seconds / 86400)} kun oldin`;
    return date.toLocaleDateString('uz-UZ');
  }

  function unmount() {
    if (!active) return;
    clearInterval(active.timer);
    document.removeEventListener('keydown', active.onKeydown);
    active.wrap.remove();
    active.panel.remove();
    active.backdrop.remove();
    active = null;
  }

  function mount(options = {}) {
    unmount();
    addStyles();
    const target = typeof options.target === 'string' ? document.querySelector(options.target) : options.target;
    if (!target) return null;
    const isAdmin = typeof options.getAdminSecret === 'function';
    const baseUrl = isAdmin ? '/api/admin/notifications' : '/api/notifications';
    const wrap = document.createElement('div');
    wrap.className = 'nc-wrap';
    wrap.innerHTML = `<button type="button" class="nc-bell" aria-label="Bildirishnomalar" aria-expanded="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"></path><path d="M10 21h4"></path></svg><span class="nc-count"></span></button>`;
    target.appendChild(wrap);

    const backdrop = document.createElement('div');
    backdrop.className = 'nc-backdrop';
    const panel = document.createElement('aside');
    panel.className = 'nc-panel';
    panel.setAttribute('aria-label', 'Bildirishnomalar markazi');
    panel.innerHTML = `<div class="nc-head"><div><div class="nc-title">Bildirishnomalar</div><div class="nc-subtitle">YANGILIKLAR VA VAZIFALAR</div></div><button type="button" class="nc-read-all">Barchasini o'qish</button></div><div class="nc-list"><div class="nc-loading">Yuklanmoqda...</div></div>`;
    document.body.append(backdrop, panel);

    const bell = wrap.querySelector('.nc-bell');
    const count = wrap.querySelector('.nc-count');
    const list = panel.querySelector('.nc-list');
    const readAll = panel.querySelector('.nc-read-all');
    const state = { items: [], unread: 0, loading: false, open: false };

    function headers() {
      if (isAdmin) return { 'X-Admin-Secret': options.getAdminSecret() || '' };
      const token = typeof options.getToken === 'function' ? options.getToken() : localStorage.getItem('ielts_token');
      return token ? { Authorization: `Bearer ${token}` } : {};
    }

    async function request(url, requestOptions = {}) {
      const response = await fetch(url, { ...requestOptions, headers: { ...headers(), ...(requestOptions.headers || {}) }, cache: 'no-store' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || 'Bildirishnomalar olinmadi');
      return data;
    }

    function updateCount() {
      count.textContent = state.unread > 99 ? '99+' : String(state.unread);
      count.classList.toggle('show', state.unread > 0);
      count.classList.remove('pulse');
      if (state.unread > 0) requestAnimationFrame(() => count.classList.add('pulse'));
      readAll.disabled = state.unread === 0;
    }

    function render() {
      updateCount();
      if (!state.items.length) {
        list.innerHTML = '<div class="nc-empty"><div class="nc-empty-mark">✓</div>Hozircha yangi bildirishnoma yo\'q.</div>';
        return;
      }
      list.innerHTML = state.items.map(item => `
        <button type="button" class="nc-item ${item.is_read ? '' : 'unread'}" data-id="${Number(item.id)}" data-url="${escapeHtml(item.action_url || '')}" data-kind="${escapeHtml(item.kind || 'info')}">
          <span class="nc-icon">${icons[item.kind] || icons.info}</span>
          <span><span class="nc-item-title">${escapeHtml(item.title)}</span><span class="nc-item-message">${escapeHtml(item.message)}</span><span class="nc-item-time">${escapeHtml(relativeTime(item.created_at))}</span></span>
          <span class="nc-dot"></span>
        </button>
      `).join('');
      list.querySelectorAll('.nc-item').forEach(button => button.addEventListener('click', async () => {
        const id = Number(button.dataset.id);
        const item = state.items.find(entry => Number(entry.id) === id);
        if (item && !item.is_read) {
          try {
            await request(`${baseUrl}/${id}/read`, { method: 'POST' });
            item.is_read = true;
            state.unread = Math.max(0, state.unread - 1);
            render();
          } catch (_) {}
        }
        const url = button.dataset.url;
        if (url && url.startsWith('/') && !url.startsWith('//')) window.location.href = url;
      }));
    }

    async function load(silent = false) {
      if (state.loading) return;
      state.loading = true;
      if (!silent && !state.items.length) list.innerHTML = '<div class="nc-loading">Yuklanmoqda...</div>';
      try {
        const data = await request(baseUrl);
        state.items = Array.isArray(data.items) ? data.items : [];
        state.unread = Number(data.unread_count) || 0;
        render();
      } catch (error) {
        if (!silent) list.innerHTML = `<div class="nc-error">${escapeHtml(error.message)}</div>`;
      } finally {
        state.loading = false;
      }
    }

    function setOpen(open) {
      state.open = open;
      panel.classList.toggle('open', open);
      backdrop.classList.toggle('open', open);
      bell.setAttribute('aria-expanded', String(open));
      if (open) load();
    }

    bell.addEventListener('click', () => setOpen(!state.open));
    backdrop.addEventListener('click', () => setOpen(false));
    readAll.addEventListener('click', async () => {
      if (!state.unread) return;
      readAll.disabled = true;
      try {
        await request(`${baseUrl}/read-all`, { method: 'POST' });
        state.items.forEach(item => { item.is_read = true; });
        state.unread = 0;
        render();
      } catch (error) {
        list.insertAdjacentHTML('afterbegin', `<div class="nc-error">${escapeHtml(error.message)}</div>`);
      }
    });
    const onKeydown = event => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('keydown', onKeydown);
    const timer = setInterval(() => { if (!document.hidden) load(true); }, 60000);
    active = { wrap, panel, backdrop, timer, onKeydown, load, close: () => setOpen(false) };
    load(true);
    return active;
  }

  window.NotificationCenter = { mount, unmount, refresh: () => active && active.load(true) };
})();
