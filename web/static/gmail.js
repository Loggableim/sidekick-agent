/**
 * Gmail Panel — G1: True Gmail Clone
 * Sidebar: Gmail-style navigation (folders, compose button, labels)
 * Main: Email list + Reading Pane + Compose Overlay
 * Rightpanel: AI Superpowers (future)
 *
 * Multi-Account support, animations, loading states, toast notifications.
 */
"use strict";

// ── State ──
const GMAIL = {
  currentFolder: 'INBOX',
  currentFilter: 'all',
  currentAccount: 'dominik',
  accounts: [],
  emails: [],
  loaded: false,
  loading: false,
  pollInterval: null,
};
let _gmailSearchSeq = 0;
let _gmailRefreshSeq = 0;
let _gmailRefreshPending = false;

// Folder display names for nav
const GMAIL_FOLDER_LABELS = {
  'INBOX': 'Posteingang',
  '[Gmail]/Gesendet': 'Gesendet',
  '[Gmail]/Entwürfe': 'Entwürfe',
  '[Gmail]/Spam': 'Spam',
  '[Gmail]/Papierkorb': 'Papierkorb',
};

// ── Helper: add account parameter to URLs ──
function _gmailAccount(url) {
  url = String(url || '').replace(/^\/+/, '');
  const sep = url.includes('?') ? '&' : '?';
  let result = `${url}${sep}account=${encodeURIComponent(GMAIL.currentAccount)}`;
  // Append active workspace slug so Gmail API calls are scoped per space
  if (typeof getActiveSpaceQuery === 'function') {
    const wsQuery = getActiveSpaceQuery(); // "?workspace=<slug>"
    if (wsQuery && wsQuery.length > 1) result += '&' + wsQuery.slice(1); // skip the leading '?'
  }
  return result;
}

function _gmailErrorMessage(e) {
  return String((e && e.message) || e || 'Unbekannter Fehler');
}

function gmailSetEmpty(el, icon, text) {
  if (!el) return;
  el.textContent = '';
  const wrap = document.createElement('div');
  wrap.className = 'gmail-empty';
  const iconEl = document.createElement('div');
  iconEl.className = 'gmail-empty-icon';
  iconEl.textContent = icon || '';
  wrap.appendChild(iconEl);
  wrap.appendChild(document.createTextNode(String(text || '')));
  el.appendChild(wrap);
}

function gmailSetAIPlaceholder(el, text) {
  if (!el) return;
  el.textContent = '';
  const wrap = document.createElement('div');
  wrap.className = 'gmail-ai-placeholder';
  const robot = document.createElement('span');
  robot.className = 'gmail-ai-robot-big';
  robot.textContent = '🤖';
  const msg = document.createElement('span');
  msg.textContent = String(text || '');
  wrap.appendChild(robot);
  wrap.appendChild(msg);
  el.appendChild(wrap);
}

// ── Init ──
function loadGmailPanel() {
  if (!GMAIL.loaded) {
    // Inject CSS once
    if (!document.getElementById('gmailPanelCss')) {
      const link = document.createElement('link');
      link.id = 'gmailPanelCss';
      link.rel = 'stylesheet';
      link.href = 'static/gmail-panel.css?v=__WEBUI_VERSION__';
      document.head.appendChild(link);
    }
    // Start polling when panel loads
    if (GMAIL.pollInterval) { clearInterval(GMAIL.pollInterval); GMAIL.pollInterval = null; }
    GMAIL.pollInterval = setInterval(gmailPollUnread, 30000);
    GMAIL.loaded = true;
  }
  // Always refresh when panel opens
  loadGmailAccounts();
  initGmailSplitResize();
  // Restore saved model in selector
  const modelSel = document.getElementById('gmailAIModelSelect');
  if (modelSel) modelSel.value = _gmailAIModel;
}

function gmailShowUnavailable(message, hint) {
  const msg = message || 'Gmail-Zugriff nicht eingerichtet';
  const sub = hint || 'Gmail konfigurieren, um E-Mails zu laden.';
  const html = `<div class="gmail-empty gmail-empty--setup"><div class="gmail-empty-icon">G</div><div>${escHtml(msg)}</div><div style="font-size:11px;color:var(--muted);margin-top:6px;">${escHtml(sub)}</div></div>`;
  const content = document.getElementById('gmailContent');
  const mainList = document.getElementById('gmailMainList');
  const meta = document.getElementById('gmailMainListMeta');
  const detailScroll = document.getElementById('gmailDetailScroll');
  if (content) content.innerHTML = html;
  if (mainList) mainList.innerHTML = html;
  if (meta) meta.textContent = msg;
  if (detailScroll) detailScroll.innerHTML = '<div class="gmail-empty">Keine E-Mail ausgewählt.</div>';
  if (typeof updateGmailAIPanel === 'function') updateGmailAIPanel(null);
}

// ── Load available accounts ──
async function loadGmailAccounts() {
  try {
    const data = await fetchJson(_gmailAccount('api/gmail/accounts'));
    GMAIL.accounts = data.accounts || [];
    // Check if this space needs Gmail setup
    if (data.needs_setup) {
      gmailShowUnavailable('Gmail-Zugriff nicht eingerichtet', 'Gmail App-Passwort für diesen Space hinterlegen.');
      showGmailSetupDialog();
      return;
    }
    // No global credentials configured — show splash overlay
    if (data.no_credentials) {
      gmailShowUnavailable('Gmail-Zugriff nicht eingerichtet', 'App-Passwort speichern, um E-Mails zu laden.');
      showGmailSplash();
      return;
    }
    // If current account not in list, reset to first
    if (!GMAIL.accounts.find(a => a.id === GMAIL.currentAccount)) {
      GMAIL.currentAccount = GMAIL.accounts[0]?.id || 'dominik';
    }
    // Populate dropdown
    const sel = document.getElementById('gmailAccountSelect');
    if (sel) {
      sel.innerHTML = GMAIL.accounts.map(a =>
        `<option value="${a.id}" ${a.id === GMAIL.currentAccount ? 'selected' : ''}>${a.email}</option>`
      ).join('');
    }
    // Load current folder when accounts ready
    gmailNavSelect(GMAIL.currentFolder);
  } catch (e) {
    console.warn('Could not load Gmail accounts:', e);
  }
}

// ── Switch account ──
function gmailSwitchAccount(accountId) {
  if (accountId === GMAIL.currentAccount) return;
  GMAIL.currentAccount = accountId;
  const sel = document.getElementById('gmailAccountSelect');
  if (sel) sel.value = accountId;
  gmailNavSelect(GMAIL.currentFolder);
}

// ── Navigation: Select a folder (replaces old tabs) ──
function gmailNavSelect(folder) {
  GMAIL.currentFolder = folder;

  // Update nav active state
  document.querySelectorAll('.gmail-nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.folder === folder);
  });

  // Update content
  const list = document.getElementById('gmailMainList');
  if (list) list.innerHTML = `<div class="gmail-loading"><div class="gmail-shimmer"></div><span>📬 Lade ${GMAIL_FOLDER_LABELS[folder] || folder}...</span></div>`;
  
  const meta = document.getElementById('gmailMainListMeta');
  if (meta) meta.textContent = `📬 Lade ${GMAIL_FOLDER_LABELS[folder] || folder}...`;

  // Close reading pane if open
  gmailCloseDetail();

  // Refresh
  gmailRefresh();
}

// ── Toast notification ──
function gmailToast(msg, type = 'info') {
  const existing = document.querySelector('.gmail-toast');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.className = `gmail-toast ${type}`;
  const icons = { success: '✅', error: '❌', info: 'ℹ️' };
  toast.innerHTML = `${icons[type] || 'ℹ️'} ${msg}`;
  document.body.appendChild(toast);
  setTimeout(() => { toast.style.opacity = '0'; toast.style.transition = 'opacity 0.3s'; setTimeout(() => toast.remove(), 300); }, 3000);
}

// ── Format date ──
function gmailFormatDate(dateStr) {
  if (!dateStr) return '';
  try {
    const d = new Date(dateStr);
    // Check for Invalid Date
    if (isNaN(d.getTime())) return dateStr.slice(0, 10);
    const now = new Date();
    const diffDays = Math.floor((now - d) / (1000*60*60*24));
    if (diffDays === 0) return d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
    if (diffDays === 1) return 'Gestern';
    if (diffDays < 7) return d.toLocaleDateString('de-DE', { weekday: 'short' });
    return d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' });
  } catch { return dateStr.slice(0, 10); }
}

