function _activeWorkspaceSlug() {
  try {
    if (typeof getActiveSpaceQuery === 'function') {
      const qs = String(getActiveSpaceQuery() || '');
      const params = new URLSearchParams(qs.replace(/^\?/, ''));
      const slug = (params.get('workspace') || '').trim();
      if (slug) return slug;
    }
  } catch (_) {}
  try {
    const fallback = (localStorage.getItem('sidekick-active-workspace') || '').trim();
    if (fallback) return fallback;
  } catch (_) {}
  return 'default';
}

function _shouldAttachWorkspaceHeader(urlObj) {
  try {
    const path = String(urlObj && urlObj.pathname || '');
    return path.startsWith('/api/');
  } catch (_) {
    return false;
  }
}

function _dashboardSessionToken() {
  try {
    if (typeof window.__SIDEKICK_SESSION_TOKEN__ === 'string' && window.__SIDEKICK_SESSION_TOKEN__) {
      return window.__SIDEKICK_SESSION_TOKEN__;
    }
  } catch (_) {}
  return '';
}

function _headersWithWorkspace(existing, urlObj, options={}) {
  const headers = new Headers(existing || {});
  if (options.defaultJson !== false && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const isDashboardApi = _shouldAttachWorkspaceHeader(urlObj);
  if (isDashboardApi && !headers.has('X-Sidekick-Workspace')) {
    const slug = _activeWorkspaceSlug();
    if (slug) headers.set('X-Sidekick-Workspace', slug);
  }
  if (isDashboardApi && !headers.has('X-Sidekick-Session-Token')) {
    const token = _dashboardSessionToken();
    if (token) headers.set('X-Sidekick-Session-Token', token);
  }
  return headers;
}

(function _installDashboardFetchAuth() {
  if (window.__SIDEKICK_FETCH_AUTH_INSTALLED__) return;
  window.__SIDEKICK_FETCH_AUTH_INSTALLED__ = true;
  const originalFetch = window.fetch.bind(window);
  window.fetch = function(input, init) {
    try {
      const rawUrl = input instanceof Request ? input.url : String(input);
      const url = new URL(rawUrl, document.baseURI || location.href);
      if (_shouldAttachWorkspaceHeader(url)) {
        const nextInit = Object.assign({}, init || {});
        const sourceHeaders = nextInit.headers || (input instanceof Request ? input.headers : undefined);
        nextInit.headers = _headersWithWorkspace(sourceHeaders, url, {defaultJson:false});
        if (input instanceof Request) return originalFetch(new Request(input, nextInit));
        return originalFetch(input, nextInit);
      }
    } catch (_) {}
    return originalFetch(input, init);
  };
})();

async function api(path,opts={}){
  // Strip leading slash so URL resolves relative to location.href (supports subpath mounts)
  const rel = path.startsWith('/') ? path.slice(1) : path;
  const url=new URL(rel,document.baseURI||location.href);
  const fetchOpts=Object.assign({},opts||{});
  const logApiError=fetchOpts.logError!==false;
  delete fetchOpts.logError;
  // Retry up to 2 times on network errors (e.g. stale keep-alive after long idle).
  // Server errors (4xx/5xx) are NOT retried — only connection failures.
  let lastErr;
  for(let attempt=0;attempt<3;attempt++){
    try{
      const headers = _headersWithWorkspace(fetchOpts.headers, url, {defaultJson:true});
      const res=await fetch(url.href,{credentials:'include',...fetchOpts,headers});
      if(!res.ok){
        // 401 means the auth session expired. Redirect to login so the user can
        // re-authenticate. This is especially important for iOS PWA (standalone mode)
        // and for subpath mounts like /sidekick/, where /login escapes to the site root.
        if(res.status===401){
          const hasDashboardToken = !!_dashboardSessionToken();
          const onLoginPage = /\/login\/?$/.test(window.location.pathname);
          if(!hasDashboardToken && !onLoginPage){
            window.location.href='login?next='+encodeURIComponent(window.location.pathname+window.location.search);
          }
        }
        const text=await res.text();
        // Parse JSON error body and surface the human-readable message,
        // rather than showing raw JSON like {"error":"Profile 'x' does not exist."}
        let message=text;
        let data=null;
        try{const j=JSON.parse(text);message=j.detail||j.message||j.error||text;}catch(e){}
        try{data=JSON.parse(text);}catch(_){}
        if(data&&typeof data==='object'){
          const errorValue=data.error;
          if(typeof errorValue==='string'){
            message=data.message||errorValue; // prefer human message over code
          }else if(errorValue&&typeof errorValue==='object'){
            message=errorValue.message||errorValue.code||data.message||text;
          }else{
            message=data.message||text;
          }
        }
        // Attach the raw HTTP context so callers can branch on status (404 stale-session
        // cleanup, 401 redirect, 503 retry, etc.) without re-parsing the message string.
        const err=new Error(message);
        err.status=res.status;
        err.statusText=res.statusText;
        err.body=text;
        err.data=data;

        // Auto-log failed API calls (but skip error-logging endpoints to avoid loops)
        const isExpectedGameModeBlock=res.status===409&&data&&data.error&&data.error.code==='game_mode_enabled';
        if(logApiError&&!isExpectedGameModeBlock&&!path.startsWith('api/errors/') && !path.startsWith('/api/errors/')){
          try{
            var _xhr=new XMLHttpRequest();
            _xhr.open('POST','api/errors/log',true);
            _xhr.setRequestHeader('Content-Type','application/json');
            var _token=_dashboardSessionToken();
            if(_token)_xhr.setRequestHeader('X-Sidekick-Session-Token',_token);
            _xhr.send(JSON.stringify({
              type:'api_error',
              message:String(message).slice(0,4000),
              stack:err.stack||'',
              path:window.location.pathname,
              status:res.status,
              method:(fetchOpts.method||'GET').toUpperCase(),
              body:String(text).slice(0,4000),
              url:path,
              meta:{attempt:attempt+1}
            }));
          }catch(_e){}
        }

        throw err;
      }
      const ct=res.headers.get('content-type')||'';
      return ct.includes('application/json')?res.json():res.text();
    }catch(e){
      lastErr=e;
      // Only retry on network errors (TypeError from fetch), not on HTTP errors
      // that were already thrown above. Re-throw 401 redirects immediately.
      if(e.message&&/401/.test(e.message)) throw e;
      if(attempt<2 && e instanceof TypeError) continue;
      throw e;
    }
  }
  throw lastErr;
}

// Persist/restore expanded directory state per workspace in localStorage
let _loadDirRev = 0;
const _LOAD_DIR_TIMEOUT_MS = 8000;
const _MAX_EXPANDED_DIR_PREFETCH = 16;

async function _apiWithTimeout(path, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs || _LOAD_DIR_TIMEOUT_MS);
  try {
    return await api(path, { signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

window._workspaceApiWithTimeout = window._workspaceApiWithTimeout || _apiWithTimeout;

function _isCurrentLoadDir(loadRev, sessionId) {
  return loadRev === _loadDirRev && S.session && S.session.session_id === sessionId;
}

function _wsExpandKey(){
  const ws=S.session&&S.session.workspace;
  return ws?'sidekick-webui-expanded:'+ws:null;
}
function _saveExpandedDirs(){
  const key=_wsExpandKey();if(!key)return;
  try{localStorage.setItem(key,JSON.stringify([...(S._expandedDirs||new Set())]));}catch(e){}
}
function _restoreExpandedDirs(){
  const key=_wsExpandKey();
  if(!key){S._expandedDirs=new Set();return;}
  try{
    const raw=localStorage.getItem(key);
    S._expandedDirs=raw?new Set(JSON.parse(raw)):new Set();
  }catch(e){S._expandedDirs=new Set();}
}

async function loadDir(path){
  if(!S.session)return;
  const loadRev = ++_loadDirRev;
  const sessionId = S.session.session_id;
  try{
    if(!path||path==='.'){
      S._dirCache={};
      _restoreExpandedDirs();  // restore per-workspace expanded state on root load
    }
    S.currentDir=path||'.';
    const data=await _apiWithTimeout(`/api/list?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}`, _LOAD_DIR_TIMEOUT_MS);
    if(!_isCurrentLoadDir(loadRev, sessionId)) return;
    S.entries=data.entries||[];renderBreadcrumb();renderFileTree();
    // Pre-fetch contents of restored expanded dirs so they render without a second click
    // (parallelized — avoids serial waterfall when multiple dirs are expanded)
    if(!path||path==='.'){
      const expanded=S._expandedDirs||new Set();
      const pending=[...expanded].filter(dirPath=>!S._dirCache[dirPath]).slice(0, _MAX_EXPANDED_DIR_PREFETCH);
      if(pending.length){
        const results=await Promise.all(pending.map(dirPath=>
          _apiWithTimeout(`/api/list?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(dirPath)}`, _LOAD_DIR_TIMEOUT_MS)
            .then(dc=>({dirPath,entries:dc.entries||[]}))
            .catch(()=>({dirPath,entries:[]}))
        ));
        if(!_isCurrentLoadDir(loadRev, sessionId)) return;
        for(const {dirPath,entries} of results) S._dirCache[dirPath]=entries;
      }
      if(expanded.size>0)renderFileTree();
    }
    if(typeof clearPreview==='function'){
      if(typeof _previewDirty!=='undefined'&&_previewDirty){
        showConfirmDialog({title:t('unsaved_confirm'),message:'',confirmLabel:'Discard',danger:true,focusCancel:true}).then(ok=>{if(ok)clearPreview({keepPanelOpen:true});});
      }else{
        clearPreview({keepPanelOpen:true});
      }
    }
    // Fetch git info for workspace root (non-blocking)
    if(!path||path==='.') _refreshGitBadge();
  }catch(e){
    const currentSessionId = S.session && S.session.session_id;
    const msg = String((e && e.message) || '');
    if (
      (e && e.name === 'AbortError') ||
      (currentSessionId && currentSessionId !== sessionId) ||
      (e && e.status === 404 && /session not found/i.test(msg))
    ) {
      return;
    }
    const emptyEl = $('wsEmptyState');
    const box = $('fileTree');
    if (emptyEl) {
      const detail = msg ? ` ${msg}` : '';
      emptyEl.textContent = (typeof t === 'function' ? t('workspace_load_failed') : 'Could not load this workspace.') + detail;
      emptyEl.style.display = 'flex';
    }
    if (box) {
      box.innerHTML = '';
      box.style.display = 'none';
    }
    console.warn('loadDir',e);
  }
}

async function _refreshGitBadge(){
  const badges=[$('gitBadge'),$('composerGitBadge')].filter(Boolean);
  if(!badges.length||!S.session)return;
  try{
    const data=await api(`/api/git-info?session_id=${encodeURIComponent(S.session.session_id)}`);
    if(data.git&&data.git.is_git){
      const g=data.git;
      let text=g.branch||'git';
      if(g.dirty>0) text+=` \u00b7 ${g.dirty}\u2206`; // middot + delta
      if(g.behind>0) text+=` \u2193${g.behind}`;
      if(g.ahead>0) text+=` \u2191${g.ahead}`;
      badges.forEach(badge=>{
        badge.textContent=text;
        badge.className='git-badge'+(g.dirty>0?' dirty':'');
        badge.style.display='';
      });
    } else {
      badges.forEach(badge=>{
        badge.style.display='none';
        badge.textContent='';
      });
    }
  }catch(e){badges.forEach(badge=>{badge.style.display='none';});}
}

function navigateUp(){
  if(!S.session||S.currentDir==='.')return;
  const parts=S.currentDir.split('/');
  parts.pop();
  loadDir(parts.length?parts.join('/'):'.');
}

// File extension sets for preview routing (must match server-side sets)
const IMAGE_EXTS  = new Set(['.png','.jpg','.jpeg','.gif','.svg','.webp','.ico','.bmp']);
const MD_EXTS     = new Set(['.md','.markdown','.mdown']);
const HTML_EXTS   = new Set(['.html','.htm']);
const PDF_EXTS    = new Set(['.pdf']);
const AUDIO_EXTS  = new Set(['.mp3','.wav','.m4a','.aac','.ogg','.oga','.opus','.flac']);
const VIDEO_EXTS  = new Set(['.mp4','.mov','.m4v','.webm','.ogv','.avi','.mkv']);
// Binary formats that should download rather than preview
const DOWNLOAD_EXTS = new Set([
  '.docx','.doc','.xlsx','.xls','.pptx','.ppt','.odt','.ods','.odp',
  '.zip','.tar','.gz','.bz2','.7z','.rar',
  '.exe','.dmg','.pkg','.deb','.rpm',
  '.woff','.woff2','.ttf','.otf','.eot',
  '.bin','.dat','.db','.sqlite','.pyc','.class','.so','.dylib','.dll',
]);

function fileExt(p){ const i=p.lastIndexOf('.'); return i>=0?p.slice(i).toLowerCase():''; }

let _previewCurrentPath = '';  // relative path of currently previewed file
let _previewCurrentMode = '';  // 'code' | 'md' | 'image' | 'html' | 'pdf' | 'audio' | 'video'
let _previewDirty = false;     // true when edits are unsaved

function showPreview(mode){
  // mode: 'code' | 'image' | 'md' | 'html' | 'pdf' | 'audio' | 'video'
  $('previewCode').style.display     = mode==='code'  ? '' : 'none';
  $('previewImgWrap').style.display  = mode==='image' ? '' : 'none';
  const mediaWrap=$('previewMediaWrap'); if(mediaWrap) mediaWrap.style.display = (mode==='audio'||mode==='video') ? '' : 'none';
  const pdfWrap=$('previewPdfWrap'); if(pdfWrap) pdfWrap.style.display = mode==='pdf' ? '' : 'none';
  $('previewMd').style.display       = mode==='md'    ? '' : 'none';
  $('previewHtmlWrap').style.display = mode==='html'  ? '' : 'none';
  $('previewEditArea').style.display = 'none';  // start in read-only
  const badge=$('previewBadge');
  badge.className='preview-badge '+mode;
  badge.textContent = mode==='image'?'image':mode==='audio'?'audio':mode==='video'?'video':mode==='pdf'?'pdf':mode==='md'?'md':mode==='html'?'html':fileExt($('previewPathText').textContent)||'text';
  _previewCurrentMode = mode;
  _previewDirty = false;
  updateEditBtn();
  // Show "Open in browser" button for iframe-backed document previews
  const openBtn=$('btnOpenInBrowser');
  if(openBtn) openBtn.style.display = (mode==='html'||mode==='pdf')?'inline-flex':'none';
}

function updateEditBtn(){
  const btn=$('btnEditFile');
  if(!btn)return;
  const editable = _previewCurrentMode==='code'||_previewCurrentMode==='md';
  btn.style.display = editable?'':'none';
  const editing = $('previewEditArea').style.display!=='none';
  btn.innerHTML = editing ? `&#128190; ${t('save')}` : `&#9998; ${t('edit')}`;
  btn.title = editing ? t('save_title') : t('edit_title');
  btn.style.color = editing ? 'var(--blue)' : '';
  if(_previewDirty) btn.innerHTML = '&#128190; Save*';
}

async function toggleEditMode(){
  const editing = $('previewEditArea').style.display!=='none';
  if(editing){
    // Save
    if(!S.session||!_previewCurrentPath)return;
    const content=$('previewEditArea').value;
    try{
      await api('/api/file/save',{method:'POST',body:JSON.stringify({
        session_id:S.session.session_id, path:_previewCurrentPath, content
      })});
      _previewDirty=false;
      // Update read-only views with proper syntax highlighting
      if(_previewCurrentMode==='code') _highlightPreviewCode(_previewCurrentPath, content);
      else { $('previewMd').innerHTML=renderMd(content); requestAnimationFrame(()=>{if(typeof renderKatexBlocks==='function')renderKatexBlocks();}); }
      $('previewEditArea').style.display='none';
      if(_previewCurrentMode==='code') $('previewCode').style.display='';
      else $('previewMd').style.display='';
      showToast(t('saved'));
    }catch(e){setStatus(t('save_failed')+e.message);}
  }else{
    // Enter edit mode: populate textarea with current content
    const currentText = _previewCurrentMode==='code'
      ? $('previewCode').textContent
      : _previewRawContent||'';
    $('previewEditArea').value=currentText;
    $('previewEditArea').style.display='';
    if(_previewCurrentMode==='code') $('previewCode').style.display='none';
    else $('previewMd').style.display='none';
    // Escape cancels the edit without saving
    $('previewEditArea').onkeydown=e=>{
      if(e.key==='Escape'){e.preventDefault();cancelEditMode();}
    };
  }
  updateEditBtn();
}

let _previewRawContent = '';  // raw text for md files (to populate editor)

function cancelEditMode(){
  // Discard changes and return to read-only view
  $('previewEditArea').style.display='none';
  $('previewEditArea').onkeydown=null;
  if(_previewCurrentMode==='code') $('previewCode').style.display='';
  else $('previewMd').style.display='';
  _previewDirty=false;
  updateEditBtn();
}

async function openFile(path){
  // Track in open-files bar: add manually opened files too
  if (typeof _openFilesMap !== 'undefined' && _openFilesMap && !_openFilesMap.has(path)) {
    _openFilesMap.set(path, { path: path, filename: path.split('/').pop() || path });
    if (_openFilesMap.size > 10) {
      const entries = [..._openFilesMap.entries()];
      _openFilesMap = new Map(entries.slice(0, 10));
    }
    if (typeof _renderOpenFilesBar === 'function') _renderOpenFilesBar();
  }
  if(!S.session)return;
  const ext=fileExt(path);

  // Binary/download-only formats: trigger browser download, don't preview
  if(DOWNLOAD_EXTS.has(ext)){
    downloadFile(path);
    return;
  }

  $('previewPathText').textContent=path;
  $('previewArea').classList.add('visible');
  $('fileTree').style.display='none';

  _previewCurrentPath = path;
  renderFileBreadcrumb(path);
  if(IMAGE_EXTS.has(ext)){
    // Image: load via raw endpoint, show as <img>
    showPreview('image');
    const url=`api/file/raw?session_id=${encodeURIComponent(S.session.session_id)}&path=${encodeURIComponent(path)}`;
    $('previewImg').alt=path;
    $('previewImg').src=url;
    $('previewImg').onerror=()=>setStatus(t('image_load_failed'));
  } else if(AUDIO_EXTS.has(ext)||VIDEO_EXTS.has(ext)){
    const mode=VIDEO_EXTS.has(ext)?'video':'audio';
    showPreview(mode);
    const url=`api/file/raw?session_id=${encodeURIComponent(S.session.session_id)}&path=${encodeURIComponent(path)}&inline=1`;
    const wrap=$('previewMediaWrap');
    if(wrap){
      wrap.innerHTML=(typeof _mediaPlayerHtml==='function')
        ? _mediaPlayerHtml(mode,url,path.split('/').pop()||path)
        : `<${mode} src="${url.replace(/"/g,'%22')}" controls preload="metadata"></${mode}>`;
      if(typeof _applyMediaPlaybackPreferences==='function') _applyMediaPlaybackPreferences(wrap);
    }
  } else if(PDF_EXTS.has(ext)){
    showPreview('pdf');
    const url=`api/file/raw?session_id=${encodeURIComponent(S.session.session_id)}&path=${encodeURIComponent(path)}&inline=1`;
    const frame=$('previewPdfFrame');
    if(frame){
      frame.src=''; // clear first to avoid stale content
      frame.src=url;
      frame.title=`PDF preview: ${path.split('/').pop()||path}`;
    }
  } else if(MD_EXTS.has(ext)){
    // Markdown: fetch text, render with renderMd, display as formatted HTML
    try{
      const data=await api(`/api/file?session_id=${encodeURIComponent(S.session.session_id)}&path=${encodeURIComponent(path)}`);
      showPreview('md');
      _previewRawContent = data.content;
      $('previewMd').innerHTML=renderMd(data.content);
      requestAnimationFrame(()=>{if(typeof renderKatexBlocks==='function')renderKatexBlocks();});
    }catch(e){setStatus(t('file_open_failed'));}
  } else if(HTML_EXTS.has(ext)){
    // HTML: render in sandboxed iframe via raw endpoint.
    // SECURITY TRADEOFF: We use sandbox="allow-scripts" which lets inline JS run
    // but prevents access to the parent frame (origin isolation). This is a
    // deliberate choice — the user is previewing their own workspace files, so
    // blocking scripts entirely would break most HTML documents. The sandbox
    // still prevents the preview from navigating the parent, accessing cookies,
    // or reading other origin data. If a stricter mode is needed, remove
    // allow-scripts (or add sandbox="") to disable all JS execution.
    showPreview('html');
    const url=`api/file/raw?session_id=${encodeURIComponent(S.session.session_id)}&path=${encodeURIComponent(path)}&inline=1`;
    const iframe=$('previewHtmlIframe');
    if(iframe){
      iframe.src=''; // clear first to avoid stale content
      iframe.src=url;
    }
  } else {
    // Plain code / text -- but fall back to download if server signals binary
    try{
      const data=await api(`/api/file?session_id=${encodeURIComponent(S.session.session_id)}&path=${encodeURIComponent(path)}`);
      if(data.binary){
        // Server flagged this as binary content
        downloadFile(path);
        return;
      }
      showPreview('code');
      _highlightPreviewCode(path, data.content);
      _addPreviewCopyBtn();
    }catch(e){
      // If it's a 400/too-large error, offer download instead
      downloadFile(path);
    }
  }
}

function downloadFile(path){
  if(!S.session)return;
  // Trigger browser download via the raw file endpoint with content-disposition attachment
  const url=`api/file/raw?session_id=${encodeURIComponent(S.session.session_id)}&path=${encodeURIComponent(path)}&download=1`;
  const filename=path.split('/').pop();
  const a=document.createElement('a');
  a.href=url;a.download=filename;
  document.body.appendChild(a);a.click();
  setTimeout(()=>document.body.removeChild(a),100);
  showToast(t('downloading',filename),2000);
}


// ── Render breadcrumb for file preview mode ──────────────────────────────────
function renderFileBreadcrumb(filePath) {
  const bar = $('breadcrumbBar');
  if (!bar) return;
  bar.style.display = 'flex';
  const upBtn = $('btnUpDir');
  if (upBtn) upBtn.style.display = '';

  bar.innerHTML = '';
  // Root
  const root = document.createElement('span');
  root.className = 'breadcrumb-seg breadcrumb-link';
  root.textContent = '~';
  root.onclick = () => { loadDir('.'); };
  bar.appendChild(root);

  const parts = filePath.split('/');
  let accumulated = '';
  for (let i = 0; i < parts.length; i++) {
    const sep = document.createElement('span');
    sep.className = 'breadcrumb-sep';
    sep.textContent = '/';
    bar.appendChild(sep);

    accumulated += (accumulated ? '/' : '') + parts[i];
    const seg = document.createElement('span');
    seg.textContent = parts[i];
    if (i < parts.length - 1) {
      seg.className = 'breadcrumb-seg breadcrumb-link';
      const target = accumulated;
      seg.onclick = () => { loadDir(target); };
    } else {
      seg.className = 'breadcrumb-seg breadcrumb-current';
    }
    bar.appendChild(seg);
  }
}

function openInBrowser(){
  if(!_previewCurrentPath||!S.session) return;
  const url=`api/file/raw?session_id=${encodeURIComponent(S.session.session_id)}&path=${encodeURIComponent(_previewCurrentPath)}`;
  window.open(url,'_blank');
}

// ── File extension → Prism language map for syntax highlighting ──────────────
const _CODE_LANG_MAP = {
  js:'javascript',   mjs:'javascript',  cjs:'javascript',  es:'javascript',
  ts:'typescript',   tsx:'tsx',
  py:'python',       rb:'ruby',          rs:'rust',         go:'go',
  java:'java',       kt:'kotlin',        scala:'scala',
  cs:'csharp',       fs:'fsharp',
  php:'php',         pl:'perl',          pm:'perl',
  c:'c',             cpp:'cpp',          h:'c',             hpp:'cpp',
  css:'css',         scss:'scss',        less:'less',
  html:'html',       htm:'html',         svg:'svg',
  xml:'xml',         xhtml:'xml',        xsl:'xml',
  json:'json',       jsonc:'json',       yaml:'yaml',       yml:'yaml',
  toml:'toml',       ini:'ini',          cfg:'ini',         env:'ini',
  sh:'bash',         bash:'bash',        zsh:'bash',        fish:'bash',
  ps1:'powershell',  psd1:'powershell',  psm1:'powershell',
  sql:'sql',         graphql:'graphql',  gql:'graphql',
  md:'markdown',     rmd:'markdown',
  dockerfile:'docker',Dockerfile:'docker',
  diff:'diff',       patch:'diff',
  makefile:'makefile',mk:'makefile',
  lua:'lua',         swift:'swift',      r:'r',
  vue:'vue',         svelte:'svelte',
  dart:'dart',       elm:'elm',
  erl:'erlang',      ex:'elixir',        exs:'elixir',
  hs:'haskell',      lhs:'haskell',
  clj:'clojure',     cljs:'clojure',
  cmake:'cmake',     bat:'batch',
  tf:'hcl',          hcl:'hcl',
  nix:'nix',
};

function _detectCodeLang(filePath){
  const i=filePath.lastIndexOf('.'); if(i<0)return '';
  const base=filePath.split('/').pop()||'';
  const lower=base.toLowerCase();
  if(lower==='dockerfile') return 'docker';
  if(lower==='makefile') return 'makefile';
  const ext=base.slice(i).toLowerCase().replace(/^\./,'');
  return _CODE_LANG_MAP[ext]||'';
}

function _highlightPreviewCode(filePath, content){
  const el=$('previewCode');
  if(!el)return;
  const lang=_detectCodeLang(filePath);
  let trimmed=content;
  while(trimmed.endsWith('\n')) trimmed=trimmed.slice(0,-1);
  const classes=['preview-code','line-numbers'];
  if(lang) classes.push('language-'+lang);
  el.className=classes.join(' ');
  let codeEl=el.querySelector('code');
  if(!codeEl){
    codeEl=document.createElement('code');
    el.textContent='';
    el.appendChild(codeEl);
  }
  codeEl.textContent=trimmed;
  if(lang) codeEl.className='language-'+lang;
  if(typeof Prism!=='undefined' && Prism.highlightElement){
    requestAnimationFrame(()=>{
      Prism.highlightElement(codeEl);
    });
  }
}

function _addPreviewCopyBtn(){
  const existing=$('previewCopyBtn');
  if(existing) existing.remove();
  const pathEl=$('previewPath');
  if(!pathEl)return;
  const el=$('previewCode');
  if(!el||!el.textContent)return;
  const codeEl=el.querySelector('code');
  const copyText=codeEl?codeEl.textContent:el.textContent;
  if(!copyText)return;
  const btn=document.createElement('button');
  btn.id='previewCopyBtn';
  btn.className='panel-icon-btn';
  btn.style.cssText='font-size:12px;width:auto;padding:2px 8px;display:inline-flex;align-items:center;gap:4px';
  btn.innerHTML='<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> '+(typeof t==='function'?t('copy'):'Copy');
  btn.onclick=async(e)=>{
    e.stopPropagation();
    try{
      await _copyText(copyText);
      btn.innerHTML='<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg> '+(typeof t==='function'?t('copied'):'Copied!');
      setTimeout(()=>{btn.innerHTML='<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> '+(typeof t==='function'?t('copy'):'Copy');},1500);
    }catch(err){
      btn.textContent='Failed';
      setTimeout(()=>{btn.innerHTML='<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> '+(typeof t==='function'?t('copy'):'Copy');},1500);
    }
  };
  const ref=$('btnEditFile');
  if(ref && ref.parentNode===pathEl){
    pathEl.insertBefore(btn, ref);
  }else{
    pathEl.appendChild(btn);
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// WebUI Error Reporting — catches JS errors and sends them to the backend
// ═══════════════════════════════════════════════════════════════════════════════

(function() {
  'use strict';

  // ── Helper: POST an error record to the backend ──────────────────────────
  function _logError(type, message, extra) {
    extra = extra || {};
    try {
      var payload = JSON.stringify({
        type: type,
        message: String(message || '').slice(0, 4000),
        stack: extra.stack || '',
        url: extra.url || window.location.href,
        line: extra.line || 0,
        col: extra.col || 0,
        path: window.location.pathname,
        status: extra.status || 0,
        method: extra.method || '',
        body: extra.body || '',
        meta: extra.meta || {}
      });
      // Use plain fetch so we don't create an infinite loop via the patched api()
      var xhr = new XMLHttpRequest();
      xhr.open('POST', 'api/errors/log', true);
      xhr.setRequestHeader('Content-Type', 'application/json');
      xhr.send(payload);
    } catch(e) {
      // Silently ignore — we're already in error territory
    }
  }

  // ── 1. Global uncaught JS exceptions ─────────────────────────────────────
  window.onerror = function(msg, url, line, col, err) {
    _logError('js_error', msg, {
      url: url || '',
      line: line || 0,
      col: col || 0,
      stack: err && err.stack ? err.stack : '',
      meta: {
        errorName: err && err.name ? err.name : '',
        event: 'window.onerror'
      }
    });
    return false;  // let default handler run (browser console still shows it)
  };

  // ── 2. Unhandled Promise rejections ──────────────────────────────────────
  window.addEventListener('unhandledrejection', function(e) {
    var reason = e.reason;
    var message = '';
    var stack = '';
    if (reason instanceof Error) {
      message = reason.message;
      stack = reason.stack || '';
    } else if (typeof reason === 'string') {
      message = reason;
    } else if (reason && typeof reason === 'object') {
      message = String(reason.message || reason.error || JSON.stringify(reason).slice(0, 500));
    } else {
      message = String(reason);
    }
    _logError('unhandled_promise', message, {
      stack: stack,
      meta: {
        event: 'unhandledrejection',
        reasonType: typeof reason
      }
    });
  });

  // ── 3. console.error interceptor ─────────────────────────────────────────
  var _origConsoleError = console.error;
  console.error = function() {
    // Forward to original first so the console always shows the error
    try { _origConsoleError.apply(console, arguments); } catch(e) {}

    try {
      var parts = [];
      for (var i = 0; i < arguments.length; i++) {
        var arg = arguments[i];
        if (arg instanceof Error) {
          parts.push(arg.message);
        } else if (typeof arg === 'object') {
          try { parts.push(JSON.stringify(arg).slice(0, 500)); }
          catch(e) { parts.push(String(arg)); }
        } else {
          parts.push(String(arg));
        }
      }
      var message = parts.join(' ').slice(0, 4000);

      // Extract stack from any Error argument
      var stack = '';
      for (var j = 0; j < arguments.length; j++) {
        if (arguments[j] instanceof Error && arguments[j].stack) {
          stack = arguments[j].stack;
          break;
        }
      }
      // Auto-generate stack trace from call site
      if (!stack) {
        try { throw new Error(); } catch(e) { stack = e.stack || ''; }
      }

      _logError('console_error', message, {
        stack: stack,
        meta: { consoleMethod: 'error' }
      });
    } catch(e) {
      // Don't re-enter error handling
    }
  };
})();
