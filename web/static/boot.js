// ── localStorage key migration: hermes-* → sidekick-* ──────────────────────
(function(){
  if(localStorage.getItem('sidekick-migrated-v1')) return;
  var keys=[], i;
  for(i=0;i<localStorage.length;i++){
    var k=localStorage.key(i);
    if(k && k.startsWith('hermes-')) keys.push(k);
  }
  for(i=0;i<keys.length;i++){
    var v=localStorage.getItem(keys[i]);
    if(v!==null) localStorage.setItem(keys[i].replace('hermes-','sidekick-'), v);
  }
  try{localStorage.setItem('sidekick-migrated-v1','1');}catch(_){}
})();

function _setConversationRestorePlaceholder(text){
  const inner = document.getElementById('msgInner');
  if (!inner) return;
  const label = String(text || 'Restoring conversation...');
  inner.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-muted);font-size:14px;padding:40px;text-align:center;">${label}</div>`;
}

async function cancelStream(){
  const streamId = S.activeStreamId;
  if(!streamId) return;
  try{
    await fetch(new URL(`api/chat/cancel?stream_id=${encodeURIComponent(streamId)}`,document.baseURI||location.href).href,{credentials:'include'});
  }catch(e){/* cancel request failed - cleanup below still runs */}
  // Mark the last assistant message as stopped (before S.activeStreamId is cleared)
  _markLastAssistantStopped();
  // Clear status unconditionally after the cancel request completes.
  // The SSE cancel event may also fire, but if the connection is already
  // closed it won't arrive — so we handle cleanup here as the guaranteed path.
  S.activeStreamId=null;
  setBusy(false);
  if(typeof setComposerStatus==='function') setComposerStatus('');
  else setStatus('');
  // Re-render to show the stopped badge immediately
  if(typeof renderMessages==='function') renderMessages({preserveScroll:true});
}
function _markLastAssistantStopped(){
  if(!S.messages||!S.messages.length) return;
  for(let i=S.messages.length-1;i>=0;i--){
    const m=S.messages[i];
    if(m&&m.role==='assistant'&&!m._stopped){
      m._stopped=true;
      break;
    }
  }
}

async function cancelSessionStream(session){
  const streamId = session&&session.active_stream_id;
  const sid = session&&session.session_id;
  if(!streamId||!sid) return;
  try{
    await fetch(new URL(`api/chat/cancel?stream_id=${encodeURIComponent(streamId)}`,document.baseURI||location.href).href,{credentials:'include'});
  }catch(e){/* cancel request failed - cleanup below still runs */}
  session.active_stream_id=null;
  delete INFLIGHT[sid];
  clearInflightState(sid);
  if(S.session&&S.session.session_id===sid){
    S.activeStreamId=null;
    if(S.session) S.session.active_stream_id=null;
    clearInflight();
    setBusy(false);
    if(typeof setComposerStatus==='function') setComposerStatus('');
    else setStatus('');
  }
  if(typeof _approvalSessionId!=='undefined' && _approvalSessionId===sid){
    stopApprovalPolling();
    hideApprovalCard(true);
  }
  if(typeof _clarifySessionId!=='undefined' && _clarifySessionId===sid){
    stopClarifyPolling();
    hideClarifyCard(true, 'cancelled');
  }
  if(typeof renderSessionList==='function') renderSessionList();
}

async function _savedSessionShouldStaySidebarOnly(sid){
  if(!sid) return false;
  try{
    const data = await api(`/api/session?session_id=${encodeURIComponent(sid)}&messages=0&resolve_model=0`);
    const session = data&&data.session;
    return !!(session&&(session.active_stream_id||session.pending_user_message));
  }catch(e){
    return false;
  }
}

// ── Mobile navigation ──────────────────────────────────────────────────────
let _workspacePanelMode='closed'; // 'closed' | 'browse' | 'preview'

function _isCompactWorkspaceViewport(){
  return window.matchMedia('(max-width: 900px)').matches;
}

function _syncWorkspacePanelInlineWidth(){
  const {panel}= _workspacePanelEls();
  if(!panel) return;

  const isCompact = _isCompactWorkspaceViewport();
  if(isCompact){
    if(panel.style.width) panel.style.removeProperty('width');
    return;
  }

  const saved = localStorage.getItem('sidekick-panel-w');
  if(!saved) return;
  const parsed = parseInt(saved, 10);
  if(Number.isNaN(parsed) || parsed <= 0) return;
  panel.style.width = `${parsed}px`;
}

function _workspacePanelEls(){
  return {
    layout: document.querySelector('.layout'),
    panel: document.querySelector('.rightpanel'),
    toggleBtn: $('btnWorkspacePanelToggle'),
    collapseBtn: $('btnCollapseWorkspacePanel'),
  };
}

function _hasWorkspacePreviewVisible(){
  const preview=$('previewArea');
  return !!(preview&&preview.classList.contains('visible'));
}

function _setWorkspacePanelMode(mode){
  const {layout,panel}= _workspacePanelEls();
  if(!layout||!panel)return;
  _workspacePanelMode=(mode==='browse'||mode==='preview')?mode:'closed';
  const open=_workspacePanelMode!=='closed';
  document.documentElement.dataset.workspacePanel=open?'open':'closed';
  // Persist open/closed across refreshes (browse/preview → open; closed → closed)
  // Do NOT overwrite the user's "keep open" preference — only track runtime state
  // so that toggleWorkspacePanel(false) from the toolbar doesn't clear the setting.
  localStorage.setItem('sidekick-webui-workspace-panel', open ? 'open' : 'closed');
  layout.classList.toggle('workspace-panel-collapsed',!open);
  if(_isCompactWorkspaceViewport()){
    if(open&&typeof closeMobileSidebar==='function') closeMobileSidebar();
    panel.classList.toggle('mobile-open',open);
  }else{
    panel.classList.remove('mobile-open');
  }
  syncWorkspacePanelUI();
}

function syncWorkspacePanelState(){
  const hasPreview=_hasWorkspacePreviewVisible();
  if(hasPreview){
    if(_workspacePanelMode==='closed') _setWorkspacePanelMode('preview');
    else syncWorkspacePanelUI();
    return;
  }
  if(!S.session){
    // No active session — if the panel was explicitly opened (browse mode), keep it
    // open so the workspace pane doesn't vanish on a fresh-page or empty-session boot.
    // The file tree will show the "no workspace" placeholder naturally via renderFileTree().
    // Only force-close if the mode is 'preview' (file preview without a session is invalid).
    if(_workspacePanelMode==='preview') _setWorkspacePanelMode('closed');
    else if(_workspacePanelMode==='browse') _setWorkspacePanelMode('browse');
    else syncWorkspacePanelUI();
    return;
  }
  _setWorkspacePanelMode(_workspacePanelMode==='preview'?'closed':_workspacePanelMode);
}

function openWorkspacePanel(mode='browse', opts={}){
  if(mode==='browse'&&!opts.force&&!S.session&&!_hasWorkspacePreviewVisible()&&!S._profileDefaultWorkspace)return;
  if(mode==='preview'&&_workspacePanelMode==='browse'){
    syncWorkspacePanelUI();
    return;
  }
  _setWorkspacePanelMode(mode);
}

function closeWorkspacePanel(){
  _setWorkspacePanelMode('closed');
}

function ensureWorkspacePreviewVisible(){
  if(_workspacePanelMode==='closed') _setWorkspacePanelMode('preview');
  else syncWorkspacePanelUI();
}

function handleWorkspaceClose(){
  if(_hasWorkspacePreviewVisible()){
    clearPreview();
    return;
  }
  closeWorkspacePanel();
}

// Panels that legitimately use the right-side pane. This is broader than the
// file-tree alone because Kanban/Gmail/Memory render panel-specific tools there.
const _WORKSPACE_PANEL_RELEVANT = new Set(['chat','memory','gmail','browser']);
function isWorkspacePanelRelevantForPanel(panel){
  return _WORKSPACE_PANEL_RELEVANT.has(panel || 'chat');
}
function syncWorkspacePanelForActivePanel(panel){
  const relevant = isWorkspacePanelRelevantForPanel(panel || (typeof _currentPanel !== 'undefined' ? _currentPanel : 'chat'));
  document.documentElement.dataset.workspacePanelRelevant = relevant ? '1' : '0';
  const {panel:rightPanel}= _workspacePanelEls();
  if(!relevant && rightPanel) rightPanel.classList.remove('mobile-open');
  syncWorkspacePanelUI();
}
window.isWorkspacePanelRelevantForPanel=isWorkspacePanelRelevantForPanel;
window.syncWorkspacePanelForActivePanel=syncWorkspacePanelForActivePanel;

/**
 * Apply file tree panel open/closed state from localStorage preference.
 * Opens the file tree panel if the user prefers it open, minimizes if closed.
 */
function _applyFileTreePanelPref(){
  const pref = localStorage.getItem('sidekick-webui-workspace-panel-pref') !== 'closed'
    || localStorage.getItem('sidekick-webui-workspace-panel') === 'open';
  const panel = $('chatFileTreePanel');
  if(!panel){
    if(pref&&_workspacePanelMode==='closed') _workspacePanelMode='browse';
    else if(!pref&&_workspacePanelMode==='browse') _workspacePanelMode='closed';
    return;
  }
  if(pref && panel.classList.contains('file-tree-panel--minimized')){
    if(typeof window.toggleFileTreePanel === 'function') window.toggleFileTreePanel();
  }else if(!pref && !panel.classList.contains('file-tree-panel--minimized')){
    const root = document.documentElement;
    const curW = parseInt(root.style.getPropertyValue('--file-tree-width')) || panel.getBoundingClientRect().width || 260;
    if(curW > 0) localStorage.setItem('sidekick-file-tree-w', curW);
    root.style.setProperty('--file-tree-width', '0px');
    panel.classList.add('file-tree-panel--minimized');
  }
}

/**
 * Set a tooltip on a button, preferring the custom CSS tooltip (`data-tooltip`)
 * when the element opts in via the `has-tooltip` class. Falls back to the
 * native `title` attribute for elements that haven't opted in.
 *
 * Critical: when the element DOES have data-tooltip, this MUST also clear any
 * existing native `title` attribute, otherwise the slow ~1.5s native browser
 * tooltip co-fires alongside the fast custom CSS tooltip — exactly the bug
 * #1775 reports. Always pair `data-tooltip` with `removeAttribute('title')`.
 */
function _setButtonTooltip(btn, text){
  if(!btn) return;
  if(btn.hasAttribute('data-tooltip')){
    btn.setAttribute('data-tooltip', text);
    if(btn.hasAttribute('title')) btn.removeAttribute('title');
  } else {
    btn.title = text;
  }
}

function syncWorkspacePanelUI(){
  const {layout,panel,toggleBtn,collapseBtn}= _workspacePanelEls();
  // Check file tree panel state in chat layout
  const fileTreePanel = $('chatFileTreePanel');
  const fileTreeMinimized = fileTreePanel ? fileTreePanel.classList.contains('file-tree-panel--minimized') : true;
  const isOpen = fileTreePanel ? !fileTreeMinimized : _workspacePanelMode!=='closed';
  const relevant = isWorkspacePanelRelevantForPanel(typeof _currentPanel !== 'undefined' ? _currentPanel : 'chat');
  const canBrowse=relevant&&(!!S.session||_hasWorkspacePreviewVisible()||!!(S._profileDefaultWorkspace));
  const hasPreview=_hasWorkspacePreviewVisible();
  if(toggleBtn){
    toggleBtn.classList.toggle('active',isOpen);
    toggleBtn.setAttribute('aria-pressed',isOpen?'true':'false');
    _setButtonTooltip(toggleBtn, isOpen?'Hide file tree panel':'Show file tree panel');
    toggleBtn.disabled=!isOpen&&!canBrowse;
  }
  if(collapseBtn){
    _setButtonTooltip(collapseBtn, 'Minimize file tree panel');
  }
  const hasSession=!!S.session;
  ['btnUpDir','btnNewFile','btnNewFolder','btnRefreshPanel'].forEach(id=>{
    const el=$(id);
    if(el)el.disabled=!hasSession;
  });
  if(!hasSession&&!hasPreview){
    const emptyEl=$('wsEmptyState');
    const fileTree=$('fileTree');
    if(emptyEl){
      emptyEl.textContent=typeof t==='function'?t('workspace_empty_no_path'):'No workspace selected.';
      emptyEl.style.display='flex';
    }
    if(fileTree){
      fileTree.innerHTML='';
      fileTree.style.display='none';
    }
  }
  const clearBtn=$('btnClearPreview');
  if(clearBtn){
    clearBtn.disabled=!isOpen;
    _setButtonTooltip(clearBtn, hasPreview?'Close preview':'Hide workspace panel');
    // On desktop, only show the X button when a file preview is open.
    // In browse mode the chevron (btnCollapseWorkspacePanel) already serves
    // as the close control, so showing both produces a duplicate X.
    if(!_isCompactWorkspaceViewport()) clearBtn.style.display=hasPreview?'':'none';
  }
}