// ── Refresh current folder ──
function gmailApplyFilter(emails) {
  const list = Array.isArray(emails) ? emails : [];
  const filter = GMAIL.currentFilter || 'all';
  if (filter === 'unread') return list.filter(email => email && email.seen === false);
  if (filter === 'today') {
    const today = new Date().toDateString();
    return list.filter(email => {
      const raw = email && (email.date_raw || email.date);
      if (!raw) return false;
      const d = new Date(raw);
      return !Number.isNaN(d.getTime()) && d.toDateString() === today;
    });
  }
  return list;
}

async function gmailRefresh() {
  if (GMAIL.loading) {
    _gmailRefreshPending = true;
    return;
  }
  GMAIL.loading = true;
  _gmailRefreshPending = false;
  const refreshSeq = ++_gmailRefreshSeq;
  const requestedFolder = GMAIL.currentFolder;
  const requestedFilter = GMAIL.currentFilter || 'all';

  try {
    const url = _gmailAccount(`api/gmail/list?max=25&folder=${encodeURIComponent(requestedFolder)}`);
    const data = await fetchJson(url);
    if (
      refreshSeq !== _gmailRefreshSeq ||
      requestedFolder !== GMAIL.currentFolder ||
      requestedFilter !== (GMAIL.currentFilter || 'all')
    ) return;
    if (data.error) {
      const mainList = document.getElementById('gmailMainList');
      gmailSetEmpty(mainList, '📭', data.error);
      const sideContent = document.getElementById('gmailContent');
      gmailSetEmpty(sideContent, '📭', data.error);
      return;
    }
    GMAIL.emails = data.emails || [];
    renderInbox({...data, emails: gmailApplyFilter(GMAIL.emails), total: GMAIL.emails.length});
    gmailRefreshNavCounts();
  } catch (e) {
    if (
      refreshSeq !== _gmailRefreshSeq ||
      requestedFolder !== GMAIL.currentFolder ||
      requestedFilter !== (GMAIL.currentFilter || 'all')
    ) return;
    const mainList = document.getElementById('gmailMainList');
    gmailSetEmpty(mainList, '⚠️', 'Verbindungsfehler: ' + _gmailErrorMessage(e));
    const sideContent = document.getElementById('gmailContent');
    gmailSetEmpty(sideContent, '⚠️', 'Verbindungsfehler: ' + _gmailErrorMessage(e));
  } finally {
    GMAIL.loading = false;
    if (_gmailRefreshPending) {
      _gmailRefreshPending = false;
      gmailRefresh();
    }
  }
}

// ── Render inbox list (Main + Sidebar) ──
function renderInbox(data) {
  const emails = data.emails || [];

  // Build email cards HTML
  let listHtml = '';
  for (const email of emails) {
    const sender = email.from_name || email.from || 'Unbekannt';
    const subj = email.subject || '(kein Betreff)';
    const date = gmailFormatDate(email.date_raw || email.date);
    const senderShort = sender.length > 30 ? sender.slice(0, 28) + '…' : sender;
    listHtml += `<div class="gmail-card" data-id="${email.id}" data-read="${email.seen !== false}" onclick="gmailOpenEmail('${email.id}')" title="${sender} · ${subj}">
      <span class="sender">${senderShort}</span>
      <span class="subject">${subj}</span>
      <span class="date">${date}</span>
    </div>`;
  }

  if (emails.length === 0) {
    const empty = `<div class="gmail-empty"><div class="gmail-empty-icon">📨</div>Keine E-Mails in "${GMAIL_FOLDER_LABELS[GMAIL.currentFolder] || GMAIL.currentFolder}"</div>`;
    const mainList = document.getElementById('gmailMainList');
    if (mainList) mainList.innerHTML = empty;
    const sideContent = document.getElementById('gmailContent');
    if (sideContent) sideContent.innerHTML = empty;
    const meta = document.getElementById('gmailMainListMeta');
    if (meta) meta.textContent = '📨 Keine E-Mails';
    return;
  }

  const accountEmail = GMAIL.accounts.find(a => a.id === GMAIL.currentAccount)?.email || GMAIL.currentAccount;
  const folderLabel = GMAIL_FOLDER_LABELS[GMAIL.currentFolder] || GMAIL.currentFolder;
  const metaText = `<span class="count">${data.count || emails.length}</span> E-Mails · ${folderLabel}`;

  // Render to Main area (#gmailMainList)
  const mainList = document.getElementById('gmailMainList');
  if (mainList) {
    mainList.innerHTML = listHtml;
    // Stagger animation
    mainList.querySelectorAll('.gmail-card').forEach((el, i) => {
      el.style.animation = `gmailFadeIn 0.25s ease-out ${i * 0.02}s both`;
    });
  }
  const meta = document.getElementById('gmailMainListMeta');
  if (meta) meta.innerHTML = metaText;

  // Render to Sidebar content area (compact version — first 8 emails)
  const sideContent = document.getElementById('gmailContent');
  if (sideContent) {
    const maxSide = Math.min(emails.length, 8);
    let sideHtml = `<div class="gmail-list-header" style="font-size:11px;padding:6px 10px;">${metaText} · <span style="font-size:10px">${accountEmail}</span></div><div class="gmail-list">`;
    for (let i = 0; i < maxSide; i++) {
      const e = emails[i];
      const sender = e.from_name || e.from || 'Unbekannt';
      const subj = e.subject || '(kein Betreff)';
      const date = gmailFormatDate(e.date_raw || e.date);
      sideHtml += `<div class="gmail-card" style="padding:6px 10px;grid-template-columns:1fr auto;" data-id="${e.id}" onclick="gmailOpenEmail('${e.id}')">
        <span class="sender" style="font-size:11px;max-width:120px;">${sender}</span>
        <span class="date" style="font-size:10px;">${date}</span>
      </div>`;
    }
    if (emails.length > 8) {
      sideHtml += `<div style="padding:6px 10px;font-size:11px;color:var(--muted);text-align:center;">+${emails.length - 8} weitere in der Hauptansicht</div>`;
    }
    sideHtml += '</div>';
    sideContent.innerHTML = sideHtml;
  }
}

// ── Refresh unread counts on nav items ──
async function gmailRefreshNavCounts() {
  const targets = {
    gmailNavCountInbox: { folder: 'INBOX' },
    gmailNavCountDrafts: { folder: '[Gmail]/Entwürfe' },
    gmailNavCountSpam: { folder: '[Gmail]/Spam' },
  };

  for (const [elId, params] of Object.entries(targets)) {
    try {
      const url = _gmailAccount(`api/gmail/list?max=1&folder=${encodeURIComponent(params.folder)}`);
      const data = await fetchJson(url);
      const el = document.getElementById(elId);
      if (el) {
        const count = data.count || 0;
        el.textContent = count > 0 ? count : '';
        el.style.display = count > 0 ? '' : 'none';
      }
    } catch {}
  }
}

// ── Filter chips ──
function gmailSetFilter(filter) {
  GMAIL.currentFilter = filter;
  document.querySelectorAll('.gmail-chip').forEach(c =>
    c.classList.toggle('active', c.dataset.filter === filter));
  gmailRefresh();
}

// ── Open email: rendert Detail im Reading Pane (Main Area) ──
async function gmailOpenEmail(id) {
  const readingPane = document.getElementById('gmailReadingPane');
  const detailScroll = document.getElementById('gmailDetailScroll');
  if (!readingPane || !detailScroll) return;

  // Show reading pane, shrink list
  readingPane.style.display = 'flex';
  gmailRestoreSplitPos();
  const listPane = document.getElementById('gmailMainListPane');
  if (listPane) listPane.classList.add('has-detail');
  const splitHandle = document.getElementById('gmailSplitHandle');
  if (splitHandle) splitHandle.style.display = 'flex';

  // Loading state in reading pane
  detailScroll.innerHTML = `<div class="gmail-loading"><div class="gmail-shimmer"></div><span>📧 Lade E-Mail...</span></div>`;

  try {
    const url = _gmailAccount(`api/gmail/read?id=${encodeURIComponent(id)}`);
    const data = await fetchJson(url);
    if (data.error) {
      gmailSetEmpty(detailScroll, '⚠️', data.error);
      return;
    }
    renderDetailInPane(data, id);
    // Update AI panel in rightpanel
    updateGmailAIPanel(data);
  } catch (e) {
    gmailSetEmpty(detailScroll, '⚠️', 'Fehler: ' + _gmailErrorMessage(e));
  }
}

