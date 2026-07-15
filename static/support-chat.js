(function () {
  'use strict';

  if (window.IELTSSupportChat) return;

  const categories = {
    technical: 'Texnik muammo',
    tests: 'Test bilan muammo',
    results: 'Natija va baholash',
    billing: "To'lov va obuna",
    organizations: "Maktab / o'quv markazi",
    other: 'Boshqa'
  };
  const statusLabels = {
    open: 'Yangi', waiting_admin: 'Javob kutilmoqda', waiting_user: 'Sizdan javob kutilmoqda',
    resolved: 'Yakunlangan', closed: 'Yopilgan'
  };
  const state = { open: false, view: 'new', tickets: [], selectedId: null, timer: null };

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[char]);
  }

  function token() { return localStorage.getItem('ielts_token') || ''; }

  function guestKey() {
    let key = localStorage.getItem('ielts_support_key');
    if (!key) {
      key = (window.crypto?.randomUUID?.() || `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`).replace(/-/g, '');
      localStorage.setItem('ielts_support_key', key);
    }
    return key;
  }

  function headers(json = false) {
    const result = { 'X-Support-Key': guestKey() };
    if (token()) result.Authorization = `Bearer ${token()}`;
    if (json) result['Content-Type'] = 'application/json';
    return result;
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: { ...headers(Boolean(options.body)), ...(options.headers || {}) },
      cache: 'no-store'
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Yordam markazi bilan bog'lanib bo'lmadi");
    return data;
  }

  function relativeTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    const minutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
    if (minutes < 1) return 'hozir';
    if (minutes < 60) return `${minutes} daqiqa oldin`;
    if (minutes < 1440) return `${Math.floor(minutes / 60)} soat oldin`;
    return date.toLocaleDateString('uz-UZ');
  }

  function addStyles() {
    if (document.getElementById('support-chat-styles')) return;
    const style = document.createElement('style');
    style.id = 'support-chat-styles';
    style.textContent = `
      .sc-widget{position:fixed;right:24px;bottom:24px;z-index:9900;font-family:Inter,Arial,sans-serif;color:#0b1733}
      .sc-fab{position:relative;width:62px;height:62px;display:grid;place-items:center;border:1px solid rgba(255,255,255,.5);border-radius:50%;background:linear-gradient(145deg,#3475ff,#164bcf);color:#fff;cursor:pointer;box-shadow:0 16px 38px rgba(26,86,232,.34),inset 0 1px 0 rgba(255,255,255,.38);transition:.25s transform,.25s box-shadow}
      .sc-fab:hover{transform:translateY(-4px) scale(1.04);box-shadow:0 22px 46px rgba(26,86,232,.42)}.sc-fab svg{width:27px;height:27px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
      .sc-fab::before{content:'Yordam markazi';position:absolute;right:73px;padding:8px 11px;border:1px solid #c9d8ff;border-radius:8px;background:rgba(255,255,255,.97);color:#0b1733;box-shadow:0 8px 24px rgba(11,23,51,.1);font:700 9px ui-monospace,Consolas,monospace;white-space:nowrap;opacity:0;transform:translateX(8px);pointer-events:none;transition:.2s}.sc-fab:hover::before{opacity:1;transform:none}
      .sc-unread{position:absolute;top:-5px;right:-4px;min-width:20px;height:20px;padding:0 5px;display:none;place-items:center;border:2px solid #fff;border-radius:999px;background:#e53935;color:#fff;font:800 9px ui-monospace,Consolas,monospace}.sc-unread.show{display:grid}
      .sc-backdrop{position:fixed;inset:0;z-index:-1;background:rgba(8,22,55,.25);opacity:0;pointer-events:none;transition:.22s}.sc-widget.open .sc-backdrop{opacity:1;pointer-events:auto}
      .sc-panel{position:absolute;right:0;bottom:78px;width:min(390px,calc(100vw - 28px));height:min(600px,calc(100dvh - 115px));display:flex;flex-direction:column;overflow:hidden;border:1px solid rgba(255,255,255,.8);border-radius:20px;background:linear-gradient(145deg,rgba(255,255,255,.93),rgba(239,245,255,.89));box-shadow:0 28px 80px rgba(11,23,51,.25),inset 0 1px 0 #fff;backdrop-filter:blur(24px) saturate(170%);-webkit-backdrop-filter:blur(24px) saturate(170%);opacity:0;visibility:hidden;filter:blur(7px);transform:translateY(22px) scale(.9);transform-origin:bottom right;pointer-events:none;transition:.25s opacity,.25s visibility,.38s filter,.46s transform cubic-bezier(.16,1,.3,1)}
      .sc-widget.open .sc-panel{opacity:1;visibility:visible;filter:none;transform:none;pointer-events:auto}
      .sc-head{padding:16px 17px 13px;color:#fff;background:linear-gradient(135deg,#194fda,#2b70ff)}.sc-head-row{display:flex;align-items:center;gap:11px}.sc-head-icon{width:35px;height:35px;display:grid;place-items:center;border-radius:10px;background:rgba(255,255,255,.16)}.sc-head-icon svg{width:19px;height:19px;fill:none;stroke:#fff;stroke-width:1.8}.sc-head-copy{flex:1}.sc-head-copy strong{display:block;font-size:14px}.sc-head-copy span{display:block;margin-top:3px;font:600 9px ui-monospace,Consolas,monospace;opacity:.75}.sc-close{width:32px;height:32px;border:0;border-radius:9px;background:rgba(255,255,255,.16);color:#fff;cursor:pointer;font-size:20px}
      .sc-tabs{display:grid;grid-template-columns:1fr 1fr;background:rgba(255,255,255,.78);border-bottom:1px solid #d5e1fa}.sc-tab{padding:12px 6px;border:0;border-bottom:2px solid transparent;background:none;color:#63718f;cursor:pointer;font:750 10px ui-monospace,Consolas,monospace}.sc-tab.active{border-bottom-color:#1a56e8;color:#1a56e8;background:#fff}
      .sc-body{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain;padding:14px}.sc-welcome{padding:13px;border:1px solid #d7e2f8;border-radius:13px;background:rgba(255,255,255,.76);font-size:12px;line-height:1.55;color:#52617f}.sc-welcome strong{display:block;margin-bottom:5px;color:#0b1733}
      .sc-form{display:grid;gap:10px;margin-top:12px}.sc-field label{display:block;margin:0 0 5px;color:#687694;font:750 9px ui-monospace,Consolas,monospace;text-transform:uppercase;letter-spacing:.05em}.sc-field input,.sc-field select,.sc-field textarea,.sc-reply textarea{width:100%;border:1px solid #cbd9f5;border-radius:10px;background:rgba(255,255,255,.8);color:#0b1733;padding:11px 12px;font:500 12px/1.45 Inter,Arial,sans-serif;outline:none}.sc-field textarea{min-height:105px;resize:vertical}.sc-field input:focus,.sc-field select:focus,.sc-field textarea:focus,.sc-reply textarea:focus{border-color:#1a56e8;box-shadow:0 0 0 3px rgba(26,86,232,.1)}
      .sc-primary{width:100%;padding:12px;border:0;border-radius:10px;background:#1a56e8;color:#fff;cursor:pointer;font:750 11px ui-monospace,Consolas,monospace}.sc-primary:disabled{opacity:.55;cursor:default}.sc-error{padding:9px 10px;border-radius:8px;background:#fff0f0;color:#b42318;font-size:11px}.sc-empty{padding:50px 20px;text-align:center;color:#71809d;font-size:12px;line-height:1.6}.sc-empty-icon{width:44px;height:44px;margin:0 auto 12px;display:grid;place-items:center;border:1px solid #d7e2f8;border-radius:13px;background:#fff;color:#1a56e8;font-size:20px}
      .sc-ticket{width:100%;padding:12px;display:grid;grid-template-columns:1fr auto;gap:8px;border:1px solid #d8e3f8;border-radius:12px;background:rgba(255,255,255,.75);text-align:left;color:inherit;cursor:pointer}.sc-ticket+.sc-ticket{margin-top:8px}.sc-ticket:hover{border-color:#9fbbf7;background:#fff}.sc-ticket-title{font-size:12px;font-weight:750}.sc-ticket-message{margin-top:5px;color:#687694;font-size:10px;line-height:1.4;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:245px}.sc-ticket-meta{margin-top:7px;color:#8390a8;font:600 8px ui-monospace,Consolas,monospace}.sc-ticket-badge{height:max-content;padding:4px 6px;border-radius:999px;background:#edf3ff;color:#1a56e8;font:700 8px ui-monospace,Consolas,monospace}.sc-ticket-dot{display:inline-grid;place-items:center;min-width:18px;height:18px;margin-left:4px;border-radius:999px;background:#e53935;color:#fff}
      .sc-conversation{display:flex;flex-direction:column;height:100%}.sc-conversation-head{display:flex;align-items:center;gap:8px;padding-bottom:10px;border-bottom:1px solid #d9e3f7}.sc-back{width:31px;height:31px;border:1px solid #d0ddf5;border-radius:8px;background:#fff;color:#1a56e8;cursor:pointer}.sc-conversation-title{font-size:12px;font-weight:800}.sc-conversation-status{margin-top:3px;color:#70809e;font:600 8px ui-monospace,Consolas,monospace}.sc-messages{flex:1;overflow:auto;padding:12px 2px}.sc-message{max-width:82%;margin-bottom:9px;padding:10px 11px;border-radius:12px 12px 12px 3px;background:#fff;border:1px solid #d9e3f7;font-size:11px;line-height:1.5;white-space:pre-wrap}.sc-message.user{margin-left:auto;border-radius:12px 12px 3px 12px;background:#1a56e8;border-color:#1a56e8;color:#fff}.sc-message-time{display:block;margin-top:5px;font:600 8px ui-monospace,Consolas,monospace;opacity:.62}.sc-reply{display:grid;grid-template-columns:1fr 42px;gap:7px;padding-top:10px;border-top:1px solid #d9e3f7}.sc-reply textarea{height:42px;min-height:42px;max-height:90px;resize:none}.sc-send{border:0;border-radius:10px;background:#1a56e8;color:#fff;cursor:pointer;font-size:18px}.sc-closed{padding:10px;text-align:center;border-radius:9px;background:#eef3ff;color:#63718f;font-size:10px}
      body.support-chat-mounted .band-calculator-widget{right:100px}
      @media(max-width:600px){.sc-widget{right:14px;bottom:max(14px,env(safe-area-inset-bottom))}.sc-fab{width:58px;height:58px}.sc-fab::before{display:none}.sc-panel{position:fixed;inset:auto 0 0 0;width:100%;height:min(76dvh,650px);border-radius:22px 22px 0 0;transform:translateY(30px)}.sc-widget.open .sc-panel{transform:none}.sc-backdrop{z-index:-1}body.support-chat-mounted .band-calculator-widget{right:14px;bottom:max(82px,calc(env(safe-area-inset-bottom) + 82px))}}
      @media(prefers-reduced-motion:reduce){.sc-panel,.sc-backdrop,.sc-fab{transition:none}}
    `;
    document.head.appendChild(style);
  }

  function profileFields() {
    if (token()) return '';
    let profile = {};
    try { profile = JSON.parse(localStorage.getItem('ielts_support_profile') || '{}'); } catch (_) {}
    return `<div class="sc-field"><label>Ismingiz</label><input id="sc-name" maxlength="120" value="${esc(profile.name || '')}" placeholder="Ism-familiyangiz"></div>
      <div class="sc-field"><label>Email</label><input id="sc-email" type="email" maxlength="180" value="${esc(profile.email || '')}" placeholder="email@example.com"></div>`;
  }

  function renderNew() {
    state.view = 'new'; state.selectedId = null;
    setTabs();
    const body = document.querySelector('.sc-body');
    body.innerHTML = `<div class="sc-welcome"><strong>Assalomu alaykum! 👋</strong>Savolingizni yozing. Murojaat saqlanadi va admin javob berganda shu yerda ko'rinadi.</div>
      <form class="sc-form" id="sc-new-form">${profileFields()}
        <div class="sc-field"><label>Mavzu</label><select id="sc-category">${Object.entries(categories).map(([value,label]) => `<option value="${value}">${label}</option>`).join('')}</select></div>
        <div class="sc-field"><label>Xabaringiz</label><textarea id="sc-message" maxlength="3000" required placeholder="Muammoni imkon qadar aniq yozing..."></textarea></div>
        <div id="sc-form-error" class="sc-error" hidden></div><button class="sc-primary" id="sc-submit" type="submit">Murojaat yuborish →</button>
      </form>`;
    document.getElementById('sc-new-form').addEventListener('submit', createTicket);
  }

  function setTabs() {
    document.querySelectorAll('.sc-tab').forEach(tab => tab.classList.toggle('active', tab.dataset.view === state.view));
  }

  async function createTicket(event) {
    event.preventDefault();
    const button = document.getElementById('sc-submit');
    const error = document.getElementById('sc-form-error');
    const payload = {
      category: document.getElementById('sc-category').value,
      message: document.getElementById('sc-message').value,
      contact_name: document.getElementById('sc-name')?.value || null,
      contact_email: document.getElementById('sc-email')?.value || null
    };
    error.hidden = true; button.disabled = true; button.textContent = 'Yuborilmoqda...';
    try {
      const result = await api('/api/support/tickets', { method: 'POST', body: JSON.stringify(payload) });
      if (!token()) localStorage.setItem('ielts_support_profile', JSON.stringify({name: payload.contact_name, email: payload.contact_email}));
      await loadTickets(false);
      await openTicket(result.id);
    } catch (err) {
      error.textContent = err.message; error.hidden = false; button.disabled = false; button.textContent = 'Murojaat yuborish →';
    }
  }

  async function loadTickets(render = true) {
    try {
      state.tickets = await api('/api/support/tickets');
      updateUnread();
      if (render) renderTickets();
    } catch (err) {
      if (render) document.querySelector('.sc-body').innerHTML = `<div class="sc-error">${esc(err.message)}</div>`;
    }
  }

  function updateUnread() {
    const total = state.tickets.reduce((sum, ticket) => sum + Number(ticket.unread_count || 0), 0);
    const badge = document.querySelector('.sc-unread');
    badge.textContent = total > 9 ? '9+' : String(total);
    badge.classList.toggle('show', total > 0);
  }

  function renderTickets() {
    state.view = 'tickets'; state.selectedId = null; setTabs();
    const body = document.querySelector('.sc-body');
    if (!state.tickets.length) {
      body.innerHTML = '<div class="sc-empty"><div class="sc-empty-icon">✓</div>Hozircha murojaatlaringiz yo\'q.<br>“Xabar yuborish” orqali yangi murojaat yarating.</div>';
      return;
    }
    body.innerHTML = state.tickets.map(ticket => `<button class="sc-ticket" type="button" data-ticket-id="${Number(ticket.id)}">
      <span><span class="sc-ticket-title">#${Number(ticket.id)} · ${esc(categories[ticket.category] || categories.other)}</span><span class="sc-ticket-message">${esc(ticket.last_message || '')}</span><span class="sc-ticket-meta">${esc(relativeTime(ticket.last_message_at))}</span></span>
      <span class="sc-ticket-badge">${esc(statusLabels[ticket.status] || ticket.status)}${ticket.unread_count ? `<span class="sc-ticket-dot">${Number(ticket.unread_count)}</span>` : ''}</span>
    </button>`).join('');
    body.querySelectorAll('[data-ticket-id]').forEach(button => button.addEventListener('click', () => openTicket(Number(button.dataset.ticketId))));
  }

  async function openTicket(id, silent = false) {
    state.selectedId = id;
    if (!silent) document.querySelector('.sc-body').innerHTML = '<div class="sc-empty">Xabarlar yuklanmoqda...</div>';
    try {
      const data = await api(`/api/support/tickets/${id}`);
      const ticket = data.ticket;
      state.tickets = state.tickets.map(item => item.id === id ? {...item, unread_count: 0, status: ticket.status} : item);
      updateUnread();
      const canReply = !['closed'].includes(ticket.status);
      document.querySelector('.sc-body').innerHTML = `<div class="sc-conversation">
        <div class="sc-conversation-head"><button class="sc-back" type="button" aria-label="Orqaga">←</button><div><div class="sc-conversation-title">#${Number(id)} · ${esc(categories[ticket.category] || categories.other)}</div><div class="sc-conversation-status">${esc(statusLabels[ticket.status] || ticket.status)}</div></div></div>
        <div class="sc-messages">${data.messages.map(message => `<div class="sc-message ${message.sender_type === 'user' ? 'user' : ''}">${esc(message.body)}<span class="sc-message-time">${esc(relativeTime(message.created_at))}</span></div>`).join('')}</div>
        ${canReply ? '<form class="sc-reply"><textarea id="sc-reply-text" maxlength="3000" placeholder="Xabaringizni yozing..."></textarea><button class="sc-send" type="submit" aria-label="Yuborish">➤</button></form>' : '<div class="sc-closed">Bu murojaat yopilgan.</div>'}
      </div>`;
      document.querySelector('.sc-back').addEventListener('click', () => loadTickets());
      document.querySelector('.sc-reply')?.addEventListener('submit', sendReply);
      const messages = document.querySelector('.sc-messages'); messages.scrollTop = messages.scrollHeight;
    } catch (err) {
      if (!silent) document.querySelector('.sc-body').innerHTML = `<div class="sc-error">${esc(err.message)}</div>`;
    }
  }

  async function sendReply(event) {
    event.preventDefault();
    const input = document.getElementById('sc-reply-text');
    const body = input.value.trim();
    if (!body) return;
    input.disabled = true;
    try {
      await api(`/api/support/tickets/${state.selectedId}/messages`, { method: 'POST', body: JSON.stringify({message: body}) });
      await openTicket(state.selectedId);
    } catch (err) { alert(err.message); input.disabled = false; }
  }

  function setOpen(open) {
    state.open = open;
    document.querySelector('.sc-widget').classList.toggle('open', open);
    document.querySelector('.sc-fab').setAttribute('aria-expanded', String(open));
    if (open) {
      if (typeof window.closeBandCalculator === 'function') window.closeBandCalculator();
      loadTickets(false);
    }
  }

  function mount() {
    if (document.querySelector('.sc-widget')) return;
    addStyles(); document.body.classList.add('support-chat-mounted');
    const widget = document.createElement('div');
    widget.className = 'sc-widget';
    widget.innerHTML = `<div class="sc-backdrop"></div><section class="sc-panel" role="dialog" aria-label="Yordam markazi">
      <header class="sc-head"><div class="sc-head-row"><span class="sc-head-icon"><svg viewBox="0 0 24 24"><path d="M4 13v-2a8 8 0 0 1 16 0v2"/><path d="M4 13a2 2 0 0 1 2-2h1v6H6a2 2 0 0 1-2-2zM20 13a2 2 0 0 0-2-2h-1v6h1a2 2 0 0 0 2-2zM17 17c0 2-2 3-5 3"/></svg></span><span class="sc-head-copy"><strong>Yordam markazi</strong><span>IELTS MOCK SS SUPPORT</span></span><button class="sc-close" type="button" aria-label="Yopish">×</button></div></header>
      <div class="sc-tabs"><button class="sc-tab active" type="button" data-view="new">Xabar yuborish</button><button class="sc-tab" type="button" data-view="tickets">Murojaatlarim</button></div><div class="sc-body"></div>
    </section><button class="sc-fab" type="button" aria-label="Yordam markazini ochish" aria-expanded="false"><svg viewBox="0 0 24 24"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.5 9.4 9.4 0 0 1-4-.9L3 21l1.7-4.4A8.5 8.5 0 1 1 21 11.5z"/><path d="M8 12h.01M12 12h.01M16 12h.01"/></svg><span class="sc-unread"></span></button>`;
    document.body.appendChild(widget);
    widget.querySelector('.sc-fab').addEventListener('click', () => setOpen(!state.open));
    widget.querySelector('.sc-close').addEventListener('click', () => setOpen(false));
    widget.querySelector('.sc-backdrop').addEventListener('click', () => setOpen(false));
    widget.querySelectorAll('.sc-tab').forEach(tab => tab.addEventListener('click', () => tab.dataset.view === 'new' ? renderNew() : loadTickets()));
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && state.open) setOpen(false); });
    renderNew();
    const requestedTicket = Number(new URLSearchParams(window.location.search).get('support'));
    if (requestedTicket > 0) {
      setOpen(true);
      loadTickets(false).then(() => openTicket(requestedTicket));
    } else loadTickets(false);
    let pollTick = 0;
    state.timer = setInterval(() => {
      if (document.hidden) return;
      pollTick += 1;
      const reply = document.getElementById('sc-reply-text');
      if (state.open && state.selectedId && (!reply || (!reply.value && document.activeElement !== reply))) openTicket(state.selectedId, true);
      else if (state.open || pollTick % 4 === 0) loadTickets(false);
    }, 15000);
  }

  window.IELTSSupportChat = { mount, open: () => setOpen(true), close: () => setOpen(false) };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