function toggleMobileSidebar(){
  const sidebar=document.querySelector('.sidebar');
  const overlay=$('mobileOverlay');
  if(!sidebar)return;
  const isOpen=sidebar.classList.contains('mobile-open');
  if(isOpen){closeMobileSidebar();}
  else{
    if(_isCompactWorkspaceViewport()&&typeof _setWorkspacePanelMode==='function') _setWorkspacePanelMode('closed');
    sidebar.classList.add('mobile-open');
    _syncMobileSidebarInlineOffset(sidebar,true);
    if(overlay)overlay.classList.add('visible');
  }
}
function closeMobileSidebar(){
  const sidebar=document.querySelector('.sidebar');
  const overlay=$('mobileOverlay');
  if(sidebar){
    sidebar.classList.remove('mobile-open');
    _syncMobileSidebarInlineOffset(sidebar,false);
  }
  if(overlay)overlay.classList.remove('visible');
}

function _syncMobileSidebarInlineOffset(sidebar,open){
  if(!sidebar)return;
  if(_isDesktopWidth()){
    sidebar.style.removeProperty('left');
    sidebar.style.removeProperty('transform');
    return;
  }
  sidebar.style.setProperty('left','-300px','important');
  sidebar.style.setProperty('transform',open?'translate3d(300px,0,0)':'none','important');
}

const windowControls={
  supported:false,
  _host(){
    return window.hermesWindowControls || window.HermesWindowControls || null;
  },
  async _call(action){
    const host=this._host();
    if(host&&typeof host[action]==='function'){
      host[action]();
      return true;
    }
    try{
      if(typeof api==='function'){
        const result=await api('/api/window/control',{method:'POST',body:JSON.stringify({action})});
        return !!(result&&result.ok);
      }
    }catch(_){}
    return false;
  },
  minimize(){
    this._call('minimize').then(ok=>{if(!ok)_windowControlUnavailable('minimize');});
  },
  maximize(){
    this._call('maximize').then(ok=>{
      if(ok)return;
      try{
        const key='sidekick-window-restore-rect';
        const stored=sessionStorage.getItem(key);
        if(stored){
          const rect=JSON.parse(stored);
          if(rect&&Number.isFinite(rect.w)&&Number.isFinite(rect.h)){
            window.moveTo(rect.x||0,rect.y||0);
            window.resizeTo(rect.w,rect.h);
            sessionStorage.removeItem(key);
            return;
          }
        }
        if(screen&&typeof window.moveTo==='function'&&typeof window.resizeTo==='function'){
          sessionStorage.setItem(key,JSON.stringify({x:window.screenX,y:window.screenY,w:window.outerWidth,h:window.outerHeight}));
          window.moveTo(screen.availLeft||0,screen.availTop||0);
          window.resizeTo(screen.availWidth||screen.width,screen.availHeight||screen.height);
          return;
        }
      }catch(_){}
      _windowControlUnavailable('maximize');
    });
  },
  close(){
    this._call('close').then(ok=>{if(!ok)window.close();});
  }
};
window.windowControls=windowControls;

function _windowControlUnavailable(action){
  if(typeof showToast==='function')showToast(`Window ${action} requires a native host bridge.`, 'error');
}

function _setWindowControlButtonState(id,enabled,reason){
  const btn=$(id);
  if(!btn)return;
  btn.disabled=!enabled;
  btn.hidden=false;
  if(reason)btn.title=reason;
}

function _syncWindowControlsGeometry(){
  try{
    const overlay=navigator.windowControlsOverlay;
    const rect=overlay&&overlay.getTitlebarAreaRect?overlay.getTitlebarAreaRect():null;
    if(rect&&Number.isFinite(rect.width)){
      document.documentElement.style.setProperty('--titlebar-area-width',rect.width+'px');
    }
  }catch(_){}
}

function initWindowControls(){
  const controls=$('windowControls');
  if(!controls)return;
  const overlay=!!(navigator.windowControlsOverlay && navigator.windowControlsOverlay.visible);
  const host=windowControls._host();
  const appMode=(()=>{try{return new URLSearchParams(location.search).get('hermes_app')==='1'||window.matchMedia('(display-mode: standalone)').matches||window.matchMedia('(display-mode: window-controls-overlay)').matches;}catch(_){return false;}})();
  const hasHost=!!host;
  windowControls.supported=overlay||hasHost||appMode;
  document.documentElement.classList.toggle('window-controls-overlay',overlay);
  document.documentElement.classList.toggle('has-custom-window-controls',windowControls.supported);
  document.documentElement.classList.toggle('has-native-window-bridge',hasHost);
  document.documentElement.classList.toggle('hermes-app-window',appMode);
  if(!windowControls.supported){
    controls.hidden=true;
    return;
  }
  controls.hidden=false;
  _setWindowControlButtonState('windowMinimizeBtn',true,'Minimize');
  _setWindowControlButtonState('windowMaximizeBtn',true,'Maximize/restore');
  _setWindowControlButtonState('windowCloseBtn',true,'Close');
  _syncWindowControlsGeometry();
  try{
    if(overlay&&navigator.windowControlsOverlay.addEventListener){
      navigator.windowControlsOverlay.addEventListener('geometrychange',_syncWindowControlsGeometry);
    }
  }catch(_){}
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initWindowControls,{once:true});
else initWindowControls();

// ── Desktop sidebar collapse toggle ────────────────────────────────────────
// Two discoverability paths into the same state:
//   (1) Click the already-active rail icon → collapse / expand the sidebar.
//   (2) Cmd/Ctrl+B keyboard shortcut (VS Code convention).
// Mobile is unaffected: the sidebar is an overlay there, and every collapse
// code path is gated on `_isDesktopWidth()` (min-width:641px).
// State is persisted via localStorage and survives reloads + bfcache.
const _RAIL_EXPANDED_KEY='sidekick-webui-rail-expanded';

function _isDesktopWidth(){
  try{return window.matchMedia('(min-width:641px)').matches;}catch(_){return true;}
}

function _isSidebarCollapsed(){
  return !document.querySelector('.layout')?.classList.contains('rail-expanded');
}

function _syncSidebarAria(){
  // Mirror the open/collapsed state on the active rail button via aria-expanded
  // so screen readers announce the toggle. Open=true, collapsed=false.
  const active=document.querySelector('.rail .rail-btn.nav-tab.active[data-panel]');
  if(active)active.setAttribute('aria-expanded',!_isSidebarCollapsed());
}

function _ensureRailButtonLabels(){
  const railButtons=document.querySelectorAll('.rail .rail-btn');
  railButtons.forEach(btn=>{
    if(btn.querySelector('.rail-btn-label')) return;
    const labelText=(btn.getAttribute('aria-label')||btn.getAttribute('data-tooltip')||'').trim();
    if(!labelText) return;
    const label=document.createElement('span');
    label.className='rail-btn-label';
    label.textContent=labelText;
    btn.appendChild(label);
  });
}

function toggleSidebar(forceState){
  if(!_isDesktopWidth())return; // mobile uses an overlay; never collapse there
  const layout=document.querySelector('.layout');
  if(!layout)return;
  const nextExpanded=typeof forceState==='boolean'?!forceState:!layout.classList.contains('rail-expanded');
  layout.classList.toggle('rail-expanded',nextExpanded);
  // Clear pre-paint markers once JS owns the state.
  try{document.documentElement.removeAttribute('data-sidebar-collapsed');}catch(_){}
  try{document.documentElement.removeAttribute('data-rail-expanded');}catch(_){}
  try{localStorage.setItem(_RAIL_EXPANDED_KEY,nextExpanded?'1':'0');}catch(_){}
  _syncSidebarAria();
  _ensureRailButtonLabels();
}

function expandSidebar(){
  if(_isSidebarCollapsed())toggleSidebar(false);
}

// Boot-time restore. The inline script in index.html sets data-rail-expanded
// before CSS paints; this promotes that pre-paint state into .layout.
(function _restoreSidebarState(){
  try{document.documentElement.removeAttribute('data-sidebar-collapsed');}catch(_){}
  try{document.documentElement.removeAttribute('data-rail-expanded');}catch(_){}
  if(!_isDesktopWidth())return;
  try{
    if(localStorage.getItem(_RAIL_EXPANDED_KEY)==='1'){
      const layout=document.querySelector('.layout');
      if(layout)layout.classList.add('rail-expanded');
    }
  }catch(_){}
  _syncSidebarAria();
  _ensureRailButtonLabels();
})();
function toggleMobileFiles(){
  toggleWorkspacePanel();
}
function toggleWorkspacePanel(force){
  const {panel}= _workspacePanelEls();
  if(!panel)return;
  const currentlyOpen=_workspacePanelMode!=='closed';
  const nextOpen=typeof force==='boolean'?force:!currentlyOpen;
  if(!nextOpen){
    closeWorkspacePanel();
    return;
  }
  const nextMode=_hasWorkspacePreviewVisible()?'preview':'browse';
  openWorkspacePanel(nextMode,{force:true});
}
window.toggleFileTreePanel=function(force){return toggleWorkspacePanel(force);};
function mobileSwitchPanel(name){
  switchPanel(name);
  if(name==='chat'){
    closeMobileSidebar();
  } else {
    const sidebar=document.querySelector('.sidebar');
    const overlay=$('mobileOverlay');
    if(sidebar){
      sidebar.classList.add('mobile-open');
      if(overlay)overlay.classList.add('visible');
    }
  }
}

$('btnSend').onclick=()=>{
  if(typeof handleComposerPrimaryAction==='function') return handleComposerPrimaryAction();
  if(window._micActive){
    window._micPendingSend=true;
    _stopMic();
    return;
  }
  // Turn-based voice mode: let the voice mode system handle the send flow
  if(typeof window._voiceModeActive==='function'&&window._voiceModeActive()){
    // Immediately send whatever is in the textarea
    if(typeof window._voiceModeImmediateSend==='function') window._voiceModeImmediateSend();
    return;
  }
  send();
};
$('btnAttach').onclick=e=>{if(e&&e.preventDefault)e.preventDefault();$('fileInput').value='';$('fileInput').click();};

function actionChipClick(cmd){
  const ta=$('msg');
  if(!ta)return;
  const command=String(cmd||'').trim();
  if(!command)return;
  const current=ta.value||'';
  ta.value=current.trim()?`${current.replace(/\s*$/,'')}\n${command} `:`${command} `;
  ta.focus();
  const len=ta.value.length;
  try{ta.setSelectionRange(len,len);}catch(_){}
  if(typeof autoResize==='function')autoResize();
  if(typeof updateSendBtn==='function')updateSendBtn();
}
window.actionChipClick=actionChipClick;