function renderDetailInPane(data, id) {
  const detailScroll = document.getElementById('gmailDetailScroll');
  const body = data.body || '(kein Inhalt)';
  const isHtml = data.content_type === 'html' && data.body_html;
  const truncated = body.length > 15000 ? body.slice(0, 15000) + '\n\n[... weitergekürzt]' : body;

  let bodyHtml = '';
  if (isHtml && data.body_html) {
    // Sanitize HTML — strip scripts, event handlers, dangerous tags
    const sanitized = gmailSanitizeHtml(data.body_html.slice(0, 30000));
    bodyHtml = `<div class="gmail-email-body">${sanitized}</div>`;
  } else {
    bodyHtml = `<div class="gmail-email-body gmail-email-body--text">${escHtml(truncated)}</div>`;
  }

  detailScroll.innerHTML = `
    <div class="gmail-detail-head">
      <h3 style="margin:0 0 10px;font-size:18px;color:var(--text);line-height:1.3;">${data.subject || '(kein Betreff)'}</h3>
      <div class="gmail-detail-meta" style="display:flex;flex-wrap:wrap;gap:4px 16px;font-size:13px;color:var(--muted);">
        <span>📤 ${data.from || 'Unbekannt'}</span>
        <span>📅 ${data.date || ''}</span>
        ${data.to ? `<span>📥 ${data.to}</span>` : ''}
        ${data.attachments && data.attachments.length ? `<span>📎 ${data.attachments.join(', ')}</span>` : ''}
      </div>
    </div>
    ${bodyHtml}
    <div style="display:flex;gap:8px;padding:10px 0;border-top:1px solid var(--border);margin-top:8px;">
      <button onclick="gmailToggleDetailFullscreen()" style="padding:6px 14px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text);cursor:pointer;font-size:12px;" id="gmailDetailFsBtn">⛶ Vollbild</button>
      <button onclick="gmailDeleteEmail('${id}')" style="padding:6px 14px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:#ef4444;cursor:pointer;font-size:12px;">🗑️ Löschen</button>
      <button onclick="gmailMoveEmail('${id}')" style="padding:6px 14px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text);cursor:pointer;font-size:12px;">📂 Verschieben</button>
      <button onclick="gmailReplyDraft('${id}')" style="padding:6px 14px;border-radius:6px;border:1px solid var(--border);background:var(--accent);color:var(--accent-text);cursor:pointer;font-size:12px;font-weight:600;">✉️ Antworten</button>
    </div>`;
}

// ── Close detail / reading pane ──
function gmailCloseDetail() {
  const readingPane = document.getElementById('gmailReadingPane');
  if (readingPane) readingPane.style.display = 'none';
  const listPane = document.getElementById('gmailMainListPane');
  const splitHandle = document.getElementById('gmailSplitHandle');

  // If we were in fullscreen mode, restore everything
  if (_gmailDetailFullscreen) {
    _gmailDetailFullscreen = false;
    if (listPane) {
      listPane.style.display = '';
      listPane.classList.remove('has-detail');
      listPane.style.flex = '';
    }
    if (splitHandle) splitHandle.style.display = 'none';
    if (readingPane) readingPane.style.flex = '';
    document.querySelectorAll('.gmail-fullscreen-btn, #gmailDetailFsBtn').forEach(b => {
      if (b) b.textContent = '⛶';
    });
  } else {
    // Normal close: just remove has-detail class and inline flex
    if (listPane) {
      listPane.classList.remove('has-detail');
      listPane.style.flex = '';
    }
    if (splitHandle) splitHandle.style.display = 'none';
  }

  // Reset AI panel
  updateGmailAIPanel(null);
}

// ── Reply draft (pre-fill compose) ──
function gmailReplyDraft(id) {
  const email = GMAIL.emails.find(e => e.id === id);
  if (!email) return;
  gmailOpenCompose();
  const toField = document.getElementById('gmailComposeTo');
  const subjField = document.getElementById('gmailComposeSubject');
  if (toField) toField.value = email.from || '';
  if (subjField) subjField.value = email.subject && !email.subject.startsWith('Re:') ? `Re: ${email.subject}` : (email.subject || '');
}

function escHtml(s) {
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

// ── HTML Sanitizer for email rendering ──
function gmailSanitizeHtml(html) {
  // Strip script/style tags and their content
  let clean = html
    .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, '')
    .replace(/<iframe[^>]*>[\s\S]*?<\/iframe>/gi, '')
    .replace(/<object[^>]*>[\s\S]*?<\/object>/gi, '')
    .replace(/<embed[^>]*>[\s\S]*?<\/embed>/gi, '')
    .replace(/on\w+\s*=\s*["'][^"']*["']/gi, '')   // onclick, onload, etc.
    .replace(/on\w+\s*=\s*\S+/gi, '')               // unquoted event handlers
    .replace(/href\s*=\s*["']\s*javascript\s*:[^"']*["']/gi, 'href="#"')
    .replace(/src\s*=\s*["']\s*javascript\s*:[^"']*["']/gi, 'src=""');

  // Parse and re-serialize to strip remaining dangerous attributes
  const parser = new DOMParser();
  const doc = parser.parseFromString(clean, 'text/html');
  const walker = document.createTreeWalker(doc.body, NodeFilter.SHOW_ELEMENT, null, false);
  const dangerousTags = new Set(['script', 'style', 'iframe', 'object', 'embed', 'frame', 'form', 'input']);
  while (walker.nextNode()) {
    const el = walker.currentNode;
    if (dangerousTags.has(el.tagName.toLowerCase())) {
      el.remove();
      continue;
    }
    // Remove event handler attributes
    [...el.attributes].forEach(attr => {
      if (/^on\w+$/i.test(attr.name)) el.removeAttribute(attr.name);
      if ((attr.name === 'href' || attr.name === 'src') && 
          attr.value.trim().toLowerCase().startsWith('javascript:')) {
        el.removeAttribute(attr.name);
      }
    });
  }
  return doc.body.innerHTML;
}

// ── Helper: add active workspace slug to JSON bodies ──
function _gmailWithWorkspace(obj) {
  if (typeof getActiveSpaceQuery === 'function') {
    const params = new URLSearchParams(getActiveSpaceQuery().slice(1));
    const ws = params.get('workspace');
    if (ws) obj.workspace = ws;
  }
  return obj;
}

// ── Gmail Setup Dialog (for non-default spaces without Gmail config) ──
let _gmailSetupEl = null;

function _gmailMainView() {
  return document.getElementById('mainGmail');
}

function _gmailSidebarView() {
  return document.querySelector('#panelGmail .gmail-sidebar') || document.querySelector('.gmail-sidebar');
}

function _gmailSetupHost() {
  return _gmailMainView() || document.getElementById('panelGmail') || document.querySelector('main.main') || document.body;
}

function _setGmailSetupVisible(visible) {
  const container = document.getElementById('gmailSetupContainer');
  if (container) container.style.display = visible ? 'flex' : 'none';
  const sidebar = _gmailSidebarView();
  if (sidebar) {
    sidebar.style.filter = visible ? 'blur(4px)' : '';
    sidebar.style.pointerEvents = visible ? 'none' : '';
  }
}

