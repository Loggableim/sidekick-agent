(function () {
  function activeWorkspaceSlug() {
    try {
      if (typeof getActiveSpaceQuery === 'function') {
        var qs = String(getActiveSpaceQuery() || '');
        var params = new URLSearchParams(qs.replace(/^\?/, ''));
        var slug = (params.get('workspace') || '').trim();
        if (slug) return slug;
      }
    } catch (_) {}
    try {
      var fallback = (localStorage.getItem('sidekick-active-workspace') || '').trim();
      if (fallback) return fallback;
    } catch (_) {}
    return 'default';
  }

  function shouldAttachWorkspaceHeader(urlObj) {
    try {
      var path = String((urlObj && urlObj.pathname) || '');
      return path.startsWith('/api/');
    } catch (_) {
      return false;
    }
  }

  function dashboardSessionToken() {
    try {
      if (typeof window.__HERMES_SESSION_TOKEN__ === 'string' && window.__HERMES_SESSION_TOKEN__) {
        return window.__HERMES_SESSION_TOKEN__;
      }
    } catch (_) {}
    return '';
  }

  function headersWithWorkspace(existing, urlObj, options) {
    var headers = new Headers(existing || {});
    var isDashboardApi = shouldAttachWorkspaceHeader(urlObj);
    var opts = options || {};
    if (opts.defaultJson && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    if (isDashboardApi && !headers.has('X-Hermes-Workspace')) {
      var slug = activeWorkspaceSlug();
      if (slug) headers.set('X-Hermes-Workspace', slug);
    }
    if (isDashboardApi && !headers.has('X-Hermes-Session-Token')) {
      var token = dashboardSessionToken();
      if (token) headers.set('X-Hermes-Session-Token', token);
    }
    return headers;
  }

  function eventSourceUrl(path) {
    var url = new URL(String(path || '').replace(/^\//, ''), document.baseURI || location.href);
    try {
      var token = dashboardSessionToken();
      if (token) url.searchParams.set('token', token);
    } catch (_) {}
    try {
      if (shouldAttachWorkspaceHeader(url) && !url.searchParams.get('workspace')) {
        var slug = activeWorkspaceSlug();
        if (slug) url.searchParams.set('workspace', slug);
      }
    } catch (_) {}
    return url.href;
  }

  window._activeWorkspaceSlug = window._activeWorkspaceSlug || activeWorkspaceSlug;
  window._shouldAttachWorkspaceHeader = window._shouldAttachWorkspaceHeader || shouldAttachWorkspaceHeader;
  window._dashboardSessionToken = window._dashboardSessionToken || dashboardSessionToken;
  window._headersWithWorkspace = window._headersWithWorkspace || headersWithWorkspace;
  window._eventSourceUrl = window._eventSourceUrl || eventSourceUrl;

  if (window.__SIDEKICK_FETCH_AUTH_INSTALLED__) return;
  window.__SIDEKICK_FETCH_AUTH_INSTALLED__ = true;

  var originalFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    try {
      var rawUrl = input instanceof Request ? input.url : String(input);
      var url = new URL(rawUrl, document.baseURI || location.href);
      if (shouldAttachWorkspaceHeader(url)) {
        var nextInit = Object.assign({}, init || {});
        var sourceHeaders = nextInit.headers || (input instanceof Request ? input.headers : undefined);
        nextInit.headers = headersWithWorkspace(sourceHeaders, url, { defaultJson: false });
        if (input instanceof Request) return originalFetch(new Request(input, nextInit));
        return originalFetch(input, nextInit);
      }
    } catch (_) {}
    return originalFetch(input, init);
  };
})();