// ── Voice input (Web Speech API + MediaRecorder fallback) ───────────────────
(function(){
  const SpeechRecognition=window.SpeechRecognition||window.webkitSpeechRecognition;
  const _canRecordAudio=!!(navigator.mediaDevices&&navigator.mediaDevices.getUserMedia&&window.MediaRecorder);
  if(!SpeechRecognition&&!_canRecordAudio) return; // Browser unsupported — mic button stays hidden

  // Persist SR failure across reloads (e.g. Tailscale/network error)
  const _micForceMediaRecorderKey='mic_force_mediarecorder';
  let _forceMediaRecorder=!SpeechRecognition||localStorage.getItem(_micForceMediaRecorderKey)==='1';

  const btn=$('btnMic');
  const status=$('micStatus');
  const ta=$('msg');
  const statusText=status?status.querySelector('.status-text'):null;
  btn.style.display=''; // Show button — browser supports speech recognition or recording fallback

  let recognition=(!_forceMediaRecorder&&SpeechRecognition)?new SpeechRecognition():null;
  let mediaRecorder=null;
  let mediaStream=null;
  let audioChunks=[];
  let _finalText='';
  let _prefix='';
  let _isRecording=false;

  function _setRecording(on){
    window._micActive=on;
    btn.classList.toggle('recording',on);
    // Active-state title flips so the tooltip is honest about what
    // pressing the button will do (#1488).
    _setButtonTooltip(btn, on ? t('voice_dictate_active') : t('voice_dictate'));
    status.style.display=on?'':'none';
    if(statusText) statusText.textContent=on?'Listening':'Listening';
    if(!on){ _finalText=''; _prefix=''; }
  }

  function _commitTranscript(text){
    const clean=(text||'').trim();
    const committed=clean
      ? (_prefix&&!_prefix.endsWith(' ')&&!_prefix.endsWith('\n')
          ? _prefix+' '+clean.trimStart()
          : _prefix+clean)
      : ta.value;
    ta.value=committed;
    autoResize();
    if(window._micPendingSend){
      window._micPendingSend=false;
      send();
    }
  }

  async function _transcribeBlob(blob){
    const ext=(blob.type&&blob.type.includes('ogg'))?'ogg':'webm';
    const form=new FormData();
    form.append('file',new File([blob],`voice-input.${ext}`,{type:blob.type||`audio/${ext}`}));
    setComposerStatus('Transcribing…');
    try{
      const res=await fetch('api/transcribe',{method:'POST',body:form});
      const data=await res.json().catch(()=>({}));
      if(!res.ok) throw new Error(data.error||'Transcription failed');
      _commitTranscript(data.transcript||'');
    }catch(err){
      window._micPendingSend=false;
      showToast(err.message||t('mic_network'));
    }finally{
      setComposerStatus('');
    }
  }

  function _stopTracks(){
    if(mediaStream){
      mediaStream.getTracks().forEach(track=>track.stop());
      mediaStream=null;
    }
  }

  function _stopMic(){
    if(!window._micActive) return;
    if(recognition){
      recognition.stop();
      return;
    }
    if(mediaRecorder&&mediaRecorder.state!=='inactive'){
      mediaRecorder.stop();
      return;
    }
    _setRecording(false);
    _stopTracks();
  }
  window._stopMic=_stopMic; // expose for send-guard above

  if(recognition && !_forceMediaRecorder){
    recognition.continuous=false;
    recognition.interimResults=true;
    recognition.lang=(typeof _locale!=='undefined'&&_locale._speech)||'en-US';

    recognition.onstart=()=>{ _finalText=''; };

    recognition.onresult=(event)=>{
      let interim='';
      let final=_finalText;
      for(let i=event.resultIndex;i<event.results.length;i++){
        const t=event.results[i][0].transcript;
        if(event.results[i].isFinal){ final+=t; _finalText=final; }
        else{ interim+=t; }
      }
      ta.value=_prefix+(final||interim);
      autoResize();
    };

    recognition.onend=()=>{
      const committed=_finalText
        ? (_prefix&&!_prefix.endsWith(' ')&&!_prefix.endsWith('\n')
            ? _prefix+' '+_finalText.trimStart()
            : _prefix+_finalText)
        : ta.value;
      _setRecording(false);
      ta.value=committed;
      autoResize();
      if(window._micPendingSend){
        window._micPendingSend=false;
        send();
      }
    };

    recognition.onerror=(event)=>{
      _setRecording(false);
      window._micPendingSend=false;
      _isRecording=false;
      if(event.error==='network'||event.error==='not-allowed'){
        // Persist SR failure: next reload will skip SpeechRecognition
        localStorage.setItem(_micForceMediaRecorderKey,'1');
        _forceMediaRecorder=true;
        recognition=null;
      }
      const msgs={
        'not-allowed':t('mic_denied'),
        'no-speech':t('mic_no_speech'),
        'network':t('mic_network'),
      };
      showToast(msgs[event.error]||t('mic_error')+event.error);
    };
  }

  btn.onclick=async()=>{
    // Race-condition guard: ignore rapid double-clicks
    if(_isRecording){
      _stopMic();
      _isRecording=false;
      return;
    }
    if(window._micActive){
      _stopMic();
      return;
    }
    _isRecording=true;
    _finalText='';
    _prefix=ta.value;
    if(recognition && !_forceMediaRecorder){
      recognition.start();
      _setRecording(true);
      return;
    }
    if(!_canRecordAudio){
      _isRecording=false;
      showToast(t('mic_network'));
      return;
    }
    try{
      mediaStream=await navigator.mediaDevices.getUserMedia({audio:true});
      const preferredTypes=['audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus','audio/ogg'];
      const mimeType=preferredTypes.find(type=>window.MediaRecorder.isTypeSupported?.(type))||'';
      mediaRecorder=new MediaRecorder(mediaStream,mimeType?{mimeType}:undefined);
      audioChunks=[];
      mediaRecorder.ondataavailable=e=>{if(e.data&&e.data.size)audioChunks.push(e.data);};
      mediaRecorder.onerror=()=>{
        _isRecording=false;
        _setRecording(false);
        window._micPendingSend=false;
        _stopTracks();
        showToast(t('mic_network'));
      };
      mediaRecorder.onstop=async()=>{
        _isRecording=false;
        const blob=new Blob(audioChunks,{type:mediaRecorder.mimeType||mimeType||'audio/webm'});
        _setRecording(false);
        _stopTracks();
        if(blob.size){ await _transcribeBlob(blob); }
        else if(window._micPendingSend){
          window._micPendingSend=false;
        }
      };
      mediaRecorder.start();
      _setRecording(true);
    }catch(err){
      _isRecording=false;
      window._micPendingSend=false;
      _stopTracks();
      showToast(t('mic_denied'));
    }
  };
})();
window._micActive=window._micActive||false;
window._micPendingSend=window._micPendingSend||false;

