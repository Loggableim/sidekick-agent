function _markSessionViewed(sid, messageCount) {
  if(typeof _setSessionViewedCount!=='function' || !sid) return;
  const next = Number.isFinite(messageCount) ? Number(messageCount) : 0;
  _setSessionViewedCount(sid, next);
}

function _isDocumentVisibleAndFocused() {
  if(typeof document!=='undefined' && document.visibilityState && document.visibilityState!=='visible') return false;
  if(typeof document!=='undefined' && typeof document.hasFocus==='function' && !document.hasFocus()) return false;
  return true;
}

function _isSessionCurrentPane(sid) {
  if(!sid || !S.session || S.session.session_id!==sid) return false;
  // During session switching, S.session still points at the previous row until
  // the next metadata request resolves. Do not let a just-finished old stream
  // update the chat pane while the user is moving to another session.
  if(typeof _loadingSessionId!=='undefined' && _loadingSessionId && _loadingSessionId!==sid) return false;
  return true;
}

function _isSessionActivelyViewed(sid) {
  if(!_isSessionCurrentPane(sid)) return false;
  if(!_isDocumentVisibleAndFocused()) return false;
  return true;
}

function _markActiveSessionViewedOnReturn() {
  if(!_isDocumentVisibleAndFocused() || !S.session || !S.session.session_id) return;
  _markSessionViewed(S.session.session_id, S.session.message_count || (S.messages&&S.messages.length) || 0);
  if(typeof _clearSessionCompletionUnread==='function') _clearSessionCompletionUnread(S.session.session_id);
  if(typeof renderSessionListFromCache==='function') renderSessionListFromCache();
}

function _deferStreamErrorIfOffline(){
  if(typeof isOfflineBannerVisible==='function' && isOfflineBannerVisible()){
    setComposerStatus(t('offline_stream_waiting'));
    return true;
  }
  if(typeof showOfflineBanner==='function' && navigator.onLine===false){
    showOfflineBanner('browser');
    setComposerStatus(t('offline_stream_waiting'));
    return true;
  }
  return false;
}

document.addEventListener('visibilitychange', _markActiveSessionViewedOnReturn);
window.addEventListener('focus', _markActiveSessionViewedOnReturn);
// TTS: pause speech synthesis when user focuses the composer (#499)
const _msgEl=document.getElementById('msg');
if(_msgEl) _msgEl.addEventListener('focus', ()=>{ if('speechSynthesis' in window && speechSynthesis.speaking) speechSynthesis.pause(); });
if(_msgEl) _msgEl.addEventListener('blur', ()=>{ if('speechSynthesis' in window && speechSynthesis.paused) speechSynthesis.resume(); });

function _gameModeWouldBlockClientModel(model, provider, spaceSlug){
  if(window._gameModeEnabled!==true) return false;
  if(_gameModeAllowsNovaRemoteFallback(spaceSlug)) return false;
  const p=String(provider||'').trim().toLowerCase();
  const m=String(model||'').trim().toLowerCase();
  const localProviders=new Set(['lmstudio','lm-studio','ollama','llamacpp','llama-cpp','vllm','tabby','tabbyapi','koboldcpp','textgen','localai','local-gpu','local-cpu','local-qwen','qwen-local']);
  if(localProviders.has(p)) return true;
  if(p.startsWith('custom:')&&localProviders.has(p.slice(7))) return true;
  return m.startsWith('@ollama:')||m.startsWith('ollama:')||m.includes('@ollama:');
}

function _gameModeAllowsNovaRemoteFallback(spaceSlug){
  const slug=String(spaceSlug||'').trim().toLowerCase();
  if(slug==='nova') return true;
  const activeSpace=String(typeof _activeSpace!=='undefined'&&_activeSpace ? _activeSpace : '').trim().toLowerCase();
  if(slug&&activeSpace&&slug!==activeSpace) return false;
  const cfg=window._activeSpaceConfig;
  return !!(cfg&&typeof cfg==='object'&&cfg.nova&&typeof cfg.nova==='object'&&cfg.nova.enabled);
}

function _showGameModeClientBlock(){
  const msg=(typeof t==='function')?t('game_mode_on'):'Game Mode: local GPU blocked';
  setComposerStatus(msg);
  if(typeof showToast==='function') showToast(msg,5000,'warning');
}

function _currentComposerModelState(){
  const sel=$('modelSelect');
  const selectedModel=String((sel&&sel.value)||(S.session&&S.session.model)||'').trim();
  const fallbackProvider=S.session&&S.session.model_provider ? String(S.session.model_provider).trim() : null;
  if(typeof _modelStateForSelect==='function'){
    const state=_modelStateForSelect(sel,selectedModel);
    if(state&&typeof state==='object'){
      return {
        model:String(state.model||selectedModel||'').trim(),
        model_provider:state.model_provider ? String(state.model_provider).trim() : fallbackProvider,
      };
    }
  }
  return {model:selectedModel, model_provider:fallbackProvider};
}

let _isSendingChat=false;

async function send(){
  if(_isSendingChat){
    if(typeof showToast==='function') showToast('Send in progress, please wait.',2500,'warning');
    return;
  }
  const originalText=$('msg').value;
  const originalFiles=[...S.pendingFiles];
  _isSendingChat=true;
  try{
  let text=$('msg').value.trim();
  // Plan/Action mode: `/plan` automatisch voranstellen
  const composerMode = (() => {
    const rawMode = typeof window._composerMode === 'string'
      ? window._composerMode
      : (typeof localStorage !== 'undefined' ? localStorage.getItem('sidekick-webui-composer-mode') : '');
    return rawMode === 'plan' ? 'plan' : 'action';
  })();
  if (composerMode === 'plan' && text && !text.startsWith('/')) {
    text='/plan '+text;
    $('msg').value=text;
  }
  if(!text&&!S.pendingFiles.length)return;
  // Don't send while an inline message edit is active
  if(document.querySelector('.msg-edit-area'))return;

  const selectedModelState=_currentComposerModelState();

  // Dismiss handoff hint when user sends a message (resets seen_at).
  if(S.session&&S.session.session_id&&typeof _dismissHandoffHint==='function'){
    _dismissHandoffHint(S.session.session_id);
  }

  const compressionRunning=typeof isCompressionUiRunning==='function'&&isCompressionUiRunning();
  // If busy or a manual compression is still running, handle based on busy_input_mode
  if(S.busy||compressionRunning){
    if(text){
      if(!S.session){await newSession();await renderSessionList();}
      // Busy-control slash commands must be intercepted HERE, before the
      // busyMode routing block, so the user can always type /steer, /interrupt,
      // or /queue while the agent is running and have them execute immediately.
      // Without this intercept they fall through to the queue and execute after
      // the current turn ends — by which point there is no active stream and
      // cmdSteer / cmdInterrupt say "No active task to stop."
      if(text.startsWith('/')){
        const _pc=typeof parseCommand==='function'&&parseCommand(text);
        if(_pc&&['steer','interrupt','queue','terminal','goal'].includes(_pc.name)){
          const _bc=COMMANDS.find(c=>c.name===_pc.name);
          if(_bc){
            $('msg').value='';autoResize();_collapseExpandIfOpen();
            await _bc.fn(_pc.args);
            return;
          }
        }
      }
      const busyMode=window._busyInputMode||'queue';
      if(busyMode==='steer'&&S.activeStreamId&&typeof _trySteer==='function'){
        // Real steer: clear the input first so the user gets immediate
        // feedback, then ship the steer payload via /api/chat/steer.
        // _trySteer falls back to queue+cancel internally if the agent
        // isn't running / cached / steer-capable.
        $('msg').value='';autoResize();_collapseExpandIfOpen();
        // Do NOT clear pendingFiles yet — _trySteer may fall back to
        // interrupt+queue and needs the files for queueSessionMessage.
        // _trySteer clears pendingFiles itself in the fallback path, and
        // the server returns accepted:true (no files sent) on success.
        await _trySteer(text, /*explicitSteer=*/false);
        // After _trySteer: clear any remaining files (success path).
        S.pendingFiles=[];renderTray();
      } else if(busyMode==='interrupt'){
        // Queue the message, then cancel so drain re-sends it.
        queueSessionMessage(S.session.session_id,{text,files:[...S.pendingFiles],model:selectedModelState.model,model_provider:selectedModelState.model_provider,profile:S.activeProfile||'default'});
        updateQueueBadge(S.session.session_id);
        $('msg').value='';autoResize();_collapseExpandIfOpen();
        S.pendingFiles=[];renderTray();
        if(S.activeStreamId&&typeof cancelStream==='function'){
          showToast(t('busy_interrupt_confirm'),2000);
          await cancelStream();
        } else {
          showToast(`Queued: "${text.slice(0,40)}${text.length>40?'…':''}"`,2000);
        }
      } else {
        // Default: queue mode (current behavior). Also the fallback for
        // 'steer' mode when no stream is active or _trySteer is unavailable.
        queueSessionMessage(S.session.session_id,{text,files:[...S.pendingFiles],model:selectedModelState.model,model_provider:selectedModelState.model_provider,profile:S.activeProfile||'default'});
        $('msg').value='';autoResize();_collapseExpandIfOpen();
        S.pendingFiles=[];renderTray();
        updateQueueBadge(S.session.session_id);
        showToast(`Queued: "${text.slice(0,40)}${text.length>40?'…':''}"`,2000);
      }
    }
    return;
  }
  if(S.session&&(S.session.read_only||S.session.is_read_only)){
    if(typeof showToast==='function') showToast('Read-only imported sessions cannot be modified.',3000);
    return;
  }
  // Slash command intercept -- local commands handled without agent round-trip.
  // We push the user message BEFORE running the handler for echo-worthy
  // commands so chat order is correct: some handlers (e.g. cmdHelp) push
  // their assistant response synchronously.  If we pushed AFTER, S.messages
  // would be [assistant, user] and the chat would show the response above
  // the user's own input — reverse chronological order (#840 ordering bug).
  if(text.startsWith('/')&&!S.pendingFiles.length){
    const _parsedCmd=parseCommand(text);
    const _cmd=_parsedCmd?COMMANDS.find(c=>c.name===_parsedCmd.name):null;
    if(_cmd){
      let _pushedUser=false;
      if(!_cmd.noEcho){
        if(!S.session){await newSession();await renderSessionList();}
        S.messages.push({role:'user',content:text,_ts:Date.now()/1000});
        _pushedUser=true;
        renderMessages();
      }
      // Run the handler directly (we already looked it up).  If it returns
      // false it's opting out — e.g. /reasoning <level> falls through so the
      // agent sees the raw text.  Roll back the echo push in that case so
      // the normal send path doesn't duplicate it.
      if(_cmd.fn(_parsedCmd.args)===false){
        if(_pushedUser){S.messages.pop();renderMessages();}
        // Fall through to normal send path
      } else {
        $('msg').value='';autoResize();_collapseExpandIfOpen();hideCmdDropdown();return;
      }
    }
    if(_parsedCmd&&!_cmd){
      const _agentCmd=typeof getAgentCommandMetadata==='function'
        ? await getAgentCommandMetadata(_parsedCmd.name)
        : null;
      if(_agentCmd&&_agentCmd.cli_only){
        if(!S.session){await newSession();await renderSessionList();}
        S.messages.push({role:'user',content:text,_ts:Date.now()/1000});
        S.messages.push({role:'assistant',content:cliOnlyCommandResponse(_parsedCmd.name,_agentCmd),_ts:Date.now()/1000});
        renderMessages();
        $('msg').value='';autoResize();_collapseExpandIfOpen();hideCmdDropdown();return;
      }
      if(_agentCmd&&_agentCmd.category==='Plugin'){
        if(!S.session){await newSession();await renderSessionList();}
        S.messages.push({role:'user',content:text,_ts:Date.now()/1000});
        let _pluginOutput='(no output)';
        try{
          _pluginOutput=typeof executeAgentPluginCommand==='function'
            ? await executeAgentPluginCommand(text,_agentCmd)
            : 'Plugin command runtime unavailable in WebUI.';
        }catch(e){
          _pluginOutput=`Plugin command error: ${e&&e.message||e}`;
        }
        S.messages.push({role:'assistant',content:String(_pluginOutput||'(no output)'),_ts:Date.now()/1000});
        renderMessages();
        $('msg').value='';autoResize();hideCmdDropdown();return;
      }
    }
  }
  if(!S.session){await newSession();await renderSessionList();}

  const activeSid=S.session.session_id;

  setComposerStatus(S.pendingFiles&&S.pendingFiles.length?'Dateien werden hochgeladen…':'Senden wird vorbereitet…',true);
  let uploaded=[];
  try{uploaded=await uploadPendingFiles();}
  catch(e){
    setComposerStatus(`Upload error: ${e.message}`,false);
    if(typeof showToast==='function') showToast(`Upload failed: ${e.message}`,5000,'error');
    $('msg').value = originalText||text;
    autoResize();
    S.pendingFiles=[...originalFiles];
    renderTray();
    return;
  }
  setComposerStatus('Senden an Server…', true);

  const uploadedNames=uploaded.map(u=>u.name||u);
  const uploadedPaths=uploaded.map(u=>u&&u.is_image?(u.name||u.filename||u):(u.path||u.name||u));
  let msgText=text;
  if(uploaded.length&&!msgText)msgText=`I've uploaded ${uploaded.length} file(s): ${uploadedPaths.join(', ')}`;
  else if(uploaded.length)msgText=`${text}\n\n[Attached files: ${uploadedPaths.join(', ')}]`;
  if(!msgText){setComposerStatus('Nothing to send');return;}
  const selectedWorkspaceSlug=String(
    (S.session&&(S.session.workspace_slug||S.session.space_slug||S.session.space))||
    (typeof _activeSpace!=='undefined'&&_activeSpace)||
    ''
  ).trim().toLowerCase();
  if(_gameModeWouldBlockClientModel(selectedModelState.model,selectedModelState.model_provider,selectedWorkspaceSlug)){
    _showGameModeClientBlock();
    return;
  }

  $('msg').value='';autoResize();_collapseExpandIfOpen();
  // Save to prompt history (non-command user messages only)
  if(text&&!text.startsWith('/')&&typeof window._pushPromptHistory==='function') window._pushPromptHistory(text);
  // Clear persisted composer draft since message was sent.
  if (activeSid && typeof _clearComposerDraft === 'function') _clearComposerDraft(activeSid);
  const displayText=text||(uploaded.length?`Uploaded: ${uploadedNames.join(', ')}`:'(file upload)');
  const userMsg={role:'user',content:displayText,attachments:uploaded.length?uploadedNames:undefined,_ts:Date.now()/1000};
  S.toolCalls=[];  // clear tool calls from previous turn
  clearLiveToolCards();  // clear any leftover live cards from last turn
  // ── Terminal live streaming: reset on new turn ──
  if(typeof TerminalStream!=='undefined' && TerminalStream.disconnect){
    TerminalStream.disconnect();
  }
  S.messages.push(userMsg);renderMessages();setBusy(true);appendThinking();
  // First optimistic pass: make the local user turn visible before /api/chat/start
  // can save pending state on the server.
  if(typeof upsertActiveSessionForLocalTurn==='function'){
    upsertActiveSessionForLocalTurn({title:displayText.slice(0,64),messageCount:S.messages.length,timestampMs:Date.now()});
  }
  const ownerWorkspaceSlug=String(
    (S.session&&(S.session.workspace_slug||S.session.space_slug||S.session.space))||
    (typeof _activeSpace!=='undefined'&&_activeSpace)||
    ''
  ).trim().toLowerCase();
  INFLIGHT[activeSid]={messages:[...S.messages],uploaded:uploadedNames,toolCalls:[],workspace_slug:ownerWorkspaceSlug};
  if(typeof saveInflightState==='function'){
    saveInflightState(activeSid,{streamId:null,messages:INFLIGHT[activeSid].messages,uploaded:uploadedNames,toolCalls:[],workspace_slug:ownerWorkspaceSlug});
  }
  if(typeof renderSessionListFromCache==='function') renderSessionListFromCache();
  startApprovalPolling(activeSid);
  if(typeof _ensureSubagentPolling==='function') _ensureSubagentPolling();
  startClarifyPolling(activeSid);
  _fetchYoloState(activeSid);  // sync YOLO pill with backend state
  S.activeStreamId = null;  // will be set after stream starts
  if(typeof updateSendBtn==='function') updateSendBtn();

  // Set provisional title from user message immediately so session appears
  // in the sidebar right away with a meaningful name (server may refine later)
  if(S.session&&(S.session.title==='Untitled'||!S.session.title)){
    const provisionalTitle=displayText.slice(0,64);
    S.session.title=provisionalTitle;
    syncTopbar();
    // Persist it in the background; keep the optimistic sidebar cache as the
    // immediate source of truth until /api/chat/start saves pending state.
    api('/api/session/rename',{method:'POST',body:JSON.stringify({
      session_id:activeSid, title:provisionalTitle
    })}).catch(()=>{});  // fire-and-forget, server refines on done
    if(typeof upsertActiveSessionForLocalTurn==='function'){
      // Second optimistic pass: carry the provisional title into the cached row
      // without re-fetching /api/sessions before pending state exists server-side.
      upsertActiveSessionForLocalTurn({title:provisionalTitle,messageCount:S.messages.length,timestampMs:Date.now()});
    }else if(typeof renderSessionListFromCache==='function') renderSessionListFromCache();
  } else if(typeof upsertActiveSessionForLocalTurn==='function'){
    upsertActiveSessionForLocalTurn({title:S.session&&S.session.title||displayText.slice(0,64),messageCount:S.messages.length,timestampMs:Date.now()});
  } else {
    renderSessionListFromCache();  // ensure it's visible even if already titled
  }

  // Start the agent via POST, get a stream_id back
  let streamId;
  try{
    const startData=await api('/api/chat/start',{method:'POST',body:JSON.stringify({
      session_id:activeSid,message:msgText,
      model:selectedModelState.model,workspace:S.session.workspace,
      workspace_slug:selectedWorkspaceSlug,
      space_slug:selectedWorkspaceSlug,
      model_provider:selectedModelState.model_provider,
      profile:S.activeProfile||S.session.profile||'default',
      mode: window._composerMode||'action',
      chat_mode: S.mode||'chat',
      attachments:uploaded.length?uploaded:undefined,
      sandbox_disabled: window._sandboxDisabled || false
    })});

    if(startData.effective_model && S.session){
      S.session.model=startData.effective_model;
      S.session.model_provider=startData.effective_model_provider||S.session.model_provider||null;
      localStorage.setItem('sidekick-webui-model', startData.effective_model);
      if(typeof _writePersistedModelState==='function') _writePersistedModelState(startData.effective_model,S.session.model_provider||null);
      if($('modelSelect')) _applyModelToDropdown(startData.effective_model, $('modelSelect'),S.session.model_provider||null);
      if(typeof syncTopbar==='function') syncTopbar();
    }else if(startData.effective_model_provider && S.session){
      S.session.model_provider=startData.effective_model_provider;
      if(typeof _writePersistedModelState==='function') _writePersistedModelState(S.session.model||'',S.session.model_provider||null);
      if($('modelSelect')&&typeof _applyModelToDropdown==='function') _applyModelToDropdown(S.session.model||'', $('modelSelect'), S.session.model_provider||null);
      if(typeof syncModelChip==='function') syncModelChip();
      if(typeof syncTopbar==='function') syncTopbar();
    }
    streamId=startData.stream_id;
    S.activeStreamId = streamId;
    // setBusy(true) already ran with activeStreamId=null; refresh now that we
    // have a stream id so the primary button can switch to Stop (see getComposerPrimaryAction).
    if(typeof updateSendBtn==='function') updateSendBtn();
    if(S.session&&typeof startData.pending_started_at==='number'){
      S.session.pending_started_at=startData.pending_started_at;
    }
    if(S.session&&S.session.session_id===activeSid){
      S.session.active_stream_id = streamId;
    }
    if(typeof upsertActiveSessionForLocalTurn==='function'){
      // Third optimistic pass: stream_id is now known, so the row can reconcile
      // against real active-stream metadata before the background refresh lands.
      upsertActiveSessionForLocalTurn({title:S.session&&S.session.title||displayText.slice(0,64),messageCount:S.messages.length,timestampMs:Date.now()});
    }
    markInflight(activeSid, streamId);
    if(typeof saveInflightState==='function'){
      saveInflightState(activeSid,{streamId,messages:INFLIGHT[activeSid].messages,uploaded:uploadedNames,toolCalls:INFLIGHT[activeSid].toolCalls||[],workspace_slug:ownerWorkspaceSlug});
    }
    // Refresh session list so background streaming indicators appear immediately for the
    // session that was just started and any others that may already be running.
    if(typeof renderSessionList === 'function') {
      void renderSessionList();
    }
  }catch(e){
    const errMsg=String((e&&e.message)||'');
    const gameModeBlocked=!!(e&&e.data&&e.data.error&&e.data.error.code==='game_mode_enabled');
    if(gameModeBlocked){
      delete INFLIGHT[activeSid];
      if(typeof clearInflightState==='function') clearInflightState(activeSid);
      stopApprovalPolling();
      stopClarifyPolling();
      if(!_approvalSessionId || _approvalSessionId===activeSid) hideApprovalCard(true);
      removeThinking();
      if(!_clarifySessionId || _clarifySessionId===activeSid) hideClarifyCard(true, 'terminal');
      if(S.messages[S.messages.length-1]===userMsg) S.messages.pop();
      const msgBox=$('msg');
      if(msgBox&&text){
        msgBox.value=text;
        if(typeof autoResize==='function') autoResize();
      }
      setBusy(false);
      if(typeof updateSendBtn==='function') updateSendBtn();
      setComposerStatus(errMsg,false);
      if(typeof showToast==='function') showToast(errMsg,5000,'warning');
      if(typeof renderMessages==='function') renderMessages();
      if(typeof clearOptimisticSessionStreaming==='function') clearOptimisticSessionStreaming(activeSid);
      if(typeof renderSessionList==='function') void renderSessionList();
      return;
    }
    const conflictActiveStream=/session already has an active stream/i.test(errMsg);
    if(conflictActiveStream){
      let conflictStreamId='';
      try{
        const conflictBody=JSON.parse(e&&e.body||'{}');
        conflictStreamId=String(conflictBody.active_stream_id||'');
      }catch(_){}
      delete INFLIGHT[activeSid];
      if(typeof clearInflightState==='function') clearInflightState(activeSid);
      stopApprovalPolling();
      stopClarifyPolling();
      // Keep the user's attempted turn by queueing it for after the current run.
      queueSessionMessage(activeSid,{text:msgText,files:[],model:selectedModelState.model,model_provider:selectedModelState.model_provider,profile:S.activeProfile||'default'});
      updateQueueBadge(activeSid);
      if(conflictStreamId&&S.session&&S.session.session_id===activeSid){
        S.activeStreamId=conflictStreamId;
        S.session.active_stream_id=conflictStreamId;
        setBusy(true);
        if(typeof updateSendBtn==='function') updateSendBtn();
        if(typeof markInflight==='function') markInflight(activeSid, conflictStreamId);
        attachLiveStream(activeSid, conflictStreamId, [], {reconnecting:true});
        setComposerStatus('Sendeanfrage läuft…', true);
        return;
      }
      showToast('Current session is still running. Queued your message.',2600);
      return;
    }

    delete INFLIGHT[activeSid];
    stopApprovalPolling();
    stopClarifyPolling();
    // Only hide approval card if it belongs to the session that just finished
    if(!_approvalSessionId || _approvalSessionId===activeSid) hideApprovalCard(true);removeThinking();
    if(!_clarifySessionId || _clarifySessionId===activeSid) hideClarifyCard(true, 'terminal');
      S.messages.push({role:'assistant',content:`**Error:** ${errMsg}`});
      _queueDrainSid=activeSid;renderMessages();setBusy(false);setComposerStatus(`Error: ${errMsg}`);
      if(typeof clearOptimisticSessionStreaming==='function') clearOptimisticSessionStreaming(activeSid);
    // Reconcile with server truth after immediately clearing the optimistic spinner.
    if(typeof renderSessionList==='function') void renderSessionList();
    return;
  }

  // Open SSE stream and render tokens live
  attachLiveStream(activeSid, streamId, uploadedNames);
  setComposerStatus('');
  } finally{
    _isSendingChat=false;
    if(typeof updateSendBtn==='function') updateSendBtn();
  }

}