function showGmailSetupDialog() {
  let container = document.getElementById('gmailSetupContainer');
  if (!container) {
    container = document.createElement('div');
    container.id = 'gmailSetupContainer';
    container.style.cssText = 'position:absolute;inset:0;z-index:120;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100%;padding:2rem;gap:1rem;background:var(--bg);overflow:auto;';
    container.innerHTML = `
      <div style="font-size:2.5rem;">📧</div>
      <h2 style="margin:0;font-size:1.1rem;">Gmail für diesen Space einrichten</h2>
      <p style="color:var(--muted);text-align:center;max-width:360px;margin:0;">
        Dieser Space hat noch kein Gmail-Konto.<br>
        Gib deine Gmail-Daten ein (App-Key erforderlich):
      </p>
      <div style="display:flex;flex-direction:column;gap:0.5rem;width:100%;max-width:360px;">
        <input id="gmailSetupEmail" type="email" placeholder="E-Mail-Adresse" style="padding:0.6rem;border:1px solid var(--border);border-radius:6px;background:var(--bg2);color:var(--fg);">
        <input id="gmailSetupPassword" type="password" placeholder="Google App-Key" style="padding:0.6rem;border:1px solid var(--border);border-radius:6px;background:var(--bg2);color:var(--fg);">
        <p style="font-size:0.8rem;color:var(--muted);margin:0;">
          <a href="https://myaccount.google.com/apppasswords" target="_blank" style="color:var(--accent);">App-Key erstellen</a>
          (benötigt 2FA)
        </p>
        <button id="gmailSetupSaveBtn" onclick="saveGmailSetup()" style="padding:0.7rem;border:none;border-radius:6px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;">Speichern & Gmail laden</button>
        <button onclick="gmailSetupSkip()" style="padding:0.5rem;border:none;border-radius:6px;background:transparent;color:var(--muted);cursor:pointer;">Später einrichten</button>
      </div>
    `;
    _gmailSetupHost().appendChild(container);
  } else {
    container.style.display = 'flex';
    if (!container.parentElement) _gmailSetupHost().appendChild(container);
  }
  _gmailSetupEl = container;
  _setGmailSetupVisible(true);
}

async function saveGmailSetup() {
  const email = document.getElementById('gmailSetupEmail')?.value?.trim();
  const password = document.getElementById('gmailSetupPassword')?.value?.trim();
  if (!email || !password) {
    gmailToast('⚠️ Bitte E-Mail und App-Key eingeben', 'error');
    return;
  }
  const btn = document.getElementById('gmailSetupSaveBtn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Speichere...'; }

  try {
    // Get current workspace slug
    const params = new URLSearchParams((typeof getActiveSpaceQuery === 'function' ? getActiveSpaceQuery() : '').slice(1));
    const slug = params.get('workspace') || window.DEFAULT_SPACE_SLUG || 'nova';

    // Save Gmail config for this space
    const accountName = email.split('@')[0].replace(/[^a-z0-9_]/gi, '_').toLowerCase() || 'gmail';
    await fetchJson('api/space/config', {
      method: 'POST',
      body: JSON.stringify({ slug, gmail: { accounts: { [accountName]: { email, password } } } }),
    });

    _setGmailSetupVisible(false);

    gmailToast('✅ Gmail-Konto gespeichert', 'success');
    // Reload Gmail
    await loadGmailAccounts();
    gmailNavSelect(GMAIL.currentFolder);
  } catch (e) {
    gmailToast('❌ Fehler: ' + e.message, 'error');
    if (btn) { btn.disabled = false; btn.textContent = 'Speichern & Gmail laden'; }
  }
}

function gmailSetupSkip() {
  _setGmailSetupVisible(false);
  gmailShowUnavailable('Gmail-Zugriff nicht eingerichtet', 'Klicke erneut auf Gmail, um den App-Code einzugeben.');
}

// ── Gmail Splash: no credentials prompt ──
function showGmailSplash() {
  gmailShowUnavailable('Gmail-Zugriff nicht eingerichtet', 'App-Passwort speichern, um E-Mails zu laden.');
  // Blur all Gmail content
  const sidebar = document.querySelector('.gmail-sidebar');
  const mainGmail = document.getElementById('mainGmail');
  if (sidebar) sidebar.style.filter = 'blur(8px)';
  if (mainGmail) mainGmail.style.filter = 'blur(8px)';

  // Remove existing splash if any
  const old = document.getElementById('gmailSplashOverlay');
  if (old) old.remove();

  const overlay = document.createElement('div');
  overlay.id = 'gmailSplashOverlay';
  overlay.style.cssText = 'position:absolute;inset:0;z-index:100;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.45);backdrop-filter:blur(2px);';
  overlay.innerHTML = `
    <div style="background:var(--bg);border:1px solid var(--border2);border-radius:16px;padding:32px;max-width:440px;width:90%;box-shadow:0 16px 48px rgba(0,0,0,0.3);display:flex;flex-direction:column;gap:16px;animation:gmailFadeIn 0.3s ease-out;position:relative;">
      <button onclick="gmailCloseSplash()"
              style="position:absolute;top:12px;right:12px;width:32px;height:32px;border:none;background:var(--hover-bg);border-radius:50%;color:var(--muted);cursor:pointer;font-size:16px;display:flex;align-items:center;justify-content:center;transition:background 0.15s,color 0.15s;"
              onmouseover="this.style.background='var(--border)';this.style.color='var(--text)'"
              onmouseout="this.style.background='var(--hover-bg)';this.style.color='var(--muted)'"
              aria-label="Schließen">✕</button>
      <div style="text-align:center;">
        <div style="font-size:48px;margin-bottom:8px;">📧</div>
        <h2 style="margin:0;font-size:18px;color:var(--text);">Gmail App Code eingeben</h2>
        <p style="color:var(--muted);font-size:13px;line-height:1.5;margin:8px 0 0;">
          Für den Gmail-Zugriff wird ein Google App-Passwort benötigt.
          <br>Das wird nur einmal lokal gespeichert.
        </p>
      </div>
      <div style="display:flex;flex-direction:column;gap:6px;">
        <label style="font-size:12px;color:var(--muted);font-weight:600;">E-Mail</label>
        <select id="gmailSplashEmail" style="padding:10px 12px;border:1px solid var(--border);border-radius:8px;background:var(--input-bg);color:var(--text);font-size:14px;outline:none;">
          <option value="dominik">dominikrnr@gmail.com</option>
          <option value="loggableim">loggableim@gmail.com</option>
          <option value="logga">logga@logga.de</option>
        </select>
      </div>
      <div style="display:flex;flex-direction:column;gap:6px;">
        <label style="font-size:12px;color:var(--muted);font-weight:600;">Google App-Passwort</label>
        <input id="gmailSplashCode" type="password" placeholder="xxxx xxxx xxxx xxxx" autofocus
               style="padding:10px 12px;border:1px solid var(--border);border-radius:8px;background:var(--input-bg);color:var(--text);font-size:14px;outline:none;font-family:monospace;letter-spacing:1px;"
               onkeydown="if(event.key==='Enter')saveGmailSplash()">
      </div>
      <button id="gmailSplashSaveBtn" onclick="saveGmailSplash()"
              style="padding:12px;border:none;border-radius:8px;background:var(--accent);color:var(--accent-text);font-weight:600;font-size:14px;cursor:pointer;transition:opacity 0.15s;">
        ⏎ Speichern & Gmail laden
      </button>
      <a href="https://myaccount.google.com/apppasswords" target="_blank"
         style="text-align:center;font-size:12px;color:var(--accent);text-decoration:none;">
        🔑 App-Passwort erstellen (benötigt 2FA)
      </a>
    </div>
  `;
  const overlayHost = document.querySelector('main.main') || document.body;
  overlayHost.appendChild(overlay);
  setTimeout(() => document.getElementById('gmailSplashCode')?.focus(), 200);
}

function gmailCloseSplash() {
  const overlay = document.getElementById('gmailSplashOverlay');
  if (overlay) overlay.remove();
  const sidebar = document.querySelector('.gmail-sidebar');
  const mainGmail = document.getElementById('mainGmail');
  if (sidebar) sidebar.style.filter = '';
  if (mainGmail) mainGmail.style.filter = '';
  gmailShowUnavailable('Gmail-Zugriff nicht eingerichtet', 'Klicke erneut auf Gmail, um den App-Code einzugeben.');
}

