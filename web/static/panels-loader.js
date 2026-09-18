// ── Lazy loader for panels.js (backlog item 12) ──────────────────────────────
// panels.js is ~517 KB and was loaded on every page view, although most
// sessions never open a panel beyond chat. This shim replaces the static
// <script src="/static/panels.js"> tag: the real file is fetched on the first
// switchPanel() call.
//
// panels.js declares its functions at top level in a classic script (not a
// module), so a dynamic import() would not expose them as globals. The loader
// therefore injects a <script> tag and resolves once it has loaded.
//
// The shim must run before any caller: it installs a placeholder switchPanel()
// that loads panels.js and then delegates to the real implementation. When
// panels.js executes, its top-level `async function switchPanel` replaces the
// placeholder on the global object, which is how the placeholder detects that
// the real code is now in place.

(function () {
  'use strict';

  var PANELS_SRC = '/static/panels.js';
  var _panelsPromise = null;

  function _panelsUrl() {
    // Preserve the cache-busting version token the static tag used to carry.
    var version = window.__WEBUI_VERSION__ || '';
    return version ? PANELS_SRC + '?v=' + encodeURIComponent(version) : PANELS_SRC;
  }

  function loadPanels() {
    if (_panelsPromise) return _panelsPromise;
    _panelsPromise = new Promise(function (resolve, reject) {
      var script = document.createElement('script');
      script.src = _panelsUrl();
      script.async = false; // keep execution order deterministic
      script.onload = function () { resolve(); };
      script.onerror = function () {
        _panelsPromise = null; // allow a retry on the next call
        reject(new Error('failed to load panels.js'));
      };
      document.head.appendChild(script);
    });
    return _panelsPromise;
  }

  window.__sidekickLoadPanels = loadPanels;
  window.__sidekickPanelsReady = function () {
    return window.switchPanel !== placeholder;
  };

  var placeholder = function (name, opts) {
    return loadPanels().then(function () {
      var real = window.switchPanel;
      if (typeof real === 'function' && real !== placeholder) {
        return real(name, opts);
      }
      // panels.js loaded but did not define switchPanel: surface it instead of
      // silently doing nothing.
      throw new Error('panels.js loaded but switchPanel is still unavailable');
    });
  };

  // Only install the placeholder when panels.js has not defined the real one.
  if (typeof window.switchPanel !== 'function') {
    window.switchPanel = placeholder;
  }
})();