const LIVE_STREAMS={};

function closeLiveStream(sessionId, streamId){
  const live=LIVE_STREAMS[sessionId];
  if(!live) return;
  if(streamId&&live.streamId!==streamId) return;
  try{live.source.close();}catch(_){ }
  delete LIVE_STREAMS[sessionId];
}

// ── Goal State (global, shared across sessions) ──
window._goalState=null;

function _goalBudgetLabel(value){
  if(value===null||value===undefined||value==='') return '∞';
  const n=Number(value);
  return Number.isFinite(n)&&n>0 ? String(n) : '∞';
}

function _goalBudgetControls(){
  return {
    input:$('goalTurnBudgetInput'),
    unlimited:$('goalUnlimitedBtn'),
    wrap:$('goalBudgetControl'),
  };
}

function _syncGoalBudgetUI(isUnlimited){
  const {input, unlimited, wrap}=_goalBudgetControls();
  if(!input||!unlimited) return;
  const active=!!isUnlimited;
  input.disabled=active;
  unlimited.classList.toggle('active', active);
  unlimited.setAttribute('aria-pressed', active?'true':'false');
  if(wrap)wrap.classList.toggle('is-unlimited', active);
}

function _setGoalBudgetDefaults(){
  const {input}=_goalBudgetControls();
  if(input) input.value='20';
  _syncGoalBudgetUI(false);
}

function _readGoalBudgetSelection(){
  const {input, unlimited}=_goalBudgetControls();
  const isUnlimited=!!(unlimited&&unlimited.classList.contains('active'));
  if(isUnlimited) return {unlimited:true, max_turns:null};
  const raw=input?parseInt(String(input.value||'').trim(),10):NaN;
  const maxTurns=Number.isFinite(raw)&&raw>0?raw:20;
  if(input) input.value=String(maxTurns);
  return {unlimited:false, max_turns:maxTurns};
}

function _toggleGoalBudgetUnlimited(){
  const {unlimited}=_goalBudgetControls();
  const next=!(unlimited&&unlimited.classList.contains('active'));
  _syncGoalBudgetUI(next);
}

function _renderGoalBanner(){
  const banner=$('goalBanner');
  const icon=$('goalBannerIcon');
  const text=$('goalBannerText');
  const turns=$('goalBannerTurns');
  const pauseBtn=$('goalBtnPause');
  const resumeBtn=$('goalBtnResume');
  const clearBtn=$('goalBtnClear');
  const toggleBtn=$('btnGoalModeToggle');
  if(!banner)return;
  const gs=window._goalState;
  if(!gs||!gs.goal||gs.status==='cleared'||gs.status==='none'){
    banner.style.display='none';
    if(toggleBtn)toggleBtn.classList.remove('active');
    return;
  }
  // Space-scoping: nur anzeigen wenn Goal in diesem Space gesetzt wurde
  if(gs.space){
    const currentSpace=typeof _activeSpace!=='undefined'?_activeSpace:
      localStorage.getItem('sidekick-active-workspace')||'nova';
    if(gs.space!==currentSpace){
      banner.style.display='none';
      return;
    }
  }
  // Session-scoping: nur anzeigen wenn Goal in dieser Session gesetzt wurde
  const activeSid=S&&S.session&&S.session.session_id;
  if(gs.session_id&&activeSid&&gs.session_id!==activeSid){
    banner.style.display='none';
    return;
  }
  const status=gs.status||'active';
  banner.style.display='flex';
  banner.className='goal-banner '+status;
  if(status==='active'){icon.textContent='⊙';}
  else if(status==='paused'){icon.textContent='⏸';}
  else if(status==='done'){icon.textContent='✓';}
  else {icon.textContent='⊙';}
  text.textContent=gs.goal.length>90?gs.goal.slice(0,87)+'…':gs.goal;
  const tu=typeof gs.turns_used==='number'?gs.turns_used:0;
  turns.textContent='('+tu+'/'+_goalBudgetLabel(gs.max_turns)+')';
  pauseBtn.style.display=(status==='active')?'':'none';
  resumeBtn.style.display=(status==='paused')?'':'none';
  clearBtn.style.display='';
  if(toggleBtn)toggleBtn.classList.remove('active');
}

function _updateGoalState(state){
  if(!state){
    window._goalState=null;
    _renderGoalBanner();
    return;
  }
  const activeSid=S&&S.session&&S.session.session_id;
  const gs={
    goal:String(state.goal||'').trim(),
    status:String(state.status||'').trim(),
    turns_used:typeof state.turns_used==='number'?state.turns_used:0,
    max_turns:state.max_turns===null||state.max_turns===undefined
      ? null
      : (Number.isFinite(Number(state.max_turns))&&Number(state.max_turns)>0 ? Number(state.max_turns) : null),
    last_verdict:state.last_verdict||null,
    last_reason:state.last_reason||null,
    paused_reason:state.paused_reason||null,
    session_id:state.session_id||activeSid||null,
    space:state.space||(typeof _activeSpace!=='undefined'?_activeSpace:
      localStorage.getItem('sidekick-active-workspace')||null),
  };
  window._goalState=gs;
  try{localStorage.setItem('sidekick-webui-goal-state',JSON.stringify(gs));}catch(_){}
  _renderGoalBanner();
}

function _clearGoalState(){
  window._goalState=null;
  try{localStorage.removeItem('sidekick-webui-goal-state');}catch(_){}
  _renderGoalBanner();
}

function _toggleGoalMode(){
  const box=$('composerBox');
  const input=$('goalInputField');
  const toggle=$('btnGoalModeToggle');
  if(!box)return;
  const isActive=box.classList.contains('goal-mode');
  if(isActive){
    box.classList.remove('goal-mode');
    if(input)input.value='';
    if(toggle)toggle.classList.remove('active');
    _setGoalBudgetDefaults();
    $('msg').focus();
  }else{
    box.classList.add('goal-mode');
    if(toggle)toggle.classList.add('active');
    _setGoalBudgetDefaults();
    if(input){input.value='';input.focus();}
  }
}

function _exitGoalMode(){
  const box=$('composerBox');
  const toggle=$('btnGoalModeToggle');
  if(!box)return;
  box.classList.remove('goal-mode');
  if(toggle)toggle.classList.remove('active');
  const input=$('goalInputField');
  if(input)input.value='';
  _setGoalBudgetDefaults();
  $('msg').focus();
}

function _submitGoal(){
  const input=$('goalInputField');
  if(!input)return;
  const text=input.value.trim();
  if(!text){showToast('Please enter a goal description.',2000);input.focus();return;}
  const budget=_readGoalBudgetSelection();
  _exitGoalMode();
  showToast('🎯 Setting goal…',1500);
  // Small delay so the UI state transition settles
  setTimeout(function(){
    if(typeof cmdGoal==='function')cmdGoal({text, ...budget});
    else showToast('Goal command not available — try /goal '+text,3000);
  },100);
}

// On init: load persisted goal state from localStorage
(function _initGoalState(){
  try{
    const saved=localStorage.getItem('sidekick-webui-goal-state');
    if(saved){
      const parsed=JSON.parse(saved);
      // Migration: alte Einträge ohne space-Feld korrigieren
      if(parsed&&parsed.goal&&parsed.status&&parsed.status!=='cleared'){
        if(!parsed.space){
          // Altes Format ohne Space → verwerfen, wird beim nächsten /goal neu gesetzt
          localStorage.removeItem('sidekick-webui-goal-state');
          window._goalState=null;
        }else{
          window._goalState=parsed;
        }
      }else{
        localStorage.removeItem('sidekick-webui-goal-state');
      }
    }
  }catch(_){}
  // Banner mit Verzögerung rendern — _activeSpace aus spaces.js muss geladen sein
  setTimeout(function(){
    _renderGoalBanner();
  },100);
})();

// Export goal UI functions globally (called from HTML onclick and commands.js)
window._renderGoalBanner=_renderGoalBanner;
window._updateGoalState=_updateGoalState;
window._clearGoalState=_clearGoalState;
window._toggleGoalMode=_toggleGoalMode;
window._exitGoalMode=_exitGoalMode;
window._submitGoal=_submitGoal;
window._togglePlanMode=_togglePlanMode;

// ── Goal: keydown handler for goal input field ──
document.addEventListener('keydown',function _goalInputKeydown(e){
  const input=$('goalInputField');
  if(!input||input!==document.activeElement)return;
  if(e.key==='Enter'&&!e.shiftKey){
    e.preventDefault();
    _submitGoal();
  }
  if(e.key==='Escape'){
    e.preventDefault();
    _exitGoalMode();
  }
});

// ── Plan Mode ─────────────────────────────────────────────
window._planMode=false;
if(typeof localStorage!=='undefined'){
  const storedMode=localStorage.getItem('sidekick-webui-composer-mode');
  window._planMode = storedMode==='plan';
}
if(!window._composerMode) window._composerMode = window._planMode ? 'plan' : 'action';

function _togglePlanMode(){
  const nextMode=window._planMode?'action':'plan';
  if(typeof setComposerMode==='function'){
    try {
      setComposerMode(nextMode);
    } catch(_){}
  }else{
    window._composerMode=nextMode;
    window._planMode=nextMode==='plan';
  }
  _renderPlanBanner();
  const box=$('composerBox');
  if(box)box.classList.toggle('plan-mode',window._planMode);
  if(typeof setComposerMode!=='function'){
    showToast(window._planMode?'🧠 Plan Mode ON':'🧠 Plan Mode OFF',1500);
  }
}

function _renderPlanBanner(){
  const banner=$('planBanner');
  if(!banner)return;
  if(window._planMode){
    banner.style.display='flex';
  }else{
    banner.style.display='none';
  }
}

function _eventSourceUrl(path){
  const url=new URL(path,document.baseURI||location.href);
  try{
    const token=(typeof _dashboardSessionToken==='function')?_dashboardSessionToken():'';
    if(token) url.searchParams.set('token',token);
  }catch(_){}
  try{
    if(!url.searchParams.get('workspace')&&typeof _activeWorkspaceSlug==='function'){
      const slug=_activeWorkspaceSlug();
      if(slug) url.searchParams.set('workspace',slug);
    }
  }catch(_){}
  return url.href;
}