async function saveGmailSplash() {
  const sel = document.getElementById('gmailSplashEmail');
  const pw = document.getElementById('gmailSplashCode')?.value?.trim();
  const btn = document.getElementById('gmailSplashSaveBtn');
  const email = sel?.selectedOptions?.[0]?.text || sel?.value || 'dominikrnr@gmail.com';
  const accountName = sel?.value || 'dominik';

  if (!pw) {
    gmailToast('⚠️ Bitte App-Passwort eingeben', 'error');
    return;
  }

  if (btn) { btn.disabled = true; btn.textContent = '⏳ Speichere...'; }

  try {
    const params = new URLSearchParams((typeof getActiveSpaceQuery === 'function' ? getActiveSpaceQuery() : '').slice(1));
    const slug = params.get('workspace') || window.DEFAULT_SPACE_SLUG || 'nova';

    // Save to space config (works for default workspace too)
    await fetchJson('api/space/config', {
      method: 'POST',
      body: JSON.stringify({
        slug,
        gmail: { accounts: { [accountName]: { email, password: pw } } },
      }),
    });

    // Remove splash + blur
    const overlay = document.getElementById('gmailSplashOverlay');
    if (overlay) overlay.remove();
    const sidebar = document.querySelector('.gmail-sidebar');
    const mainGmail = document.getElementById('mainGmail');
    if (sidebar) sidebar.style.filter = '';
    if (mainGmail) mainGmail.style.filter = '';

    gmailToast('✅ Gmail-Zugang gespeichert', 'success');
    // Reload Gmail
    await loadGmailAccounts();
    gmailNavSelect(GMAIL.currentFolder);
  } catch (e) {
    gmailToast('❌ Fehler: ' + e.message, 'error');
    if (btn) { btn.disabled = false; btn.textContent = '⏎ Speichern & Gmail laden'; }
  }
}

// ── Delete email ──
async function gmailDeleteEmail(id, folder) {
  const card = document.querySelector(`.gmail-card[data-id="${id}"]`);
  if (card) card.classList.add('gmail-trashing');

  try {
    const data = await fetchJson('api/gmail/delete', {
      method: 'POST',
      body: JSON.stringify(_gmailWithWorkspace({ id, folder: folder || GMAIL.currentFolder, account: GMAIL.currentAccount })),
    });
    if (data.status === 'deleted' || data.status === 'trashed') {
      gmailToast('🗑️ In Papierkorb verschoben', 'success');
      gmailCloseDetail();
      setTimeout(() => gmailRefresh(), 400);
    } else {
      gmailToast('❌ ' + (data.error || 'Löschen fehlgeschlagen'), 'error');
    }
  } catch (e) {
    gmailToast('❌ Fehler: ' + e.message, 'error');
  }
}

// ── Move email ──
async function gmailMoveEmail(id, toFolder) {
  if (!toFolder) {
    const folder = prompt('In welchen Ordner verschieben? (z.B. [Gmail]/Papierkorb)');
    if (!folder) return;
    toFolder = folder;
  }
  try {
    const data = await fetchJson('api/gmail/move', {
      method: 'POST',
      body: JSON.stringify(_gmailWithWorkspace({ id, to_folder: toFolder, from_folder: GMAIL.currentFolder, account: GMAIL.currentAccount })),
    });
    if (data.status === 'moved') {
      gmailToast(`📨 → ${toFolder}`, 'success');
      gmailCloseDetail();
      gmailRefresh();
    } else {
      gmailToast('❌ ' + (data.error || 'Verschieben fehlgeschlagen'), 'error');
    }
  } catch (e) {
    gmailToast('❌ Fehler: ' + e.message, 'error');
  }
}

// ── Open Search in main area ──
function gmailOpenSearch() {
  gmailCloseDetail();
  const mainList = document.getElementById('gmailMainList');
  if (!mainList) return;
  mainList.innerHTML = `
    <div style="padding:24px 16px;display:flex;flex-direction:column;align-items:center;gap:12px;">
      <div class="gmail-search-box" style="width:100%;max-width:500px;display:flex;gap:6px;">
        <input id="gmailSearchInput" type="text" placeholder="Suche: from:github, subject:deploy, Freitext..." autofocus
               style="flex:1;padding:8px 12px;border:1px solid var(--border);border-radius:8px;background:var(--input-bg);color:var(--text);font-size:14px;outline:none;"
               onkeydown="if(event.key==='Enter') gmailDoSearch()">
        <button onclick="gmailDoSearch()" style="padding:8px 16px;background:var(--accent);color:var(--accent-text);border:none;border-radius:8px;cursor:pointer;font-weight:600;">🔍 Suchen</button>
      </div>
      <div style="color:var(--muted);font-size:13px;">Beispiele: from:github · subject:deploy · has:attachment</div>
    </div>`;
  setTimeout(() => document.getElementById('gmailSearchInput')?.focus(), 100);
  const meta = document.getElementById('gmailMainListMeta');
  if (meta) meta.textContent = '🔍 Suche';
}

async function gmailDoSearch() {
  const input = document.getElementById('gmailSearchInput') || document.getElementById('gmailMainSearch');
  const query = input?.value.trim();
  const searchSeq = ++_gmailSearchSeq;
  if (!query) {
    await gmailRefresh();
    return;
  }

  const mainList = document.getElementById('gmailMainList');
  if (!mainList) return;
  mainList.innerHTML = `<div class="gmail-loading"><div class="gmail-shimmer"></div><span>🔍 Suche "${query}"...</span></div>`;
  const meta = document.getElementById('gmailMainListMeta');
  if (meta) meta.textContent = `🔍 "${query}"`;

  try {
    const url = _gmailAccount(`api/gmail/search?query=${encodeURIComponent(query)}&max=25`);
    const data = await fetchJson(url);
    if (searchSeq !== _gmailSearchSeq) return;
    if (data.error) throw new Error(data.error);

    const emails = data.emails || [];
    if (emails.length === 0) {
      gmailSetEmpty(mainList, '🔍', 'Keine Treffer für "' + query + '"');
      if (meta) meta.textContent = `🔍 "${query}" · 0 Treffer`;
      return;
    }

    let html = '';
    for (const email of emails) {
      const sender = email.from_name || email.from || 'Unbekannt';
      const subj = email.subject || '(kein Betreff)';
      const date = gmailFormatDate(email.date_raw || email.date);
      html += `<div class="gmail-card" data-id="${email.id}" onclick="gmailOpenEmail('${email.id}')">
        <span class="sender">${sender.length > 30 ? sender.slice(0, 28) + '…' : sender}</span>
        <span class="subject">${subj}</span>
        <span class="date">${date}</span>
      </div>`;
    }
    mainList.innerHTML = html;
    if (meta) meta.textContent = `🔍 "${query}" · ${data.count} Treffer`;
  } catch (e) {
    gmailSetEmpty(mainList, '⚠️', 'Fehler: ' + _gmailErrorMessage(e));
  }
}

// ── Open Compose as Overlay in Main area ──
function _gmailComposeKeydown(e) {
  if (e.key !== 'Escape') return;
  const overlay = document.getElementById('gmailComposeOverlay');
  if (!overlay || overlay.style.display === 'none') return;
  e.preventDefault();
  e.stopPropagation();
  gmailCloseCompose();
}

function gmailOpenCompose() {
  const overlay = document.getElementById('gmailComposeOverlay');
  if (!overlay) return;
  
  // Fill account info
  const accInfo = document.getElementById('gmailComposeAccountInfo');
  if (accInfo) {
    const email = GMAIL.accounts.find(a => a.id === GMAIL.currentAccount)?.email || GMAIL.currentAccount;
    accInfo.textContent = email;
  }

  // Clear previous values if not a reply
  const toField = document.getElementById('gmailComposeTo');
  if (toField && !toField.value) {
    // Only clear if not pre-filled by reply
    // Already pre-filled by gmailReplyDraft, so we leave it
  }

  overlay.style.display = 'flex';
  document.removeEventListener('keydown', _gmailComposeKeydown);
  document.addEventListener('keydown', _gmailComposeKeydown);
  setTimeout(() => {
    const to = document.getElementById('gmailComposeTo');
    if (to && !to.value) to.focus();
  }, 150);
}

function gmailCloseCompose() {
  const overlay = document.getElementById('gmailComposeOverlay');
  if (overlay) overlay.style.display = 'none';
  document.removeEventListener('keydown', _gmailComposeKeydown);
  // Also clear fields (gently, next open resets)
}

// ── Gmail Split-View Resize ──
let _gmailSplitDragging = false;
let _gmailSplitStartY = 0;
let _gmailSplitStartBasis = 0;

function initGmailSplitResize() {
  const handle = document.getElementById('gmailSplitHandle');
  if (!handle) return;
  // Remove any previous listener to avoid duplicates
  handle.removeEventListener('mousedown', _gmailSplitStart);
  handle.removeEventListener('touchstart', _gmailSplitTouchStart);
  handle.addEventListener('mousedown', _gmailSplitStart);
  handle.addEventListener('touchstart', _gmailSplitTouchStart, { passive: false });
  // Restore saved position
  gmailRestoreSplitPos();
}

