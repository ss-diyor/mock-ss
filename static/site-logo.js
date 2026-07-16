(function () {
  const src = '/static/assets/site-logo-sd.png';

  function logoImage(size) {
    const img = document.createElement('img');
    img.src = src;
    img.alt = 'IELTS Mock SS';
    img.width = size;
    img.height = size;
    img.style.cssText = 'width:100%;height:100%;object-fit:contain;display:block;transform:scale(1.65);';
    return img;
  }

  function replaceMark(element) {
    if (!element || element.querySelector('img')) return;
    element.textContent = '';
    element.style.background = 'transparent';
    element.style.padding = '0';
    element.style.overflow = 'hidden';
    element.appendChild(logoImage(46));
  }

  function applySiteLogo() {
    document.querySelectorAll('.nav-logo-icon, .site-logo, .brand > .logo, main.wrap > .logo')
      .forEach(replaceMark);

    const header = document.querySelector('body > header');
    if (header && !header.querySelector('.nav-logo-icon, .site-logo, .brand > .logo, .global-site-logo')) {
      const link = document.createElement('a');
      link.href = '/';
      link.className = 'global-site-logo';
      link.setAttribute('aria-label', 'IELTS Mock SS bosh sahifa');
      link.style.cssText = 'width:42px;height:42px;flex:0 0 42px;display:block;overflow:hidden;';
      link.appendChild(logoImage(42));
      header.prepend(link);
    }

    if (!document.querySelector('.nav-logo-icon, .site-logo, .brand > .logo, main.wrap > .logo, .global-site-logo')) {
      const floating = document.createElement('a');
      floating.href = '/';
      floating.className = 'global-site-logo';
      floating.setAttribute('aria-label', 'IELTS Mock SS bosh sahifa');
      floating.style.cssText = 'position:fixed;left:18px;top:14px;width:42px;height:42px;z-index:1000;display:block;overflow:hidden;';
      floating.appendChild(logoImage(42));
      document.body.appendChild(floating);
    }

    if (!document.querySelector('link[rel="icon"]')) {
      const favicon = document.createElement('link');
      favicon.rel = 'icon';
      favicon.type = 'image/png';
      favicon.href = src;
      document.head.appendChild(favicon);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applySiteLogo, { once: true });
  } else {
    applySiteLogo();
  }
})();