function attachLiveStream(activeSid, streamId, uploaded=[], options={}){
  if(!activeSid||!streamId) return;
  const reconnecting=!!options.reconnecting;
  const ownerVisibleNow=!!(S.session&&S.session.session_id===activeSid);
  const ownerWorkspaceSlug=String(
    options.workspace_slug||options.workspace||
    (S.session&&S.session.session_id===activeSid&&(S.session.workspace_slug||S.session.space_slug||S.session.space))||
    (INFLIGHT[activeSid]&&(INFLIGHT[activeSid].workspace_slug||INFLIGHT[activeSid].space_slug||INFLIGHT[activeSid].space))||
    (typeof _activeSpace!=='undefined'&&_activeSpace)||
    ''
  ).trim().toLowerCase();
  if(!reconnecting) _clearPlanState();
  // Restore the live state immediately on reattach so the chat shell knows the
  // session is still active before the SSE transport has produced a new event.
  if(ownerVisibleNow){
    S.activeStreamId = streamId;
    if (typeof setBusy === 'function') setBusy(true);
    else S.busy = true;
    if(S.session) S.session.active_stream_id = streamId;
    if(typeof updateSendBtn==='function') updateSendBtn();
  }
  if (ownerVisibleNow && reconnecting && typeof appendThinking === 'function') {
    try { appendThinking(); } catch (_) {}
  }
  if(!INFLIGHT[activeSid]) INFLIGHT[activeSid]={messages:ownerVisibleNow?[...S.messages]:[],uploaded:[...uploaded],toolCalls:[],workspace_slug:ownerWorkspaceSlug};
  else {
    if(uploaded.length) INFLIGHT[activeSid].uploaded=[...uploaded];
    if(!Array.isArray(INFLIGHT[activeSid].toolCalls)) INFLIGHT[activeSid].toolCalls=[];
    if(ownerWorkspaceSlug) INFLIGHT[activeSid].workspace_slug=ownerWorkspaceSlug;
  }
  const existingLive=LIVE_STREAMS[activeSid];
  if(
    existingLive&&existingLive.streamId===streamId&&existingLive.source&&
    // A same-stream transport can be reused unless the browser has already
    // marked it closed; closed streams must still fall through to reopen.
    (typeof EventSource==='undefined'||existingLive.source.readyState!==EventSource.CLOSED)
  ){
    return;
  }
  closeLiveStream(activeSid);

let assistantText='';
  let reasoningText='';
  let liveReasoningText='';
let _latestGoalStatus=null;
  let _pendingGoalContinuation=null;

  // Task progress tracking (from 'progress' SSE events)
  let _progressData=null;  // {current, total, label, done}
  let _progressEl=null;    // .task-progress DOM element
  let assistantRow=null;
  let assistantBody=null;
  let segmentStart=0;      // char offset in assistantText where current segment begins
  let _freshSegment=false; // true after a tool call — forces a new DOM segment
  // streaming-markdown state: incremental DOM-building parser per segment
  let _smdParser=null;     // current smd parser instance (null until first content)
  let _smdWrittenLen=0;    // how many chars of displayText have been fed to smd parser
  let _smdWrittenText='';  // exact displayText snapshot used for prefix-alignment checks
  let _lastFallbackRenderedText=null; // avoids no-op innerHTML rewrites when smd is unavailable
  // On reconnect, the assistantBody already has partial smd-rendered content.
  // We clear it on first new token and restart the parser from the reconnect point.
  let _smdReconnect=reconnecting;
  // Thinking tag patterns for streaming display
  const _thinkPairs=[
    {open:'<think>',close:'</think>'},
    {open:'<|channel>thought\n',close:'<channel|>'},
    {open:'<|turn|>thinking\n',close:'<turn|>'}  // Gemma 4
  ];

  function _isActiveSession(){
    return !!(S.session&&S.session.session_id===activeSid);
  }
  function _clearActivePaneInflightIfOwner(){
    if(_isActiveSession()) clearInflight();
  }
  function _approvalBelongsToOwner(){
    return _approvalSessionId===activeSid||(!_approvalSessionId&&_isActiveSession());
  }
  function _clarifyBelongsToOwner(){
    return _clarifySessionId===activeSid||(!_clarifySessionId&&_isActiveSession());
  }
  function _clearApprovalForOwner(){
    _clearApprovalPendingForSession(activeSid);
    if(!_approvalBelongsToOwner()) return;
    stopApprovalPolling();
    hideApprovalCard(true);
  }
  function _clearClarifyForOwner(reason){
    _clearClarifyPendingForSession(activeSid);
    if(!_clarifyBelongsToOwner()) return;
    stopClarifyPolling();
    hideClarifyCard(true, reason||'terminal');
  }
  function _clearOwnerInflightState(){
    delete INFLIGHT[activeSid];
    clearInflightState(activeSid);
    _clearActivePaneInflightIfOwner();
  }
  function _setActivePaneIdleIfOwner(){
    if(_isActiveSession()||!S.session||!INFLIGHT[S.session.session_id]){
      setBusy(false);
      setComposerStatus('');
      if(typeof setStatus==='function') setStatus('');
    }
  }
  function _capContent(text, maxLen){
    if(!text||typeof text!=='string') return text;
    return text.length<=maxLen?text:text.slice(0,maxLen)+'…[truncated]';
  }
  function _capMessageForPersist(msg){
    if(!msg||typeof msg!=='object') return msg;
    const capped={...msg};
    // Cap content fields to 500 chars — inflight state is only for reload recovery,
    // not a full transcript archive. This prevents LocalStorage quota exhaustion.
    if(typeof capped.content==='string') capped.content=_capContent(capped.content,500);
    if(typeof capped.text==='string') capped.text=_capContent(capped.text,500);
    if(Array.isArray(capped.tool_calls)){
      capped.tool_calls=capped.tool_calls.map(tc=>{
        if(!tc||typeof tc!=='object') return tc;
        const c={...tc};
        if(c.toolResult&&typeof c.toolResult.content==='string'&&c.toolResult.content.length>500)
          c.toolResult={content:_capContent(c.toolResult.content,500)};
        return c;
      });
    }
    return capped;
  }
  function persistInflightState(){
    const inflight=INFLIGHT[activeSid];
    if(!inflight||typeof saveInflightState!=='function') return;
    const cappedMessages=(inflight.messages||[]).map(_capMessageForPersist);
    saveInflightState(activeSid,{
      streamId,
      messages:cappedMessages,
      uploaded:inflight.uploaded||[...uploaded],
      toolCalls:inflight.toolCalls||[],
      workspace_slug:inflight.workspace_slug||ownerWorkspaceSlug||'',
    });
  }
  // Throttled variant for token-by-token updates. persistInflightState()
  // calls saveInflightState() which does JSON.parse + JSON.stringify + write
  // on the entire inflight map every call. On a fast model at 60 tok/s with
  // a 10KB messages array this is ~36MB of JSON churn per second — a major
  // GC pressure source that causes the renderer to crash under load.
  // State transitions (tool events, done, error) still call persistInflightState()
  // directly so no more than 2s of progress is lost on a crash.
  let _persistTimer=null;
  function _throttledPersist(){
    if(_persistTimer) return;
    _persistTimer=setTimeout(()=>{_persistTimer=null;persistInflightState();},2000);
  }
  function _closeSource(){
    closeLiveStream(activeSid, streamId);
  }
  function _clearReconnectTimers(){
    if(_streamReconnectTimer){clearTimeout(_streamReconnectTimer);_streamReconnectTimer=null;}
    if(_streamSettledPollTimer){clearTimeout(_streamSettledPollTimer);_streamSettledPollTimer=null;}
  }
  function _openEventSource(){
    const es=new EventSource(_eventSourceUrl(_ownerScopedApiPath(`api/chat/stream?stream_id=${encodeURIComponent(streamId)}`)),{withCredentials:true});
    _wireSSE(es);
    return es;
  }
  function _ownerScopedApiPath(path){
    if(!ownerWorkspaceSlug) return path;
    try{
      const url=new URL(String(path||'').replace(/^\//,''),document.baseURI||location.href);
      url.searchParams.set('workspace',ownerWorkspaceSlug);
      return url.pathname.replace(/^\//,'')+url.search;
    }catch(_){
      const sep=String(path||'').includes('?')?'&':'?';
      return String(path||'')+sep+'workspace='+encodeURIComponent(ownerWorkspaceSlug);
    }
  }
  function _goalContinuationRequestBody(goalNext){
    return {
      session_id:goalNext.sid,
      message:goalNext.text,
      model:goalNext.model||undefined,
      model_provider:goalNext.model_provider||null,
      profile:goalNext.profile||'default',
      workspace:goalNext.workspace||undefined,
      mode:'action',
      chat_mode:S.mode||'chat',
      attachments:[],
      sandbox_disabled:window._sandboxDisabled||false,
    };
  }
  function _startGoalContinuation(goalNext, attempt=0){
    if(!goalNext||!goalNext.sid||!goalNext.text) return;
    const visible=!!(S.session&&S.session.session_id===goalNext.sid);
    if(visible&&S.busy){
      _queueDrainSid=goalNext.sid;
      queueSessionMessage(goalNext.sid,{
        text:goalNext.text,
        files:[],
        model:goalNext.model,
        model_provider:goalNext.model_provider,
        profile:goalNext.profile,
      });
      if(typeof updateQueueBadge==='function')updateQueueBadge(goalNext.sid);
      return;
    }
    api(_ownerScopedApiPath('/api/chat/start'),{
      method:'POST',
      body:JSON.stringify(_goalContinuationRequestBody(goalNext)),
    }).then((startData)=>{
      const nextStreamId=startData&&startData.stream_id;
      if(!nextStreamId) return;
      if(typeof markInflight==='function') markInflight(goalNext.sid,nextStreamId);
      if(typeof saveInflightState==='function'){
        saveInflightState(goalNext.sid,{
          streamId:nextStreamId,
          messages:[],
          uploaded:[],
          toolCalls:[],
          workspace_slug:goalNext.workspace_slug||ownerWorkspaceSlug||'',
        });
      }
      attachLiveStream(goalNext.sid,nextStreamId,[],{
        reconnecting:true,
        workspace_slug:goalNext.workspace_slug||ownerWorkspaceSlug||'',
        workspace:goalNext.workspace||'',
        model:goalNext.model||'',
        model_provider:goalNext.model_provider||null,
        profile:goalNext.profile||'default',
      });
      if(typeof renderSessionList==='function') void renderSessionList();
    }).catch((e)=>{
      const msg=String((e&&e.message)||'');
      if(/already has an active stream/i.test(msg)&&attempt<8){
        setTimeout(()=>_startGoalContinuation(goalNext,attempt+1),500+attempt*250);
        return;
      }
      queueSessionMessage(goalNext.sid,{
        text:goalNext.text,
        files:[],
        model:goalNext.model,
        model_provider:goalNext.model_provider,
        profile:goalNext.profile,
      });
      if(typeof updateQueueBadge==='function')updateQueueBadge(goalNext.sid);
      if(typeof showToast==='function')showToast('Goal continuation queued; it will continue when this session is opened.',3500,'warning');
    });
  }
  function _pollSettledSessionUntilDone(){
    if(_streamSettledPollTimer||_terminalStateReached||_streamFinalized) return;
    setComposerStatus('Live connection lost; waiting for completion...');
    const tick=async()=>{
      _streamSettledPollTimer=null;
      if(_terminalStateReached||_streamFinalized) return;
      if(await _restoreSettledSession()) return;
      try{
        const st=await api(_ownerScopedApiPath(`/api/chat/stream/status?stream_id=${encodeURIComponent(streamId)}`));
        if(st&&st.active){
          _scheduleStreamReconnect(2500);
          _streamSettledPollTimer=setTimeout(tick,5000);
          return;
        }
      }catch(_){
        if(_deferStreamErrorIfOffline()) return;
      }
      if(await _restoreSettledSession()) return;
      _handleStreamError();
    };
    _streamSettledPollTimer=setTimeout(tick,2000);
  }
  function _scheduleStreamReconnect(delayMs=1200){
    if(_streamReconnectTimer||_terminalStateReached||_streamFinalized) return;
    _streamReconnectTimer=setTimeout(async()=>{
      _streamReconnectTimer=null;
      if(_terminalStateReached||_streamFinalized) return;
      try{
        const st=await api(_ownerScopedApiPath(`/api/chat/stream/status?stream_id=${encodeURIComponent(streamId)}`));
        if(st&&st.active){
          if(_streamReconnectAttempts>=_STREAM_RECONNECT_MAX_ATTEMPTS){
            _pollSettledSessionUntilDone();
            return;
          }
          _streamReconnectAttempts++;
          setComposerStatus(`Reconnecting live stream (${_streamReconnectAttempts}/${_STREAM_RECONNECT_MAX_ATTEMPTS})...`);
          try{
            _openEventSource();
            return;
          }catch(_){
            _scheduleStreamReconnect(Math.min(5000,1200+_streamReconnectAttempts*350));
            return;
          }
        }
      }catch(_){
        if(_deferStreamErrorIfOffline()) return;
        _streamReconnectAttempts++;
        if(_streamReconnectAttempts>=_STREAM_RECONNECT_MAX_ATTEMPTS){
          _pollSettledSessionUntilDone();
          return;
        }
        _scheduleStreamReconnect(Math.min(5000,1200+_streamReconnectAttempts*350));
        return;
      }
      if(await _restoreSettledSession()) return;
      if(_deferStreamErrorIfOffline()) return;
      _handleStreamError();
    },delayMs);
  }
  function syncInflightAssistantMessage(){
    const inflight=INFLIGHT[activeSid];
    if(!inflight) return;
    if(!Array.isArray(inflight.messages)) inflight.messages=[];
    let assistantIdx=-1;
    for(let i=inflight.messages.length-1;i>=0;i--){
      const msg=inflight.messages[i];
      if(msg&&msg.role==='assistant'&&msg._live){assistantIdx=i;break;}
    }
    const ts=Date.now()/1000;
    if(assistantIdx>=0){
      inflight.messages[assistantIdx].content=assistantText;
      inflight.messages[assistantIdx].reasoning=reasoningText||undefined;
      inflight.messages[assistantIdx]._ts=inflight.messages[assistantIdx]._ts||ts;
      _throttledPersist();
      return;
    }
    inflight.messages.push({role:'assistant',content:assistantText,reasoning:reasoningText||undefined,_live:true,_ts:ts});
    _throttledPersist();
  }
  function ensureAssistantRow(force=false){
    if(!_isActiveSession()) return;
    if(assistantRow&&!assistantRow.isConnected){assistantRow=null;assistantBody=null;}
    if(!force&&!assistantRow){
      const parsed=_parseStreamState();
      if(!String((parsed&&parsed.displayText)||'').trim()) return;
    }
    let turn=$('liveAssistantTurn');
    if(!turn){
      appendThinking();
      turn=$('liveAssistantTurn');
    }
    const blocks=(typeof _assistantTurnBlocks==='function')?_assistantTurnBlocks(turn):null;
    if(!blocks) return;
    if(!assistantRow){
      // Only reuse an existing segment on the very first creation (e.g. reconnect).
      // After a tool call _freshSegment=true, so we always create a new segment
      // below the tool card rather than re-attaching to the old one above it.
      if(!_freshSegment){
        const existing=blocks.querySelector('[data-live-assistant="1"]');
        if(existing){
          assistantRow=existing;
          assistantBody=existing.querySelector('.msg-body');
        }
      }
    }
    if(assistantRow){
      if(typeof placeLiveToolCardsHost==='function') placeLiveToolCardsHost();
      return;
    }

    const tr=$('toolRunningRow');if(tr)tr.remove();
    $('emptyState').style.display='none';
    assistantRow=document.createElement('div');
    assistantRow.className='assistant-segment';
    assistantRow.setAttribute('data-live-assistant','1');
    assistantBody=document.createElement('div');assistantBody.className='msg-body';
    assistantRow.appendChild(assistantBody);
    // Add stop button on live assistant message during streaming
    if(S.busy && typeof cancelStream==='function' && typeof li==='function'){
      const _stopBtn=document.createElement('button');
      _stopBtn.className='msg-action-btn msg-stop-btn';
      _stopBtn.title=typeof t==='function'?t('stop_response'):'Stop response';
      _stopBtn.innerHTML=li('square',13);
      _stopBtn.onclick=function(){cancelStream();};
      assistantRow.appendChild(_stopBtn);
    }
    blocks.appendChild(assistantRow);
    _freshSegment=false; // consumed — next reuse check is normal again
  }

  // ── Shared SSE handler wiring (used for initial connection and reconnect) ──
  const _STREAM_RECONNECT_MAX_ATTEMPTS=24;
  let _streamReconnectAttempts=0;
  let _streamReconnectTimer=null;
  let _streamSettledPollTimer=null;
  let _terminalStateReached=false;

  // Bug A fix (#631): track whether the stream has been finalized so any rAF
  // scheduled by a trailing 'token'/'reasoning' event that arrives in the same
  // microtask batch as 'done' does not fire after renderMessages() has already
  // settled the DOM — which was causing the thinking card to reappear below
  // the final answer or the response to render twice.
  let _streamFinalized=false;
  let _pendingRafHandle=null;

  // rAF-throttled rendering: buffer tokens, render at most once per frame
  let _renderPending=false;
  // Extract display text from assistantText, stripping completed thinking blocks
  // and hiding content still inside an open thinking block.
  function _stripXmlToolCalls(s){
    // Strip <function_calls>...</function_calls> blocks (DeepSeek XML tool syntax).
    // These are processed as tool calls server-side; showing them raw in the bubble
    // looks broken. Also handles orphaned opening tags mid-stream. (#702)
    // Also handles DSML-prefixed variants from DeepSeek/Bedrock, including
    // spacing variants like "<｜DSML |function_calls" and truncated prefixes.
    if(!s) return s;
    const lo=String(s).toLowerCase();
    if(lo.indexOf('function_calls')===-1 && lo.indexOf('dsml')===-1) return s;
    // Support both plain <function_calls> and DSML-prefixed variants.
    s=s.replace(/<(?:\s*｜\s*DSML\s*[｜|]\s*)?function_calls>[\s\S]*?<\/(?:\s*｜\s*DSML\s*[｜|]\s*)?function_calls>/gi,'');
    // Also remove truncated opening tags (missing closing ">" at stream tail).
    s=s.replace(/<(?:\s*｜\s*DSML\s*[｜|]\s*)?function_calls(?:>|$)[\s\S]*$/i,'');
    // Remove malformed DSML tag fragments like "<｜DSML |" that can leak in tokens.
    s=s.replace(/<\s*｜\s*DSML\s*[｜|]\s*/gi,'');
    return s.trim();
  }
  function _streamDisplay(){
    const raw=_stripXmlToolCalls(assistantText);
    // Always run think-block stripping even when reasoningText is populated.
    // Some providers emit reasoning content via on_reasoning AND wrap it in
    // <think> tags in the token stream — the early-return caused the thinking
    // card and main response to show identical content (closes #852).
    for(const {open,close} of _thinkPairs){
      // Trim leading whitespace before checking for the open tag — some models
      // (e.g. MiniMax) emit newlines before <think>.
      const trimmed=raw.trimStart();
      if(trimmed.startsWith(open)){
        const ci=trimmed.indexOf(close,open.length);
        if(ci!==-1){
          // Thinking block complete — strip it, show the rest
          return trimmed.slice(ci+close.length).replace(/^\s+/,'');
        }
        // Still inside thinking block — show placeholder
        return '';
      }
      // Hide partial tag prefixes while streaming so users don't see
      // `<thi`, `<think`, etc. before the model finishes the token.
      if(open.startsWith(trimmed)) return '';
    }
    return raw;
  }
  function _parseStreamState(){
    const raw=_stripXmlToolCalls(assistantText);
    if(reasoningText){
      return {thinkingText:liveReasoningText, displayText:_streamDisplay(), inThinking:false};
    }
    for(const {open,close} of _thinkPairs){
      const trimmed=raw.trimStart();
      if(trimmed.startsWith(open)){
        const ci=trimmed.indexOf(close,open.length);
        if(ci!==-1){
          return {
            thinkingText: trimmed.slice(open.length, ci).trim(),
            displayText: trimmed.slice(ci+close.length).replace(/^\s+/,''),
            inThinking:false,
          };
        }
        return {
          thinkingText: trimmed.slice(open.length).trim(),
          displayText:'',
          inThinking:true,
        };
      }
      if(open.startsWith(trimmed)){
        return {thinkingText:'', displayText:'', inThinking:true};
      }
    }
    return {thinkingText:'', displayText:raw, inThinking:false};
  }
  function _renderLiveThinking(parsed){
    if(window._showThinking===false){removeThinking();return;}
    const text=(parsed&&parsed.thinkingText)||'';
    if(text||(parsed&&parsed.inThinking)){
      if(typeof updateThinking==='function') updateThinking(text||'Thinking…');
      else appendThinking();
      return;
    }
    // Only remove thinking if we're not in an active reasoning phase.
    // When reasoningText is set but liveReasoningText was just reset (post-tool),
    // don't wipe the finalized thinking card — it has no id anymore so
    // removeThinking() won't find it anyway, but guard explicitly.
    if(!reasoningText) removeThinking();
  }
  // Helper: create (or recreate) the smd parser bound to a given DOM element.
  // Called when assistantBody is first created and after each tool-call segment reset.
  function _smdNewParser(el){
    _smdWrittenLen=0;
    _smdWrittenText='';
    if(!window.smd){_smdParser=null;return;}
    const renderer=window.smd.default_renderer(el);
    _smdParser=window.smd.parser(renderer);
  }
  // Helper: end the current smd parser (flushes remaining state) and null it out.
  function _smdEndParser(){
    if(_smdParser&&window.smd){
      try{window.smd.parser_end(_smdParser);}catch(_){}
      // parser_end may flush remaining markdown that creates new links/images —
      // re-sanitize the body before the DOM is handed off to highlightCode / renderMessages.
      if(assistantBody){_sanitizeSmdLinks(assistantBody);}
    }
    _smdParser=null;
    _smdWrittenLen=0;
    _smdWrittenText='';
  }
  // Helper: feed new displayText delta to the smd parser.
  // Only feeds chars beyond what has already been written (_smdWrittenLen).
  function _smdWrite(displayText){
    if(!_smdParser||!window.smd) return;
    displayText=String(displayText||'');
    // Self-heal desyncs: if displayText no longer starts with what we've already
    // written (e.g. due to stream sanitization/tag stripping), incremental slicing
    // can skip characters. Rebuild parser from the full current displayText.
    if(_smdWrittenText && !displayText.startsWith(_smdWrittenText)){
      _smdParser=null;
      _smdWrittenLen=0;
      _smdWrittenText='';
      if(assistantBody) assistantBody.innerHTML='';
      _smdNewParser(assistantBody);
      if(!_smdParser) return;
    }
    const delta=displayText.slice(_smdWrittenText.length);
    if(!delta) return;
    try{window.smd.parser_write(_smdParser,delta);}catch(_){}
    _smdWrittenLen=displayText.length;
    _smdWrittenText=displayText;
    // streaming-markdown does NOT sanitize URL schemes — `[click](javascript:...)`
    // and `![alt](javascript:...)` survive as href/src.  Strip any unsafe schemes
    // from anchors/images that were just added to the live DOM.  The existing
    // renderMd() path filters these via its http(s)-only regex; we need a matching
    // guard here so the live-stream path isn't an XSS vector for agent-echoed
    // prompt-injection content.  The final renderMessages() call at `done` uses
    // renderMd which is already safe, but during streaming the user could click
    // a malicious link before that replacement happens.
    if(assistantBody){_sanitizeSmdLinks(assistantBody);}
  }
  // Allowed URL schemes for anchors and images rendered from agent-streamed markdown.
  // Matches the effective allowlist of renderMd() (http/https via regex + relative).
  const _SMD_SAFE_URL_RE=/^(?:https?:|mailto:|tel:|\/|#|\?|\.)/i;
  function _sanitizeSmdLinks(root){
    if(!root||!root.querySelectorAll) return;
    const _a=root.querySelectorAll('a[href]');
    for(let i=0;i<_a.length;i++){
      const n=_a[i],v=n.getAttribute('href')||'';
      if(!_SMD_SAFE_URL_RE.test(v)){n.removeAttribute('href');n.setAttribute('data-blocked-scheme','1');}
    }
    const _im=root.querySelectorAll('img[src]');
    for(let i=0;i<_im.length;i++){
      const n=_im[i],v=n.getAttribute('src')||'';
      if(!_SMD_SAFE_URL_RE.test(v)){n.removeAttribute('src');n.setAttribute('data-blocked-scheme','1');}
    }
  }
  function _resetAssistantSegment(){
    assistantRow=null;
    assistantBody=null;
    segmentStart=assistantText.length;
    _freshSegment=true;
    _smdEndParser();
  }

  let _lastRenderMs=0;
  // Long active chats flicker if markdown is rebuilt too often while streaming.
  // Batch live DOM updates without disabling incremental response display.
  const _LIVE_RENDER_MIN_INTERVAL_MS=220;
  function _scheduleRender(){
    if(_renderPending) return;
    if(_streamFinalized) return; // Bug A: don't schedule new rAF after stream finalized
    _renderPending=true;
    // Cap render rate to ~15fps. The browser's rAF fires at 60fps, but each DOM
    // update takes 50-150ms on large sessions. During GC pauses, rAF callbacks
    // accumulate and then execute all at once, blocking the main thread for
    // multi-second stretches and crashing the renderer (Chrome error code 4/5).
    // Throttling to 66ms intervals prevents this pileup without noticeable
    // visual degradation — streaming text updates still feel immediate.
    // performance.now() is monotonic so tab suspend/resume and NTP adjustments
    // can't produce negative or enormous deltas.
    const sinceLastMs=performance.now()-_lastRenderMs;
    const _doRender=()=>{
      _pendingRafHandle=null;
      _renderPending=false;
      // Guard: a pending setTimeout+rAF can outlive stream finalization.
      if(_streamFinalized) return;
      _lastRenderMs=performance.now();
      const parsed=_parseStreamState();
      _renderLiveThinking(parsed);
      if(assistantBody){
        const displayText = segmentStart===0
          ? parsed.displayText                          // first segment: uses think-tag stripping
          : _stripXmlToolCalls(assistantText.slice(segmentStart));
        if(!_smdParser&&window.smd){
          // On reconnect: prior content in assistantBody came from a different smd parser run.
          // Clear it and start fresh — renderMessages() on done will restore the full content.
          if(_smdReconnect){assistantBody.innerHTML='';_smdReconnect=false;}
          _smdNewParser(assistantBody);
        }
        if(_smdParser){
          _smdWrite(displayText);
        } else {
          // Fallback: smd not loaded yet, reconnect session, or smd unavailable — use renderMd
          // for every live segment. Without this, the first segment inserts raw
          // parsed.displayText and users see unformatted markdown until done.
          const fallbackText = segmentStart===0
            ? parsed.displayText
            : _stripXmlToolCalls(assistantText.slice(segmentStart));
          if(fallbackText!==_lastFallbackRenderedText){
            assistantBody.innerHTML = renderMd ? renderMd(fallbackText) : esc(fallbackText);
            _lastFallbackRenderedText=fallbackText;
          }
        }
      }
      scrollIfPinned();
    };
    if(sinceLastMs>=_LIVE_RENDER_MIN_INTERVAL_MS){
      _pendingRafHandle=requestAnimationFrame(_doRender);
    } else {
      _pendingRafHandle=setTimeout(()=>requestAnimationFrame(_doRender), _LIVE_RENDER_MIN_INTERVAL_MS-sinceLastMs);
    }
  }

  // ── Progress bar: render streaming task progress ──
  function _updateProgress(data){
    if(!data) return;
    _progressData=data;
    const turn=document.getElementById('liveAssistantTurn');
    if(!turn) return;
    const blocks=_assistantTurnBlocks(turn);
    if(!blocks||!S.session||S.session.session_id!==activeSid) return;
    if(!_progressEl){
      _progressEl=document.createElement('div');
      _progressEl.className='task-progress';
      _progressEl.innerHTML='<div class="progress-bar"><div class="progress-fill"></div></div><div class="progress-label"></div>';
      // Insert after thinking card if present, otherwise at start of blocks
      const thinking=blocks.querySelector('.reasoning-accordion-row');
      if(thinking) thinking.insertAdjacentElement('afterend', _progressEl);
      else blocks.insertBefore(_progressEl, blocks.firstChild);
    }
    const fill=_progressEl.querySelector('.progress-fill');
    const label=_progressEl.querySelector('.progress-label');
    if(!fill||!label) return;
    const hasTotal=typeof data.total==='number'&&data.total>0;
    const hasCurrent=typeof data.current==='number';
    if(data.done){
      _progressEl.classList.remove('progress-indeterminate');
      _progressEl.classList.add('progress-done');
      fill.style.width='100%';
      label.textContent=data.label||(typeof t==='function'?t('kanban_status_done'):'Complete');
      return;
    }
    _progressEl.classList.remove('progress-done');
    if(hasTotal&&hasCurrent){
      const pct=Math.min(100,Math.max(0,(data.current/data.total)*100));
      _progressEl.classList.remove('progress-indeterminate');
      fill.style.width=pct+'%';
      label.textContent=(data.label||'')+(pct>0?' ('+Math.round(pct)+'%)':'');
    } else {
      _progressEl.classList.add('progress-indeterminate');
      fill.style.width='';
      label.textContent=data.label||'Working…';
    }
  }
  function _removeProgressEl(){
    if(_progressEl&&_progressEl.parentNode) _progressEl.parentNode.removeChild(_progressEl);
    _progressEl=null;
    _progressData=null;
  }

  function _wireSSE(source){
    LIVE_STREAMS[activeSid]={streamId,source};
    source.addEventListener('open',()=>{
      _streamReconnectAttempts=0;
      if(!_terminalStateReached&&!_streamFinalized) setComposerStatus('');
    });
    source.addEventListener('heartbeat',e=>{
      if(_terminalStateReached||_streamFinalized) return;
      if(!S.session||S.session.session_id!==activeSid) return;
      try{
        const d=JSON.parse(e.data||'{}');
        const run=d.run||{};
        const age=Number(run.age_seconds||0);
        if(age>=60){
          const minutes=Math.max(1,Math.round(age/60));
          const phase=run.phase?String(run.phase):'running';
          setComposerStatus(`Agent still ${phase} (${minutes} min, waiting for output)...`);
        }
      }catch(_){}
    });
    // Note on #631 Bug B: the original PR description stated the server
    // "replays buffered token events" on reconnect, and proposed resetting
    // the accumulators here so the re-sent tokens wouldn't double the prefix.
    // That is NOT how the server actually works — api/routes._handle_sse_stream
    // reads a one-shot queue.Queue() that delivers each event to exactly one
    // consumer; a reconnect picks up from the current queue position and gets
    // only events produced during the outage.  Resetting the accumulators here
    // would wipe the already-displayed content and restart the response from
    // the first post-reconnect token — a real data-loss regression.
    //
    // The "doubled response" / "stuck cursor" symptom is fully explained by
    // Bug A (trailing rAF after `done` inserting a new live-turn wrapper) —
    // the fixes below (_streamFinalized guard + cancelAnimationFrame in the
    // terminal handlers) address it without needing a reset here.

    source.addEventListener('token',e=>{
      if(typeof addStreamCursor==='function') setTimeout(addStreamCursor,0);
      if(!S.session||S.session.session_id!==activeSid) return;
      const d=JSON.parse(e.data);
      assistantText+=d.text;
      syncInflightAssistantMessage();
      if(!S.session||S.session.session_id!==activeSid) return;
      const parsed=_parseStreamState();
      if(String((parsed&&parsed.displayText)||'').trim()||assistantRow) ensureAssistantRow();
      _scheduleRender();
    });

    source.addEventListener('interim_assistant',e=>{
      if(!S.session||S.session.session_id!==activeSid) return;
      const d=JSON.parse(e.data);
      const visible=String(d&&d.text?d.text:'').trim();
      const alreadyStreamed=!!(d&&d.already_streamed);
      if(!visible){
        return;
      }
      if(alreadyStreamed){
        _resetAssistantSegment();
        return;
      }
      assistantText+=visible;
      syncInflightAssistantMessage();
      if(!S.session||S.session.session_id!==activeSid) return;
      const parsed=_parseStreamState();
      if(String((parsed&&parsed.displayText)||'').trim()||assistantRow) ensureAssistantRow();
      _scheduleRender();
    });

    source.addEventListener('reasoning',e=>{
      const d=JSON.parse(e.data);
      reasoningText += d.text || '';
      liveReasoningText += d.text || '';
      syncInflightAssistantMessage();
      if(!S.session||S.session.session_id!==activeSid) return;
      // Render thinking card synchronously — not via rAF — so the DOM is
      // up-to-date before a 'tool' event in the same microtask batch calls
      // finalizeThinkingCard(). The old rAF-only path caused a race where
      // the thinking row was still a spinner when finalized.
      if(window._showThinking!==false){
        if(typeof updateThinking==='function') updateThinking(liveReasoningText||'Thinking…');
        else appendThinking(liveReasoningText);
      }
      _scheduleRender();
    });

    source.addEventListener('tool',e=>{
      const d=JSON.parse(e.data);
      if(d.name==='clarify') return;
      // Generate unique tool ID for both live progress card and inline tool card
      const liveTid=d.tid||'tool-'+String(d.name).replace(/[^a-z0-9]/gi,'_')+'-'+Date.now()+'-'+Math.random().toString(36).slice(2,8);
      const tc={name:d.name, preview:d.preview||'', args:d.args||{}, snippet:'', done:false, tid:liveTid};
      const inflight = INFLIGHT[activeSid] || (INFLIGHT[activeSid] = {
        messages:[...S.messages],
        uploaded:[],
        toolCalls:[]
      });
      if(!Array.isArray(inflight.toolCalls)) inflight.toolCalls=[];
      INFLIGHT[activeSid].toolCalls.push(tc);
      persistInflightState();

      if(!S.session||S.session.session_id!==activeSid) return;
      S.toolCalls=INFLIGHT[activeSid].toolCalls;
      // Show live progress card (spinner + timer in #liveToolCards) — after guard
      if(typeof renderToolLiveCard==='function') renderToolLiveCard(liveTid,d.name,'running',Date.now());
      // NOTE: don't removeThinking() here — keep the thinking card visible
      // above the tool card so the turn reads top-to-bottom as:
      // user → thinking → tool cards → response. Removing it caused the card
      // to be re-created below everything when reasoning resumed post-tool.
      if(typeof finalizeThinkingCard==='function') finalizeThinkingCard();
      liveReasoningText='';
      const oldRow=$('toolRunningRow');if(oldRow)oldRow.remove();
      appendLiveToolCard(tc);
      // Reset the live assistant row reference so that any text tokens arriving
      // after this tool call create a NEW segment appended below the tool card,
      // rather than updating the old segment that sits above it in the DOM.
      _freshSegment=true;
      _smdEndParser();
      _resetAssistantSegment();
      scrollIfPinned();
    });

    source.addEventListener('tool_complete',e=>{
      const d=JSON.parse(e.data);
      if(d.name==='clarify') return;
      const inflight=INFLIGHT[activeSid];
      if(!inflight) return;
      if(!Array.isArray(inflight.toolCalls)) inflight.toolCalls=[];
      let tc=null;
      for(let i=inflight.toolCalls.length-1;i>=0;i--){
        const cur=inflight.toolCalls[i];
        if(cur&&cur.done===false&&(!d.name||cur.name===d.name)){
          tc=cur;
          break;
        }
      }
      if(!tc){
        tc={name:d.name||'tool', preview:d.preview||'', args:d.args||{}, snippet:'', done:true};
        inflight.toolCalls.push(tc);
      }
      tc.preview=d.preview||tc.preview||'';
      tc.args=d.args||tc.args||{};
      tc.done=true;
      tc.is_error=!!d.is_error;
      if(d.duration!==undefined) tc.duration=d.duration;
      // Preserve client-side start timestamp if available
      if(tc._ts_start===undefined) tc._ts_start=Date.now()-((d.duration||0)*1000);
      persistInflightState();
      if(!S.session||S.session.session_id!==activeSid) return;
      S.toolCalls=inflight.toolCalls;
      // Update live progress card in #liveToolCards — after guard
      if(tc&&tc.tid&&typeof updateToolLiveCard==='function') updateToolLiveCard(tc.tid,{status:tc.is_error?'error':'done'});
      appendLiveToolCard(tc);
      scrollIfPinned();
    });

    source.addEventListener('approval',e=>{
      const d=JSON.parse(e.data);
      showApprovalForSession(activeSid, d, 1);
      playNotificationSound();
      sendBrowserNotification('Approval required',d.description||'Tool approval needed');
    });

    // Subagent lifecycle events — stash session_id on the current tool call
    // so the subagent progress card can link to the child's live session.
    source.addEventListener('subagent_event',e=>{
      const d=JSON.parse(e.data);
      if(!d.session_id) return;
      const inflight=INFLIGHT[activeSid];
      if(!inflight||!Array.isArray(inflight.toolCalls)) return;
      // Attach session_id to the most recent subagent tool call
      for(let i=inflight.toolCalls.length-1;i>=0;i--){
        const cur=inflight.toolCalls[i];
        if(cur&&cur.name==='subagent_progress'&&!cur._child_session_id){
          cur._child_session_id=d.session_id;
          cur._child_subagent_id=d.subagent_id;
          cur._child_goal=d.goal;
          break;
        }
      }
      // Optimistically add the child session to the sidebar if not already present
      if(typeof _allSessions!=='undefined'&&Array.isArray(_allSessions)){
        if(!_allSessions.some(s=>s&&s.session_id===d.session_id)){
          _allSessions.unshift({
            session_id:d.session_id,
            title:d.goal||'Subagent',
            message_count:0,
            last_message_at:Math.floor(Date.now()/1000),
            updated_at:Math.floor(Date.now()/1000),
            is_streaming:true,
            relationship_type:'child_session',
            parent_session_id:activeSid,
            source:'webui',
          });
          if(typeof renderSessionListFromCache==='function') renderSessionListFromCache();
          if(typeof _refreshSubagentPanel==='function') void _refreshSubagentPanel();
        }
      }
    });

    source.addEventListener('clarify',e=>{
      const d=JSON.parse(e.data);
      showClarifyForSession(activeSid, d);
      playNotificationSound();
      sendBrowserNotification('Clarification needed',d.question||'Tool clarification needed');
    });

    source.addEventListener('title',e=>{
      let d={};
      try{ d=JSON.parse(e.data||'{}'); }catch(_){}
      if((d.session_id||activeSid)!==activeSid) return;
      const newTitle=String(d.title||'').trim();
      if(!newTitle) return;
      if(S.session&&S.session.session_id===activeSid){
        S.session.title=newTitle;
        syncTopbar();
      }
      if(typeof _allSessions!=='undefined'&&Array.isArray(_allSessions)){
        const row=_allSessions.find(s=>s&&s.session_id===activeSid);
        if(row) row.title=newTitle;
      }
      if(typeof renderSessionListFromCache==='function') renderSessionListFromCache();
      else if(typeof renderSessionList==='function') renderSessionList();
    });

    source.addEventListener('title_status',e=>{
      let d={};
      try{ d=JSON.parse(e.data||'{}'); }catch(_){}
      if((d.session_id||activeSid)!==activeSid) return;
      try{
        console.info('[title]', {
          status:String(d.status||''),
          reason:String(d.reason||''),
          title:String(d.title||''),
          raw_preview:String(d.raw_preview||''),
          session_id:String(d.session_id||activeSid)
        });
      }catch(_){}
    });

    // ── Plan event (Plan-Then-Code two-phase mode) ──
    source.addEventListener('plan', e => {
      try{
        const d=JSON.parse(e.data||'{}');
        if((d.session_id||activeSid)!==activeSid) return;
        const planText=String(d.text||d.plan||'').trim();
        const planId=String(d.plan_id||d.id||'').trim() || `plan_${Date.now()}`;
        if(!planText) return;
        window._activePlan = {
          id: planId,
          text: planText,
          sessionId: activeSid,
          status: 'pending',
          decisionMsg: null,
        };
        _markCurrentAssistantAsPlan(planText);
        showToast('📋 Plan received — review and accept/revise', 3000);
      }catch(_){}
    });

    function _resolveGoalMessage(d){
      const key=String(d && d.message_key ? d.message_key : '').trim();
      const args=Array.isArray(d && d.message_args) ? d.message_args : [];
      const raw=String(d&&d.message||'').trim();
      if(key && typeof t==='function'){
        try{
          const translated=String(t(key,...args));
          if(translated && translated!==key)return translated;
        }catch(_){}
      }
      return raw;
    }

source.addEventListener('goal',e=>{
      try{
        const d=JSON.parse(e.data||'{}');
        if((d.session_id||activeSid)!==activeSid) return;
        const goalState=String(d.state||'').trim();
        const goalEvaluatingMessage=t('goal_evaluating_progress');
        if(goalState==='evaluating'){
          setComposerStatus(goalEvaluatingMessage);
          showToast('⏳ ' + goalEvaluatingMessage, 3000);
          return;
        }
        const msg=_resolveGoalMessage(d);
        if(!msg)return;
        _latestGoalStatus={message:msg,decision:d.decision||null,state:goalState||null};
        setComposerStatus(msg);
        showToast(msg.split('\n')[0],2600);
        // Update goal banner from decision payload
        if(d.decision&&d.decision.status){
          _updateGoalState({
            goal:window._goalState&&window._goalState.goal||'',
            status:d.decision.status,
            turns_used:d.decision.turns_used,
            max_turns:d.decision.max_turns,
            last_verdict:d.decision.verdict,
            last_reason:d.decision.reason,
            paused_reason:d.decision.paused_reason||null,
            space:window._goalState&&window._goalState.space||null,
          });
        }
      }catch(_){}
    });

    source.addEventListener('goal_continue',e=>{
      try{
        const d=JSON.parse(e.data||'{}');
        const sid=d.session_id||activeSid;
        const continuation_prompt=String(d.continuation_prompt||d.text||'').trim();
        if(!continuation_prompt||sid!==activeSid)return;
        _pendingGoalContinuation={
          sid,
          text:continuation_prompt,
          model:(S.session&&S.session.session_id===sid&&S.session.model)||options.model||'',
          model_provider:(S.session&&S.session.session_id===sid&&S.session.model_provider)||options.model_provider||null,
          profile:options.profile||S.activeProfile||'default',
          workspace:(S.session&&S.session.session_id===sid&&S.session.workspace)||options.workspace||'',
          workspace_slug:ownerWorkspaceSlug||'',
        };
        const toast=t('goal_continuing_toast');
        const cmsg=_resolveGoalMessage(d);
        showToast((toast&&cmsg&&cmsg!==toast)?cmsg.split('\n')[0]:toast,2200);
      }catch(_){}
    });

    // ── Task progress events (long-running task progress bar) ──
    source.addEventListener('progress',e=>{
      if(!S.session||S.session.session_id!==activeSid) return;
      try{
        const d=JSON.parse(e.data||'{}');
        _updateProgress({
          current:typeof d.current==='number'?d.current:null,
          total:typeof d.total==='number'?d.total:null,
          label:String(d.label||'').trim(),
          done:!!d.done,
        });
      }catch(_){}
    });

source.addEventListener('done',e=>{
      if(typeof clearToolLiveCards==='function') clearToolLiveCards();
      if(typeof removeStreamCursor==='function') removeStreamCursor();
      _terminalStateReached=true;
      _clearReconnectTimers();
      if(_persistTimer){clearTimeout(_persistTimer);_persistTimer=null;}
      // Bug A fix: cancel any pending rAF and mark stream finalized before
      // the DOM is settled by renderMessages, so no trailing token/reasoning rAF
      // can reintroduce a stale thinking card or duplicate content.
      _streamFinalized=true;
      if(_pendingRafHandle!==null){cancelAnimationFrame(_pendingRafHandle);clearTimeout(_pendingRafHandle);_pendingRafHandle=null;_renderPending=false;}
      if(typeof finalizeThinkingCard==='function') finalizeThinkingCard();
      // ── Terminal live streaming: disconnect on done ──
      if(typeof TerminalStream!=='undefined' && TerminalStream.disconnect){
        TerminalStream.disconnect();
      }
      // Finalize smd parser — flushes any remaining buffered markdown state
      // and runs Prism + copy buttons on the live segment before the DOM is replaced
      if(assistantBody){
        const _finBody=assistantBody;
        _smdEndParser();
        requestAnimationFrame(()=>{
          if(typeof highlightCode==='function') highlightCode(_finBody);
          if(typeof addCopyButtons==='function') addCopyButtons(_finBody);
          if(typeof renderKatexBlocks==='function') renderKatexBlocks();
        });
      } else {
        _smdEndParser();
      }
      const d=JSON.parse(e.data);
      const isActiveSession=_isSessionCurrentPane(activeSid);
      const isSessionViewed=_isSessionActivelyViewed(activeSid);
      const completedSession=d.session||{session_id:activeSid};
      const completedSid=completedSession.session_id||activeSid;
      if(!isSessionViewed && typeof _markSessionCompletionUnread==='function'){
        _markSessionCompletionUnread(completedSid, completedSession.message_count);
      }
      _clearOwnerInflightState();
      if(typeof _markSessionCompletedInList==='function'){
        _markSessionCompletedInList(completedSession, activeSid);
      }
      _clearApprovalForOwner();
      _clearClarifyForOwner('terminal');
      const shouldFollowOnDone=isActiveSession&&((typeof _shouldFollowMessagesOnDomReplace==='function')
        ? _shouldFollowMessagesOnDomReplace()
        : (typeof _isMessagePaneNearBottom==='function'&&_isMessagePaneNearBottom(1200)));
      if(isActiveSession){
        S.activeStreamId=null;
      }
      if(isActiveSession){
        // Capture previous session totals BEFORE overwriting S.session with the new
        // cumulative values from the done event. prevIn/prevOut are the totals as of
        // the start of this turn; curIn/curOut are the full post-turn totals — the
        // delta is the per-turn usage for #1159.
        const _prevIn=(S.session&&S.session.input_tokens)||0;
        const _prevOut=(S.session&&S.session.output_tokens)||0;
        const _prevCost=(S.session&&S.session.estimated_cost)||0;
        S.session=d.session;S.messages=d.session.messages||[];if(typeof _messagesTruncated!=='undefined')_messagesTruncated=!!d.session._messages_truncated;
        if(S.session&&S.session.session_id){
          localStorage.setItem('sidekick-webui-session',S.session.session_id);
          if(typeof _setActiveSessionUrl==='function') _setActiveSessionUrl(S.session.session_id);
        }
        if(
          window._compressionUi&&window._compressionUi.automatic&&
          window._compressionUi.sessionId===activeSid&&
          d.session&&d.session.session_id
        ){
          window._compressionUi={...window._compressionUi, sessionId:d.session.session_id};
        }
        // Find the last assistant message once for both reasoning persistence and timestamp
        const lastAsst=[...S.messages].reverse().find(m=>m.role==='assistant');
        // Persist reasoning trace so thinking card survives page reload
        if(reasoningText&&lastAsst&&!lastAsst.reasoning) lastAsst.reasoning=reasoningText;
        // Stamp _ts on the last assistant message if it has no timestamp
        if(lastAsst&&!lastAsst._ts&&!lastAsst.timestamp) lastAsst._ts=Date.now()/1000;
        if(typeof _reviewFinalizeFromAssistant==='function') _reviewFinalizeFromAssistant(activeSid, lastAsst, d.session);
        if(d.usage){
          S.lastUsage=d.usage;_syncCtxIndicator(d.usage);
          // #503 — compute per-turn cost delta and attach to last assistant message
          if(lastAsst){
            const prevIn=_prevIn;
            const prevOut=_prevOut;
            const prevCost=_prevCost;
            const curIn=d.usage.input_tokens||0;
            const curOut=d.usage.output_tokens||0;
            const curCost=d.usage.estimated_cost||0;
            // Only set delta if values actually increased (skip no-op turns)
            if(curIn>prevIn||curOut>prevOut){
              lastAsst._turnUsage={
                input_tokens:Math.max(0,curIn-prevIn),
                output_tokens:Math.max(0,curOut-prevOut),
                estimated_cost:Math.max(0,curCost-prevCost),
              };
            }
            if(typeof d.usage.duration_seconds==='number'){
              lastAsst._turnDuration=d.usage.duration_seconds;
            }
            if(typeof d.usage.tps==='number'&&d.usage.tps>0){
              lastAsst._turnTps=d.usage.tps;
            }
            if(d.usage.gateway_routing){
              lastAsst._gatewayRouting=d.usage.gateway_routing;
              if(S.session)S.session.gateway_routing=d.usage.gateway_routing;
              if(S.session&&Array.isArray(S.session.gateway_routing_history))S.session.gateway_routing_history.push(d.usage.gateway_routing);
              else if(S.session)S.session.gateway_routing_history=[d.usage.gateway_routing];
            }
          }
        }
        if(d.session.tool_calls&&d.session.tool_calls.length){
          const tsMap={};
          const durMap={};
          INFLIGHT[activeSid]&&INFLIGHT[activeSid].toolCalls&&INFLIGHT[activeSid].toolCalls.forEach(ft=>{
            if(ft.name){
              if(ft._ts_start) tsMap[ft.name]=ft._ts_start;
              if(ft.duration!==undefined) durMap[ft.name]=ft.duration;
            }
          });
          S.toolCalls=d.session.tool_calls.map(tc=>{
            const pt=tsMap[tc.name];
            const pd=durMap[tc.name];
            return{...tc,done:true,...(pt?{_ts_start:pt}:{}),...(pd!==undefined?{duration:pd}:{})};
          });
        } else {
          S.toolCalls=S.toolCalls.map(tc=>({...tc,done:true}));
        }
        if(typeof _copyActivityDisclosureState==='function'&&lastAsst){
          const assistantIdx=S.messages.indexOf(lastAsst);
          if(assistantIdx>=0) _copyActivityDisclosureState('live:'+streamId, 'assistant:'+assistantIdx);
        }
        if(uploaded.length){
          const lastUser=[...S.messages].reverse().find(m=>m.role==='user');
          if(lastUser)lastUser.attachments=uploaded;
        }
if(_latestGoalStatus&&_latestGoalStatus.message){
          S.messages.push({
            role:'assistant',
            content:String(_latestGoalStatus.message),
            _ts:Date.now()/1000,
            _goalStatus:true,
            _transient:true,
          });
          // Update goal banner from latest goal status
          const _gs=_latestGoalStatus;
          if(_gs.decision&&_gs.decision.status){
            _updateGoalState({
              goal:window._goalState&&window._goalState.goal||'',
              status:_gs.decision.status,
              turns_used:_gs.decision.turns_used,
              max_turns:_gs.decision.max_turns,
              last_verdict:_gs.decision.verdict,
              last_reason:_gs.decision.reason,
              paused_reason:_gs.decision.paused_reason||null,
            });
          }
        }
        clearLiveToolCards();_removeProgressEl();
        S.busy=false;
        // No-reply guard (#373): if agent returned nothing, show inline error
        if(!S.messages.some(m=>m.role==='assistant'&&String(m.content||'').trim())&&!assistantText){removeThinking();S.messages.push({role:'assistant',content:'**No response received.** Check your API key and model selection.'});}
        if(isSessionViewed) _markSessionViewed(completedSid, completedSession.message_count ?? S.messages.length);
        syncTopbar();renderMessages({preserveScroll:true});
        if(shouldFollowOnDone&&typeof scrollToBottom==='function') scrollToBottom();
        loadDir('.');
        // TTS auto-read: speak the last assistant response if enabled (#499)
        if(typeof autoReadLastAssistant==='function') setTimeout(()=>autoReadLastAssistant(), 300);
      }
      if(_pendingGoalContinuation&&typeof queueSessionMessage==='function'){
        const _goalNext=_pendingGoalContinuation;
        _pendingGoalContinuation=null;
        setTimeout(()=>_startGoalContinuation(_goalNext),250);
      }
      if(isActiveSession) _queueDrainSid=activeSid;
      renderSessionList();
      _setActivePaneIdleIfOwner();
      playNotificationSound();
      sendBrowserNotification('Response complete',assistantText?assistantText.slice(0,100):'Task finished');
    });

    source.addEventListener('stream_end',e=>{
      _terminalStateReached=true;
      _clearReconnectTimers();
      try{
        const d=JSON.parse(e.data||'{}');
        if((d.session_id||activeSid)!==activeSid) return;
      }catch(_){}
      source.close();
    });

    source.addEventListener('pending_steer_leftover',e=>{
      // The agent finished its turn with steer text still stashed (no
      // tool-result boundary fired). Match the CLI's leftover-delivery
      // behaviour: queue the leftover text as a next-turn user message
      // so the existing drain in setBusy(false) ships it.
      try{
        const d=JSON.parse(e.data||'{}');
        const sid=d.session_id||activeSid;
        const txt=String(d.text||'').trim();
        if(!txt||sid!==activeSid) return;
        if(typeof queueSessionMessage==='function'){
          const composerModelState=_currentComposerModelState();
          queueSessionMessage(sid,{
            text:txt,files:[],
            model:composerModelState.model,
            model_provider:composerModelState.model_provider,
            profile:S.activeProfile||'default',
          });
          if(typeof updateQueueBadge==='function') updateQueueBadge(sid);
          showToast(t('steer_leftover_queued'),3000);
        }
      }catch(_){}
    });

    source.addEventListener('compressing',e=>{
      // Context auto-compression is starting. Surface the same calm running
      // compression card as manual /compress while the summarizer LLM call runs.
      if(!S.session||S.session.session_id!==activeSid) return;
      let d={};
      try{ d=JSON.parse(e.data||'{}')||{}; }catch(_){ d={}; }
      if(d.session_id&&d.session_id!==activeSid) return;
      if(typeof setCompressionUi==='function'){
        setCompressionUi({
          sessionId:activeSid,
          phase:'running',
          automatic:true,
          message:d.message||'Auto-compressing context...',
        });
      }
      if(typeof renderMessages==='function') renderMessages({preserveScroll:true});
    });

    source.addEventListener('compressed',e=>{
      // Context was auto-compressed during this turn. Render it through the
      // same transient compression-card path as manual /compress, without
      // inserting a fake assistant message into history or model context.
      if(!S.session||S.session.session_id!==activeSid) return;
      let d={};
      try{ d=JSON.parse(e.data||'{}')||{}; }catch(_){ d={}; }
      const message=String(d.message||'Context auto-compressed to continue the conversation').trim();
      if(typeof setCompressionUi==='function'){
        setCompressionUi({
          sessionId:activeSid,
          phase:'done',
          automatic:true,
          message,
          summary:{headline:message},
        });
      }
      if(typeof _setCompressionSessionLock==='function') _setCompressionSessionLock(null);
      if(!S.busy&&typeof renderMessages==='function') renderMessages();
      showToast(message||'Context compressed', 8000);
    });

    source.addEventListener('metering',e=>{
      try{
        const d=JSON.parse(e.data||'{}');
        if((d.session_id||activeSid)!==activeSid) return;
        if(d.usage&&typeof _syncCtxIndicator==='function'){
          S.lastUsage={...(S.lastUsage||{}),...d.usage};
          _syncCtxIndicator(S.lastUsage);
        }
        if(d.estimated===true||d.tps_available!==true||typeof d.tps!=='number'||d.tps<=0){
          if(typeof _setLiveAssistantTps==='function') _setLiveAssistantTps(null);
          return;
        }
        if(typeof _setLiveAssistantTps==='function') _setLiveAssistantTps(d.tps);
      }catch(_){}
    });

    source.addEventListener('apperror',e=>{
      if(typeof removeStreamCursor==='function') removeStreamCursor();
      _terminalStateReached=true;
      _clearReconnectTimers();
      if(_persistTimer){clearTimeout(_persistTimer);_persistTimer=null;}
      _streamFinalized=true;
      if(_pendingRafHandle!==null){cancelAnimationFrame(_pendingRafHandle);clearTimeout(_pendingRafHandle);_pendingRafHandle=null;_renderPending=false;}
      _smdEndParser();
      if(typeof finalizeThinkingCard==='function') finalizeThinkingCard();
      // ── Terminal live streaming: disconnect on error ──
      if(typeof TerminalStream!=='undefined' && TerminalStream.disconnect){
        TerminalStream.disconnect();
      }
      // Application-level error sent explicitly by the server (rate limit, crash, etc.)
      // This is distinct from the SSE network 'error' event below.
      source.close();
      _clearOwnerInflightState();
      _clearApprovalForOwner();
      _clearClarifyForOwner('terminal');
      if(S.session&&S.session.session_id===activeSid){
        S.activeStreamId=null;
        clearLiveToolCards();if(!assistantText)removeThinking();_removeProgressEl();
        try{
          const d=JSON.parse(e.data);
          const isRateLimit=d.type==='rate_limit';
          const isQuotaExhausted=d.type==='quota_exhausted';
          const isAuthMismatch=d.type==='auth_mismatch';
          const isModelNotFound=d.type==='model_not_found';
          const isNoResponse=d.type==='no_response'||d.type==='silent_failure';
          const label=isQuotaExhausted?'Out of credits':isRateLimit?'Rate limit reached':isAuthMismatch?(typeof t==='function'?t('provider_mismatch_label'):'Provider mismatch'):isModelNotFound?(typeof t==='function'?t('model_not_found_label'):'Model not found'):isNoResponse?'No response received':'Error';
          const hint=d.hint?`\n\n*${d.hint}*`:'';
          const details=d.details?String(d.details).replace(/```/g,'`\u200b``'):'';
          S.messages.push({role:'assistant',content:`**${label}:** ${d.message}${hint}`,provider_details:details});
        }catch(_){
          S.messages.push({role:'assistant',content:'**Error:** An error occurred. Check server logs.'});
        }
        _markSessionViewed(activeSid, S.messages.length);
        renderMessages({preserveScroll:true});
      }else if(typeof trackBackgroundError==='function'){
        const _errTitle=(typeof _allSessions!=='undefined'&&_allSessions.find(s=>s.session_id===activeSid)||{}).title||null;
        try{const d=JSON.parse(e.data);trackBackgroundError(activeSid,_errTitle,d.message||'Error');}
        catch(_){trackBackgroundError(activeSid,_errTitle,'Error');}
      }
      _setActivePaneIdleIfOwner();
      renderSessionList(); // clear streaming indicator immediately on apperror
    });

    source.addEventListener('warning',e=>{
      // Non-fatal warning from server (e.g. fallback activated, retrying)
      if(!S.session||S.session.session_id!==activeSid) return;
      try{
        const d=JSON.parse(e.data);
        // Show as a small inline notice, not a full error
        setComposerStatus(`${d.message||'Warning'}`);
        // If it's a fallback notice, show it briefly then clear
        if(d.type==='fallback') setTimeout(()=>setComposerStatus(''),4000);
      }catch(_){}
    });

    source.addEventListener('proposed_patch', e => {
      if(!S.session||S.session.session_id!==activeSid) return;
      try {
        const d = JSON.parse(e.data);
        const diffText = d.diff || d.patch || d.content || '';
        const fileName = d.file || d.filename || '';
        if (!diffText) return;

        // Build the diff viewer element
        const viewer = renderDiffViewer(diffText, {
          maxHeight: d.maxHeight || '360px',
        });

        // Style it as an inline card
        viewer.style.margin = '8px 0';

        // Create a wrapper message-like card
        const card = document.createElement('div');
        card.className = 'msg-row assistant-turn proposed-patch-card';
        card.style.margin = '8px 0';
        const role = document.createElement('div');
        role.className = 'msg-role assistant';
        role.style.fontSize = '11px';
        role.style.padding = '4px 10px';
        role.style.opacity = '0.7';
        role.textContent = '📝 ' + (typeof t === 'function' ? t('proposed_patch') : 'Proposed patch');
        card.appendChild(role);
        card.appendChild(viewer);

        // Insert into the live turn or after the current assistant content
        const liveTurn = document.getElementById('liveAssistantTurn');
        if (liveTurn) {
          liveTurn.parentNode.insertBefore(card, liveTurn.nextSibling);
        } else {
          const msgInner = document.getElementById('msgInner');
          if (msgInner) msgInner.appendChild(card);
        }
        scrollIfPinned();
      } catch(_) { /* ignore malformed proposed_patch events */ }
    });

    source.addEventListener('error',async e=>{
      source.close();
      closeLiveStream(activeSid, streamId);
      if(_deferStreamErrorIfOffline()) return;
      if(_terminalStateReached || _streamFinalized){
        _closeSource();
        return;
      }
      _scheduleStreamReconnect(1200);
    });

    source.addEventListener('cancel',e=>{
      if(typeof removeStreamCursor==='function') removeStreamCursor();
      _terminalStateReached=true;
      _clearReconnectTimers();
      if(_persistTimer){clearTimeout(_persistTimer);_persistTimer=null;}
      _streamFinalized=true;
      if(_pendingRafHandle!==null){cancelAnimationFrame(_pendingRafHandle);clearTimeout(_pendingRafHandle);_pendingRafHandle=null;_renderPending=false;}
      _smdEndParser();
      if(typeof finalizeThinkingCard==='function') finalizeThinkingCard();
      // ── Terminal live streaming: disconnect on cancel ──
      if(typeof TerminalStream!=='undefined' && TerminalStream.disconnect){
        TerminalStream.disconnect();
      }
      source.close();
      _clearOwnerInflightState();
      _clearApprovalForOwner();
      _clearClarifyForOwner('cancelled');
      if(S.session&&S.session.session_id===activeSid){
        S.activeStreamId=null;_removeProgressEl();
      }
      // Fetch latest session from server to get accurate message list (includes cancel status)
      // This ensures messages stay in sync with server, fixing race condition where local
      // "*Task cancelled.*" message gets lost when done event overwrites S.messages
      (async()=>{
        try{
          const data=await api(_ownerScopedApiPath(`/api/session?session_id=${encodeURIComponent(activeSid)}&messages=0&resolve_model=0`));
          if(data&&data.session&&S.session&&S.session.session_id===activeSid){
            S.session={...S.session,...data.session};
            // Re-apply stopped marking while preserving locally streamed messages.
            if(typeof _markLastAssistantStopped==='function') _markLastAssistantStopped();
            clearLiveToolCards();if(!assistantText)removeThinking();
            _markSessionViewed(activeSid, data.session.message_count ?? S.messages.length);
            renderMessages({preserveScroll:true});
          }
        }catch(_){
          // Fallback to local cancel message if API fails
          if(S.session&&S.session.session_id===activeSid){
            clearLiveToolCards();if(!assistantText)removeThinking();
            S.messages.push({role:'assistant',content:'*Task cancelled.*'});renderMessages({preserveScroll:true});
            _markSessionViewed(activeSid, S.messages.length);
          }
        }
      })();
      renderSessionList();
      _setActivePaneIdleIfOwner();
    });
  }

  async function _restoreSettledSession(){
    try{
      const data=await api(_ownerScopedApiPath(`/api/session?session_id=${encodeURIComponent(activeSid)}&messages=0&resolve_model=0`));
      const session=data&&data.session;
      if(!session) return false;
      if(session.active_stream_id||session.pending_user_message) return false;
      _clearReconnectTimers();
      _clearOwnerInflightState();
      _closeSource();
      _clearApprovalForOwner();
      _clearClarifyForOwner('terminal');
      const isSessionViewed=_isSessionActivelyViewed(activeSid);
      const completedSid=session.session_id||activeSid;
      if(!isSessionViewed && typeof _markSessionCompletionUnread==='function'){
        _markSessionCompletionUnread(completedSid, session.message_count);
      }
      const isActiveSession=_isSessionCurrentPane(activeSid);
      if(isActiveSession){
        S.activeStreamId=null;
        clearLiveToolCards();if(!assistantText)removeThinking();
        S.session={...S.session,...session};
        if(S.session&&S.session.session_id){
          localStorage.setItem('sidekick-webui-session',S.session.session_id);
          if(typeof _setActiveSessionUrl==='function') _setActiveSessionUrl(S.session.session_id);
        }
        const hasMessageToolMetadata=S.messages.some(m=>{
          if(!m||m.role!=='assistant') return false;
          // Recognize both the standard `tool_calls` (used by completed assistant
          // turns where the LLM emitted tool_call entries) and the WebUI-internal
          // `_partial_tool_calls` (used on Stop/Cancel partial messages — see
          // api/streaming.py cancel_stream).
          const hasTc=Array.isArray(m.tool_calls)&&m.tool_calls.length>0;
          const hasPartialTc=Array.isArray(m._partial_tool_calls)&&m._partial_tool_calls.length>0;
          const hasTu=Array.isArray(m.content)&&m.content.some(p=>p&&p.type==='tool_use');
          return hasTc||hasPartialTc||hasTu;
        });
        if(!hasMessageToolMetadata&&session.tool_calls&&session.tool_calls.length){
          S.toolCalls=(session.tool_calls||[]).map(tc=>({...tc,done:true}));
        }else{
          S.toolCalls=[];
        }
        if(isSessionViewed) _markSessionViewed(completedSid, session.message_count ?? S.messages.length);
        syncTopbar();renderMessages({preserveScroll:true});
      }
      if(_isActiveSession()) _queueDrainSid=activeSid;
      renderSessionList();
      _setActivePaneIdleIfOwner();
      return true;
    }catch(_){
      return false;
    }
  }

  function _handleStreamError(){
    // Opus review Q1: mirror done/apperror/cancel finalization so any pending rAF
    // cannot fire after renderMessages() has settled the DOM with the error message.
    _clearReconnectTimers();
    if(_persistTimer){clearTimeout(_persistTimer);_persistTimer=null;}
    _streamFinalized=true;
    if(_pendingRafHandle!==null){cancelAnimationFrame(_pendingRafHandle);clearTimeout(_pendingRafHandle);_pendingRafHandle=null;_renderPending=false;}
    if(typeof finalizeThinkingCard==='function') finalizeThinkingCard();
    _clearOwnerInflightState();
    _closeSource();
    _clearApprovalForOwner();
    _clearClarifyForOwner('terminal');
    if(S.session&&S.session.session_id===activeSid){
      S.activeStreamId=null;
      clearLiveToolCards();if(!assistantText)removeThinking();_removeProgressEl();
      S.messages.push({role:'assistant',content:'**Error:** Connection lost'});renderMessages({preserveScroll:true});
      _markSessionViewed(activeSid, S.messages.length);
    }else{
      if(typeof trackBackgroundError==='function'){
        const _errTitle=(typeof _allSessions!=='undefined'&&_allSessions.find(s=>s.session_id===activeSid)||{}).title||null;
        trackBackgroundError(activeSid,_errTitle,'Connection lost');
      }
    }
    _setActivePaneIdleIfOwner();
  }

  (async()=>{
    // Reattach path can carry stale stream ids after server restart; preflight
    // status avoids opening a dead SSE URL that will 404 in the console.
    if(reconnecting){
      try{
        const st=await api(_ownerScopedApiPath(`/api/chat/stream/status?stream_id=${encodeURIComponent(streamId)}`));
        if(!st.active){
          _clearOwnerInflightState();
          _clearApprovalForOwner();
          _clearClarifyForOwner('terminal');
          if(S.session&&S.session.session_id===activeSid){
            S.activeStreamId=null;
            clearLiveToolCards();
            removeThinking();
            if(_isActiveSession()) _queueDrainSid=activeSid;
            _setActivePaneIdleIfOwner();
            renderMessages({preserveScroll:true});
            renderSessionList();
          }
          return;
        }
      }catch(_){}
    }
    _openEventSource();
  })();

}

function transcript(){
  const lines=[`# Nova session ${S.session?.session_id||''}`,``,
    `Workspace: ${S.session?.workspace||''}`,`Model: ${S.session?.model||''}`,``];
  for(const m of S.messages){
    if(!m||m.role==='tool')continue;
    let c=m.content||'';
    if(Array.isArray(c))c=c.filter(p=>p&&p.type==='text').map(p=>p.text||'').join('\n');
    const ct=String(c).trim();
    if(!ct&&!m.attachments?.length)continue;
    const attach=m.attachments?.length?`\n\n_Files: ${m.attachments.join(', ')}_`:'';
    lines.push(`## ${m.role}`,'',ct+attach,'');
  }
  return lines.join('\n');
}

function autoResize(){const el=$('msg');if(!el)return;el.style.height='auto';el.style.height=Math.min(el.scrollHeight,300)+'px';updateSendBtn();_updateCharCounter();_syncExpandBar();}

/* Collapse expanded editor overlay if active */
function _collapseExpandIfOpen(){
  const wrap=document.querySelector('.composer-expanded');
  if(wrap&&typeof toggleExpandEditor==='function') toggleExpandEditor();
}

/* Show/hide expand bar based on textarea content */
function _syncExpandBar(){
  const bar=$('msgExpandBar');
  const el=$('msg');
  if(!bar||!el)return;
  const hasText=el.value.trim().length>0;
  const isExpanded=document.querySelector('.composer-expanded');
  if(isExpanded||hasText){bar.classList.remove('is-collapsed');}
  else {bar.classList.add('is-collapsed');}
}

/* ── Character counter near textarea ── */
function _updateCharCounter(){
  const el=$('msg');
  const cc=$('msgCharCounter');
  if(!el||!cc)return;
  const len=el.value.length;
  if(len<=1000){cc.textContent='';cc.className='msg-char-counter';}
  else if(len<=2000){cc.textContent=len+' chars';cc.className='msg-char-counter warning';}
  else {cc.textContent=len+' chars';cc.className='msg-char-counter danger';}
}

/* ── Expand/collapse full-viewport editor ── */
function toggleExpandEditor(){
  const box=document.querySelector('.composer-box');
  const wrap=document.querySelector('.composer-wrap');
  const msg=$('msg');
  if(!box||!msg)return;
  const isExpanded=box.closest('.composer-expanded');
  if(isExpanded){
    // Collapse: remove expanded mode
    const backdrop=document.querySelector('.composer-expanded-backdrop');
    const closeBtn=document.querySelector('.composer-expanded-close');
    if(backdrop)backdrop.remove();
    if(closeBtn)closeBtn.remove();
    document.querySelectorAll('.composer-expanded').forEach(el=>el.classList.remove('composer-expanded'));
    msg.style.height='auto';
    setTimeout(()=>{autoResize();msg.focus();},10);
  }else{
    // Expand: add class to parent containers, show backdrop + close button
    box.classList.add('composer-expanded');
    if(wrap)wrap.classList.add('composer-expanded');
    // Close button
    const closeBtn=document.createElement('button');
    closeBtn.className='composer-expanded-close';
    closeBtn.innerHTML='<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Close (Esc)';
    closeBtn.onclick=toggleExpandEditor;
    document.body.appendChild(closeBtn);
    // Backdrop
    const backdrop=document.createElement('div');
    backdrop.className='composer-expanded-backdrop';
    backdrop.onclick=toggleExpandEditor;
    document.body.appendChild(backdrop);
    // Focus and scroll to top
    msg.style.height='80vh';
    msg.focus();
    msg.scrollTop=0;
  }
}

// Keyboard shortcut: Ctrl+Shift+E to toggle expand
document.addEventListener('keydown',function(e){
  if((e.ctrlKey||e.metaKey)&&e.shiftKey&&(e.key==='e'||e.key==='E')){
    const active=document.activeElement;
    if(active&&active.id==='msg'){e.preventDefault();toggleExpandEditor();}
  }
});


// ── YOLO mode state ──
// Session-scoped; stored server-side in memory (tools/approval.py).
// Lifecycle:
//   • Page reload: state PERSISTS — _fetchYoloState() re-syncs from backend.
//   • Cross-tab: state is SHARED — enabling YOLO in Tab A affects Tab B for
//     the same session (both poll the same server-side flag).
//   • Server restart: state is LOST — in-memory only, not persisted to disk.
//   • Session switch: state resets — loadSession() clears _yoloEnabled and
//     fetches the new session's state.
let _yoloEnabled = false;

async function _fetchYoloState(sid) {
  try {
    const data = await api('/api/session/yolo?session_id=' + encodeURIComponent(sid));
    _yoloEnabled = !!data.yolo_enabled;
    _updateYoloPill();
  } catch (_) { /* ignore */ }
}

function _updateYoloPill() {
  const pill = $('yoloPill');
  if (!pill) return;
  pill.style.display = _yoloEnabled ? '' : 'none';
  if (_yoloEnabled) {
    pill.title = t('yolo_pill_title_active');
    pill.setAttribute('data-i18n-title', 'yolo_pill_title_active');
  }
  if (typeof applyLocaleToDOM === 'function') applyLocaleToDOM();
}

async function toggleYoloFromApproval() {
  const sid = S.session && S.session.session_id;
  if (!sid) return;
  try {
    await api('/api/session/yolo', {
      method: 'POST',
      body: JSON.stringify({ session_id: sid, enabled: true }),
    });
    _yoloEnabled = true;
    _updateYoloPill();
    hideApprovalCard(true);
    showToast(t('yolo_enabled'));
  } catch (e) { showToast('YOLO: ' + e.message); }
}

// ── Inline Approval (im Chat-Verlauf, kein Popup) ──
// Global state for the currently active inline approval card
let _inlineApprovalState = null; // {element, status, pending, pendingCount, choice, timeoutTimer}

// Track session_id of the active approval so respond goes to the right session
let _approvalSessionId = null;
let _approvalCurrentId = null;  // approval_id of the card currently shown
let _approvalPendingBySession = new Map();

// ── Global cross-session approval polling ─────────────────────────────────
// Polls ALL sessions for pending approvals every 3s, so badges and toasts
// appear even when the user is looking at a different session/space/panel.
let _globalApprovalPollTimer = null;
let _globalApprovalSessionsSeen = new Set();

function _startGlobalApprovalPoll() {
  _stopGlobalApprovalPoll();
  _globalApprovalPollTimer = setInterval(async () => {
    try {
      const data = await api("/api/approval/pending-all");
      if (!data || !data.sessions) return;
      for (const [sid, entry] of Object.entries(data.sessions)) {
        if (entry && entry.pending) {
          entry.pending._session_id = sid;
          _approvalPendingBySession.set(sid, {pending: entry.pending, pendingCount: entry.pending_count || 1});
          if (!_approvalPromptBelongsToActiveSession(sid)) {
            if (!_globalApprovalSessionsSeen.has(sid)) {
              _globalApprovalSessionsSeen.add(sid);
              let title = sid.slice(0, 8) + '…';
              const el = document.querySelector('.session-item[data-sid="' + sid.replace(/"/g,'') + '"] .session-title');
              if (el) title = el.textContent || title;
              const desc = entry.pending.description || entry.pending.command || '';
              showToast('🔴 ' + (t('approval_needed') || 'Approval needed') + ': "' + title + '" — ' + (desc.length > 60 ? desc.slice(0,60)+'…' : desc), 10000);
            }
          }
        } else {
          _approvalPendingBySession.delete(sid);
          _globalApprovalSessionsSeen.delete(sid);
        }
      }
      for (const sid of _approvalPendingBySession.keys()) {
        if (!data.sessions[sid]) {
          _approvalPendingBySession.delete(sid);
          _globalApprovalSessionsSeen.delete(sid);
        }
      }
      // Refresh sidebar badges
      if (typeof renderSessionListFromCache === 'function') renderSessionListFromCache();
    } catch (_) {}
  }, 3000);
}

function _stopGlobalApprovalPoll() {
  if (_globalApprovalPollTimer) {
    clearInterval(_globalApprovalPollTimer);
    _globalApprovalPollTimer = null;
  }
}

function _promptActiveSessionId() {
  return (S.session && S.session.session_id) || null;
}

function _approvalPromptBelongsToActiveSession(sid) {
  return !!(sid && _promptActiveSessionId() === sid);
}

function _rememberApprovalPending(pending, pendingCount) {
  if (!pending) return null;
  const sid = pending._session_id || _promptActiveSessionId();
  if (!sid) return null;
  const nextPending = {...pending, _session_id: sid};
  _approvalPendingBySession.set(sid, {pending: nextPending, pendingCount: pendingCount || 1});
  return sid;
}

function _clearApprovalPendingForSession(sid) {
  if (sid) {
    _approvalPendingBySession.delete(sid);
    _globalApprovalSessionsSeen.delete(sid);
  }
}

function _clearApprovalTimeoutTimer() {
  if (_inlineApprovalState && _inlineApprovalState.timeoutTimer) {
    clearTimeout(_inlineApprovalState.timeoutTimer);
    _inlineApprovalState.timeoutTimer = null;
  }
}

function _setInlineApprovalTimedOut() {
  if (!_inlineApprovalState || _inlineApprovalState.status !== 'pending') return;
  _inlineApprovalState.status = 'timedout';
  _clearApprovalTimeoutTimer();
  _updateInlineApprovalUI();
}

function _updateInlineApprovalUI() {
  const state = _inlineApprovalState;
  if (!state || !state.element) return;
  const el = state.element;
  const status = state.status;
  
  // Update status pill
  const statusEl = el.querySelector('.inline-approval-status');
  if (statusEl) {
    if (status === 'pending') {
      statusEl.className = 'inline-approval-status pending';
      statusEl.textContent = '⏳ ' + (state.pendingCount > 1 ? '1 of ' + state.pendingCount : 'Awaiting approval');
    } else if (status === 'approved') {
      statusEl.className = 'inline-approval-status approved';
      statusEl.textContent = '✅ Approved';
    } else if (status === 'denied') {
      statusEl.className = 'inline-approval-status denied';
      statusEl.textContent = '❌ Denied';
    } else if (status === 'timedout') {
      statusEl.className = 'inline-approval-status timed-out';
      statusEl.textContent = '⏰ Timed out';
    }
  }
  
  // Update card class
  el.classList.remove('resolved', 'timed-out');
  if (status === 'approved' || status === 'denied') {
    el.classList.add('resolved');
  } else if (status === 'timedout') {
    el.classList.add('timed-out');
  }
  
  // Handle buttons vs resolution text
  const btnContainer = el.querySelector('.inline-approval-btns');
  const resolutionEl = el.querySelector('.inline-approval-resolution');
  
  if (status !== 'pending') {
    // Remove buttons, show resolution
    if (btnContainer) btnContainer.style.display = 'none';
    if (resolutionEl) {
      resolutionEl.style.display = '';
      if (status === 'approved') {
        const choiceLabel = state.choice === 'once' ? 'Allowed once' 
          : state.choice === 'session' ? 'Allowed for session'
          : state.choice === 'always' ? 'Always allowed'
          : 'Approved';
        resolutionEl.textContent = choiceLabel;
      } else if (status === 'denied') {
        resolutionEl.textContent = 'Denied — command was not executed';
      } else if (status === 'timedout') {
        resolutionEl.textContent = 'Timed out — no response within 30s';
      }
    }
  } else {
    if (btnContainer) btnContainer.style.display = '';
    if (resolutionEl) resolutionEl.style.display = 'none';
  }
  
  el.dataset.approvalStatus = status;
}

function _removeInlineApproval() {
  _clearApprovalTimeoutTimer();
  if (_inlineApprovalState && _inlineApprovalState.element && _inlineApprovalState.element.parentNode) {
    _inlineApprovalState.element.parentNode.removeChild(_inlineApprovalState.element);
  }
  _inlineApprovalState = null;
  _approvalSessionId = null;
  _approvalCurrentId = null;
}

function _hideApprovalCardIfOwner(sid, force=false) {
  if (!sid || _approvalSessionId === sid) {
    // If still pending, mark as timed out instead of removing
    if (_inlineApprovalState && _inlineApprovalState.status === 'pending') {
      _setInlineApprovalTimedOut();
    } else {
      _removeInlineApproval();
    }
  }
}

function _renderPendingApprovalForActiveSession() {
  const sid = _promptActiveSessionId();
  if (!sid) return;
  if (_approvalSessionId && _approvalSessionId !== sid) _removeInlineApproval();
  const entry = _approvalPendingBySession.get(sid);
  if (entry) showApprovalCard(entry.pending, entry.pendingCount);
}

function showApprovalForSession(sid, pending, pendingCount) {
  if (!pending) return;
  pending._session_id = sid;
  showApprovalCard(pending, pendingCount);
}

function showApprovalCard(pending, pendingCount) {
  const sid = _rememberApprovalPending(pending, pendingCount);
  if (!_approvalPromptBelongsToActiveSession(sid)) return;
  
  const keys = pending.pattern_keys || (pending.pattern_key ? [pending.pattern_key] : []);
  const desc = (pending.description || '') + (keys.length ? ' [' + keys.join(', ') + ']' : '');
  const cmd = pending.command || '';
  
  _approvalSessionId = sid;
  _approvalCurrentId = pending.approval_id || null;
  
  // If an inline card already exists for this session, update it
  if (_inlineApprovalState && _inlineApprovalState.element && _inlineApprovalState.element.parentNode) {
    // Re-use the existing element
    const el = _inlineApprovalState.element;
    el.querySelector('.inline-approval-desc').textContent = desc;
    el.querySelector('.inline-approval-cmd').textContent = cmd;
    // Reset status to pending
    _inlineApprovalState.status = 'pending';
    _inlineApprovalState.pending = pending;
    _inlineApprovalState.pendingCount = pendingCount || 1;
    _clearApprovalTimeoutTimer();
    _updateInlineApprovalUI();
    // Start timeout
    _inlineApprovalState.timeoutTimer = setTimeout(_setInlineApprovalTimedOut, 30000);
    return;
  }
  
  // Build a new inline approval card element
  const el = document.createElement('div');
  el.className = 'inline-approval-card';
  el.dataset.approvalStatus = 'pending';
  
  // ── Header ──
  const header = document.createElement('div');
  header.className = 'inline-approval-header';
  header.innerHTML =
    '<svg class="warning-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
    '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>' +
    '<line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>' +
    '<span class="card-title">' + (t('approval_heading') || 'Approval required') + '</span>' +
    '<span class="inline-approval-status pending">⏳ ' + (pendingCount > 1 ? '1 of ' + pendingCount : 'Awaiting approval') + '</span>';
  el.appendChild(header);
  
  // ── Description ──
  const descEl = document.createElement('div');
  descEl.className = 'inline-approval-desc';
  descEl.textContent = desc;
  el.appendChild(descEl);
  
  // ── Command ──
  const cmdEl = document.createElement('div');
  cmdEl.className = 'inline-approval-cmd';
  cmdEl.textContent = cmd;
  el.appendChild(cmdEl);
  
  // ── Buttons ──
  const btns = document.createElement('div');
  btns.className = 'inline-approval-btns';
  btns.innerHTML =
    '<button class="inline-approval-btn approve" onclick="respondApproval(\'once\')" title="' + esc(t('approval_btn_once_title') || 'Allow this one command (Enter)') + '">' +
      '<span class="inline-approval-btn-icon">✓</span>' +
      '<span class="inline-approval-btn-label">' + esc(t('approval_btn_once') || 'Allow once') + '</span>' +
      '<kbd class="inline-approval-btn-kbd">↵</kbd>' +
    '</button>' +
    '<button class="inline-approval-btn session" onclick="respondApproval(\'session\')" title="' + esc(t('approval_btn_session_title') || 'Allow for this session') + '">' +
      '<span class="inline-approval-btn-icon">🔒</span>' +
      '<span class="inline-approval-btn-label">' + esc(t('approval_btn_session') || 'Allow session') + '</span>' +
    '</button>' +
    '<button class="inline-approval-btn always" onclick="respondApproval(\'always\')" title="' + esc(t('approval_btn_always_title') || 'Always allow this command pattern') + '">' +
      '<span class="inline-approval-btn-icon">★</span>' +
      '<span class="inline-approval-btn-label">' + esc(t('approval_btn_always') || 'Always allow') + '</span>' +
    '</button>' +
    '<button class="inline-approval-btn deny" onclick="respondApproval(\'deny\')" title="' + esc(t('approval_btn_deny_title') || 'Deny — do not run this command') + '">' +
      '<span class="inline-approval-btn-icon">✕</span>' +
      '<span class="inline-approval-btn-label">' + esc(t('approval_btn_deny') || 'Deny') + '</span>' +
    '</button>' +
    '<button class="inline-approval-btn yolo" onclick="toggleYoloFromApproval()" title="' + esc(t('approval_skip_all_title') || 'Skip all approvals this session') + '">' +
      '<span class="inline-approval-btn-icon" aria-hidden="true">⚡</span>' +
      '<span class="inline-approval-btn-label">' + esc(t('approval_skip_all') || 'Skip all') + '</span>' +
    '</button>';
  el.appendChild(btns);
  
  // ── Resolution text (hidden initially) ──
  const resolution = document.createElement('div');
  resolution.className = 'inline-approval-resolution';
  resolution.style.display = 'none';
  el.appendChild(resolution);
  
  // Store state
  _clearApprovalTimeoutTimer();
  _inlineApprovalState = {
    element: el,
    status: 'pending',
    pending: pending,
    pendingCount: pendingCount || 1,
    choice: null,
    timeoutTimer: null,
  };
  
  // Append to message inner
  const inner = $('msgInner');
  if (inner) {
    inner.appendChild(el);
    scrollIfPinned();
    // Start 30s timeout
    _inlineApprovalState.timeoutTimer = setTimeout(_setInlineApprovalTimedOut, 30000);
    // Focus the "Allow once" button
    const approveBtn = el.querySelector('.inline-approval-btn.approve');
    if (approveBtn) setTimeout(() => approveBtn.focus({preventScroll: true}), 50);
  }
}

async function respondApproval(choice) {
  const state = _inlineApprovalState;
  if (!state || state.status !== 'pending') return;
  const sid = _approvalSessionId || (S.session && S.session.session_id);
  if (!sid) return;
  const approvalId = _approvalCurrentId;
  
  // Disable buttons immediately to prevent double-submit
  const btns = state.element ? state.element.querySelectorAll('.inline-approval-btn') : [];
  btns.forEach(b => { b.disabled = true; });
  // Add loading class to the clicked button
  if (state.element) {
    const clickedBtn = state.element.querySelector('.inline-approval-btn.approve, .inline-approval-btn.session, .inline-approval-btn.always, .inline-approval-btn.deny');
    if (clickedBtn && clickedBtn.onclick && clickedBtn.onclick.toString().includes("'" + choice + "'")) {
      clickedBtn.classList.add('loading');
    }
  }
  
  // Mark as resolved without hiding
  state.status = choice === 'deny' ? 'denied' : 'approved';
  state.choice = choice;
  _clearApprovalTimeoutTimer();
  _approvalSessionId = null;
  _approvalCurrentId = null;
  _clearApprovalPendingForSession(sid);
  _updateInlineApprovalUI();
  
  // Call the API
  try {
    await api('/api/approval/respond', {
      method: 'POST',
      body: JSON.stringify({ session_id: sid, choice, approval_id: approvalId })
    });
  } catch(e) { setStatus(t('approval_responding') + ' ' + e.message); }
}

function hideApprovalCard(force=false) {
  // For inline approval: remove the card from DOM and clear state
  _removeInlineApproval();
}

function startApprovalPolling(sid) {
  stopApprovalPolling();
  _approvalPollingSessionId = sid || null;
  // ── SSE (preferred): long-lived connection, server pushes instantly ──
  try {
    const es = new EventSource(_eventSourceUrl('api/approval/stream?session_id=' + encodeURIComponent(sid)));
    let _fallbackActive = false;

    es.addEventListener('initial', e => {
      const d = JSON.parse(e.data);
      if (d.pending) { showApprovalForSession(sid, d.pending, d.pending_count || 1); }
      else { _clearApprovalPendingForSession(sid); _hideApprovalCardIfOwner(sid); }
    });

    es.addEventListener('approval', e => {
      const d = JSON.parse(e.data);
      if (d.pending) { showApprovalForSession(sid, d.pending, d.pending_count || 1); }
      else { _clearApprovalPendingForSession(sid); _hideApprovalCardIfOwner(sid); }
    });

    es.onerror = () => {
      // SSE failed — fall back to HTTP polling (3s interval)
      if (_fallbackActive) return;
      _fallbackActive = true;
      try { es.close(); } catch(_){}
      _startApprovalFallbackPoll(sid);
    };

    // If the session changes or stops being busy, close the SSE.
    // We detect this via a periodic check (cheap — no network request).
    _approvalSSEHealthTimer = setInterval(() => {
      if (!S.busy || !S.session || S.session.session_id !== sid) {
        stopApprovalPolling(); _hideApprovalCardIfOwner(sid, true);
      }
    }, 5000);

    _approvalEventSource = es;
  } catch(_e) {
    // EventSource constructor failed — use polling directly
    _startApprovalFallbackPoll(sid);
  }
}

let _approvalPollTimer = null;
let _approvalEventSource = null;
let _approvalSSEHealthTimer = null;
let _approvalPollingSessionId = null;

// ── Global cross-session approval monitor ──────────────────────────
// Polls /api/approval/pending-all every 3s to detect approvals in
// OTHER sessions. Surfaces them via sidebar badges + toasts.
let _globalApprovalTimer = null;
let _globalApprovalKnown = new Set();  // session_ids with known pending

function _startGlobalApprovalPoll() {
  if (_globalApprovalTimer) return;
  _globalApprovalTimer = setInterval(_pollGlobalApprovals, 3000);
  _pollGlobalApprovals();  // immediate first poll
}

function _stopGlobalApprovalPoll() {
  if (_globalApprovalTimer) {
    clearInterval(_globalApprovalTimer);
    _globalApprovalTimer = null;
  }
}

async function _pollGlobalApprovals() {
  try {
    const data = await api("/api/approval/pending-all");
    if (!data || !data.sessions) return;

    const activeSid = (S && S.session && S.session.session_id) || null;
    const nowPending = new Set();
    let changed = false;

    for (const [sid, entry] of Object.entries(data.sessions)) {
      nowPending.add(sid);
      if (!_approvalPendingBySession.has(sid)) {
        _approvalPendingBySession.set(sid, {
          pending: entry.pending,
          pendingCount: entry.pending_count || 1,
        });
        changed = true;

        // Toast for non-active sessions
        if (sid !== activeSid) {
          _notifyCrossSessionApproval(sid, entry.pending);
        }
      }
    }

    // Clear sessions that no longer have pending approvals
    for (const sid of _globalApprovalKnown) {
      if (!nowPending.has(sid) && _approvalPendingBySession.has(sid)) {
        _approvalPendingBySession.delete(sid);
        changed = true;
      }
    }

    _globalApprovalKnown = nowPending;

    if (changed && typeof renderSessionListFromCache === 'function') {
      renderSessionListFromCache();
    }
  } catch (_e) {
    // Silently ignore poll errors (network blips, server restart)
  }
}

function _notifyCrossSessionApproval(sid, pending) {
  // Store the latest sid so the toast onclick can jump there
  window._approvalToastSid = sid;
  const session = (typeof _allSessions !== 'undefined' && _allSessions)
    ? _allSessions.find(s => s && s.session_id === sid) : null;
  const title = (session && session.title) || sid.slice(0, 8);
  const desc = pending.description || 'Genehmigung benötigt';
  if (typeof showToast === 'function') {
    showToast(`🔴 ${title}: ${desc}`, 15000, 'warning');
    // Make the toast clickable — jumps to the session
    const el = document.getElementById('toast');
    if (el) {
      el.style.cursor = 'pointer';
      el.onclick = function _approvalToastClick() {
        const sid = window._approvalToastSid;
        if (sid && typeof loadSession === 'function') {
          loadSession(sid).then(() => {
            if (typeof renderSessionListFromCache === 'function') renderSessionListFromCache();
          }).catch(() => {});
        }
        // Hide the toast
        el.className = 'toast';
        el.onclick = null;
      };
    }
  }
}

function _startApprovalFallbackPoll(sid) {
  _approvalPollTimer = setInterval(async () => {
    if (!S.busy || !S.session || S.session.session_id !== sid) {
      stopApprovalPolling(); _hideApprovalCardIfOwner(sid, true); return;
    }
    try {
      const data = await api("/api/approval/pending?session_id=" + encodeURIComponent(sid));
      if (data.pending) { showApprovalForSession(sid, data.pending, data.pending_count||1); }
      else { _clearApprovalPendingForSession(sid); _hideApprovalCardIfOwner(sid); }
    } catch(e) { /* ignore poll errors */ }
  }, 1500);  // matches the v0.50.247 polling cadence so degraded-mode users see the same responsiveness
}

function stopApprovalPollingForSession(sid) {
  if(sid && _approvalPollingSessionId && _approvalPollingSessionId!==sid) return;
  stopApprovalPolling();
}

function stopApprovalPolling() {
  if (_approvalPollTimer) { clearInterval(_approvalPollTimer); _approvalPollTimer = null; }
  if (_approvalEventSource) { try { _approvalEventSource.close(); } catch(_){} _approvalEventSource = null; }
  if (_approvalSSEHealthTimer) { clearInterval(_approvalSSEHealthTimer); _approvalSSEHealthTimer = null; }
  _approvalPollingSessionId = null;
}

// ── Active subagents panel ────────────────────────────────────────────────
let _subagentPollTimer = null;
let _subagentPollSessionId = null;
let _subagentPanelState = null;

function _subagentCurrentSessionId() {
  return (S && S.session && S.session.session_id) || null;
}

function _subagentElapsed(startedAt) {
  const started = Number(startedAt || 0);
  if (!Number.isFinite(started) || started <= 0) return '';
  const elapsed = Math.max(0, (Date.now() / 1000) - started);
  if (elapsed < 60) return Math.max(1, Math.round(elapsed)) + 's';
  const mins = Math.floor(elapsed / 60);
  const secs = Math.round(elapsed % 60);
  return secs ? `${mins}m ${secs}s` : `${mins}m`;
}

function _subagentRemovePanel() {
  if (_subagentPanelState && _subagentPanelState.element && _subagentPanelState.element.parentNode) {
    _subagentPanelState.element.parentNode.removeChild(_subagentPanelState.element);
  }
  if (_subagentPanelState) {
    _subagentPanelState.visible = false;
    _subagentPanelState.active = [];
  }
}

function _subagentEnsurePanelState() {
  if (_subagentPanelState && _subagentPanelState.element) return _subagentPanelState;

  const element = document.createElement('div');
  element.className = 'subagent-panel-card';
  element.setAttribute('role', 'region');
  element.setAttribute('aria-label', 'Active subagents');
  element.setAttribute('aria-live', 'polite');

  const header = document.createElement('div');
  header.className = 'subagent-panel-header';

  const title = document.createElement('span');
  title.className = 'subagent-panel-title';
  title.textContent = (typeof t === 'function' ? (t('subagent_children') || 'Subagent sessions') : 'Subagent sessions');
  header.appendChild(title);

  const status = document.createElement('span');
  status.className = 'subagent-panel-status';
  header.appendChild(status);

  const actions = document.createElement('div');
  actions.className = 'subagent-panel-actions';
  const toggleBtn = document.createElement('button');
  toggleBtn.type = 'button';
  toggleBtn.className = 'subagent-panel-btn';
  toggleBtn.textContent = 'Pause spawning';
  actions.appendChild(toggleBtn);
  header.appendChild(actions);

  const list = document.createElement('div');
  list.className = 'subagent-panel-list';

  element.appendChild(header);
  element.appendChild(list);

  _subagentPanelState = {
    element,
    header,
    title,
    status,
    actions,
    toggleBtn,
    list,
    visible: false,
    sessionId: null,
    spawnPaused: false,
    active: [],
  };

  toggleBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    const next = !(_subagentPanelState && _subagentPanelState.spawnPaused);
    try {
      const data = await api('/api/subagents', {
        method: 'POST',
        body: JSON.stringify({spawn_paused: next}),
      });
      _subagentRenderPanel(data || {});
      showToast(next ? 'Subagent spawning paused' : 'Subagent spawning resumed');
    } catch (err) {
      showToast('Subagents: ' + (err && err.message ? err.message : 'update failed'));
    }
  });

  return _subagentPanelState;
}

function _subagentBuildRow(entry) {
  const row = document.createElement('div');
  const childSid = String(entry && entry.session_id || '').trim();
  row.className = 'subagent-panel-row' + (childSid ? ' clickable' : '');
  if (childSid) row.title = 'Open subagent session';

  const main = document.createElement('div');
  main.className = 'subagent-panel-row-main';

  const goal = document.createElement('div');
  goal.className = 'subagent-panel-goal';
  goal.textContent = String(entry && entry.goal || 'Subagent');
  main.appendChild(goal);

  const meta = document.createElement('div');
  meta.className = 'subagent-panel-sub';
  const bits = [];
  const subId = String(entry && entry.subagent_id || '').trim();
  const model = String(entry && entry.model || '').trim();
  const depth = Number(entry && entry.depth);
  const toolCount = Number(entry && entry.tool_count);
  const age = _subagentElapsed(entry && entry.started_at);
  const status = String(entry && entry.status || '').trim();
  if (subId) bits.push(subId.slice(0, 8));
  if (model) bits.push(model);
  if (Number.isFinite(depth)) bits.push('depth ' + depth);
  if (Number.isFinite(toolCount)) bits.push(toolCount + ' tool' + (toolCount === 1 ? '' : 's'));
  if (age) bits.push(age);
  if (status) bits.push(status);
  meta.textContent = bits.join(' · ');
  main.appendChild(meta);

  const actions = document.createElement('div');
  actions.className = 'subagent-panel-row-actions';

  if (childSid) {
    const openBtn = document.createElement('button');
    openBtn.type = 'button';
    openBtn.className = 'subagent-panel-row-btn';
    openBtn.textContent = 'Open';
    openBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (typeof loadSession !== 'function') return;
      try {
        await loadSession(childSid);
        if (typeof renderSessionListFromCache === 'function') renderSessionListFromCache();
      } catch (_) {}
    });
    actions.appendChild(openBtn);
  }

  if (subId) {
    const interruptBtn = document.createElement('button');
    interruptBtn.type = 'button';
    interruptBtn.className = 'subagent-panel-row-btn danger';
    interruptBtn.textContent = 'Interrupt';
    interruptBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      try {
        const data = await api('/api/subagents', {
          method: 'POST',
          body: JSON.stringify({subagent_id: subId}),
        });
        _subagentRenderPanel(data || {});
        showToast((data && data.interrupted) ? 'Subagent interrupt requested' : 'Subagent already finished');
      } catch (err) {
        showToast('Subagent interrupt failed: ' + (err && err.message ? err.message : subId));
      }
    });
    actions.appendChild(interruptBtn);
  }

  row.appendChild(main);
  row.appendChild(actions);

  if (childSid) {
    row.addEventListener('click', async (e) => {
      if (e.target && e.target.closest && e.target.closest('button')) return;
      if (typeof loadSession !== 'function') return;
      try {
        await loadSession(childSid);
        if (typeof renderSessionListFromCache === 'function') renderSessionListFromCache();
      } catch (_) {}
    });
  }

  return row;
}