function _gmailSplitGetY(e) {
  return e.type && e.type.startsWith('touch') ? e.touches[0].clientY : e.clientY;
}

function _gmailSplitTouchStart(e) {
  e.preventDefault();
  _gmailSplitStart(e);
}

function _gmailSplitStart(e) {
  e.preventDefault();
  const listPane = document.getElementById('gmailMainListPane');
  const readingPane = document.getElementById('gmailReadingPane');
  const handle = document.getElementById('gmailSplitHandle');
  if (!listPane || !readingPane) return;
  // Don't start drag if reading pane is hidden
  if (readingPane.style.display === 'none') return;

  _gmailSplitDragging = true;
  _gmailSplitStartY = _gmailSplitGetY(e);

  // Get current flex-basis as percentage of split-view height
  const splitView = document.getElementById('gmailSplitView');
  const svRect = splitView.getBoundingClientRect();
  if (svRect.height > 0) {
    _gmailSplitStartBasis = (listPane.getBoundingClientRect().height / svRect.height) * 100;
  }

  // Visual feedback
  if (handle) handle.classList.add('active');
  document.body.classList.add('gmail-split-resizing');

  document.addEventListener('mousemove', _gmailSplitMove);
  document.addEventListener('mouseup', _gmailSplitEnd);
  document.addEventListener('touchmove', _gmailSplitTouchMove, { passive: false });
  document.addEventListener('touchend', _gmailSplitEnd);
}

function _gmailSplitTouchMove(e) {
  e.preventDefault();
  if (!_gmailSplitDragging) return;
  // Create a synthetic event-like object
  _gmailSplitMove({ clientY: e.touches[0].clientY });
}

function _gmailSplitMove(e) {
  if (!_gmailSplitDragging) return;
  const listPane = document.getElementById('gmailMainListPane');
  const splitView = document.getElementById('gmailSplitView');
  const svRect = splitView.getBoundingClientRect();
  if (svRect.height <= 0) return;

  const deltaY = e.clientY - _gmailSplitStartY;
  const deltaPercent = (deltaY / svRect.height) * 100;
  let newPercent = _gmailSplitStartBasis + deltaPercent;

  // Clamp between 20% and 80%
  newPercent = Math.max(20, Math.min(80, newPercent));

  listPane.style.flex = `0 0 ${newPercent}%`;
}

function _gmailSplitEnd() {
  if (!_gmailSplitDragging) return;
  _gmailSplitDragging = false;

  const handle = document.getElementById('gmailSplitHandle');
  if (handle) handle.classList.remove('active');
  document.body.classList.remove('gmail-split-resizing');

  document.removeEventListener('mousemove', _gmailSplitMove);
  document.removeEventListener('mouseup', _gmailSplitEnd);
  document.removeEventListener('touchmove', _gmailSplitTouchMove);
  document.removeEventListener('touchend', _gmailSplitEnd);

  // Persist to localStorage
  gmailSaveSplitPos();
}

function gmailSaveSplitPos() {
  const listPane = document.getElementById('gmailMainListPane');
  const splitView = document.getElementById('gmailSplitView');
  if (!listPane || !splitView) return;
  const svRect = splitView.getBoundingClientRect();
  if (svRect.height > 0) {
    const pct = (listPane.getBoundingClientRect().height / svRect.height) * 100;
    try {
      localStorage.setItem('sidekick-gmail-split-pos', String(Math.round(pct)));
    } catch {}
  }
}

function gmailRestoreSplitPos() {
  const listPane = document.getElementById('gmailMainListPane');
  if (!listPane) return;
  try {
    const saved = localStorage.getItem('sidekick-gmail-split-pos');
    if (saved) {
      const pct = parseFloat(saved);
      if (pct >= 20 && pct <= 80) {
        listPane.style.flex = `0 0 ${pct}%`;
      }
    }
  } catch {}
}

// ── Send from overlay compose ──
async function gmailSendMain() {
  const to = document.getElementById('gmailComposeTo')?.value.trim();
  const subject = document.getElementById('gmailComposeSubject')?.value.trim();
  const body = document.getElementById('gmailComposeBody')?.value.trim();
  const status = document.getElementById('gmailMainSendStatus');
  const btn = document.querySelector('.gmail-primary-btn[onclick*="gmailSendMain"]');

  if (!to || !subject || !body) {
    if (status) { status.textContent = '⚠️ Bitte alle Felder ausfüllen'; status.style.color = '#ef4444'; }
    return;
  }
  if (!to.includes('@')) {
    if (status) { status.textContent = '⚠️ Ungültige E-Mail-Adresse'; status.style.color = '#ef4444'; }
    return;
  }

  if (btn) { btn.disabled = true; btn.innerHTML = '⏳ Wird gesendet...'; }
  if (status) { status.textContent = '📤 Sende...'; status.style.color = 'var(--muted)'; }

  try {
    const data = await fetchJson('api/gmail/send', {
      method: 'POST',
      body: JSON.stringify(_gmailWithWorkspace({ to, subject, body, account: GMAIL.currentAccount })),
    });

    if (data.status === 'sent') {
      if (status) { status.textContent = `✅ Gesendet an ${to}`; status.style.color = '#22c55e'; }
      if (btn) btn.innerHTML = '✅ Gesendet';
      gmailToast(`✉️ Gesendet an ${to}`, 'success');
      // Clear fields and close after brief delay
      setTimeout(() => {
        const toF = document.getElementById('gmailComposeTo'); if (toF) toF.value = '';
        const subjF = document.getElementById('gmailComposeSubject'); if (subjF) subjF.value = '';
        const bodyF = document.getElementById('gmailComposeBody'); if (bodyF) bodyF.value = '';
        if (status) { status.textContent = ''; }
        gmailCloseCompose();
      }, 1200);
    } else {
      throw new Error(data.error || 'Send failed');
    }
  } catch (e) {
    if (status) { status.textContent = `❌ Fehler: ${e.message}`; status.style.color = '#ef4444'; }
    if (btn) { btn.disabled = false; btn.innerHTML = '✉️ Senden'; }
  }
}

// ── AI Draft (placeholder) ──
function gmailAIDraftFromOverlay() {
  const body = document.getElementById('gmailComposeBody');
  if (!body) return;
  gmailToast('🤖 KI-Verbesserung kommt in einem späteren Schritt', 'info');
}

// ── Labels toggle ──
function gmailToggleLabels() {
  const section = document.querySelector('.gmail-labels-section');
  if (section) section.classList.toggle('gmail-labels-collapsed');
}

// ── Auto-poll for unread indicator ──
async function gmailPollUnread() {
  try {
    const url = _gmailAccount('api/gmail/list?max=1');
    const data = await fetchJson(url);
    const dot = document.getElementById('gmailUnreadDot');
    if (dot) {
      dot.style.display = (data.count && data.count > 0) ? 'block' : 'none';
    }
    gmailRefreshNavCounts();
  } catch {}
}

// Start polling is handled in loadGmailPanel()

// ── Rightpanel Tab-Switcher ──
function switchRightpanelTab(tabId) {
  // Update tab buttons
  document.querySelectorAll('.rightpanel-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.rtab === tabId);
  });
  // Update content panels using data-rtab attribute
  document.querySelectorAll('.rightpanel-tab-content').forEach(c => {
    const isActive = c.dataset.rtab === tabId;
    c.classList.toggle('active', isActive);
    c.style.display = isActive ? '' : 'none';
  });
}

// ── Fullscreen toggle for reading pane ──
let _gmailDetailFullscreen = false;

function gmailSyncReadingPaneControls() {
  const shrink = document.getElementById('gmailPaneShrinkBtn');
  const grow = document.getElementById('gmailPaneGrowBtn');
  [shrink, grow].forEach(btn => {
    if (!btn) return;
    btn.disabled = !!_gmailDetailFullscreen;
    btn.classList.toggle('gmail-pane-control-disabled', !!_gmailDetailFullscreen);
    btn.title = _gmailDetailFullscreen
      ? 'Im Vollbild nicht verfügbar'
      : (btn.id === 'gmailPaneGrowBtn' ? 'Lesebereich vergrößern' : 'Lesebereich verkleinern');
  });
}

