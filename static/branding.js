(function () {
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
  }

  fetch(`/api/branding/${encodeURIComponent(slug)}`)
    .then(res => res.ok ? res.json() : Promise.reject())
    .then(apply)
    .catch(() => {
      if (requestedSlug) localStorage.removeItem('organization_slug');
    });
})();
