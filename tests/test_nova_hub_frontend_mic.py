from pathlib import Path


APP_JS = Path(r"C:\HermesPortable\home\cockpit\dashboard\app.js")
APP_HTML = Path(r"C:\HermesPortable\home\cockpit\dashboard\index.html")


def test_frontend_persists_selected_microphone_device():
    js = APP_JS.read_text(encoding="utf-8")

    assert "NOVA_MIC_DEVICE_KEY" in js
    assert "localStorage.getItem(NOVA_MIC_DEVICE_KEY)" in js
    assert "localStorage.setItem(NOVA_MIC_DEVICE_KEY" in js
    assert "storedMicPref()" in js
    assert "storedMicId()" in js
    assert "saveMicSelection(id,label)" in js
    assert "deviceId:{exact:id}" in js


def test_frontend_clears_stale_stored_microphone_before_opening_stream():
    js = APP_JS.read_text(encoding="utf-8")

    assert "await loadMics(storedMicId())" in js
    assert "else if(keep&&storedMicId()===keep)" in js
    assert "saveMicSelection('','')" in js


def test_frontend_can_restore_microphone_by_label_when_device_id_changes():
    js = APP_JS.read_text(encoding="utf-8")

    assert "storedMicLabel()" in js
    assert "inputs.find(d=>keepLabel&&d.label===keepLabel)" in js
    assert "saveMicSelection(match.deviceId,match.label||'')" in js


def test_frontend_mic_listing_preserves_active_listening_indicator():
    js = APP_JS.read_text(encoding="utf-8")

    assert "micStatus(inputs.length?`${inputs.length} Mikrofone`:'kein Mikrofon',inputs.length?micOn:false)" in js


def test_frontend_mutes_stt_while_nova_tts_is_playing():
    js = APP_JS.read_text(encoding="utf-8")

    assert "let micStream=null" in js
    assert "micTtsMuted=false" in js
    assert "function sendMicPayload(payload)" in js
    assert "function setMicTtsMute(muted,seconds=45)" in js
    assert "setMicTtsMute(true,90)" in js
    assert "setMicTtsMute(false,2.2)" in js
    assert "muted?'tts_start':'tts_end'" in js
    assert "if(!micOn||!micWs" in js


def test_frontend_supports_tts_barge_in_without_streaming_nova_echo():
    js = APP_JS.read_text(encoding="utf-8")

    assert "function maybeBargeIn(input,rate)" in js
    assert "function sendMicInput(input)" in js
    assert "if(micTtsMuted){" in js
    assert "maybeBargeIn(input,micAudioCtx.sampleRate)" in js
    assert "voiceAudioEl.pause()" in js
    assert "setMicTtsMute(false,.2)" in js
    assert "activateMicFollowup()" in js
    assert "sendMicPayload(downsampleTo16k(input,micAudioCtx.sampleRate))" in js


def test_frontend_does_not_prefetch_live_tts_for_envelope_decode():
    js = APP_JS.read_text(encoding="utf-8")

    assert "if(url.includes('/tts-live/'))return;" in js


def test_frontend_retries_live_tts_when_audio_arrives_after_play_call():
    js = APP_JS.read_text(encoding="utf-8")

    assert "function retryLiveTTS(url,attempt=1)" in js
    assert "const isLive=opts.streaming||url.includes('/tts-live/')" in js
    assert "retryLiveTTS(url,1)" in js
    assert "playTTS(d.url,false,d)" in js


def test_frontend_voicebar_uses_kitt_sized_audio_equalizer():
    js = APP_JS.read_text(encoding="utf-8")
    html = APP_HTML.read_text(encoding="utf-8")

    assert "const VOICE_BAR_COUNT=33" in js
    assert "function ensureVoiceBars()" in js
    assert "function applyVoiceBarLevel(bar,level,i,total)" in js
    assert "bar.style.setProperty('--level'" in js
    assert "Math.abs(i-mid)" in js
    assert "voiceAnalyser.getByteFrequencyData(voiceData)" in js
    assert "core.style.setProperty('--scan-x'" in js
    assert ".tp.voice-active .voicebar-core{height:32px" in html
    assert ".voicebar-core::after" in html
    assert "repeating-linear-gradient(to top" in html


def test_frontend_uses_only_top_voice_equalizer_not_duplicate_comm_pulse():
    html = APP_HTML.read_text(encoding="utf-8")

    assert "commpulse" not in html
    assert "cpulse" not in html
    assert 'id="voicebar"' in html


def test_frontend_ignores_stale_stt_websocket_events():
    js = APP_JS.read_text(encoding="utf-8")

    assert "const ws=new WebSocket" in js
    assert "micWs=ws" in js
    assert "if(ws!==micWs||ws.readyState!==WebSocket.OPEN)throw new Error('STT offline')" in js
    assert "if(ws!==micWs)return" in js
    assert "if(!sendMicPayload(JSON.stringify({type:'config'" in js


def test_frontend_uses_audio_worklet_for_microphone_when_available():
    js = APP_JS.read_text(encoding="utf-8")

    assert "MIC_WORKLET_CODE" in js
    assert "micAudioCtx.audioWorklet" in js
    assert "await micAudioCtx.audioWorklet.addModule(url)" in js
    assert "new AudioWorkletNode(micAudioCtx,'nova-mic-processor'" in js
    assert "createScriptProcessor(4096,1,1)" in js
    assert "Fall through to ScriptProcessor" in js


def test_frontend_has_settings_panel_for_stt_and_microphone():
    js = APP_JS.read_text(encoding="utf-8")
    html = APP_HTML.read_text(encoding="utf-8")

    assert 'id="settingsBtn"' in html
    assert 'id="sttProviderSelect"' in html
    assert 'id="micSelect"' in html
    assert "NOVA SETTINGS" in html
    assert "loadSttSettings()" in js
    assert "saveSttProvider(provider)" in js
    assert "jp(`${B}/api/stt/settings`,{provider})" in js
    assert "$('#settingsBtn')?.addEventListener('click',toggleSettingsPanel)" in js


def test_frontend_shows_actionable_crypto_futures_error():
    js = APP_JS.read_text(encoding="utf-8")

    assert "ft?.message||ft?.error||'Futures nicht verbunden'" in js


def test_frontend_app_script_is_cache_busted():
    html = APP_HTML.read_text(encoding="utf-8")

    assert 'src="app.js?v=' in html