function gmailToggleDetailFullscreen() {
  _gmailDetailFullscreen = !_gmailDetailFullscreen;
  const listPane = document.getElementById('gmailMainListPane');
  const readingPane = document.getElementById('gmailReadingPane');
  const splitHandle = document.getElementById('gmailSplitHandle');
  const fsBtn = document.getElementById('gmailFullscreenBtn') || document.getElementById('gmailDetailFsBtn');

  if (_gmailDetailFullscreen) {
    // Fullscreen: reading pane fills everything, list hidden
    if (listPane) { listPane.style.display = 'none'; listPane.classList.remove('has-detail'); }
    if (splitHandle) splitHandle.style.display = 'none';
    if (readingPane) readingPane.style.flex = '1 1 100%';
    document.querySelectorAll('.gmail-fullscreen-btn, #gmailDetailFsBtn').forEach(b => {
      if (b) b.textContent = '✕';
    });
  } else {
    // Split back: restore list + handle
    if (listPane) { listPane.style.display = ''; listPane.classList.add('has-detail'); }
    if (splitHandle) splitHandle.style.display = 'flex';
    if (readingPane) readingPane.style.flex = '';
    // Restore saved split position
    gmailRestoreSplitPos();
    document.querySelectorAll('.gmail-fullscreen-btn, #gmailDetailFsBtn').forEach(b => {
      if (b) b.textContent = '⛶';
    });
  }
  gmailSyncReadingPaneControls();
}

// ── AI-Panel: Update when context changes (chat or email) ──
function gmailAdjustReadingPane(delta) {
  const listPane = document.getElementById('gmailMainListPane');
  const readingPane = document.getElementById('gmailReadingPane');
  const splitView = document.getElementById('gmailSplitView');
  if (!listPane || !readingPane || !splitView) return;
  if (readingPane.style.display === 'none') return;
  if (_gmailDetailFullscreen) {
    gmailToast('Lesebereich-Größe ist im Vollbild fixiert.', 'info');
    gmailSyncReadingPaneControls();
    return;
  }

  const svRect = splitView.getBoundingClientRect();
  if (svRect.height <= 0) return;

  let current = parseFloat((listPane.style.flex || '').replace(/^0 0 /, '').replace('%', ''));
  if (!Number.isFinite(current)) current = (listPane.getBoundingClientRect().height / svRect.height) * 100;
  if (!Number.isFinite(current)) current = 45;

  // Positive delta enlarges the reading pane by shrinking the list pane.
  let next = current - Number(delta || 0);
  next = Math.max(20, Math.min(80, next));
  listPane.style.flex = `0 0 ${next}%`;
  gmailSaveSplitPos();
}

let _gmailCurrentEmailId = null;
let _gmailSummaryAborted = false;
let _gmailAIModel = localStorage.getItem('sidekick-gmail-ai-model') || 'llama3.2:latest';

function gmailAISetModel(model) {
  _gmailAIModel = model;
  try { localStorage.setItem('sidekick-gmail-ai-model', model); } catch {}
}

// ── Universal AI Summarize (Chat or Email) ──
function toolsAISummarize() {
  // If in Gmail mode with an email open, summarize that
  if (_gmailCurrentEmailId && document.body.classList.contains('showing-gmail')) {
    gmailStreamSummaryForEmail();
    return;
  }
  // Otherwise summarize the current chat conversation
  toolsSummarizeChat();
}

function gmailStreamSummaryForEmail() {
  if (!_gmailCurrentEmailId) return;
  const summaryBody = document.getElementById('gmailAISummaryBody');
  if (!summaryBody) return;
  _gmailSummaryAborted = true;
  summaryBody.innerHTML = '<div class="gmail-ai-placeholder"><span class="gmail-ai-robot-big">🤖</span><span>Generiere Zusammenfassung...</span></div>';

  const model = _gmailAIModel;
  const url = _gmailAccount(`api/gmail/ai/summary/stream?id=${encodeURIComponent(_gmailCurrentEmailId)}&model=${encodeURIComponent(model)}`);
  _gmailSummaryAborted = false;

  fetch(sidekickApiUrl(url).href)
    .then(response => {
      if (!response.ok) throw new Error('Stream error');
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '', summaryText = '';
      function readChunk() {
        if (_gmailSummaryAborted) { reader.cancel(); return; }
        reader.read().then(({ done, value }) => {
          if (done) {
            if (summaryBody && summaryBody.querySelector('.gmail-ai-streaming')) summaryBody.innerHTML = summaryText;
            gmailAIGenerateDraft(_gmailCurrentEmailId);
            gmailAIFetchRelated(_gmailCurrentEmailId);
            return;
          }
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop();
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try {
              const data = JSON.parse(line.slice(6));
              if (data.token) { summaryText += data.token; summaryBody.innerHTML = summaryText + '<span class="gmail-ai-streaming"></span>'; }
              if (data.done) break;
            } catch {}
          }
          readChunk();
        }).catch(() => {});
      }
      readChunk();
    })
    .catch(e => {
      if (!_gmailSummaryAborted) gmailSetAIPlaceholder(summaryBody, '⚠️ ' + _gmailErrorMessage(e));
    });
}

// ── Quick Actions ──
function toolsSummarizeChat() {
  const summaryBody = document.getElementById('gmailAISummaryBody');
  if (!summaryBody) return;
  _gmailSummaryAborted = true;

  // Get visible messages from chat
  let chatText = '';
  if (typeof S !== 'undefined' && S && S.messages) {
    chatText = S.messages
      .filter(m => m && m.role && m.role !== 'tool')
      .slice(-10)
      .map(m => `${m.role}: ${(m.content || '').slice(0, 500)}`)
      .join('\n\n');
  }
  if (!chatText) {
    summaryBody.innerHTML = '<div class="gmail-ai-placeholder"><span class="gmail-ai-robot-big">🤖</span><span>Keine Chat-Nachrichten vorhanden</span></div>';
    return;
  }

  summaryBody.innerHTML = '<div class="gmail-ai-placeholder"><span class="gmail-ai-robot-big">🤖</span><span>Fasse Chat zusammen...</span></div>';
  _gmailSummaryAborted = false;

  fetch(sidekickApiUrl('api/gmail/ai/summary/stream?model=' + encodeURIComponent(_gmailAIModel) + '&id=chat').href, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt: 'Fasse die folgende Unterhaltung auf Deutsch kurz zusammen:\n\n' + chatText.slice(0, 5000) }),
  })
  .catch(() => {
    // Fallback: just use the non-streaming summary endpoint
    summaryBody.innerHTML = '<div class="gmail-ai-placeholder"><span class="gmail-ai-robot-big">🤖</span><span>KI nicht verfügbar</span></div>';
  });
}

function toolsExplainCode() {
  const lastCode = _getLastCodeBlock();
  if (!lastCode) { gmailToast('⚠️ Kein Code in letzten Nachrichten', 'info'); return; }
  gmailToast('🔍 Erkläre Code... (Streaming in Kürze)', 'info');
  // TODO: stream explanation to summary card
}

function toolsFindBugs() {
  const lastCode = _getLastCodeBlock();
  if (!lastCode) { gmailToast('⚠️ Kein Code in letzten Nachrichten', 'info'); return; }
  gmailToast('🐛 Suche nach Bugs...', 'info');
}

function toolsImproveCode() {
  const lastCode = _getLastCodeBlock();
  if (!lastCode) { gmailToast('⚠️ Kein Code in letzten Nachrichten', 'info'); return; }
  gmailToast('✨ Verbessere Code...', 'info');
}

function toolsCopyTranscript() {
  if (typeof S === 'undefined' || !S || !S.messages) { gmailToast('⚠️ Keine Nachrichten', 'info'); return; }
  const text = S.messages.filter(m => m && m.role && m.role !== 'tool').map(m => `${m.role}: ${m.content || ''}`).join('\n\n---\n\n');
  navigator.clipboard.writeText(text).then(() => gmailToast('📋 Transkript kopiert', 'success')).catch(() => gmailToast('⚠️ Kopieren fehlgeschlagen', 'error'));
}

