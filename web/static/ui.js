// Add lazy loading for panels module
let _panelsLoaded = false;
async function loadPanelsModule() {
  if (_panelsLoaded) return;
  try {
    await import('/static/panels.js?v=__WEBUI_VERSION__');
    _panelsLoaded = true;
  } catch (e) {
    console.error('Failed to load panels.js', e);
  }
}

// Modify switchPanel to load panels lazily
const originalSwitchPanel = window.switchPanel;
window.switchPanel = async function(name, options) {
  await loadPanelsModule();
  return originalSwitchPanel.call(this, name, options);
};