function _subagentRenderPanel(payload) {
  const sid = _subagentCurrentSessionId();
  if (!sid) {
    stopSubagentPolling();
    return;
  }

  const active = Array.isArray(payload && payload.active)
    ? payload.active.filter(entry => String(entry && entry.session_id || '') === sid)
    : [];
  const spawnPaused = !!(payload && payload.spawn_paused);
  const streamActive = !!(S && (S.busy || S.activeStreamId || (S.session && S.session.active_stream_id)));
  if (!spawnPaused && !active.length) {
    _subagentRemovePanel();
    if (_subagentPanelState) _subagentPanelState.visible = false;
    if (!streamActive) stopSubagentPolling();
    return;
  }

  const state = _subagentEnsurePanelState();
  state.visible = true;
  state.sessionId = sid;
  state.spawnPaused = spawnPaused;
  state.active = active;
  state.status.className = 'subagent-panel-status' + (spawnPaused ? ' paused' : '');

  const statusBits = [];
  if (active.length) statusBits.push(active.length + (active.length === 1 ? ' active subagent' : ' active subagents'));
  if (spawnPaused) statusBits.push('spawning paused');
  state.status.textContent = statusBits.join(' · ') || 'Subagents';
  state.toggleBtn.textContent = spawnPaused ? 'Resume spawning' : 'Pause spawning';

  state.list.innerHTML = '';
  if (active.length) {
    active
      .slice()
      .sort((a, b) => Number(b && b.started_at || 0) - Number(a && a.started_at || 0))
      .forEach(entry => state.list.appendChild(_subagentBuildRow(entry)));
  } else {
    const empty = document.createElement('div');
    empty.className = 'subagent-panel-empty';
    empty.textContent = 'Subagent spawning is paused.';
    state.list.appendChild(empty);
  }
}