function toolsExportMarkdown() {
  if (typeof S === 'undefined' || !S || !S.messages) { gmailToast('⚠️ Keine Nachrichten', 'info'); return; }
  const md = '# Chat Transcript\n\n' + S.messages.filter(m => m && m.role && m.role !== 'tool').map(m => `## ${m.role}\n\n${m.content || ''}`).join('\n\n');
  const blob = new Blob([md], { type: 'text/markdown' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'chat-transcript.md';
  a.click();
  URL.revokeObjectURL(a.href);
  gmailToast('📤 Als MD exportiert', 'success');
}

function _getLastCodeBlock() {
  if (typeof S === 'undefined' || !S || !S.messages) return null;
  for (let i = S.messages.length - 1; i >= 0; i--) {
    const m = S.messages[i];
    if (m && m.content) {
      const match = m.content.match(/```[\s\S]*?```/);
      if (match) return match[0];
    }
  }
  return null;
}

// ── AI Summary for email (called from updateGmailAIPanel) ──
function updateGmailAIPanel(emailData) {
  _gmailCurrentEmailId = emailData ? emailData.id : null;

  if (!emailData) {
    const summaryBody = document.getElementById('gmailAISummaryBody');
    if (summaryBody) summaryBody.innerHTML = '<div class="gmail-ai-placeholder"><span class="gmail-ai-robot-big">🤖</span><span>Wähle eine E-Mail aus</span></div>';
    const replyBody = document.getElementById('gmailAIReplyBody');
    if (replyBody) replyBody.innerHTML = '<div style="color:var(--muted);font-size:11px;">Wähle eine E-Mail aus.</div>';
    const replyActions = document.getElementById('gmailAIReplyActions');
    if (replyActions) replyActions.style.display = 'none';
    const relatedBody = document.getElementById('gmailAIRelatedBody');
    if (relatedBody) relatedBody.innerHTML = '<div style="color:var(--muted);font-size:11px;">Verwandte E-Mails erscheinen hier.</div>';
    _gmailSummaryAborted = true;
    return;
  }

  // Summarize + Reply options + Related
  gmailStreamSummaryForEmail();
  gmailAIFetchRelated(emailData.id);
}

// ── Reply options (3 suggestions, with draft/send buttons) ──
function gmailShowReplyOptions(emailId) {
  if (!emailId) return;
  _gmailSummaryAborted = true;

  const summaryBody = document.getElementById('gmailAISummaryBody');
  if (!summaryBody) return;
  summaryBody.innerHTML = '<div class="gmail-ai-placeholder"><span class="gmail-ai-robot-big">🤖</span><span>Generiere Antwort-Optionen...</span></div>';

  fetchJson(_gmailAccount(`api/gmail/ai/draft?id=${encodeURIComponent(emailId)}&variants=3`))
    .then(data => {
      let html = '<div class="gmail-reply-options">';
      const variants = data.variants || [data.draft || 'Vielen Dank für Ihre Nachricht.'];
      for (let i = 0; i < Math.min(variants.length, 3); i++) {
        const v = variants[i];
        html += `<div class="gmail-reply-option" onclick="gmailReplyPreview('${escHtml(v)}')">${escHtml(v)}</div>`;
        html += `<div class="gmail-reply-actions">
          <button class="gmail-reply-btn" onclick="gmailReplyDraftFromOption('${escHtml(v)}')">📋 Entwurf</button>
          <button class="gmail-reply-btn primary" onclick="gmailReplySendDirect('${escHtml(v)}', '${escHtml(data.subject || '')}')">✉️ Senden</button>
        </div>`;
      }
      html += '</div>';
      summaryBody.innerHTML = html;
    })
    .catch(() => {
      summaryBody.innerHTML = '<div class="gmail-ai-placeholder"><span class="gmail-ai-robot-big">🤖</span><span>Antwort-Optionen nicht verfügbar</span></div>';
    });
}

function gmailReplyPreview(text) {
  gmailToast('✍️ ' + text.slice(0, 80) + '...', 'info');
}

function gmailReplyDraftFromOption(text) {
  const toField = document.getElementById('gmailComposeTo');
  const bodyField = document.getElementById('gmailComposeBody');
  gmailOpenCompose();
  if (bodyField) bodyField.value = text;
  gmailToast('📋 Entwurf übernommen', 'success');
}

function gmailReplySendDirect(text, subject) {
  gmailReplyDraftFromOption(text);
  // Auto-send after brief preview
  setTimeout(() => gmailSendMain(), 500);
}

// ── Legacy: Draft + Related (kept for gmail-only cards) ──
async function gmailAIFetchSummary(emailId, bodyText) { /* legacy placeholder */ }

async function gmailAIGenerateDraft(emailId) {
  const replyBody = document.getElementById('gmailAIReplyBody');
  const replyActions = document.getElementById('gmailAIReplyActions');
  if (!replyBody) return;
  try {
    const data = await fetchJson(_gmailAccount(`api/gmail/ai/draft?id=${encodeURIComponent(emailId)}`));
    if (data.draft) {
      _gmailCurrentDraft = data.draft;
      replyBody.textContent = data.draft;
      if (replyActions) replyActions.style.display = 'flex';
    } else {
      replyBody.innerHTML = '<div style="color:var(--muted);font-size:11px;">Kein Entwurf verfügbar.</div>';
    }
  } catch {
    replyBody.innerHTML = '<div style="color:var(--muted);font-size:11px;">Entwurf nicht verfügbar.</div>';
  }
}

let _gmailCurrentDraft = '';

async function gmailAIFetchRelated(emailId) {
  const relatedBody = document.getElementById('gmailAIRelatedBody');
  if (!relatedBody) return;
  try {
    const data = await fetchJson(_gmailAccount(`api/gmail/ai/related?id=${encodeURIComponent(emailId)}`));
    if (data.related && data.related.length > 0) {
      let html = '';
      for (const r of data.related) {
        html += `<div style="padding:4px 0;font-size:11px;cursor:pointer;color:var(--accent);" onclick="gmailOpenEmail('${r.id}')">${r.subject || '(kein Betreff)'}</div>`;
      }
      relatedBody.innerHTML = html;
    } else {
      relatedBody.innerHTML = '<div style="color:var(--muted);font-size:11px;">Keine verwandten E-Mails gefunden.</div>';
    }
  } catch {
    relatedBody.innerHTML = '<div style="color:var(--muted);font-size:11px;">Verwandte nicht verfügbar.</div>';
  }
}

// ── Gmail Task Creation (kept for Quick Actions) ──
function gmailCreateTask() {
  if (!_gmailCurrentEmailId) { gmailToast('⚠️ Keine E-Mail ausgewählt', 'info'); return; }
  const email = GMAIL.emails.find(e => e.id === _gmailCurrentEmailId);
  if (!email) { gmailToast('⚠️ E-Mail nicht in aktueller Liste', 'info'); return; }
  gmailToast('📋 Erstelle Task...', 'info');
  fetch(sidekickApiUrl('api/gmail/ai/task').href, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(_gmailWithWorkspace({ id: _gmailCurrentEmailId, title: (email.subject || 'Email Task').slice(0, 120), account: GMAIL.currentAccount, priority: 1 })),
  })
  .then(r => r.json())
  .then(data => {
    if (data.status === 'created') {
      gmailToast(data.message || '📋 Task erstellt', 'success');
      if (typeof switchPanel === 'function') switchPanel('kanban');
    } else { gmailToast('❌ ' + (data.error || 'Task-Erstellung fehlgeschlagen'), 'error'); }
  })
  .catch(e => { gmailToast('❌ Fehler: ' + e.message, 'error'); });
}

// ── Reply Draft actions ──
function gmailAIRegenerate() {
  if (!_gmailCurrentEmailId) { gmailToast('⚠️ Keine E-Mail ausgewählt', 'info'); return; }
  gmailAIGenerateDraft(_gmailCurrentEmailId);
  gmailToast('🔄 Entwurf wird neu generiert...', 'info');
}

function gmailAIUseDraft() {
  if (!_gmailCurrentDraft) { gmailToast('⚠️ Kein Entwurf verfügbar', 'info'); return; }
  gmailOpenCompose();
  const bodyField = document.getElementById('gmailComposeBody');
  if (bodyField) bodyField.value = _gmailCurrentDraft;
  gmailToast('📋 Entwurf übernommen', 'success');
}

function gmailAIEditDraft() {
  gmailAIUseDraft();
  setTimeout(() => { document.getElementById('gmailComposeBody')?.focus(); }, 200);
}

// ── Smart Action Stubs ──
function gmailAISetReminder() {
  gmailToast('🔄 Follow-up in 3 Tagen erinnert (Feature kommt später)', 'info');
}
