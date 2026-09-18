// ── Lazy loader for non-critical feature scripts (backlog item 13) ───────────
// browser.js, gmail.js, discord.js, discord-chat.js, agents.js, swarm.js,
// onboarding.js and enhancements.js total ~553 KB and were all loaded on every
// page view, although most sessions never open the matching panel.
//
// This shim replaces their static <script> tags. Each file is fetched the first
// time its panel is opened (or when its feature is requested explicitly).
//
// Like panels-loader.js this injects a <script> tag rather than using
// import(): the files are classic scripts that declare their functions at top
// level, so a module import would not expose them as globals.

(function () {
  'use strict';

  // panel name -> script file. A panel may pull more than one file.
  var FEATURE_SCRIPTS = {
    browser: ['browser.js'],
    gmail: ['gmail.js'],
    mail: ['gmail.js'],
    discord: ['discord.js', 'discord-chat.js'],
    agents: ['agents.js'],
    swarm: ['swarm.js'],
    onboarding: ['onboarding.js'],
  };

  // Files that must be present before the first user interaction because they
  // register their own DOMContentLoaded/global handlers.
  var EAGER = ['enhancements.js'];

  var _loaded = Object.create(null);
  var _promises = Object.create(null);

  function _url(file) {
    var version = window.__WEBUI_VERSION__ || '';
    return version
      ? '/static/' + file + '?v=' + encodeURIComponent(version)
      : '/static/' + file;
  }

  function loadScript(file) {
    if (_loaded[file]) return Promise.resolve();
    if (_promises[file]) return _promises[file];
    _promises[file] = new Promise(function (resolve, reject) {
      var script = document.createElement('script');
      script.src = _url(file);
      script.async = false;
      script.onload = function () {
        _loaded[file] = true;
        resolve();
      };
      script.onerror = function () {
        _promises[file] = null; // allow a retry
        reject(new Error('failed to load ' + file));
      };
      document.head.appendChild(script);
    });
    return _promises[file];
  }

  function loadForPanel(panel) {
    var files = FEATURE_SCRIPTS[panel];
    if (!files) return Promise.resolve();
    return Promise.all(files.map(loadScript));
  }

  window.__sidekickLoadFeatureScripts = loadScript;
  window.__sidekickLoadPanelScripts = loadForPanel;

  // Wrap switchPanel so opening a panel pulls its scripts first. The wrapper is
  // installed lazily on DOMContentLoaded because panels-loader.js (and
  // agents.js, which patches switchPanel too) run after this file.
  //
  // agents.js replaces window.switchPanel with its own wrapper, so a single
  // install would be lost. Re-install on a short interval until the page has
  // settled, and re-wrap whenever the current function is not ours.
  function installWrapper() {
    var current = window.switchPanel;
    if (typeof current !== 'function' || current.__sidekickFeatureWrapper) return;
    var wrapped = function (panel, opts) {
      return loadForPanel(panel).then(function () {
        return current(panel, opts);
      });
    };
    wrapped.__sidekickFeatureWrapper = true;
    window.switchPanel = wrapped;
  }

  var _installAttempts = 0;
  function installUntilStable() {
    installWrapper();
    _installAttempts += 1;
    if (_installAttempts < 20) setTimeout(installUntilStable, 250);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', installUntilStable);
  } else {
    installUntilStable();
  }

  // enhancements.js registers its own DOMContentLoaded handler, so it has to be
  // present before that event fires.
  EAGER.forEach(function (file) { loadScript(file); });
})();