async function _refreshSubagentPanel() {
  const sid = _subagentCurrentSessionId();
  if (!sid) {
    stopSubagentPolling();
    return;
  }
  try {
    const data = await api('/api/subagents');
    if (sid !== _subagentCurrentSessionId()) return;
    _subagentRenderPanel(data || {});
  } catch (_) {
    // ignore transient polling errors
  }
}

function _ensureSubagentPolling() {
  const sid = _subagentCurrentSessionId();
  if (!sid) {
    stopSubagentPolling();
    return;
  }
  const state = _subagentPanelState;
  const streamActive = !!(S && (S.busy || S.activeStreamId || (S.session && S.session.active_stream_id)));
  if (!streamActive && !(state && (state.visible || state.spawnPaused))) {
    stopSubagentPolling();
    return;
  }
  if (_subagentPollSessionId !== sid || !_subagentPollTimer) {
    startSubagentPolling(sid);
  }
}

function startSubagentPolling(sid) {
  stopSubagentPolling();
  if (!sid) return;
  _subagentPollSessionId = sid;
  _subagentPollTimer = setInterval(async () => {
    if (!S || !S.session || S.session.session_id !== sid) {
      stopSubagentPolling();
      return;
    }
    await _refreshSubagentPanel();
  }, 2500);
  void _refreshSubagentPanel();
}