// ── Turn-based voice mode (#1333) ────────────────────────────────────────
// Chained flow: listen → send → (agent processes) → TTS response → listen again
(function(){
  const SpeechRecognition=window.SpeechRecognition||window.webkitSpeechRecognition;
  const hasSTT=!(!SpeechRecognition);
  const hasTTS=!!('speechSynthesis' in window);

  // Need both STT and TTS for turn-based voice mode
  if(!hasSTT||!hasTTS) return;

  const modeBtn=$('btnVoiceMode');
  const bar=$('voiceModeBar');
  const indicator=$('voiceModeIndicator');
  const label=$('voiceModeLabel');
  const micBtn=$('btnMic');
  const ta=$('msg');

  if(!modeBtn||!bar||!indicator||!label) return;

  // Voice-mode button is gated behind a Preferences toggle (#1488).
  // Default off — keeps the composer footer uncluttered for users who
  // only need plain dictation. The hands-free conversation feature is
  // a power-user surface; explicit opt-in avoids the visual confusion
  // of two near-identical mic icons.
  function _voiceModePrefEnabled(){
    try{ return localStorage.getItem('sidekick-voice-mode-button')==='true'; }
    catch(_){ return false; }
  }
  let _voiceModeActive=false;

  function _applyVoiceModePref(){
    const enabled = _voiceModePrefEnabled();
    modeBtn.style.display = enabled ? '' : 'none';
    if(!enabled && _voiceModeActive) _deactivate();
  }
  _applyVoiceModePref();
  // Expose so the settings pane can re-apply immediately on toggle.
  window._applyVoiceModePref = _applyVoiceModePref;

  let _voiceModeState='idle'; // idle | listening | thinking | speaking
  let _recognition=null;
  let _silenceTimer=null;
  // Capture the session id at thinking-time so the TTS callback won't read
  // a different session's last assistant reply if the user navigated away
  // between send and stream completion. (Opus pre-release advisor.)
  let _voiceModeThinkingSid=null;
  const SILENCE_MS=1800; // auto-send after 1.8s silence

  function _setState(state){
    _voiceModeState=state;
    indicator.className='voice-mode-indicator '+state;
    label.textContent=state==='listening'?t('voice_listening')
      :state==='speaking'?t('voice_speaking')
      :state==='thinking'?t('voice_thinking')
      :'';
    bar.style.display=_voiceModeActive?(state==='idle'?'none':''):'none';
  }

  function _startListening(){
    if(!_voiceModeActive) return;
    _setState('listening');

    _recognition=new SpeechRecognition();
    _recognition.continuous=false;
    _recognition.interimResults=true;
    _recognition.lang=(typeof _locale!=='undefined'&&_locale._speech)||'en-US';

    let _finalText='';

    _recognition.onstart=()=>{ _finalText=''; };

    _recognition.onresult=(event)=>{
      // Reset silence timer on any result
      clearTimeout(_silenceTimer);
      let interim='';
      let final=_finalText;
      for(let i=event.resultIndex;i<event.results.length;i++){
        const txt=event.results[i][0].transcript;
        if(event.results[i].isFinal){ final+=txt; _finalText=final; }
        else{ interim+=txt; }
      }
      ta.value=final||interim;
      autoResize();

      // Auto-send on silence after final result
      if(_finalText){
        _silenceTimer=setTimeout(()=>{
          _voiceModeSend();
        },SILENCE_MS);
      }
    };

    _recognition.onend=()=>{
      clearTimeout(_silenceTimer);
      // If we have text and haven't sent yet, send it
      if(_finalText&&_voiceModeActive&&_voiceModeState==='listening'){
        _voiceModeSend();
      } else if(_voiceModeActive&&_voiceModeState==='listening'){
        // No speech detected — restart listening
        setTimeout(()=>{ if(_voiceModeActive) _startListening(); },500);
      }
    };

    _recognition.onerror=(event)=>{
      clearTimeout(_silenceTimer);
      if(event.error==='no-speech'||event.error==='aborted'){
        // Restart if still active
        if(_voiceModeActive){
          setTimeout(()=>{ if(_voiceModeActive) _startListening(); },800);
        }
        return;
      }
      if(event.error==='not-allowed'||event.error==='service-not-allowed'||event.error==='audio-capture'){
        _deactivate();
        showToast(t('mic_denied'));
        return;
      }
      // Other errors — try to restart
      if(_voiceModeActive){
        setTimeout(()=>{ if(_voiceModeActive) _startListening(); },1500);
      }
    };

    try{ _recognition.start(); }catch(e){
      // Already started or other error — retry shortly
      setTimeout(()=>{ if(_voiceModeActive) _startListening(); },1000);
    }
  }

  function _voiceModeSend(){
    if(!_voiceModeActive) return;
    const text=(ta.value||'').trim();
    if(!text){
      ta.value='';
      setTimeout(()=>{ if(_voiceModeActive) _startListening(); },300);
      return;
    }
    _setState('thinking');
    // Pin the active session id so the TTS callback won't speak a different
    // session's reply if the user navigates away mid-stream.
    _voiceModeThinkingSid=(typeof S!=='undefined'&&S.session)?S.session.session_id:null;
    try{ if(_recognition) _recognition.abort(); }catch(_){}
    _recognition=null;
    // send() is global from boot.js
    if(typeof send==='function') send();
  }

  function _speakResponse(){
    if(!_voiceModeActive) return;
    // Bail out if the user navigated to a different session between send and
    // stream completion. The patched autoReadLastAssistant fires globally;
    // without this guard it would TTS-read the wrong session's last assistant
    // message. Drop back to listening on the new session instead.
    const currentSid=(typeof S!=='undefined'&&S.session)?S.session.session_id:null;
    if(_voiceModeThinkingSid && currentSid && currentSid!==_voiceModeThinkingSid){
      _voiceModeThinkingSid=null;
      _startListening();
      return;
    }
    _voiceModeThinkingSid=null;
    _setState('speaking');

    // Find last assistant message
    const rows=document.querySelectorAll('.msg-row[data-role="assistant"], .assistant-segment[data-raw-text]');
    if(!rows.length){ _startListening(); return; }
    const last=rows[rows.length-1];
    const rawText=last.dataset.rawText||'';
    if(!rawText.trim()){ _startListening(); return; }

    // Strip for TTS (reuse existing helper if available)
    let clean=rawText;
    if(typeof _stripForTTS==='function') clean=_stripForTTS(rawText);
    else{
      // Basic strip: remove code blocks, images, links
      clean=clean.replace(/```[\s\S]*?```/g,' code block ')
        .replace(/`([^`]*)`/g,'$1')
        .replace(/!\[([^\]]*)\]\([^)]*\)/g,'$1')
        .replace(/\[([^\]]*)\]\([^)]*\)/g,'$1')
        .replace(/#{1,6}\s/g,'')
        .replace(/[*_~]+/g,'')
        .replace(/\n{2,}/g,'. ')
        .replace(/\n/g,' ')
        .trim();
    }
    if(!clean){ _startListening(); return; }

    const utter=new SpeechSynthesisUtterance(clean);

    // Apply saved voice preferences
    const savedVoice=localStorage.getItem('sidekick-tts-voice');
    const voices=speechSynthesis.getVoices();
    if(savedVoice&&voices.length){
      const match=voices.find(v=>v.name===savedVoice);
      if(match) utter.voice=match;
    }
    const savedRate=parseFloat(localStorage.getItem('sidekick-tts-rate'));
    if(!isNaN(savedRate)) utter.rate=Math.min(2,Math.max(0.5,savedRate));
    const savedPitch=parseFloat(localStorage.getItem('sidekick-tts-pitch'));
    if(!isNaN(savedPitch)) utter.pitch=Math.min(2,Math.max(0,savedPitch));

    utter.onend=()=>{
      // After speaking, go back to listening
      if(_voiceModeActive) setTimeout(()=>_startListening(),500);
    };
    utter.onerror=()=>{
      if(_voiceModeActive) setTimeout(()=>_startListening(),1000);
    };

    speechSynthesis.speak(utter);
  }

  // Hook into response completion — observe when the agent finishes
  // We patch setComposerStatus to detect when a response completes
  const _origSetComposerStatus=(typeof setComposerStatus==='function')?setComposerStatus.bind(window):null;

  window._voiceModeOnResponseComplete=function(){
    if(_voiceModeActive&&_voiceModeState==='thinking'){
      // Small delay to let DOM render the final message
      setTimeout(()=>{
        if(_voiceModeActive&&_voiceModeState==='thinking'){
          _speakResponse();
        }
      },400);
    }
  };

  // Observe S.busy changes to detect response completion
  // The existing code calls setBusy(false) when response completes
  const _origSetBusy=(typeof setBusy==='function')?setBusy.bind(window):null;
  if(_origSetBusy){
    // We use a MutationObserver-style approach via polling S.busy
    // Actually, we'll use a simpler approach: hook into the message stream completion
  }

  // Most reliable hook: use the existing autoReadLastAssistant call site.
  // We override autoReadLastAssistant so that if voice mode is active, we use our
  // own speak-and-resume flow instead of the default auto-read.
  const _origAutoRead=(typeof autoReadLastAssistant==='function')?autoReadLastAssistant:null;
  window.autoReadLastAssistant=function(){
    if(_voiceModeActive&&_voiceModeState==='thinking'){
      _speakResponse();
      return;
    }
    if(_origAutoRead) _origAutoRead.apply(this,arguments);
  };

  function _activate(){
    _voiceModeActive=true;
    modeBtn.classList.add('active');
    _setButtonTooltip(modeBtn, t('voice_mode_toggle_active'));
    showToast(t('voice_mode_active'),1500);
    // If the agent is busy, wait — state will be 'thinking' and we'll detect completion
    if(typeof S!=='undefined'&&S.busy){
      _setState('thinking');
      return;
    }
    // Cancel any existing TTS
    if(typeof stopTTS==='function') stopTTS();
    _startListening();
  }

  function _deactivate(){
    _voiceModeActive=false;
    _voiceModeState='idle';
    _voiceModeThinkingSid=null;
    modeBtn.classList.remove('active');
    _setButtonTooltip(modeBtn, t('voice_mode_toggle'));
    bar.style.display='none';
    clearTimeout(_silenceTimer);
    try{ if(_recognition) _recognition.abort(); }catch(_){}
    _recognition=null;
    if(typeof stopTTS==='function') stopTTS();
    // Restore original autoReadLastAssistant
    if(_origAutoRead) window.autoReadLastAssistant=_origAutoRead;
    // Clear textarea if it was only voice input
    ta.value='';
    autoResize();
  }

  modeBtn.onclick=()=>{
    if(_voiceModeActive){
      _deactivate();
      showToast(t('voice_mode_off'),1500);
    }else{
      _activate();
    }
  };

  // Expose for external use
  window._voiceModeActive=()=>_voiceModeActive;
  window._voiceModeDeactivate=_deactivate;
  window._voiceModeImmediateSend=_voiceModeSend;
})();
$('fileInput').onchange=e=>{addFiles(Array.from(e.target.files));e.target.value='';};
$('btnNewChat').onclick=async()=>{
  if (typeof sidekickNewChat === 'function') await sidekickNewChat();
};
$('btnDownload').onclick=()=>{
  if(!S.session)return;
  const blob=new Blob([transcript()],{type:'text/markdown'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download=`sidekick-${S.session.session_id}.md`;a.click();URL.revokeObjectURL(a.href);
};
$('btnExportJSON').onclick=()=>{
  if(!S.session)return;
  const url=`/api/session/export?session_id=${encodeURIComponent(S.session.session_id)}`;
  const a=document.createElement('a');a.href=url;
  a.download=`sidekick-${S.session.session_id}.json`;a.click();
};
$('btnImportJSON').onclick=()=>$('importFileInput').click();
$('importFileInput').onchange=async(e)=>{
  const file=e.target.files[0];
  if(!file)return;
  e.target.value='';
  try{
    const text=await file.text();
    const data=JSON.parse(text);
    const res=await api('/api/session/import',{method:'POST',body:JSON.stringify(data)});
    if(res.ok&&res.session){
      await loadSession(res.session.session_id);
      await renderSessionList();
      if(_currentPanel==='settings') switchPanel('chat');
      showToast(t('session_imported'));
    }
  }catch(err){
    showToast(t('import_failed')+(err.message||t('import_invalid_json')));
  }
};
// btnRefreshFiles is now panel-icon-btn in header (see HTML)
function clearPreview(opts={}){
  const keepPanelOpen=!!(opts&&opts.keepPanelOpen);
  // Restore directory breadcrumb after closing file preview
  if(typeof renderBreadcrumb==='function') renderBreadcrumb();
  const closePanelAfter=_workspacePanelMode==='preview'&&!keepPanelOpen;
  const pa=$('previewArea');if(pa)pa.classList.remove('visible');
  const pi=$('previewImg');if(pi){pi.onerror=null;pi.src='';}
  const pdf=$('previewPdfFrame');if(pdf)pdf.src='';
  const html=$('previewHtmlIframe');if(html)html.src='';
  const pm=$('previewMd');if(pm)pm.innerHTML='';
  const pc=$('previewCode');if(pc)pc.textContent='';
  const pp=$('previewPathText');if(pp)pp.textContent='';
  const ft=$('fileTree');if(ft)ft.style.display='';
  _previewCurrentPath='';_previewCurrentMode='';_previewDirty=false;
  if(closePanelAfter)closeWorkspacePanel();
  else if(keepPanelOpen&&_workspacePanelMode==='preview')openWorkspacePanel('browse');
  else syncWorkspacePanelUI();
}
$('btnClearPreview').onclick=handleWorkspaceClose;
// workspacePath click handler removed -- use topbar workspace chip dropdown instead
$('modelSelect').onchange=async()=>{
  if(!S.session)return;
  const selectedModel=$('modelSelect').value;
  const modelState=(typeof _modelStateForSelect==='function')
    ? _modelStateForSelect($('modelSelect'),selectedModel)
    : {model:selectedModel,model_provider:null};
  if(typeof closeModelDropdown==='function') closeModelDropdown();
  if(typeof _writePersistedModelState==='function') _writePersistedModelState(modelState.model,modelState.model_provider);
  else try{localStorage.setItem('sidekick-webui-model',modelState.model)}catch{}
  await api('/api/session/update',{method:'POST',body:JSON.stringify({
    session_id:S.session.session_id,
    workspace:S.session.workspace,
    model:modelState.model,
    model_provider:modelState.model_provider||null,
  })});
  S.session.model=modelState.model;
  S.session.model_provider=modelState.model_provider||null;
  if(typeof syncModelChip==='function') syncModelChip();
  if(typeof syncReasoningChip==='function') syncReasoningChip();
  syncTopbar();
  // Clarify scope: composer model changes are session-local, not the global default.
  if(typeof showToast==='function'){
    showToast(t('model_scope_toast')||'Applies to this conversation from your next message.', 3000);
  }
  // Warn if selected model belongs to a different provider than what Sidekick is configured for
  if(typeof _checkProviderMismatch==='function'){
    const warn=_checkProviderMismatch(selectedModel);
    if(warn&&typeof showToast==='function') showToast(warn,4000);
  }
};
$('msg').addEventListener('input',()=>{
  autoResize();
  updateSendBtn();
  // Persist composer draft to server (debounced in _saveComposerDraft).
  const sid = S && S.session && S.session.session_id;
  if (sid && typeof _saveComposerDraft === 'function') {
    _saveComposerDraft(sid, $('msg').value, S.pendingFiles ? [...S.pendingFiles] : []);
  }
  const text=$('msg').value;
  if(text.startsWith('/')&&text.indexOf('\n')===-1){
    if(typeof getSlashAutocompleteMatches==='function'){
      getSlashAutocompleteMatches(text).then(matches=>{
        if(($('msg').value||'')!==text) return;
        if(matches.length)showCmdDropdown(matches); else hideCmdDropdown();
      });
    }else{
      const prefix=text.slice(1);
      const matches=getMatchingCommands(prefix);
      if(matches.length)showCmdDropdown(matches); else hideCmdDropdown();
    }
    if(typeof ensureSkillCommandsLoadedForAutocomplete==='function') ensureSkillCommandsLoadedForAutocomplete();
  } else {
    hideCmdDropdown();
  }
});
// Track IME composition for East Asian input. Safari fires the committing
// keydown AFTER compositionend with isComposing=false, so we also keep a
// manual flag and reset it on the next tick to swallow that trailing Enter.
// Also reset on blur so the flag can never get stuck in a true state if
// compositionend never fires (focus loss with some IME implementations).
//
// The `_imeComposing` flag is bound to the chat composer (`#msg`); other
// inputs (session/project rename, app dialog, message edit, workspace rename)
// rely on the state-free `e.isComposing || e.keyCode === 229` part of
// `_isImeEnter`, which is sufficient for the Safari race because keyCode 229
// is the canonical "still composing" signal regardless of which field is
// focused. Promote `_isImeEnter` to `window` so other modules can reuse it
// without duplicating the full IIFE per input (issue #1443).
let _imeComposing=false;
(()=>{const _c=$('msg');if(!_c)return;
  _c.addEventListener('compositionstart',()=>{_imeComposing=true;});
  _c.addEventListener('compositionend',()=>{setTimeout(()=>{_imeComposing=false;},0);});
  _c.addEventListener('blur',()=>{_imeComposing=false;});
})();
function _isImeEnter(e){return e.isComposing||e.keyCode===229||_imeComposing;}
window._isImeEnter=_isImeEnter;
$('msg').addEventListener('keydown',e=>{
  // Autocomplete navigation when dropdown is open
  const dd=$('cmdDropdown');
  const dropdownOpen=dd&&dd.classList.contains('open');
  if(dropdownOpen){
    if(e.key==='ArrowUp'){e.preventDefault();navigateCmdDropdown(-1);return;}
    if(e.key==='ArrowDown'){e.preventDefault();navigateCmdDropdown(1);return;}
    if(e.key==='Tab'){e.preventDefault();selectCmdDropdownItem();return;}
    if(e.key==='Escape'){e.preventDefault();hideCmdDropdown();return;}
    if(e.key==='Enter'&&!e.shiftKey){
      if(_isImeEnter(e)){return;}
      e.preventDefault();
      selectCmdDropdownItem();
      return;
    }
  }
  // Prompt history navigation (only when dropdown is closed)
  if(e.key==='ArrowUp'&&!e.altKey&&!e.ctrlKey&&!e.metaKey){
    e.preventDefault();
    if(typeof window._navigatePromptHistory==='function') window._navigatePromptHistory(-1);
    return;
  }
  if(e.key==='ArrowDown'&&!e.altKey&&!e.ctrlKey&&!e.metaKey){
    if(typeof window._navigatePromptHistory==='function'&&window._navigatePromptHistory(1)){
      e.preventDefault();
      return;
    }
  }
  // Send key: respect user preference.
  // Default (multiline mode): Enter = newline, Ctrl/Cmd+Enter = send.
  // User can set _sendKey='enter' for old behavior (plain Enter = send).
  // On touch-primary devices (software keyboard), Enter is always newline
  // since there's no Ctrl key — users send via the Send button.
  if(e.key==='Enter'){
    if(_isImeEnter(e)){return;}
    const _sendMode=window._sendKey||'ctrl+enter';
    const _onMobile=matchMedia('(pointer:coarse)').matches;
    if(_sendMode==='enter'&&!_onMobile){
      // Legacy mode: plain Enter sends (unless Shift held for newline)
      if(!e.shiftKey){e.preventDefault();send();}
    } else {
      // Multiline mode (default): Ctrl/Cmd+Enter sends, plain Enter = newline
      if(e.ctrlKey||e.metaKey){e.preventDefault();send();}
    }
  }
});
// B14: Cmd/Ctrl+K creates a new chat from anywhere
document.addEventListener('keydown',async e=>{
  // Cmd/Ctrl+B toggles desktop sidebar collapse (VS Code convention).
  // Skip when typing in an input/textarea/contenteditable so text-edit
  // shortcuts (e.g. bold in some embedded editors) are never stolen.
  if((e.metaKey||e.ctrlKey)&&!e.shiftKey&&!e.altKey&&(e.key==='b'||e.key==='B')){
    const t=e.target;
    const isText=t&&(t.tagName==='INPUT'||t.tagName==='TEXTAREA'||t.isContentEditable);
    if(!isText&&typeof toggleSidebar==='function'&&_isDesktopWidth()){
      e.preventDefault();
      toggleSidebar();
      return;
    }
  }
  // Enter on inline approval card = Allow once
  if(e.key==='Enter'&&!e.metaKey&&!e.ctrlKey&&!e.shiftKey){
    const tag=(document.activeElement||{}).tagName||'';
    if(tag!=='TEXTAREA'&&tag!=='INPUT'&&tag!=='SELECT'){
      const inlineCard=document.querySelector('.inline-approval-card[data-approval-status="pending"]');
      if(inlineCard && typeof respondApproval==='function'){
        e.preventDefault();
        respondApproval('once');
        return;
      }
    }
  }
  if((e.metaKey||e.ctrlKey)&&e.key==='k'){
    e.preventDefault();
    // If the current session has no messages AND nothing is in flight, just focus
    // the composer rather than creating another empty session that will clutter
    // the sidebar list (#1171). See the matching guard in $('btnNewChat').onclick
    // and bug #1432 for why the in-flight check is needed.
    if(S.session
       && (S.session.message_count||0)===0
       && !S.busy
       && !S.session.active_stream_id
       && !S.session.pending_user_message){
      $('msg').focus();return;
    }
    // Cmd/Ctrl+K should always create a new conversation, even while the current
    // one is still streaming. The old !S.busy guard meant users had to wait for
    // a long generation to finish before they could start something new — exactly
    // the moment they want to switch context. newSession() leaves the in-flight
    // stream running on its own session; the user just gets a fresh blank one.
    await newSession();await renderSessionList();closeMobileSidebar();$('msg').focus();
  }
  // Cmd/Ctrl+Shift+K opens the workflow palette from anywhere.
  if((e.metaKey||e.ctrlKey)&&e.shiftKey&&!e.altKey&&(e.key==='k'||e.key==='K')){
    e.preventDefault();
    if(typeof workflowToggleHeaderMenu==='function') workflowToggleHeaderMenu(e);
    return;
  }
  // Ctrl+Shift+F: toggle Focus Mode (hide all panels, only chat full-width)
  if((e.metaKey||e.ctrlKey)&&e.shiftKey&&(e.key==='f'||e.key==='F')){
    e.preventDefault();
    document.body.classList.toggle('focus-mode');
    // Focus the composer input so user can type immediately
    $('msg').focus();
    return;
  }
  // Ctrl+F / Cmd+F: open conversation search bar
  if((e.metaKey||e.ctrlKey)&&!e.shiftKey&&!e.altKey&&(e.key==='f'||e.key==='F')){
    const inTextInput=e.target&&(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA'||e.target.isContentEditable);
    // Allow default browser find when in text inputs (user wants browser search)
    if(!inTextInput){
      e.preventDefault();
      const searchBar=$('chatSearchBar');
      if(searchBar&&searchBar.hidden){
        openChatSearch();
      }else if(searchBar){
        // Already open — focus the input
        $('chatSearchInput')&&$('chatSearchInput').focus();
      }
      return;
    }
  }
  if(e.key==='Escape'){
    // Close expand editor if active
    const expanded=document.querySelector('.composer-expanded');
    if(expanded&&typeof toggleExpandEditor==='function'){toggleExpandEditor();return;}
    // Close onboarding overlay if open (skip/dismiss the wizard)
    const onboardingOverlay=$('onboardingOverlay');
    if(onboardingOverlay&&onboardingOverlay.style.display!=='none'){
      if(typeof skipOnboarding==='function') skipOnboarding();
      return;
    }
    // Close conversation search if open
    const searchBar=$('chatSearchBar');
    if(searchBar&&!searchBar.hidden&&typeof closeChatSearch==='function'){
      closeChatSearch();
      return;
    }
    // Close settings panel if active
    if(_currentPanel==='settings'){_closeSettingsPanel();return;}
    // Close workspace dropdown
    closeWsDropdown();
    // Clear session search
    const ss=$('sessionSearch');
    if(ss&&ss.value){ss.value='';filterSessions();}
    // Cancel any active message edit
    const editArea=document.querySelector('.msg-edit-area');
    if(editArea){
      const bar=editArea.closest('.msg-row')&&editArea.closest('.msg-row').querySelector('.msg-edit-bar');
      if(bar){const cancel=bar.querySelector('.msg-edit-cancel');if(cancel)cancel.click();}
    }
  }
  // Keyboard shortcut: E = edit last user message (composer edit & resend)
  if(e.key==='e'&&!e.metaKey&&!e.ctrlKey&&!e.altKey&&!e.shiftKey){
    const tag=(document.activeElement||{}).tagName||'';
    if(tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT') return;
    e.preventDefault();
    if(typeof editLastUserMessage==='function') editLastUserMessage();
    return;
  }
});
$('msg').addEventListener('paste',e=>{
  const items=Array.from(e.clipboardData?.items||[]);
  // When the clipboard carries BOTH text and an image (common from Notes,
  // Word, browsers, Slack — the OS attaches a rendered preview alongside
  // the plain text), prefer the text and let the browser paste normally.
  // Only intercept when the clipboard is image-only (true screenshot paste).
  // Tighten the image filter to kind==='file' so string items advertising an
  // image MIME (e.g. text/html with an embedded data URI) are not misclassified.
  const hasText=items.some(i=>i.kind==='string'&&(i.type==='text/plain'||i.type==='text/html'));
  const imageItems=items.filter(i=>i.kind==='file'&&i.type.startsWith('image/'));
  if(!imageItems.length||hasText)return;
  e.preventDefault();
  const pasteTs=Date.now();
  const files=imageItems.map((i,idx)=>{
    const blob=i.getAsFile();
    const ext=i.type.split('/')[1]||'png';
    const suffix=imageItems.length>1?`-${idx+1}`:'';
    return new File([blob],`screenshot-${pasteTs}${suffix}.${ext}`,{type:i.type});
  });
  addFiles(files);
  setStatus(t('image_pasted')+files.map(f=>f.name).join(', '));
});
document.querySelectorAll('.suggestion').forEach(btn=>{
  btn.onclick=()=>{$('msg').value=btn.dataset.msg;send();};
});

window.addEventListener('resize',()=>{
  _syncMobileSidebarInlineOffset(document.querySelector('.sidebar'),document.querySelector('.sidebar')?.classList.contains('mobile-open'));
  _syncWorkspacePanelInlineWidth();
  syncWorkspacePanelState();
});

// Boot: restore last session or start fresh
// ── Resizable panels ──────────────────────────────────────────────────────
(function(){
  const SIDEBAR_MIN=180, SIDEBAR_MAX=420, SIDEBAR_DEFAULT=300;
  const PANEL_MIN=180,   PANEL_MAX=1200, PANEL_DEFAULT=300;

  function initResize(handleId, targetEl, edge, minW, maxW, storageKey, defaultW){
    const handle = $(handleId);
    if(!handle || !targetEl) return;

    // Restore saved width
    if(storageKey === 'sidekick-panel-w'){
      _syncWorkspacePanelInlineWidth();
    }else{
      const saved = localStorage.getItem(storageKey);
      if(saved) targetEl.style.width = saved + 'px';
    }

    // Reset to default width on double-click
    handle.addEventListener('dblclick', e=>{
      e.preventDefault();
      const w = defaultW || parseInt(targetEl.style.width) || 300;
      targetEl.style.width = w + 'px';
      localStorage.setItem(storageKey, w);
    });

    let startX=0, startW=0;

    handle.addEventListener('mousedown', e=>{
      e.preventDefault();
      startX = e.clientX;
      startW = targetEl.getBoundingClientRect().width;
      handle.classList.add('dragging');
      document.body.classList.add('resizing');

      const onMove = ev=>{
        const delta = edge==='right' ? ev.clientX - startX : startX - ev.clientX;
        const newW = Math.min(maxW, Math.max(minW, startW + delta));
        targetEl.style.width = newW + 'px';
      };
      const onUp = ()=>{
        handle.classList.remove('dragging');
        document.body.classList.remove('resizing');
        localStorage.setItem(storageKey, parseInt(targetEl.style.width));
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  // Run after DOM ready (called from boot)
  window._initResizePanels = function(){
    const sidebar    = document.querySelector('.sidebar');
    const rightpanel = document.querySelector('.rightpanel');
    initResize('sidebarResize',    sidebar,    'right', SIDEBAR_MIN, SIDEBAR_MAX, 'sidekick-sidebar-w');
    // Rightpanel resize is handled by initSplitPane() via #chatSplitResize
  };

  window._initChatVerticalResize = function(){
    const handle=$('chatResizeHandle');
    const layout=$('chatSplitLayout');
    if(!handle||!layout)return;
    const MIN_H=100, MAX_H=600;
    const STORAGE_KEY='sidekick-webui-terminal-height';
    const saved=localStorage.getItem(STORAGE_KEY);
    if(saved) layout.style.setProperty('--terminal-pane-height',saved+'px');
    let startY=0, startH=0;
    handle.addEventListener('mousedown',function(e){
      e.preventDefault();
      startY=e.clientY;
      startH=layout.getBoundingClientRect().height;
      const pane=$('terminalPane');
      if(pane) startH=parseInt(getComputedStyle(pane).height)||280;
      handle.classList.add('dragging');
      document.body.classList.add('resizing-vertical');
      const onMove=function(ev){
        // layout total = chat + handle + terminal. We adjust terminal height.
        const delta=startY-ev.clientY;  // negative = drag down = bigger terminal
        const layoutRect=layout.getBoundingClientRect();
        const paneRect=($('terminalPane')||{}).getBoundingClientRect();
        if(!paneRect)return;
        // From the layout grid: terminal height = startH + delta
        const newH=Math.min(MAX_H,Math.max(MIN_H,startH-delta));
        layout.style.setProperty('--terminal-pane-height',newH+'px');
      };
      const onUp=function(){
        handle.classList.remove('dragging');
        document.body.classList.remove('resizing-vertical');
        document.removeEventListener('mousemove',onMove);
        document.removeEventListener('mouseup',onUp);
        const finalH=layout.style.getPropertyValue('--terminal-pane-height');
        if(finalH) localStorage.setItem(STORAGE_KEY,parseInt(finalH));
      };
      document.addEventListener('mousemove',onMove);
      document.addEventListener('mouseup',onUp);
    });
  };
})();

// ── Appearance helpers (theme = light/dark/system, skin = accent color) ──────
const _THEMES=[
  {name:'Light', value:'light', colors:['#FEFCF7','#FAF7F0','#B8860B']},
  {name:'Dark', value:'dark', colors:['#0D0D1A','#141425','#FFD700']},
  {name:'System', value:'system', colors:['#FEFCF7','#0D0D1A','#B8860B']},
];
const _SKINS=[
  {name:'Default',  colors:['#FFD700','#FFBF00','#CD7F32']},
  {name:'Ares',     colors:['#FF4444','#CC3333','#992222']},
  {name:'Mono',     colors:['#CCCCCC','#999999','#666666']},
  {name:'Slate',    colors:['#334155','#475569','#64748b']},
  {name:'Poseidon', colors:['#0EA5E9','#0284C7','#0369A1']},
  {name:'Sisyphus', colors:['#A78BFA','#8B5CF6','#7C3AED']},
  {name:'Charizard',colors:['#FB923C','#F97316','#EA580C']},
  {name:'Sienna',   colors:['#D97757','#C06A49','#9A523A']},
  {name:'Matrix',   colors:['#00FF41','#00DD33','#55CC55']},
];
const _VALID_THEMES=new Set((_THEMES||[]).map(t=>t.value));
const _VALID_SKINS=new Set((_SKINS||[]).map(s=>s.name.toLowerCase()));
const _LEGACY_THEME_MAP={
  slate:{theme:'dark',skin:'slate'},
  solarized:{theme:'dark',skin:'poseidon'},
  monokai:{theme:'dark',skin:'sisyphus'},
  nord:{theme:'dark',skin:'slate'},
  oled:{theme:'dark',skin:'default'},
};
let _systemThemeMq=null;
let _onSystemThemeChange=null;

function _normalizeAppearance(theme,skin){
  const rawTheme=typeof theme==='string'?theme.trim().toLowerCase():'';
  const rawSkin=typeof skin==='string'?skin.trim().toLowerCase():'';
  const legacy=_LEGACY_THEME_MAP[rawTheme];
  const nextTheme=legacy?legacy.theme:(_VALID_THEMES.has(rawTheme)?rawTheme:'dark');
  const nextSkin=_VALID_SKINS.has(rawSkin)?rawSkin:(legacy?legacy.skin:'default');
  return {theme:nextTheme,skin:nextSkin};
}

// Sync <meta name="theme-color"> with the active theme's computed --bg.
// This surfaces the WebUI's exact theme background to:
//   1. Mobile Safari status bar (the prefers-color-scheme media variants in index.html
//      cover the pre-load case; this updater handles user-toggled changes mid-session).
//   2. iOS PWA / Add to Home Screen status bar.
//   3. Native WKWebView wrappers (e.g. hermes-swift-mac) that read this attribute as
//      the source of truth for AppKit chrome (tab bar, title bar, traffic-light area)
//      instead of pixel-sampling — overlay-resistant and IPC-free.
// Reading getComputedStyle(html).getPropertyValue('--bg') picks up the active skin
// (Default, Sienna, Sisyphus, Charizard, etc.) so each skin's distinct paint reaches
// the meta tag.
function _syncThemeColorMeta(){
  try{
    const bg=getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
    if(!bg) return;
    const known=document.getElementById('sidekick-theme-color');
    if(known){
      known.setAttribute('content',bg);
      known.removeAttribute('media');
    }
    document.querySelectorAll('meta[name="theme-color"]').forEach(meta=>{
      meta.setAttribute('content',bg);
      meta.removeAttribute('media');
    });
  }catch(e){}
}

function _setResolvedTheme(isDark){
  document.documentElement.classList.toggle('dark',!!isDark);
  _applySyntaxTheme(); // respects user's syntax theme preference, falls back to dark/light
  _syncThemeColorMeta();
}

// ── Syntax highlighting theme (3 presets) ──
const _SYNTAX_THEMES = {
  'tomorrow-night': 'https://cdn.jsdelivr.net/npm/prismjs@1.29.0/themes/prism-tomorrow.min.css',
  'one-dark':       'https://cdn.jsdelivr.net/npm/prism-themes@1.9.0/themes/prism-one-dark.min.css',
  'github-light':   'https://cdn.jsdelivr.net/npm/prism-themes@1.9.0/themes/prism-ghcolors.min.css',
};

function _applySyntaxTheme(override){
  const saved = override || localStorage.getItem('sidekick-syntax-theme') || '';
  const link = document.getElementById('prism-theme');
  if(!link) return;
  const isDark = document.documentElement.classList.contains('dark');
  let want;
  if(saved && _SYNTAX_THEMES[saved]){
    want = _SYNTAX_THEMES[saved];
    document.documentElement.dataset.syntaxTheme = saved;
  } else {
    // Fallback: dark → Tomorrow Night, light → default
    want = isDark
      ? 'https://cdn.jsdelivr.net/npm/prismjs@1.29.0/themes/prism-tomorrow.min.css'
      : 'https://cdn.jsdelivr.net/npm/prismjs@1.29.0/themes/prism.min.css';
    delete document.documentElement.dataset.syntaxTheme;
  }
  if(link.href !== want){ link.integrity = ''; link.href = want; }
}

function _pickSyntaxTheme(name){
  localStorage.setItem('sidekick-syntax-theme', name);
  _applySyntaxTheme(name);
  _syncSyntaxThemePicker(name);
  // Sync settings hidden input if the settings panel is open
  const hidden = $('settingsSyntaxTheme');
  if(hidden) hidden.value = name;
  // Highlight existing code blocks with new theme
  if(typeof highlightCode === 'function') highlightCode(null, true);
  if(typeof _scheduleAppearanceAutosave==='function') _scheduleAppearanceAutosave();
}

function _applyTheme(name){
  const normalized=_normalizeAppearance(name,'default');
  delete document.documentElement.dataset.theme;
  if(_systemThemeMq&&_onSystemThemeChange){
    _systemThemeMq.removeEventListener('change',_onSystemThemeChange);
    _systemThemeMq=null;
    _onSystemThemeChange=null;
  }
  if(normalized.theme==='system'){
    _systemThemeMq=window.matchMedia('(prefers-color-scheme:dark)');
    _onSystemThemeChange=()=>_setResolvedTheme(_systemThemeMq.matches);
    _setResolvedTheme(_systemThemeMq.matches);
    _systemThemeMq.addEventListener('change',_onSystemThemeChange);
    return;
  }
  _setResolvedTheme(normalized.theme==='dark');
}

function _applySkin(name){
  const key=(name||'default').toLowerCase();
  if(key==='default') delete document.documentElement.dataset.skin;
  else document.documentElement.dataset.skin=key;
  _syncThemeColorMeta();
}

function _pickTheme(name){
  const currentSkin=localStorage.getItem('sidekick-skin');
  const appearance=_normalizeAppearance(name,currentSkin);
  localStorage.setItem('sidekick-theme',appearance.theme);
  localStorage.setItem('sidekick-skin',appearance.skin);
  _applyTheme(appearance.theme);
  _applySkin(appearance.skin);
  _syncThemePicker(appearance.theme);
  _syncSkinPicker(appearance.skin);
  const hidden=$('settingsTheme');
  if(hidden) hidden.value=appearance.theme;
  const skinHidden=$('settingsSkin');
  if(skinHidden) skinHidden.value=appearance.skin;
  if(typeof _scheduleAppearanceAutosave==='function') _scheduleAppearanceAutosave();
}

function _pickSkin(name){
  const appearance=_normalizeAppearance(localStorage.getItem('sidekick-theme'),name);
  localStorage.setItem('sidekick-theme',appearance.theme);
  localStorage.setItem('sidekick-skin',appearance.skin);
  _applyTheme(appearance.theme);
  _applySkin(appearance.skin);
  _syncThemePicker(appearance.theme);
  _syncSkinPicker(appearance.skin);
  const hidden=$('settingsSkin');
  if(hidden) hidden.value=appearance.skin;
  const themeHidden=$('settingsTheme');
  if(themeHidden) themeHidden.value=appearance.theme;
  if(typeof _scheduleAppearanceAutosave==='function') _scheduleAppearanceAutosave();
}

function _syncThemePicker(active){
  document.querySelectorAll('#themePickerGrid .theme-pick-btn').forEach(btn=>{
    btn.classList.toggle('active',btn.dataset.themeVal===active);
    btn.style.borderColor='';
    btn.style.boxShadow='';
  });
}

function _syncSkinPicker(active){
  document.querySelectorAll('#skinPickerGrid .skin-pick-btn').forEach(btn=>{
    btn.classList.toggle('active',btn.dataset.skinVal===active);
    btn.style.borderColor='';
    btn.style.boxShadow='';
  });
}

function _applyFontSize(size){
  if(size&&size!=='default'){
    document.documentElement.dataset.fontSize=size;
  } else {
    delete document.documentElement.dataset.fontSize;
  }
}

function _pickFontSize(size){
  localStorage.setItem('sidekick-font-size',size);
  _applyFontSize(size);
  _syncFontSizePicker(size);
  const hidden=$('settingsFontSize');
  if(hidden) hidden.value=size;
  if(typeof _scheduleAppearanceAutosave==='function') _scheduleAppearanceAutosave();
}

function _syncFontSizePicker(active){
  document.querySelectorAll('#fontSizePickerGrid .font-size-pick-btn').forEach(btn=>{
    btn.classList.toggle('active',btn.dataset.fontSizeVal===(active||'default'));
    btn.style.borderColor='';
    btn.style.boxShadow='';
  });
}

function _syncSyntaxThemePicker(active){
  document.querySelectorAll('#syntaxThemePickerGrid .syntax-theme-pick-btn').forEach(btn=>{
    btn.classList.toggle('active',btn.dataset.syntaxThemeVal===active);
    btn.style.borderColor='';
    btn.style.boxShadow='';
  });
}

function _buildSkinPicker(activeSkin){
  const grid=$('skinPickerGrid');
  if(!grid) return;
  grid.innerHTML='';
  for(const skin of _SKINS){
    const key=skin.name.toLowerCase();
    const btn=document.createElement('button');
    btn.type='button';
    btn.className='skin-pick-btn';
    btn.dataset.skinVal=key;
    btn.style.cssText='border:1px solid var(--border2);border-radius:8px;padding:8px 4px;text-align:center;cursor:pointer;background:none;transition:all .15s';
    btn.onclick=()=>_pickSkin(skin.name);
    const dots=skin.colors.map(c=>`<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${c}"></span>`).join('');
    btn.innerHTML=`<div style="display:flex;gap:3px;justify-content:center;margin-bottom:4px">${dots}</div><span style="font-size:11px;color:var(--text)">${skin.name}</span>`;
    grid.appendChild(btn);
  }
  _syncSkinPicker((activeSkin||'default').toLowerCase());
}

function applyBotName(){
  // Prefer profile name over global bot_name for personalised placeholder.
  // If activeProfile is set and not 'default', use it (capitalised).
  // Falls back to window._botName (global bot_name setting) or 'Nova'.
  let name;
  if(S.activeProfile && S.activeProfile!=='default'){
    name=S.activeProfile.charAt(0).toUpperCase()+S.activeProfile.slice(1);
  }else{
    name=window._botName||'Nova';
  }
  document.title=name;
  // ─── DEV MODE: roter Titelbalken + Warnbanner ─────────────────────
  if(window._devMode){
    document.title='⚠️ DEV — '+document.title;
    const tb=document.querySelector('.app-titlebar');
    if(tb){
      tb.style.background='#8B0000';
      tb.style.borderBottom='2px solid #FF4444';
      const existing=tb.querySelector('.dev-banner');
      if(!existing){
        const b=document.createElement('span');
        b.className='dev-banner';
        b.textContent='⚠️ DEV VERSION — DO NOT USE PRODUCTIVE';
        Object.assign(b.style,{
          marginLeft:'auto',
          padding:'3px 12px',
          background:'#FF0000',
          color:'#fff',
          fontSize:'11px',
          fontWeight:'bold',
          letterSpacing:'1px',
          borderRadius:'4px',
          whiteSpace:'nowrap',
          lineHeight:'normal',
        });
        tb.appendChild(b);
      }
    }
    // Auch die mobile/rail-Header färben
    document.querySelectorAll('.sidebar-header,.rail').forEach(function(el){
      el.style.background='#8B0000';
    });
  }
  // ─── END DEV MODE ─────────────────────────────────────────────────
  const sidebarH1=document.querySelector('.sidebar-header h1');
  if(sidebarH1) sidebarH1.textContent=name;
  const logo=document.querySelector('.sidebar-header .logo');
  if(logo) logo.textContent=name.charAt(0).toUpperCase();
  const msg=$('msg');
  if(msg) msg.placeholder='Message '+name+'…';
}

/**
 * Load the active space's config into window._activeSpaceConfig so
 * newSession() reads project_dir and model defaults from the start,
 * not just after an explicit selectSpace() call.
 *
 * This runs after loadWorkspaceList() during boot.  It is the same
 * API call that selectSpace() makes on space switch — keeping them
 * in sync so the first new chat honours the space's config.
 */
async function _loadActiveSpaceConfig() {
  const slug = (typeof _activeSpace !== 'undefined' ? _activeSpace : null)
    || localStorage.getItem('sidekick-active-workspace')
    || 'nova';
  try {
    const resp = await api('/api/space/config?slug=' + encodeURIComponent(slug));
    window._activeSpaceConfig = resp.config || null;
  } catch (_) {
    // Non-fatal — fall through with null config; newSession() inherits
    // from the session chain or the profile default workspace.
    window._activeSpaceConfig = null;
  }
}

function _bootTimeout(promise, ms, label) {
  let timer = null;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error((label || 'boot operation') + ' timed out')), ms);
  });
  return Promise.race([promise, timeout]).finally(() => {
    if (timer) clearTimeout(timer);
  });
}

(async()=>{
  // Load send key preference
  let _bootSettings={};
  try{
    // Settings can be slow during startup on cold/backlogged instances.
    const s=await _bootTimeout(api('/api/settings'),20000,'settings');
    _bootSettings=s;
    window._sendKey=s.send_key||'enter';
    window._showTokenUsage=!!s.show_token_usage;
    window._showTps=!!s.show_tps;
    window._showCliSessions=!!s.show_cli_sessions;
    window._soundEnabled=!!s.sound_enabled;
    window._notificationsEnabled=!!s.notifications_enabled;
    window._showThinking=s.show_thinking!==false;
    window._simplifiedToolCalling=s.simplified_tool_calling!==false;
    window._sidebarDensity=(s.sidebar_density==='detailed'?'detailed':'compact');
    window._busyInputMode=(s.busy_input_mode||'queue');
    window._composerMode=(s.composer_mode||'action');
    window._gameModeEnabled=!!s.game_mode_enabled;
    try{localStorage.setItem('sidekick-game-mode-enabled',window._gameModeEnabled?'1':'0');}catch(_){}
    try{localStorage.setItem('sidekick-webui-composer-mode',window._composerMode);}catch(_){}
    window._sessionEndlessScrollEnabled=!!s.session_endless_scroll;
    window._botName=s.bot_name||'Nova';
    if(s.default_model) window._defaultModel=s.default_model;
    // Persist default workspace so the blank new-chat page can show it
    // and workspace actions (New file/folder) work before the first session (#804).
    if(s.default_workspace) S._profileDefaultWorkspace=s.default_workspace;
    window._sessionJumpButtonsEnabled=!!s.session_jump_buttons;
    const appearance=_normalizeAppearance(s.theme,s.skin);
    localStorage.setItem('sidekick-theme',appearance.theme);
    _applyTheme(appearance.theme);
    localStorage.setItem('sidekick-skin',appearance.skin);
    _applySkin(appearance.skin);
    const fontSize=(s.font_size||localStorage.getItem('sidekick-font-size')||'default');
    localStorage.setItem('sidekick-font-size',fontSize);
    _applyFontSize(fontSize);
    if(typeof setLocale==='function'){
      const _lang=typeof resolvePreferredLocale==='function'
        ? resolvePreferredLocale(s.language, localStorage.getItem('sidekick-lang'))
        : (s.language || localStorage.getItem('sidekick-lang') || 'en');
      setLocale(_lang);
      if(typeof applyLocaleToDOM==='function')applyLocaleToDOM();
    }
    applyBotName();
    if(typeof syncGameModeButton==='function')syncGameModeButton();
    // TTS: apply enabled state on boot so buttons show/hide correctly (#499)
    if(typeof _applyTtsEnabled==='function') _applyTtsEnabled(localStorage.getItem('sidekick-tts-enabled')==='true');
  }catch(e){
    console.warn('[boot] settings load failed', e);
    window._sendKey='enter';
    window._showTokenUsage=false;
    window._showTps=false;
    window._showCliSessions=false;
    window._soundEnabled=false;
    window._notificationsEnabled=false;
    window._showThinking=true;
    window._simplifiedToolCalling=true;
    window._sessionJumpButtonsEnabled=false;
    window._sidebarDensity='compact';
    window._busyInputMode='queue';
    try{
      const raw=localStorage.getItem('sidekick-game-mode-enabled');
      window._gameModeEnabled=(raw==='1'||raw==='true');
    }catch(_){
      window._gameModeEnabled=false;
    }
    window._sessionEndlessScrollEnabled=false;
    window._botName='Nova';
    _bootSettings={check_for_updates:false};
    if(typeof setLocale==='function'){
      const _lang=typeof resolvePreferredLocale==='function'
        ? resolvePreferredLocale(null, localStorage.getItem('sidekick-lang'))
        : (localStorage.getItem('sidekick-lang') || 'en');
      setLocale(_lang);
      if(typeof applyLocaleToDOM==='function')applyLocaleToDOM();
    }
    applyBotName();
    if(typeof syncGameModeButton==='function')syncGameModeButton();
    if(typeof _applyTtsEnabled==='function') _applyTtsEnabled(localStorage.getItem('sidekick-tts-enabled')==='true');
  }
  // Non-blocking update check (fire-and-forget, once per tab session)
  // ?test_updates=1 in URL forces banner display for testing (bypasses sessionStorage guards)
  const _testUpdates=new URLSearchParams(location.search).get('test_updates')==='1';
  if(_testUpdates||(_bootSettings.check_for_updates!==false&&!sessionStorage.getItem('sidekick-update-checked')&&!sessionStorage.getItem('sidekick-update-dismissed'))){
    const _checkUrl='api/updates/check'+(_testUpdates?'?simulate=1':'');
    api(_checkUrl).then(d=>{if(!_testUpdates)sessionStorage.setItem('sidekick-update-checked','1');if((d.webui&&d.webui.behind>0)||(d.agent&&d.agent.behind>0))_showUpdateBanner(d);}).catch(()=>{});
  }
  // Fetch active profile. This endpoint is useful metadata, but must never
  // block first paint/session rendering if the backend is busy.
  try{
    const p=await _bootTimeout(api('/api/profile/active'),20000,'active profile');
    S.activeProfile=p.name||'default';
  }catch(e){
    S.activeProfile='default';
    console.warn('[boot] active profile unavailable, using default', e);
  }
  // Update profile chip label immediately
  const profileLabel=$('profileChipLabel');
  if(profileLabel) profileLabel.textContent=S.activeProfile||'default';
  // Fetch available models without blocking session restore. The static HTML
  // options are enough for first paint; the dynamic provider list can settle
  // after the saved session is visible.
  const _modelDropdownReady=populateModelDropdown().then(()=>{
    const savedState=(typeof _readPersistedModelState==='function')
      ? _readPersistedModelState()
      : (localStorage.getItem('sidekick-webui-model')?{model:localStorage.getItem('sidekick-webui-model'),model_provider:null}:null);
    const savedModel=savedState&&savedState.model;
    if(savedModel && $('modelSelect')){
      const applied=(typeof _applyModelToDropdown==='function')
        ? _applyModelToDropdown(savedModel,$('modelSelect'),savedState.model_provider||null)
        : null;
      if(!applied) $('modelSelect').value=savedModel;
      // If the value didn't take (model not in list), clear the bad pref
      if(!applied&&$('modelSelect').value!==savedModel){
        if(typeof _clearPersistedModelState==='function') _clearPersistedModelState();
        else localStorage.removeItem('sidekick-webui-model');
      }
      else if(typeof syncModelChip==='function') syncModelChip();
    }
    if(S.session) syncTopbar();
  }).catch(()=>{});
  window._modelDropdownReady=_modelDropdownReady;
  // Pre-load workspace list so sidebar name is correct from first render.
  // Render the session list before restoring the saved conversation so a stale
  // saved-session/client-side boot error cannot leave the sidebar empty forever.
  try {
    const _bootEarlySessionId = (typeof _sessionIdFromLocation === 'function') ? _sessionIdFromLocation() : null;
    if (_bootEarlySessionId) _setConversationRestorePlaceholder('Restoring conversation...');
  } catch (_) {}
  await _bootTimeout(loadWorkspaceList(),10000,'workspace list').catch((e)=>{
    console.warn('[boot] workspace list unavailable, continuing', e);
  });
  // Load the active space's config (project_dir, default model, etc.) so
  // newSession() sees the correct spaceDefaultPath right from the first
  // new chat, not just after an explicit space switch (#spaces-default-dir).
  await _bootTimeout(_loadActiveSpaceConfig(),8000,'space config').catch((e)=>{
    window._activeSpaceConfig=null;
    console.warn('[boot] active space config unavailable, continuing', e);
  });
  await _bootTimeout(loadOnboardingWizard(),8000,'onboarding').catch((e)=>{
    console.warn('[boot] onboarding unavailable, continuing', e);
  });
  const urlSession=(typeof _sessionIdFromLocation==='function')?_sessionIdFromLocation():null;
  const urlWorkspace = (() => {
    try {
      return String(new URLSearchParams(location.search).get('workspace') || '').trim().toLowerCase();
    } catch (_) {
      return '';
    }
  })();
  const savedLocal=localStorage.getItem('sidekick-webui-session');
  const saved=urlSession||savedLocal;
  const _bootRestoreEpoch=Number(window.__sidekickSessionNavigationEpoch||0)||0;
  const _bootRestoreCanceled=()=>(
    !!window.__sidekickSkipBootSessionRestore ||
    ((Number(window.__sidekickSessionNavigationEpoch||0)||0)!==_bootRestoreEpoch)
  );
  if (urlSession && saved && !_bootRestoreCanceled()) {
    _setConversationRestorePlaceholder('Restoring conversation...');
  }
  let _bootSavedSessionLoadPromise = null;
  let _bootMissingSession = false;
  if (urlSession && saved && !_bootRestoreCanceled()) {
    // Direct session URLs should start loading immediately instead of waiting
    // for sidebar/session-list rendering to finish.
    _bootSavedSessionLoadPromise = loadSession(saved, { expectedSpace: urlWorkspace || '', suppressMissingSessionMessage: true }).catch((e) => {
      if (!_bootRestoreCanceled()) throw e;
    });
  }
  await renderSessionList();
  _initResizePanels();
  // Workspace panel restore happens AFTER loadSession so we know if
  // the session has a workspace — prevents the snap-open-then-closed flash (#576).
  // Fix #822: clear any browser-restored value before first render. This
  // covers fresh page loads and reloads. The bfcache restore case is handled
  // separately below by a `pageshow` listener — the async IIFE here does NOT
  // re-run when the browser restores the page from bfcache.
  const _srch = document.getElementById('sessionSearch'); if (_srch) _srch.value = '';
  // Initialize reasoning chip on boot (fixes #1103 — chip hidden until session load)
  if(typeof fetchReasoningChip==='function') fetchReasoningChip();
  if(saved&&!_bootRestoreCanceled()){
    try{
      if(!urlSession&&savedLocal&&!_bootRestoreCanceled()&&await _savedSessionShouldStaySidebarOnly(savedLocal)){
        if(_bootRestoreCanceled()) throw new Error('boot session restore canceled');
        S.session=null; S.messages=[]; S.activeStreamId=null; S.busy=false;
        S._bootReady=true;
        syncTopbar();syncWorkspacePanelState();
        $('emptyState').style.display='';
        await renderSessionList();if(typeof startGatewaySSE==='function')startGatewaySSE();
        return;
      }
      if(_bootRestoreCanceled()) throw new Error('boot session restore canceled');
      if (_bootSavedSessionLoadPromise) {
        const _bootLoadResult = await _bootSavedSessionLoadPromise;
        _bootMissingSession = !!(_bootLoadResult && _bootLoadResult.missingSession);
      }
      else if(!_bootRestoreCanceled()) {
        const _bootLoadResult = await loadSession(saved);
        _bootMissingSession = !!(_bootLoadResult && _bootLoadResult.missingSession);
      }
      if(_bootRestoreCanceled()) throw new Error('boot session restore canceled');
      if (saved && (!_bootSavedSessionLoadPromise || !S.session || S.session.session_id !== saved || !Array.isArray(S.messages) || !S.messages.length)) {
        const _bootRetryResult = await loadSession(saved, { expectedSpace: urlWorkspace || '', suppressMissingSessionMessage: true }).catch(() => {});
        _bootMissingSession = _bootMissingSession || !!(_bootRetryResult && _bootRetryResult.missingSession);
      }
      if (_bootMissingSession && urlSession && saved) {
        if (typeof newSession === 'function') {
          await newSession();
          if (typeof showToast === 'function') {
            showToast('Previous session was missing. Started a new one.', 3000, 'info');
          }
          if (typeof renderSessionList === 'function') await renderSessionList();
          if (typeof startGatewaySSE === 'function') startGatewaySSE();
          return;
        }
      }
      // If the restored session has no messages it is an ephemeral scratch pad —
      // treat the page as a fresh start rather than resuming a blank conversation.
      // loadSession() already ran, so loadDir() has populated the workspace file tree.
      // Do NOT remove the session ID from localStorage — keeping it means every
      // subsequent refresh will also run loadSession() → loadDir() → files stay visible.
      // Removing it here caused the file tree to go blank on the second refresh
      // because the "no saved session" path never calls loadDir (#workspace-files).
      const _restoredInFlight = S.session && (
        S.session.active_stream_id ||
        S.session.pending_user_message
      );
      if(S.session && (S.session.message_count||0) === 0 && !_restoredInFlight){
        S.session=null; S.messages=[];
        S._bootReady=true;
        // Restore panel pref before syncing so the workspace panel stays visible
        // even though there is no active session (#workspace-persist).
        const _ephPanelPref=localStorage.getItem('sidekick-webui-workspace-panel-pref')==='open'
          || localStorage.getItem('sidekick-webui-workspace-panel')==='open';
        if(_ephPanelPref) _workspacePanelMode='browse';
        // Sync file tree panel state in chat layout
        _applyFileTreePanelPref();
        syncTopbar();syncWorkspacePanelState();
        $('emptyState').style.display='';
        await renderSessionList();if(typeof startGatewaySSE==='function')startGatewaySSE();
        return;
      }
      // Restore the panel from localStorage when the session has a workspace.
      // Preference key takes priority over runtime state so that closing
      // the panel via toolbar X doesn't suppress the "keep open" setting.
      const panelPref=localStorage.getItem('sidekick-webui-workspace-panel-pref')==='open'
        || localStorage.getItem('sidekick-webui-workspace-panel')==='open';
      if(S.session&&S.session.workspace&&panelPref){
        _workspacePanelMode='browse';
      }
      // Sync file tree panel state in chat layout
      _applyFileTreePanelPref();
      S._bootReady=true;
      syncTopbar();syncWorkspacePanelState();await renderSessionList();if(typeof startGatewaySSE==='function')startGatewaySSE();await checkInflightOnBoot(saved);return;}
    catch(e){if(!_bootRestoreCanceled()) localStorage.removeItem('sidekick-webui-session');}
  }
  // no saved session - show empty state, wait for user to hit +
  S._bootReady=true;
  syncTopbar();
  // Restore panel pref so the workspace panel stays visible on a fresh load if the
  // user had it open during their last session (#workspace-persist).
  const _freshPanelPref=localStorage.getItem('sidekick-webui-workspace-panel-pref')==='open'
    || localStorage.getItem('sidekick-webui-workspace-panel')==='open';
  if(_freshPanelPref) _workspacePanelMode='browse';
  // Sync file tree panel state in chat layout
  _applyFileTreePanelPref();
  syncWorkspacePanelState();
  $('emptyState').style.display='';
  await renderSessionList();
  // Start real-time gateway session sync if setting is enabled
  if(typeof startGatewaySSE==='function') startGatewaySSE();
  // Init the composer mode chips from loaded settings
  if(typeof updateComposerModeChips==='function') updateComposerModeChips();
  // Init the right-panel split handle (Codex-style split pane)
  if(typeof initSplitPane==='function') initSplitPane();
  // Init mode toggle from loaded settings
  if(typeof syncComposerModeButtons==='function') syncComposerModeButtons(window._composerMode||'action');
  // Init chat/code mode toggle from localStorage
  const savedChatMode=localStorage.getItem('sidekick-webui-chat-mode');
  if(savedChatMode&&typeof setChatMode==='function') setChatMode(savedChatMode);
  // Init terminal pane vertical resize
  if(typeof _initChatVerticalResize==='function') _initChatVerticalResize();
  if(typeof initDragDrop==='function') initDragDrop();
  if(typeof _initScrollDetection==='function') _initScrollDetection();
  // ── Start global cross-session approval polling ──
  // Polls all sessions for pending approvals every 3s so badges and toasts
  // appear even when the user is on a different session/space/panel.
  if(typeof _startGlobalApprovalPoll==='function') _startGlobalApprovalPoll();
  // Init sandbox toggle from localStorage
  const _savedSandboxDisabled = localStorage.getItem('sidekick-sandbox-disabled');
  window._sandboxDisabled = _savedSandboxDisabled === 'true';
  const _sandboxToggle = document.getElementById('sandboxToggle');
  if (_sandboxToggle) {
    _sandboxToggle.checked = !window._sandboxDisabled;
    _sandboxToggle.addEventListener('change', function() {
      window._sandboxDisabled = !this.checked;
      localStorage.setItem('sidekick-sandbox-disabled', window._sandboxDisabled ? 'true' : 'false');
      const _label = document.getElementById('sandboxToggleLabel');
      if (_label) {
        const _icon = _label.querySelector('.sandbox-toggle-icon');
        if (_icon) _icon.textContent = window._sandboxDisabled ? '🚫' : '🛡️';
        _label.title = window._sandboxDisabled
          ? 'Sandbox deaktiviert - Agent kann auf alle Dateien zugreifen'
          : 'Sandbox-Einschränkung aktiv';
      }
    });
  }
})().catch(e=>{
  console.error('[hermes] boot failed', e);
  try{S._bootReady=true;}catch(_){}
  try{syncTopbar();}catch(_){}
  try{syncWorkspacePanelState();}catch(_){}
  try{$('emptyState').style.display='';}catch(_){}
  try{if(typeof renderSessionList==='function') void renderSessionList();}catch(_){}
});

// Fix #822 (bfcache path): when the browser restores the page from the
// back-forward cache, the async boot IIFE above does NOT re-run, but the
// DOM — including any stale value in #sessionSearch — IS restored.  A
// prior search string would silently hide all sessions via the filter in
// renderSessionListFromCache().  Clear the field and re-run the full layout
// sync whenever the page is restored from cache (`event.persisted === true`).
// Fix #1045: also re-run topbar/workspace/panel state so the rail and layout
// chrome aren't left in the stale bfcache snapshot.
window.addEventListener('pageshow', async (event) => {
  if (!event.persisted) return;  // fresh loads are handled by the IIFE above
  const _srch = document.getElementById('sessionSearch');
  if (_srch) _srch.value = '';
  // Close any dropdowns/popovers that were open when the user navigated away.
  // bfcache freezes DOM state, so a dropdown left open remains open on restore.
  if (typeof closeModelDropdown === 'function') try { closeModelDropdown(); } catch (_) {}
  if (typeof closeReasoningDropdown === 'function') try { closeReasoningDropdown(); } catch (_) {}
  if (typeof closeWsDropdown === 'function') try { closeWsDropdown(); } catch (_) {}
  if (typeof closeProfileDropdown === 'function') try { closeProfileDropdown(); } catch (_) {}
  // BFCache restores the frozen DOM without rerunning boot. Refresh the active
  // session through the normal load path so in-flight sessions with
  // active_stream_id / pending_user_message can reattach like a reload restore.
  if (S.session && S.session.session_id && typeof loadSession === 'function') {
    try {
      _setConversationRestorePlaceholder('Restoring conversation...');
      await loadSession(S.session.session_id);
      if (S.session && S.session.session_id && typeof checkInflightOnBoot === 'function') {
        try { await checkInflightOnBoot(S.session.session_id); } catch (_) {}
      }
      if (typeof renderMessages === 'function') {
        try { renderMessages({preserveScroll:true}); } catch (_) {}
      }
      if (typeof browserSyncToCurrentSession === 'function') {
        try { browserSyncToCurrentSession({force:true, allowPending:true}); } catch (_) {}
      }
    } catch (_) {}
  }
  // Re-synchronise layout chrome that the boot IIFE sets up but bfcache
  // doesn't re-run. Each call is guarded so missing helpers degrade silently.
  if (typeof syncTopbar === 'function') try { syncTopbar(); } catch (_) {}
  if (typeof syncWorkspacePanelState === 'function') try { syncWorkspacePanelState(); } catch (_) {}
  if (typeof renderSessionListFromCache === 'function') {
    try { renderSessionListFromCache(); } catch (_) {}
  }
  // Restart the gateway SSE watcher — the persisted connection is dead after bfcache
  if (typeof startGatewaySSE === 'function') try { startGatewaySSE(); } catch (_) {}
  // Re-sync primary rail expanded state from localStorage. bfcache restored the
  // frozen DOM but another tab may have toggled the rail in the meantime.
  if (typeof _isSidebarCollapsed === 'function' && typeof toggleSidebar === 'function') {
    try {
      const _wantExpanded = localStorage.getItem('sidekick-webui-rail-expanded') === '1';
      const _haveExpanded = !_isSidebarCollapsed();
      if (_wantExpanded !== _haveExpanded) toggleSidebar(!_wantExpanded);
      if (typeof _syncSidebarAria === 'function') _syncSidebarAria();
    } catch (_) {}
  }
});

/* ── Conversation search (Find in Chat) ── */
function openChatSearch(){
  const bar=$('chatSearchBar');
  if(!bar)return;
  bar.hidden=false;
  const input=$('chatSearchInput');
  if(input){
    input.value='';
    input.focus();
  }
  $('chatSearchCount').textContent='';
  $('chatSearchPrev').disabled=true;
  $('chatSearchNext').disabled=true;
  // Remove any previous search state
  document.querySelectorAll('.msg-row.msg-search-hidden, .msg-row.msg-search-current-match').forEach(el=>{
    el.classList.remove('msg-search-hidden','msg-search-current-match');
  });
}

function closeChatSearch(){
  const bar=$('chatSearchBar');
  if(!bar)return;
  bar.hidden=true;
  const input=$('chatSearchInput');
  if(input) input.value='';
  $('chatSearchCount').textContent='';
  document.querySelectorAll('.msg-row.msg-search-hidden, .msg-row.msg-search-current-match').forEach(el=>{
    el.classList.remove('msg-search-hidden','msg-search-current-match');
  });
  // Focus back on the composer
  $('msg')&&$('msg').focus();
}

function onChatSearchInput(){
  const query=($('chatSearchInput')||{}).value||'';
  const rows=Array.from(document.querySelectorAll('#msgInner > .msg-row'));
  const count=$('chatSearchCount');
  const prevBtn=$('chatSearchPrev');
  const nextBtn=$('chatSearchNext');

  // Clear previous highlights
  document.querySelectorAll('.msg-row.msg-search-hidden, .msg-row.msg-search-current-match').forEach(el=>{
    el.classList.remove('msg-search-hidden','msg-search-current-match');
  });

  if(!query||!rows.length){
    count.textContent='';
    prevBtn.disabled=true;
    nextBtn.disabled=true;
    return;
  }

  const lower=query.toLowerCase();
  let matchCount=0;
  for(const row of rows){
    const text=(row.textContent||'').toLowerCase();
    if(text.includes(lower)){
      row.classList.remove('msg-search-hidden');
      matchCount++;
    }else{
      row.classList.add('msg-search-hidden');
    }
  }

  count.textContent=matchCount>0 ? `1/${matchCount}` : '0/0';
  prevBtn.disabled=matchCount===0;
  nextBtn.disabled=matchCount===0;

  // Highlight first match
  if(matchCount>0){
    const firstMatch=rows.find(r=>!r.classList.contains('msg-search-hidden'));
    if(firstMatch){
      firstMatch.classList.add('msg-search-current-match');
      firstMatch.scrollIntoView({behavior:'smooth',block:'center'});
    }
  }
}

// Track the current match index
let _chatSearchMatchIdx=0;

function _refreshChatSearchMatchIdx(rows, countEl){
  const visible=rows.filter(r=>!r.classList.contains('msg-search-hidden'));
  if(!visible.length){_chatSearchMatchIdx=0;return;}
  // Find which visible row is currently highlighted
  const currentIdx=visible.findIndex(r=>r.classList.contains('msg-search-current-match'));
  if(currentIdx>=0){
    _chatSearchMatchIdx=currentIdx;
  }else{
    _chatSearchMatchIdx=0;
  }
}

function onChatSearchNav(direction){
  const rows=Array.from(document.querySelectorAll('#msgInner > .msg-row'));
  const visible=rows.filter(r=>!r.classList.contains('msg-search-hidden'));
  const countEl=$('chatSearchCount');
  if(!visible.length)return;

  // Remove current highlight
  rows.forEach(r=>r.classList.remove('msg-search-current-match'));

  // Calculate new index
  _refreshChatSearchMatchIdx(rows, countEl);
  let newIdx=_chatSearchMatchIdx+direction;
  if(newIdx<0) newIdx=visible.length-1;
  if(newIdx>=visible.length) newIdx=0;
  _chatSearchMatchIdx=newIdx;

  // Highlight and scroll to new match
  const target=visible[newIdx];
  target.classList.add('msg-search-current-match');
  target.scrollIntoView({behavior:'smooth',block:'center'});

  // Update count
  countEl.textContent=`${newIdx+1}/${visible.length}`;
}

// ─── Shutdown / Reboot ─────────────────────────────────────────────────────
function confirmShutdown() {
  if (!confirm('⚠️ Sidekick Server herunterfahren?\n\nBrowser und Server werden geschlossen.')) return;
  const btn = document.getElementById('btnShutdownSidekick');
  btn.disabled = true;
  btn.style.opacity = '0.5';
  api('/api/system/shutdown').catch(function(){});
  setTimeout(function(){ window.close(); }, 1200);
}
function confirmReboot() {
  if (!confirm('⚠️ Sidekick Server neustarten?\n\nBrowser schliessen, Cache löschen, Server+Browser neu starten.')) return;
  const btn = document.getElementById('btnRebootSidekick');
  btn.disabled = true;
  btn.style.opacity = '0.5';
  api('/api/system/restart').catch(function(){});
  setTimeout(function(){ window.close(); }, 1200);
}
