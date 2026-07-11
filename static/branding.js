(function () {
  function renderContactFooter(branding) {
    let footer = document.getElementById('white-label-footer');
    if (!footer) {
      footer = document.createElement('footer');
      footer.id = 'white-label-footer';
      footer.style.cssText = 'margin-top:48px;padding:22px 24px;border-top:1px solid var(--border,#c9d8ff);background:var(--surface,#fff);text-align:center;font-family:var(--mono,monospace);font-size:11px;color:var(--muted,#4a5978);line-height:1.8;';
      document.body.appendChild(footer);
    }
    footer.replaceChildren();

    const name = document.createElement('div');
    name.textContent = branding.brand_name;
    name.style.cssText = 'font-weight:700;color:var(--text,#0b1733);font-size:12px;';
    footer.appendChild(name);

    if (branding.contact_email || branding.contact_phone) {
      const contact = document.createElement('div');
      if (branding.contact_email) {
        const email = document.createElement('a');
        email.href = `mailto:${branding.contact_email}`;
        email.textContent = branding.contact_email;
        email.style.cssText = 'color:var(--blue,#1a56e8);text-decoration:none;';
        contact.appendChild(email);
      }
      if (branding.contact_email && branding.contact_phone) contact.appendChild(document.createTextNode(' · '));
      if (branding.contact_phone) {
        const phone = document.createElement('a');
        phone.href = `tel:${branding.contact_phone.replace(/[^+\d]/g, '')}`;
        phone.textContent = branding.contact_phone;
        phone.style.cssText = 'color:var(--blue,#1a56e8);text-decoration:none;';
        contact.appendChild(phone);
      }
      footer.appendChild(contact);
    }

    if (branding.show_powered_by) {
      const powered = document.createElement('div');
      powered.textContent = 'Powered by IELTS Mock SS';
      powered.style.opacity = '.75';
      footer.appendChild(powered);
    }
  }
  window.renderWhiteLabelContactFooter = renderContactFooter;

  const params = new URLSearchParams(window.location.search);
  const requestedSlug = params.get('org');
  if (requestedSlug) localStorage.setItem('organization_slug', requestedSlug);
  const slug = requestedSlug || localStorage.getItem('organization_slug');
  if (!slug) return;

  function apply(branding) {
    if (!branding) return;
    document.documentElement.style.setProperty('--blue', branding.primary_color);
    document.documentElement.style.setProperty('--blue-hover', branding.primary_color);
    document.documentElement.style.setProperty('--text', branding.secondary_color);
    document.title = document.title.replace(/IELTS Mock SS|IELTS Mock/g, branding.brand_name);

    const logoText = document.querySelector('.nav-logo-text span:first-child');
    if (logoText) logoText.textContent = branding.brand_name;
    const logoIcon = document.querySelector('.nav-logo-icon');
    if (logoIcon) {
      if (branding.logo_url) {
        logoIcon.innerHTML = `<img src="${branding.logo_url}" alt="" style="width:100%;height:100%;object-fit:contain;border-radius:inherit;">`;
        logoIcon.style.background = 'transparent';
      } else {
        logoIcon.textContent = branding.brand_name.charAt(0).toUpperCase();
        logoIcon.style.background = branding.primary_color;
      }
    }
    if (branding.favicon_url) {
      let favicon = document.querySelector('link[rel="icon"]');
      if (!favicon) {
        favicon = document.createElement('link');
        favicon.rel = 'icon';
        document.head.appendChild(favicon);
      }
      favicon.href = branding.favicon_url;
    }
    document.querySelectorAll('[data-brand-name]').forEach(el => el.textContent = branding.brand_name);
    document.querySelectorAll('[data-powered-by]').forEach(el => {
      el.style.display = branding.show_powered_by ? '' : 'none';
    });
    renderContactFooter(branding);
  }

  fetch(`/api/branding/${encodeURIComponent(slug)}`)
    .then(res => res.ok ? res.json() : Promise.reject())
    .then(apply)
    .catch(() => {
      if (requestedSlug) localStorage.removeItem('organization_slug');
    });
})();