function stopSubagentPolling() {
  if (_subagentPollTimer) {
    clearInterval(_subagentPollTimer);
    _subagentPollTimer = null;
  }
  _subagentPollSessionId = null;
  _subagentRemovePanel();
}

// ── Clarify polling ──
let _clarifyPollTimer = null;
let _clarifyHideTimer = null;
let _clarifyVisibleSince = 0;
let _clarifySignature = '';
let _clarifySessionId = null;
let _clarifyMissingEndpointWarned = false;
let _clarifyCountdownTimer = null;
let _clarifyExpiresAt = 0;
let _clarifyPendingBySession = new Map();
const CLARIFY_MIN_VISIBLE_MS = 30000;

function _clarifyPromptBelongsToActiveSession(sid) {
  return !!(sid && _promptActiveSessionId() === sid);
}

function _rememberClarifyPending(pending) {
  if (!pending) return null;
  const sid = pending._session_id || _promptActiveSessionId();
  if (!sid) return null;
  const nextPending = {...pending, _session_id: sid};
  _clarifyPendingBySession.set(sid, {pending: nextPending});
  return sid;
}

function _clearClarifyPendingForSession(sid) {
  if (sid) _clarifyPendingBySession.delete(sid);
}

function _hideClarifyCardIfOwner(sid, force=false, reason="dismissed") {
  if (!sid || _clarifySessionId === sid) hideClarifyCard(force, reason);
}

function _renderPendingClarifyForActiveSession() {
  const sid = _promptActiveSessionId();
  if (!sid) return;
  if (_clarifySessionId && _clarifySessionId !== sid) hideClarifyCard(true, 'session');
  const entry = _clarifyPendingBySession.get(sid);
  if (entry) showClarifyCard(entry.pending);
}

function showClarifyForSession(sid, pending) {
  if (!pending) return;
  pending._session_id = sid;
  showClarifyCard(pending);
}

function _renderPendingPromptsForActiveSession() {
  _renderPendingApprovalForActiveSession();
  _renderPendingClarifyForActiveSession();
}

function _ensureClarifyCardDom() {
  let card = $("clarifyCard");
  if (card) return card;
  const host = $("msgInner") || $("messages");
  if (!host) return null;
  card = document.createElement("div");
  card.className = "clarify-card";
  card.id = "clarifyCard";
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-labelledby", "clarifyHeading");
  card.setAttribute("aria-describedby", "clarifyQuestion clarifyHint");
  card.setAttribute("aria-hidden", "true");
  card.setAttribute("inert", "");
  card.innerHTML = `
    <div class="clarify-inner">
      <div class="clarify-header">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 17h.01"/><path d="M9.09 9a3 3 0 1 1 5.82 1c0 2-3 2-3 4"/><circle cx="12" cy="12" r="10"/></svg>
        <span id="clarifyHeading" data-i18n="clarify_heading">Clarification needed</span>
        <span class="clarify-countdown" id="clarifyCountdown"></span>
      </div>
      <div class="clarify-question" id="clarifyQuestion"></div>
      <div class="clarify-choices" id="clarifyChoices"></div>
      <div class="clarify-response">
        <input class="clarify-input" id="clarifyInput" type="text" data-i18n-placeholder="clarify_input_placeholder" placeholder="Type your response…">
        <button class="clarify-submit" id="clarifySubmit" data-i18n="clarify_send">Send</button>
      </div>
      <div class="clarify-hint" id="clarifyHint" data-i18n="clarify_hint">Please choose one option, or type your own response below.</div>
    </div>
  `;
  host.appendChild(card);
  const submit = $("clarifySubmit");
  if (submit) submit.onclick = () => respondClarify();
  if (typeof applyLocaleToDOM === "function") applyLocaleToDOM();
  return card;
}

function _clearClarifyHideTimer() {
  if (_clarifyHideTimer) {
    clearTimeout(_clarifyHideTimer);
    _clarifyHideTimer = null;
  }
}

function _clearClarifyCountdownTimer() {
  if (_clarifyCountdownTimer) {
    clearInterval(_clarifyCountdownTimer);
    _clarifyCountdownTimer = null;
  }
  _clarifyExpiresAt = 0;
  const countdown = $("clarifyCountdown");
  if (countdown) {
    countdown.textContent = "";
    countdown.classList.remove("urgent");
  }
}

function _clarifyExpiryMs(pending) {
  const expiresAt = Number(pending && pending.expires_at);
  if (Number.isFinite(expiresAt) && expiresAt > 0) return expiresAt * 1000;
  const requestedAt = Number(pending && pending.requested_at);
  const timeoutSeconds = Number(pending && pending.timeout_seconds);
  if (Number.isFinite(requestedAt) && Number.isFinite(timeoutSeconds)) {
    return (requestedAt + timeoutSeconds) * 1000;
  }
  return 0;
}

function _updateClarifyCountdown() {
  const countdown = $("clarifyCountdown");
  if (!countdown || !_clarifyExpiresAt) return;
  const remaining = Math.max(0, Math.ceil((_clarifyExpiresAt - Date.now()) / 1000));
  countdown.textContent = `${remaining}s`;
  countdown.classList.toggle("urgent", remaining <= 10);
}

function _startClarifyCountdown(pending) {
  const expiresAt = _clarifyExpiryMs(pending);
  if (_clarifyCountdownTimer && _clarifyExpiresAt === expiresAt) return;
  _clearClarifyCountdownTimer();
  _clarifyExpiresAt = expiresAt;
  if (!_clarifyExpiresAt) return;
  _updateClarifyCountdown();
  _clarifyCountdownTimer = setInterval(_updateClarifyCountdown, 1000);
}

function _stashClarifyDraft(reason) {
  if (reason !== "expired" && reason !== "terminal") return false;
  const input = $("clarifyInput");
  const draft = String((input && input.value) || "").trim();
  if (!draft) return false;
  const sid = _clarifySessionId || (S.session && S.session.session_id) || "unknown";
  const key = `sidekick-clarify-draft-${sid}-${_clarifySignature || "unknown"}`;
  try {
    sessionStorage.setItem(key, JSON.stringify({
      draft,
      reason,
      saved_at: Date.now(),
    }));
  } catch (_) {}
  const composer = $('msg');
  if (composer) {
    const current = String(composer.value || "");
    composer.value = current.trim() ? `${current.replace(/\s+$/, "")}\n\n${draft}` : draft;
    if (typeof autoResize === "function") autoResize();
    if (typeof updateSendBtn === "function") updateSendBtn();
  }
  const notice = reason === "expired"
    ? "Clarification timed out. Your draft was kept in the composer."
    : "Clarification closed. Your draft was kept in the composer.";
  if (typeof setComposerStatus === "function") setComposerStatus(notice);
  else if (typeof setStatus === "function") setStatus(notice);
  if (typeof showToast === "function") showToast(notice, 5000);
  return true;
}

function _resetClarifyCardState() {
  _clearClarifyHideTimer();
  _clearClarifyCountdownTimer();
  _clarifyVisibleSince = 0;
  _clarifySignature = '';
}

function hideClarifyCard(force=false, reason="dismissed") {
  const card = $("clarifyCard");
  if (!card) {
    _clarifySessionId = null;
    _resetClarifyCardState();
    if (typeof unlockComposerForClarify === "function") unlockComposerForClarify();
    return;
  }
  if (!force && reason !== "expired" && _clarifyVisibleSince) {
    const remaining = CLARIFY_MIN_VISIBLE_MS - (Date.now() - _clarifyVisibleSince);
    if (remaining > 0) {
      const scheduledSignature = _clarifySignature;
      _clearClarifyHideTimer();
      _clarifyHideTimer = setTimeout(() => {
        _clarifyHideTimer = null;
        if (_clarifySignature !== scheduledSignature) return;
        hideClarifyCard(true, reason);
      }, remaining);
      return;
    }
  }
  _stashClarifyDraft(reason);
  _clarifySessionId = null;
  _resetClarifyCardState();
  card.classList.remove("visible");
  card.setAttribute("aria-hidden", "true");
  card.setAttribute("inert", "");
  if (typeof unlockComposerForClarify === "function") unlockComposerForClarify();
  $("clarifyQuestion").textContent = "";
  $("clarifyChoices").innerHTML = "";
  $("clarifyInput").value = "";
  $("clarifyInput").disabled = false;
  $("clarifyInput").onkeydown = null;
  const submit = $("clarifySubmit");
  if (submit) { submit.disabled = false; submit.classList.remove("loading"); }
}

function _clarifySetControlsDisabled(disabled, loading=false) {
  const input = $("clarifyInput");
  const submit = $("clarifySubmit");
  if (input) input.disabled = disabled;
  if (submit) {
    submit.disabled = disabled;
    submit.classList.toggle("loading", !!loading);
  }
  const choices = $("clarifyChoices");
  if (choices) {
    choices.querySelectorAll("button").forEach(btn => {
      btn.disabled = disabled;
      if (loading && btn.dataset && btn.dataset.choice === "other") {
        btn.classList.toggle("loading", false);
      }
    });
  }
}

function showClarifyCard(pending) {
  const sid = _rememberClarifyPending(pending);
  if (!_clarifyPromptBelongsToActiveSession(sid)) return;
  const question = pending.question || pending.description || '';
  const choices = Array.isArray(pending.choices_offered)
    ? pending.choices_offered
    : (Array.isArray(pending.choices) ? pending.choices : []);
  const sig = JSON.stringify({
    question,
    choices,
    sid: pending._session_id || (S.session && S.session.session_id) || null,
  });
  const card = _ensureClarifyCardDom();
  if (!card) return;
  const questionEl = $("clarifyQuestion");
  const choicesEl = $("clarifyChoices");
  const input = $("clarifyInput");
  const sameClarify = card.classList.contains("visible") && _clarifySignature === sig;
  _clarifySessionId = sid;
  _clarifySignature = sig;
  _startClarifyCountdown(pending);
  if (!sameClarify) {
    _clarifyVisibleSince = Date.now();
    _clearClarifyHideTimer();
  }
  if (questionEl) questionEl.textContent = question;
  if (choicesEl) {
    choicesEl.innerHTML = '';
    choicesEl.style.display = choices.length ? '' : 'none';
    if (choices.length) {
      choices.forEach((choice, idx) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'clarify-choice';
        btn.dataset.choice = choice;
        btn.onclick = () => respondClarify(choice);
        const badge = document.createElement('span');
        badge.className = 'clarify-choice-badge';
        badge.textContent = String(idx + 1);
        const text = document.createElement('span');
        text.className = 'clarify-choice-text';
        text.textContent = choice;
        btn.appendChild(badge);
        btn.appendChild(text);
        choicesEl.appendChild(btn);
      });
      const other = document.createElement('button');
      other.type = 'button';
      other.className = 'clarify-choice other';
      other.dataset.choice = 'other';
      other.setAttribute('data-i18n', 'clarify_other');
      const otherBadge = document.createElement('span');
      otherBadge.className = 'clarify-choice-badge other';
      otherBadge.textContent = '•';
      const otherText = document.createElement('span');
      otherText.className = 'clarify-choice-text';
      otherText.textContent = t('clarify_other') || 'Other';
      other.appendChild(otherBadge);
      other.appendChild(otherText);
      other.onclick = () => {
        const el = $("clarifyInput");
        if (el) {
          el.focus();
          if (typeof el.select === 'function') el.select();
        }
      };
      choicesEl.appendChild(other);
    }
  }
  if (input) {
    if (!sameClarify) input.value = '';
    input.disabled = false;
    input.onkeydown = (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        respondClarify();
      }
    };
  }
  if (typeof lockComposerForClarify === "function") {
    lockComposerForClarify(question ? `Clarification needed: ${question}` : "Clarification needed");
  }
  _clarifySetControlsDisabled(false, false);
  card.removeAttribute("inert");
  card.setAttribute("aria-hidden", "false");
  card.classList.add("visible");
  if (typeof applyLocaleToDOM === "function") applyLocaleToDOM();
  if (input && !sameClarify) setTimeout(() => input.focus({preventScroll: true}), 50);
}

async function respondClarify(response) {
  const sid = _clarifySessionId || (S.session && S.session.session_id);
  if (!sid) return;
  const input = $("clarifyInput");
  let value = typeof response === 'string' ? response : (input ? input.value : '');
  value = String(value || '').trim();
  if (!value) {
    if (input) input.focus();
    return;
  }
  _clarifySessionId = null;
  _clearClarifyPendingForSession(sid);
  _clarifySetControlsDisabled(true, true);
  hideClarifyCard(true, 'sent');
  try {
    await api("/api/clarify/respond", {
      method: "POST",
      body: JSON.stringify({ session_id: sid, response: value })
    });
  } catch(e) { setStatus(t("clarify_responding") + " " + e.message); }
}

var _clarifyEventSource = null;
var _clarifyFallbackTimer = null;
var _clarifyHealthTimer = null;
let _clarifyPollingSessionId = null;

function startClarifyPolling(sid) {
  stopClarifyPolling();
  _clarifyPollingSessionId = sid || null;
  _clarifyMissingEndpointWarned = false;

  // SSE primary path: long-lived connection pushes events instantly.
  try {
    _clarifyEventSource = new EventSource(_eventSourceUrl('api/clarify/stream?session_id=' + encodeURIComponent(sid)));
  } catch(e) {
    _startClarifyFallbackPoll(sid);
    return;
  }

  _clarifyEventSource.addEventListener('initial', function(ev) {
    try {
      var d = JSON.parse(ev.data);
      if (d.pending) { showClarifyForSession(sid, d.pending); }
      else { _clearClarifyPendingForSession(sid); _hideClarifyCardIfOwner(sid, false, 'expired'); }
    } catch(e) {}
  });

  _clarifyEventSource.addEventListener('clarify', function(ev) {
    try {
      var d = JSON.parse(ev.data);
      if (d.pending) { showClarifyForSession(sid, d.pending); }
      else { _clearClarifyPendingForSession(sid); _hideClarifyCardIfOwner(sid, false, 'expired'); }
    } catch(e) {}
  });

  _clarifyEventSource.onerror = function() {
    stopClarifyPolling();
    _startClarifyFallbackPoll(sid);
  };

  // Stale-detector: track last event timestamp; only reconnect if no event
  // (initial or clarify) has arrived in 60s. The server sends a keepalive
  // comment line every 30s but EventSource silently consumes those; we only
  // bump lastEventAt on actual application events. With no real events for
  // 60s on a long-lived clarify connection the server is effectively silent
  // and a reconnect is the safe move.
  //
  // Without the lastEventAt gate the original PR force-reconnected every 60s
  // regardless of activity, which churned one TCP/SSE setup per minute per
  // active session. (Opus pre-release review of v0.50.249.)
  let _lastClarifyEventAt = Date.now();
  const _markClarifyEvent = () => { _lastClarifyEventAt = Date.now(); };
  _clarifyEventSource.addEventListener('initial', _markClarifyEvent);
  _clarifyEventSource.addEventListener('clarify', _markClarifyEvent);
  _clarifyHealthTimer = setInterval(function() {
    if (Date.now() - _lastClarifyEventAt < 60000) return;
    if (_clarifyEventSource) {
      try { _clarifyEventSource.close(); } catch(_){}
      _clarifyEventSource = null;
    }
    clearInterval(_clarifyHealthTimer); _clarifyHealthTimer = null;
    startClarifyPolling(sid);
  }, 60000);
}

function _startClarifyFallbackPoll(sid) {
  _clarifyFallbackTimer = setInterval(async () => {
    if (!S.session || S.session.session_id !== sid) {
      stopClarifyPolling(); _hideClarifyCardIfOwner(sid, true, 'session'); return;
    }
    try {
      const data = await api("/api/clarify/pending?session_id=" + encodeURIComponent(sid));
      if (data.pending) { showClarifyForSession(sid, data.pending); }
      else { _clearClarifyPendingForSession(sid); _hideClarifyCardIfOwner(sid, false, 'expired'); }
    } catch(e) {
      const msg = String((e && e.message) || "");
      if (!_clarifyMissingEndpointWarned && /(^|\b)(404|not found)(\b|$)/i.test(msg)) {
        _clarifyMissingEndpointWarned = true;
        setComposerStatus("Clarify unavailable on current server build. Restart server.");
        if (typeof showToast === "function") {
          showToast("Clarify endpoint unavailable. Please restart server.", 5000);
        }
        stopClarifyPolling();
      }
    }
  }, 3000);
}

function stopClarifyPollingForSession(sid) {
  if(sid && _clarifyPollingSessionId && _clarifyPollingSessionId!==sid) return;
  stopClarifyPolling();
}

function stopClarifyPolling() {
  if (_clarifyEventSource) { try { _clarifyEventSource.close(); } catch(_){} _clarifyEventSource = null; }
  if (_clarifyFallbackTimer) { clearInterval(_clarifyFallbackTimer); _clarifyFallbackTimer = null; }
  if (_clarifyHealthTimer) { clearInterval(_clarifyHealthTimer); _clarifyHealthTimer = null; }
  _clarifyPollingSessionId = null;
}

// ── Notifications and Sound ──────────────────────────────────────────────────

function playNotificationSound(){
  if(!window._soundEnabled) return;
  try{
    const ctx=new (window.AudioContext||window.webkitAudioContext)();
    const osc=ctx.createOscillator();
    const gain=ctx.createGain();
    osc.connect(gain);gain.connect(ctx.destination);
    osc.type='sine';osc.frequency.setValueAtTime(660,ctx.currentTime);
    osc.frequency.setValueAtTime(880,ctx.currentTime+0.1);
    gain.gain.setValueAtTime(0.3,ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01,ctx.currentTime+0.3);
    osc.start(ctx.currentTime);osc.stop(ctx.currentTime+0.3);
    osc.onended=()=>ctx.close();
  }catch(e){console.warn('Notification sound failed:',e);}
}

function sendBrowserNotification(title,body){
  if(!window._notificationsEnabled||!document.hidden) return;
  if(!('Notification' in window)) return;
  const botName=window._botName||'Nova';
  if(Notification.permission==='granted'){
    new Notification(title||botName,{body:body});
  }else if(Notification.permission!=='denied'){
    Notification.requestPermission().then(p=>{
      if(p==='granted') new Notification(title||botName,{body:body});
    });
  }
}

// ── /btw ephemeral stream ────────────────────────────────────────────────────
// Connects to the ephemeral SSE stream from /api/btw and renders the answer
// in a visually distinct bubble that is NOT persisted to session history.

function attachBtwStream(parentSid, streamId, question){
  if(!parentSid||!streamId) return;
  const src=new EventSource(_eventSourceUrl('api/chat/stream?stream_id='+encodeURIComponent(streamId)));
  let answer='';
  let btwRow=null;
  let _streamDone=false;
  function _ensureBtwRow(){
    if(btwRow&&btwRow.isConnected) return;
    const inner=$('msgInner');
    if(!inner) return;
    btwRow=document.createElement('div');
    btwRow.className='msg-row msg-row-btw';
    btwRow.dataset.role='assistant';
    btwRow.dataset.btw='1';
    const labelEl=document.createElement('div');
    labelEl.className='msg-btw-label';
    labelEl.textContent=t('btw_label');
    const qEl=document.createElement('div');
    qEl.className='msg-body';
    qEl.textContent=question;
    const ansEl=document.createElement('div');
    ansEl.className='msg-body msg-btw-answer';
    ansEl.textContent='...';
    btwRow.appendChild(labelEl);
    btwRow.appendChild(qEl);
    btwRow.appendChild(ansEl);
    inner.appendChild(btwRow);
    btwRow.scrollIntoView({behavior:'smooth',block:'end'});
  }
  src.addEventListener('token',e=>{
    try{answer+=JSON.parse(e.data).text||'';}catch(_){}
    _ensureBtwRow();
    const ansEl=btwRow&&btwRow.querySelector('.msg-btw-answer');
    if(ansEl) ansEl.innerHTML=renderMd(answer);
  });
  src.addEventListener('done',e=>{
    _streamDone=true;
    src.close();
    try{
      const d=JSON.parse(e.data);
      if(d.answer&&!answer) answer=d.answer;
    }catch(_){}
    if(S.session&&S.session.session_id===parentSid) _ensureBtwRow();
    if(btwRow&&btwRow.isConnected){
      const ansEl=btwRow.querySelector('.msg-btw-answer');
      if(ansEl) ansEl.innerHTML=renderMd(answer||t('btw_no_answer'));
    }
    showToast(t('btw_done'));
  });
  src.addEventListener('apperror',e=>{
    _streamDone=true;
    src.close();
    try{
      const d=JSON.parse(e.data);
      showToast(t('btw_failed')+(d.message||''));
    }catch(_){showToast(t('btw_failed'));}
    if(btwRow&&btwRow.isConnected) btwRow.remove();
  });
  src.addEventListener('stream_end',()=>{_streamDone=true;src.close();});
  src.onerror=()=>{src.close();if(!_streamDone&&btwRow&&btwRow.isConnected) btwRow.remove();};
}

// ── /background task tracking ────────────────────────────────────────────────

let _bgPollTimers={};
let _bgActiveTasks=new Set();

function showBackgroundBadge(taskId){
  _bgActiveTasks.add(taskId);
  const badge=$('bgBadge');
  if(badge){
    badge.textContent=String(_bgActiveTasks.size);
    badge.style.display=_bgActiveTasks.size?'':'none';
  }
}
function hideBackgroundBadge(taskId){
  _bgActiveTasks.delete(taskId);
  const badge=$('bgBadge');
  if(badge){
    badge.textContent=String(_bgActiveTasks.size);
    badge.style.display=_bgActiveTasks.size?'':'none';
  }
}
function startBackgroundPolling(parentSid, taskId, prompt){
  if(_bgPollTimers[taskId]) return;
  async function _poll(){
    try{
      const r=await api('/api/background/status?session_id='+encodeURIComponent(parentSid));
      if(r&&r.results){
        for(const res of r.results){
          if(res.task_id===taskId){
            hideBackgroundBadge(taskId);
            delete _bgPollTimers[taskId];
            const msg={role:'assistant',content:`**${t('bg_label')}** ${prompt.slice(0,80)}\n\n${res.answer||t('bg_no_answer')}`,'_background':true,_ts:Date.now()/1000};
            S.messages.push(msg);
            renderMessages({preserveScroll:true});
            showToast(t('bg_complete'));
            return;
          }
        }
      }
    }catch(_){}
    _bgPollTimers[taskId]=setTimeout(_poll,3000);
  }
  _poll();
}

// ── Clickable file paths in messages ────────────────────────────────────────
// After renderMessages builds the DOM, scan .msg-body text nodes for file path
// patterns and wrap them in clickable spans that open the file in the workspace.
// Uses event delegation so cached DOM from _sessionHtmlCache retains function.

const FILE_PATH_REGEX = /(?:^|[\s()[\]{},"';:>])((?:\w[\w./-]*)?[\w.-]+\.[a-zA-Z]{2,6})(?=[\s()[\]{},"';:<]|$)/g;

function _makeFilePathsClickable() {
  const container = document.getElementById('msgInner');
  if (!container) return;
  // Scan only .msg-body elements that haven't been processed yet.
  // After a full renderMessages() rebuild, all bodies are new DOM nodes so
  // they lack data-paths-done and will be picked up naturally.
  const bodies = container.querySelectorAll('.msg-body:not([data-paths-done])');
  for (const body of bodies) {
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT, null, false);
    const textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);

    let modified = false;
    // Process in reverse order so node splits don't affect sibling indices
    for (let i = textNodes.length - 1; i >= 0; i--) {
      const node = textNodes[i];
      if (!node.textContent.trim()) continue;

      // Skip nodes inside <a>, <code>, <pre>, <button>, <textarea>
      let skip = false;
      for (let p = node.parentElement; p && p !== body; p = p.parentElement) {
        const tag = p.tagName.toLowerCase();
        if (tag === 'a' || tag === 'code' || tag === 'pre' || tag === 'button' || tag === 'textarea' || tag === 'script') {
          skip = true;
          break;
        }
      }
      if (skip) continue;

      const text = node.textContent;
      // Collect all matches: full match at pos, and the captured path
      FILE_PATH_REGEX.lastIndex = 0;
      const allMatches = [];
      let m;
      while ((m = FILE_PATH_REGEX.exec(text)) !== null) {
        const fullMatch = m[0];           // " src/foo.ts" (with leading space)
        const thePath = m[1];             // "src/foo.ts"
        const fullIdx = m.index;          // position of full match in text
        // Position of the path inside the full match
        const pathOffset = fullMatch.indexOf(thePath);
        const pathStart = fullIdx + pathOffset;
        const pathEnd = pathStart + thePath.length;
        allMatches.push({ thePath, pathStart, pathEnd });
      }
      if (!allMatches.length) continue;

      modified = true;
      const fragment = document.createDocumentFragment();
      let cursor = 0;

      for (const match of allMatches) {
        // Text before this match
        if (match.pathStart > cursor) {
          fragment.appendChild(document.createTextNode(text.slice(cursor, match.pathStart)));
        }
        // The clickable span
        const span = document.createElement('span');
        span.className = 'clickable-file-path';
        span.setAttribute('data-path', match.thePath);
        span.textContent = match.thePath;
        fragment.appendChild(span);
        cursor = match.pathEnd;
      }

      // Remaining text after last match
      if (cursor < text.length) {
        fragment.appendChild(document.createTextNode(text.slice(cursor)));
      }

      node.parentNode.replaceChild(fragment, node);
    }
    if (modified) body.dataset.pathsDone = '1';
  }
}

// Delegated click handler for .clickable-file-path spans
document.addEventListener('click', function _handleClickablePath(e) {
  const target = e.target.closest('.clickable-file-path');
  if (!target) return;
  e.preventDefault();
  e.stopPropagation();
  const filePath = target.getAttribute('data-path');
  if (filePath && typeof openFileInWorkspace === 'function') {
    openFileInWorkspace(filePath);
  }
});

// ── Inline Diff Viewer (Side-by-Side) ─────────────────────────────────────
// renderDiffViewer(diffText, options) — parses unified-diff format and returns
// a DOM element with side-by-side old/new columns.
// Options:
//   mode: 'split' (default) or 'unified'
//   maxHeight: max scroll height (default '480px')
//   showStats: show stats bar (default true)

function _diffEscHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function _parseUnifiedDiff(text) {
  // Returns array of file objects: { oldName, newName, hunks, added, removed }
  // Each hunk: { header, lines: [{prefix, content, oldLine, newLine}] }
  const files = [];
  const lines = text.split('\n');
  let currentFile = null;
  let currentHunk = null;
  let oldLineNum = 0, newLineNum = 0;

  function _flushHunk() {
    if (currentHunk && currentHunk.lines.length) {
      currentFile.hunks.push(currentHunk);
    }
    currentHunk = null;
  }

  function _flushFile() {
    _flushHunk();
    if (currentFile) {
      // Count stats
      let added = 0, removed = 0;
      currentFile.hunks.forEach(h => h.lines.forEach(l => {
        if (l.prefix === '+') added++;
        if (l.prefix === '-') removed++;
      }));
      currentFile.added = added;
      currentFile.removed = removed;
      files.push(currentFile);
    }
    currentFile = null;
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // diff --git a/file b/file
    if (line.startsWith('diff --git ')) {
      _flushFile();
      const parts = line.slice(11).split(' ');
      currentFile = {
        oldName: parts[0] || '',
        newName: parts[1] || '',
        hunks: [],
        added: 0,
        removed: 0,
      };
      continue;
    }

    if (!currentFile) continue;

    // --- a/file
    if (line.startsWith('--- ')) {
      currentFile.oldName = line.slice(4).trim();
      continue;
    }
    // +++ b/file
    if (line.startsWith('+++ ')) {
      currentFile.newName = line.slice(4).trim();
      continue;
    }

    // @@ -a,b +c,d @@ [section]
    const hunkMatch = line.match(/^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@(.*)$/);
    if (hunkMatch) {
      _flushHunk();
      oldLineNum = parseInt(hunkMatch[1], 10);
      newLineNum = parseInt(hunkMatch[2], 10);
      currentHunk = {
        header: line,
        lines: [],
      };
      continue;
    }

    if (!currentHunk) continue;

    // Regular diff line
    const prefix = line.charAt(0);
    if (prefix === ' ' || prefix === '+' || prefix === '-') {
      currentHunk.lines.push({
        prefix: prefix,
        content: line.slice(1),
        oldLine: prefix !== '+' ? oldLineNum++ : null,
        newLine: prefix !== '-' ? newLineNum++ : null,
      });
    }
    // Skip lines that don't start with space/+/- (index lines, new file mode, etc.)
  }

  _flushFile();
  return files;
}

function renderDiffViewer(diffText, options) {
  options = options || {};
  const mode = options.mode || 'split'; // 'split' or 'unified'
  const maxHeight = options.maxHeight || '480px';
  const showStats = options.showStats !== false;

  if (!diffText || !diffText.trim()) {
    const empty = document.createElement('div');
    empty.className = 'diff-viewer';
    empty.style.padding = '12px';
    empty.style.textAlign = 'center';
    empty.style.color = 'var(--muted, #5C5344)';
    empty.style.fontFamily = 'var(--font-ui, sans-serif)';
    empty.style.fontSize = '12px';
    empty.textContent = '(empty diff)';
    return empty;
  }

  const files = _parseUnifiedDiff(diffText);

  // Guard: if no files detected, wrap entire text as a fallback block
  if (!files.length) {
    // Try treating the whole text as a single unnamed file
    // with one hunk containing all lines
    const fallbackViewer = document.createElement('div');
    fallbackViewer.className = 'diff-viewer';

    const statsBar = document.createElement('div');
    statsBar.className = 'diff-stats-bar';
    let _a = 0, _r = 0;
    diffText.split('\n').forEach(l => {
      if (l.startsWith('+') && !l.startsWith('+++')) _a++;
      if (l.startsWith('-') && !l.startsWith('---')) _r++;
    });
    const _statsFileSpan = document.createElement('span');
    _statsFileSpan.className = 'diff-stats-files';
    _statsFileSpan.textContent = 'Diff';
    const _statsAddSpan = document.createElement('span');
    _statsAddSpan.className = 'diff-stats-add';
    _statsAddSpan.textContent = '+' + _a;
    const _statsDelSpan = document.createElement('span');
    _statsDelSpan.className = 'diff-stats-del';
    _statsDelSpan.textContent = '-' + _r;
    statsBar.appendChild(_statsFileSpan);
    statsBar.appendChild(_statsAddSpan);
    statsBar.appendChild(_statsDelSpan);
    if (showStats) fallbackViewer.appendChild(statsBar);

    const body = document.createElement('div');
    body.className = 'diff-body';
    body.style.maxHeight = maxHeight;

    // Parse lines without file/hunk structure
    const _lines = diffText.split('\n');
    let _oldLn = 1, _newLn = 1;
    _lines.forEach(l => {
      const pf = l.charAt(0);
      const isAdd = pf === '+' && !l.startsWith('+++');
      const isDel = pf === '-' && !l.startsWith('---');
      const isHunk = l.startsWith('@@');

      if (isHunk) {
        // Try to extract line numbers from hunk header
        const hm = l.match(/^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@/);
        if (hm) { _oldLn = parseInt(hm[1], 10); _newLn = parseInt(hm[2], 10); }
        const hdr = document.createElement('div');
        hdr.className = 'diff-hunk-header';
        hdr.textContent = l;
        body.appendChild(hdr);
        return;
      }

      if (pf !== ' ' && !isAdd && !isDel) return; // skip index/mode lines

      const row = document.createElement('div');
      row.className = 'diff-row';

      // Old side
      const og = document.createElement('div');
      og.className = 'diff-old-gutter' + (isDel ? ' diff-del-gutter' : '');
      og.textContent = isDel || pf === ' ' ? String(_oldLn) : '';
      const oc = document.createElement('div');
      oc.className = 'diff-old-line';
      if (isDel) oc.classList.add('diff-del');
      oc.textContent = isDel ? l : '';

      // New side
      const ng = document.createElement('div');
      ng.className = 'diff-new-gutter' + (isAdd ? ' diff-add-gutter' : '');
      ng.textContent = isAdd || pf === ' ' ? String(_newLn) : '';
      const nc = document.createElement('div');
      nc.className = 'diff-new-line';
      if (isAdd) nc.classList.add('diff-add');
      nc.textContent = isAdd ? l : '';

      row.appendChild(og);
      row.appendChild(oc);
      row.appendChild(ng);
      row.appendChild(nc);

      if (isDel) row.classList.add('diff-del');
      if (isAdd) row.classList.add('diff-add');

      body.appendChild(row);

      if (pf === ' ' || isDel) _oldLn++;
      if (pf === ' ' || isAdd) _newLn++;
    });

    fallbackViewer.appendChild(body);
    return fallbackViewer;
  }

  // ── Normal path: render parsed files ────────────────────────────────
  const container = document.createElement('div');
  container.className = 'diff-viewer';

  // Overall stats bar
  if (showStats) {
    const totalAdded = files.reduce((s, f) => s + f.added, 0);
    const totalRemoved = files.reduce((s, f) => s + f.removed, 0);
    const statsBar = document.createElement('div');
    statsBar.className = 'diff-stats-bar';

    const fileSpan = document.createElement('span');
    fileSpan.className = 'diff-stats-files';
    fileSpan.textContent = files.length + ' file' + (files.length !== 1 ? 's' : '');
    const addSpan = document.createElement('span');
    addSpan.className = 'diff-stats-add';
    addSpan.textContent = '+' + totalAdded;
    const delSpan = document.createElement('span');
    delSpan.className = 'diff-stats-del';
    delSpan.textContent = '-' + totalRemoved;

    statsBar.appendChild(fileSpan);
    statsBar.appendChild(addSpan);
    statsBar.appendChild(delSpan);
    container.appendChild(statsBar);
  }

  // Render each file
  files.forEach(file => {
    const fileEl = document.createElement('div');
    fileEl.className = 'diff-file';

    // Filename header
    const displayName = file.newName
      ? file.newName.replace(/^b\//, '').replace(/^["']|["']$/g, '')
      : (file.oldName ? file.oldName.replace(/^a\//, '') : 'file');
    const fnEl = document.createElement('div');
    fnEl.className = 'diff-filename';
    fnEl.title = file.oldName && file.oldName !== file.newName
      ? (file.oldName + ' → ' + file.newName)
      : file.newName;
    fnEl.textContent = displayName;

    // Toggle collapse on click
    let _collapsed = false;
    const _bodyEl = document.createElement('div');
    _bodyEl.className = 'diff-body';
    _bodyEl.style.maxHeight = maxHeight;
    fnEl.addEventListener('click', () => {
      _collapsed = !_collapsed;
      _bodyEl.style.display = _collapsed ? 'none' : '';
      fnEl.style.opacity = _collapsed ? '0.5' : '1';
    });

    fileEl.appendChild(fnEl);

    // Render hunks
    file.hunks.forEach((hunk, hi) => {
      // Hunk header line
      const hdrEl = document.createElement('div');
      hdrEl.className = 'diff-hunk-header';
      hdrEl.textContent = hunk.header;
      _bodyEl.appendChild(hdrEl);

      // Group lines into aligned rows for side-by-side
      // For each line in the hunk, build a row with old and new columns
      hunk.lines.forEach(line => {
        const row = document.createElement('div');
        row.className = 'diff-row';

        const isDel = line.prefix === '-';
        const isAdd = line.prefix === '+';
        const isCtx = line.prefix === ' ';

        // Old gutter + content
        const og = document.createElement('div');
        og.className = 'diff-old-gutter' + (isDel ? ' diff-del-gutter' : '');
        og.textContent = line.oldLine !== null ? String(line.oldLine) : '';

        const oc = document.createElement('div');
        oc.className = 'diff-old-line';
        if (isDel) oc.classList.add('diff-del');
        oc.textContent = isDel ? ('-' + line.content) : (isCtx ? line.content : '');

        // New gutter + content
        const ng = document.createElement('div');
        ng.className = 'diff-new-gutter' + (isAdd ? ' diff-add-gutter' : '');
        ng.textContent = line.newLine !== null ? String(line.newLine) : '';

        const nc = document.createElement('div');
        nc.className = 'diff-new-line';
        if (isAdd) nc.classList.add('diff-add');
        nc.textContent = isAdd ? ('+' + line.content) : (isCtx ? line.content : '');

        // Mark row background
        if (isDel) row.classList.add('diff-del');
        if (isAdd) row.classList.add('diff-add');

        row.appendChild(og);
        row.appendChild(oc);
        row.appendChild(ng);
        row.appendChild(nc);
        _bodyEl.appendChild(row);
      });
    });

    fileEl.appendChild(_bodyEl);
    container.appendChild(fileEl);
  });

  return container;
}

// ── Message Result Toggle (compact/expanded per-message) ────────────────
// Store expanded state on S.messages[i]._resultExpanded (bool).
// Default: auto-expand when content <= 5 lines.
function _toggleMessageResult(rawIdx) {
  const m = S.messages[rawIdx];
  if (!m) return;
  m._resultExpanded = !m._resultExpanded;
  renderMessages({ preserveScroll: true });
}

function _getResultLineCount(content) {
  const t = String(content || '');
  if (!t.trim()) return 0;
  return t.split('\n').length;
}

function _getResultLastLine(content) {
  const t = String(content || '');
  const lines = t.split('\n');
  return lines.length > 1 ? lines[lines.length - 1] : lines[0] || '';
}

// ── Open Files Bar (VS Code-style tabs over chat) ────────────────────────
// Tracks file paths referenced in agent messages and shows them as clickable
// tabs. Clicking a tab opens the file in the workspace panel.
// Max 10 tabs shown; overflow scrolls horizontally.

let _openFilesMap = new Map(); // path → {path, filename}

// Known editable file extensions — only these trigger tab creation.
const _OFB_EXTS = new Set([
  'js','ts','jsx','tsx','mjs','cjs','mts','cts',
  'py','pyw','rb','go','rs','java','kt','swift','c','cpp','h','hpp','cs','php','pl','pm',
  'css','scss','sass','less','styl','html','htm','xhtml','vue','svelte','astro',
  'json','yaml','yml','toml','xml','svg',
  'md','mdx','txt','log','env','ini','cfg','conf','sh','bash','zsh','ps1','bat',
  'sql','graphql','gql',
  'tf','tfvars','hcl',
  'editorconfig','gitignore','npmrc','prettierrc','eslintrc',
]);

function _parseFilePathsFromText(text) {
  if (!text || typeof text !== 'string') return [];
  const paths = new Set();
  // Match backtick-quoted file paths with known extensions
  // e.g. `static/messages.js`, `src/components/App.tsx`, `C:\Users\file.py`
  const re = /`((?:[a-zA-Z]:)?[\\/]?(?:[^\s`]+[\\/])*[^\s`]+\.([a-zA-Z0-9]{1,6}))`/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const raw = m[1].trim();
    const ext = m[2].toLowerCase();
    // Skip URLs, pure numbers, and extensions that look like version dots
    if (/^https?:\/\//i.test(raw)) continue;
    if (/^\d+$/.test(ext)) continue;
    if (ext.length > 6) continue;
    if (!_OFB_EXTS.has(ext)) continue;
    // Normalise backslashes to forward slashes
    const norm = raw.replace(/\\/g, '/');
    paths.add(norm);
  }
  return [...paths];
}

// Scan all assistant messages for file path references and update the bar.
// Clears on session switch (filtered by S.messages content).
function _scanMessagesForFiles() {
  if (!S.messages || !S.messages.length) {
    _openFilesMap.clear();
    _renderOpenFilesBar();
    if(typeof setCurrentActiveFile==='function') setCurrentActiveFile('');
    return;
  }

  const found = new Set();
  let lastReferencedFile = '';
  for (const m of S.messages) {
    if (!m || m.role === 'tool') continue;
    let text = m.content || '';
    if (Array.isArray(text)) {
      text = text.filter(p => p && p.type === 'text').map(p => p.text || p.content || '').join('\n');
    }
    const parsed = _parseFilePathsFromText(String(text));
    if (parsed.length > 0) {
      // Track the last file referenced in the most recent message
      lastReferencedFile = parsed[parsed.length - 1];
    }
    for (const p of parsed) {
      if (!_openFilesMap.has(p)) {
        _openFilesMap.set(p, { path: p, filename: p.split('/').pop() || p });
      }
      found.add(p);
    }
  }

  // Prune files no longer referenced in any message
  for (const [p] of _openFilesMap) {
    if (!found.has(p)) _openFilesMap.delete(p);
  }

  // Enforce cap at 10 tabs
  if (_openFilesMap.size > 10) {
    const entries = [..._openFilesMap.entries()];
    _openFilesMap = new Map(entries.slice(0, 10));
  }

  _renderOpenFilesBar();

  // Highlight the most recently referenced file in the file tree
  if(lastReferencedFile && typeof setCurrentActiveFile==='function'){
    setCurrentActiveFile(lastReferencedFile);
  }
}

function _renderOpenFilesBar() {
  const bar = document.getElementById('openFilesBar');
  if (!bar) return;

  if (!_openFilesMap.size) {
    bar.style.display = 'none';
    bar.innerHTML = '';
    return;
  }

  bar.style.display = 'flex';
  bar.innerHTML = '';

  for (const [path, info] of _openFilesMap) {
    const tab = document.createElement('div');
    tab.className = 'ofb-tab';
    tab.dataset.filePath = path;
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-label', info.filename);

    // File type icon
    const icon = document.createElement('span');
    icon.className = 'ofb-tab-icon';
    const ext = path.split('.').pop().toLowerCase();
    icon.innerHTML = _ofbSvgIcon(ext);
    tab.appendChild(icon);

    // Label
    const label = document.createElement('span');
    label.className = 'ofb-tab-label';
    label.title = path;
    label.textContent = info.filename;
    tab.appendChild(label);

    // Close button (×)
    const close = document.createElement('button');
    close.className = 'ofb-tab-close';
    close.type = 'button';
    close.innerHTML = '×';
    close.title = 'Close ' + info.filename;
    close.addEventListener('click', function (e) {
      e.stopPropagation();
      _openFilesMap.delete(path);
      _renderOpenFilesBar();
    });
    tab.appendChild(close);

    // Click → open file in workspace panel
    tab.addEventListener('click', function () {
      bar.querySelectorAll('.ofb-tab.active').forEach(function (el) { el.classList.remove('active'); });
      tab.classList.add('active');
      if (typeof setCurrentActiveFile === 'function') setCurrentActiveFile(path);
      if (typeof openFile === 'function' && S.session) {
        openFile(path);
      }
    });

    // Mark as active if this matches the currently active file
    if(typeof _currentActiveFilePath !== 'undefined' && path === _currentActiveFilePath){
      tab.classList.add('active');
    }

    bar.appendChild(tab);
  }
}

function _ofbSvgIcon(ext) {
  const icons = {
    js:      '<svg viewBox="0 0 24 24" fill="none" stroke="#F7DF1E" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><path d="M10 15a2 2 0 1 0 4 0v-5"/></svg>',
    ts:      '<svg viewBox="0 0 24 24" fill="none" stroke="#3178C6" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><path d="M9 14l2-1.5 2 1.5V12l-2-1.5L9 12v2z"/></svg>',
    jsx:     '<svg viewBox="0 0 24 24" fill="none" stroke="#61DAFB" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><circle cx="12" cy="12" r="2"/></svg>',
    tsx:     '<svg viewBox="0 0 24 24" fill="none" stroke="#61DAFB" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><path d="M9 14l2-1.5 2 1.5V12l-2-1.5L9 12v2z"/><circle cx="16" cy="9" r="1"/></svg>',
    py:      '<svg viewBox="0 0 24 24" fill="none" stroke="#3776AB" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><path d="M8 14v-3a2 2 0 0 1 2-2h4l2-2"/></svg>',
    css:     '<svg viewBox="0 0 24 24" fill="none" stroke="#1572B6" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><path d="M6 8l1 10 5 2 5-2 1-10H6z"/></svg>',
    html:    '<svg viewBox="0 0 24 24" fill="none" stroke="#E34F26" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><path d="M6 8l10 0-1 4H9l-1 4 10 0"/></svg>',
    json:    '<svg viewBox="0 0 24 24" fill="none" stroke="#5E5C5C" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><path d="M10 8h4v8h-4"/></svg>',
    md:      '<svg viewBox="0 0 24 24" fill="none" stroke="#083FA1" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><path d="M8 15v-6l3 3 3-3v6"/></svg>',
    yaml:    '<svg viewBox="0 0 24 24" fill="none" stroke="#6A5ACD" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><path d="M7 8l5 8 5-8"/></svg>',
    yml:     '<svg viewBox="0 0 24 24" fill="none" stroke="#6A5ACD" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><path d="M7 8l5 8 5-8"/></svg>',
    svg:     '<svg viewBox="0 0 24 24" fill="none" stroke="#FFB13B" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><circle cx="12" cy="12" r="6"/></svg>',
    sh:      '<svg viewBox="0 0 24 24" fill="none" stroke="#4EAA25" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><path d="M7 8h10M7 12h6M7 16h8"/></svg>',
    go:      '<svg viewBox="0 0 24 24" fill="none" stroke="#00ADD8" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><path d="M8 12h2v5M14 12h2v5"/></svg>',
    rs:      '<svg viewBox="0 0 24 24" fill="none" stroke="#DEA584" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><path d="M12 6v12M8 9l4-3 4 3"/></svg>',
    rb:      '<svg viewBox="0 0 24 24" fill="none" stroke="#CC342D" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><path d="M12 6c-3 0-4 2-4 4s2 4 4 4 4-2 4-4-1-4-4-4"/><path d="M9 18h6"/></svg>',
    vue:     '<svg viewBox="0 0 24 24" fill="none" stroke="#4FC08D" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><path d="M12 6l-4 7 4 5 4-5-4-7z"/></svg>',
    svelte:  '<svg viewBox="0 0 24 24" fill="none" stroke="#FF3E00" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><path d="M12 6v8a2 2 0 0 0 2 2h0a2 2 0 0 0 2-2v-2"/></svg>',
  };
  return icons[ext] || '<svg viewBox="0 0 24 24" fill="none" stroke="var(--muted)" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="3"/><path d="M14 2v6h6"/><path d="M6 8h12"/></svg>';
}

// ── Panel navigation (Chat / Tasks / Skills / Memory) ──

// ── Plan-Then-Code helpers ──
// Global plan state (also read by ui.js renderMessages)
window._activePlan = null;  // {id, text, sessionId, status, decisionMsg, msgIdx}

function _markCurrentAssistantAsPlan(planText){
  // Mark the last assistant message in S.messages as a plan
  if(!S || !Array.isArray(S.messages)) return;
  for(let i=S.messages.length-1; i>=0; i--){
    const m=S.messages[i];
    if(m && m.role==='assistant'){
      m._isPlan=true;
      m._planText=planText;
      m._planStatus='pending';
      m._planId=window._activePlan ? window._activePlan.id : 'plan_'+Date.now();
      return;
    }
  }
}

function _clearPlanState(){
  window._activePlan=null;
}
