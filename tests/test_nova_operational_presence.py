from pathlib import Path


def test_presence_operational_projection_keeps_space_yolo_boundary_and_paused_chain_actionable():
    from web.api.nova_presence import _operational_projection

    payload = _operational_projection(
        managed_spaces=[{"space": "aquarium-zentrum"}],
        blockers=[
            {"space": "aquarium-zentrum", "code": "model_chain_exhausted"},
            {"space": "finanzjunkie", "code": "model_chain_exhausted"},
        ],
        supervision={"running": False},
    )
    assert payload == {
        "management_mode": "space_yolo_only",
        "enrollment_required": True,
        "legacy_global_yolo": "source_mode_only",
        "managed_space_count": 1,
        "ticker": "inactive",
        "runtime_status": "offline",
        "lease_state": "inactive",
        "lease_liveness": "not_observed",
        "availability": "offline",
        "paused_model_chain_spaces": ["aquarium-zentrum", "finanzjunkie"],
        "model_provider": "ollama-cloud",
        "model_chain_state": "paused",
        "model_chains": {
            "scout": ["deepseek-v4-flash", "deepseek-v4-pro"],
            "planner": ["deepseek-v4-pro", "kimi-k2.6"],
            "builder": ["minimax-m3"],
            "critic": ["minimax-m3"],
            "coding": ["glm-5.2", "glm-5.1"],
            "review_a": ["glm-5.2"],
            "review_b": ["kimi-k2.7-code"],
            "integrator": ["nemotron-3-super"],
            "vision": ["qwen3.5", "gemma4:31b"],
        },
        "next_step_code": "refresh_ollama_catalog",
    }


def test_presence_card_renders_operational_policy_instead_of_implying_autonomy():
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")
    assert "novaPresencePolicy" in ui_js
    assert "Ticker inaktiv - keine autonome Arbeit" in ui_js
    assert "globale YOLO bleibt Quellmodus" in ui_js
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    assert 'id="novaPresencePolicy"' in index_html
from pathlib import Path
import json
import subprocess


def test_presence_card_renders_inactive_ticker_and_paused_model_chain():
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")
    start = ui_js.index("const _NOVA_CARD_SPACE_RE=")
    end = ui_js.index("\nfunction renderMessages(", start)
    code = ui_js[start:end]
    program = r'''
const vm=require('node:vm'); const elements={};
function el(){return {children:[],hidden:false,textContent:'',dataset:{},style:{},replaceChildren(){this.children=[]},appendChild(c){this.children.push(c);return c}};}
for(const id of ['novaPresenceState','novaPresenceFocus','novaPresenceSupervision','novaPresencePolicy','novaPresencePending','novaPresenceUnread','novaPresenceOffline','novaPresenceSlot','novaPresenceReleaseSlot','novaManagedSpaces','novaAuditedResults','novaBlockers','novaActivity','novaTickerEvents','novaUnreadEvents']) elements[id]=el();
const context={window:{_activeSpace:'nova'},document:{createElement:el},$:id=>elements[id]||null,Promise,Set,String,Object,Array,Error,Math};
vm.runInNewContext(__CODE__,context,{filename:'ui.js'});
context._renderNovaPresenceCard({state:'available',managed_spaces:[{space:'aquarium-zentrum',state:'paused'}],operational:{management_mode:'space_yolo_only',managed_space_count:1,ticker:'inactive',paused_model_chain_spaces:['aquarium-zentrum']},supervision:{running:false},blockers:[{space:'aquarium-zentrum',code:'model_chain_exhausted'}]});
const text=elements.novaPresencePolicy.textContent;
if(!text.includes('keine autonome Arbeit l?uft')) throw new Error(text);
if(!text.includes('Aquarium Zentrum')) throw new Error(text);
if(!text.includes('globale YOLO bleibt Quellmodus')) throw new Error(text);
'''.replace('__CODE__',json.dumps(code))
    result=subprocess.run(['node','-e',program],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15)
    assert result.returncode==0, result.stderr or result.stdout



def test_presence_focus_surfaces_queued_space_before_admission():
    from web.api.nova_presence import _focus_for

    assert _focus_for(
        [], [{"space": "aquarium-zentrum", "state": "idle", "pending_actions": 1}], "available"
    ) == {"kind": "pending", "space": "aquarium-zentrum", "state": "idle"}

def test_presence_card_renders_queued_focus_as_entity_work():
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")
    start = ui_js.index("const _NOVA_CARD_SPACE_RE=")
    end = ui_js.index("\nfunction renderMessages(", start)
    code = ui_js[start:end]
    program = r'''
const vm=require('node:vm'); const elements={};
function el(){return {children:[],hidden:false,textContent:'',dataset:{},style:{},replaceChildren(){this.children=[]},appendChild(c){this.children.push(c);return c}};}
for(const id of ['novaPresenceState','novaPresenceFocus','novaPresenceSupervision','novaPresencePolicy','novaPresencePending','novaPresenceUnread','novaPresenceOffline','novaPresenceSlot','novaPresenceReleaseSlot','novaManagedSpaces','novaAuditedResults','novaBlockers','novaActivity','novaTickerEvents','novaUnreadEvents']) elements[id]=el();
const context={window:{_activeSpace:'nova'},document:{createElement:el},$:id=>elements[id]||null,Promise,Set,String,Object,Array,Error,Math};
vm.runInNewContext(__CODE__,context,{filename:'ui.js'});
context._renderNovaPresenceCard({state:'available',managed_spaces:[{space:'aquarium-zentrum',state:'idle',pending_actions:1,pending_signals:2}],focus:{kind:'pending',space:'aquarium-zentrum',state:'idle'}});
if(!elements.novaPresenceFocus.textContent.includes('bereite Aquarium Zentrum vor')) throw new Error(elements.novaPresenceFocus.textContent);
'''.replace('__CODE__',json.dumps(code))
    result=subprocess.run(['node','-e',program],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15)
    assert result.returncode==0, result.stderr or result.stdout



def test_presence_operational_projection_marks_unverified_lease_degraded():
    from web.api.nova_presence import _operational_projection

    payload = _operational_projection(
        managed_spaces=[{"space": "aquarium-zentrum"}],
        blockers=[{"space": "aquarium-zentrum", "code": "model_chain_exhausted"}],
        supervision={
            "running": True,
            "lease": {"state": "active", "liveness": "lease_unverified"},
        },
    )
    assert payload["runtime_status"] == "degraded"
    assert payload["availability"] == "degraded"
    assert payload["lease_liveness"] == "lease_unverified"


def test_presence_card_warns_for_legacy_unverified_lease_liveness():
    """The UI must fail closed for both lease liveness spellings."""
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")
    start = ui_js.index("const _NOVA_CARD_SPACE_RE=")
    end = ui_js.index("\nfunction renderMessages(", start)
    code = ui_js[start:end]
    program = r"""
const vm=require('node:vm'); const elements={};
function el(){return {children:[],hidden:false,textContent:'',dataset:{},style:{},replaceChildren(){this.children=[]},appendChild(c){this.children.push(c);return c}};}
for(const id of ['novaPresenceState','novaPresenceFocus','novaPresenceSupervision','novaPresencePolicy','novaPresencePending','novaPresenceUnread','novaPresenceOffline','novaPresenceSlot','novaPresenceReleaseSlot','novaManagedSpaces','novaAuditedResults','novaBlockers','novaActivity','novaTickerEvents','novaUnreadEvents']) elements[id]=el();
const context={window:{_activeSpace:'nova'},document:{createElement:el},$:id=>elements[id]||null,Promise,Set,String,Object,Array,Error,Math};
vm.runInNewContext(__CODE__,context,{filename:'ui.js'});
context._renderNovaPresenceCard({state:'available',managed_spaces:[{space:'aquarium-zentrum',state:'active'}],supervision:{running:true,lease:{state:'active',liveness:'unverified'}},operational:{management_mode:'space_yolo_only',managed_space_count:1,ticker:'active',runtime_status:'degraded',lease_liveness:'unverified'}});
if(!elements.novaPresenceSupervision.textContent.includes('Lease aktiv, Hostprozess nicht verifiziert')) throw new Error(elements.novaPresenceSupervision.textContent);
if(!elements.novaPresencePolicy.textContent.includes('Lease aktiv, Hostprozess nicht verifiziert')) throw new Error(elements.novaPresencePolicy.textContent);
""".replace('__CODE__',json.dumps(code))
    result=subprocess.run(['node','-e',program],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15)
    assert result.returncode==0, result.stderr or result.stdout


def test_presence_card_ui_does_not_call_an_unverified_lease_available():
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")
    assert "Lease aktiv, Hostprozess nicht verifiziert" in ui_js
    assert "kein verl?ssliches Available-Signal" in ui_js


def test_presence_card_warns_when_only_compact_operational_lease_is_unverified():
    """A compact read-only payload must not hide an unverified active lease."""
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")
    start = ui_js.index("const _NOVA_CARD_SPACE_RE=")
    end = ui_js.index("\nfunction renderMessages(", start)
    code = ui_js[start:end]
    program = r"""
const vm=require('node:vm'); const elements={};
function el(){return {children:[],hidden:false,textContent:'',dataset:{},style:{},replaceChildren(){this.children=[]},appendChild(c){this.children.push(c);return c}};}
for(const id of ['novaPresenceState','novaPresenceFocus','novaPresenceSupervision','novaPresencePolicy','novaPresencePending','novaPresenceUnread','novaPresenceOffline','novaPresenceSlot','novaPresenceReleaseSlot','novaManagedSpaces','novaAuditedResults','novaBlockers','novaActivity','novaTickerEvents','novaUnreadEvents']) elements[id]=el();
const context={window:{_activeSpace:'nova'},document:{createElement:el},$:id=>elements[id]||null,Promise,Set,String,Object,Array,Error,Math};
vm.runInNewContext(__CODE__,context,{filename:'ui.js'});
context._renderNovaPresenceCard({state:'available',managed_spaces:[{space:'aquarium-zentrum',state:'active'}],operational:{management_mode:'space_yolo_only',managed_space_count:1,ticker:'active',runtime_status:'degraded',lease_state:'active',lease_liveness:'unverified'}});
if(!elements.novaPresencePolicy.textContent.includes('Lease aktiv, Hostprozess nicht verifiziert')) throw new Error(elements.novaPresencePolicy.textContent);
""".replace('__CODE__',json.dumps(code))
    result=subprocess.run(['node','-e',program],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15)
    assert result.returncode==0, result.stderr or result.stdout

def test_presence_model_chain_hint_is_ollama_cloud_only_and_read_only():
    from web.api.nova_presence import _operational_projection

    payload = _operational_projection(
        managed_spaces=[{"space": "aquarium-zentrum"}],
        blockers=[{"space": "aquarium-zentrum", "code": "model_chain_exhausted"}],
        supervision={"running": True},
    )
    assert payload["model_provider"] == "ollama-cloud"
    assert payload["model_chain_state"] == "paused"
    assert payload["next_step_code"] == "refresh_ollama_catalog"
    rendered = str(payload).lower()
    assert "gpt-oss" not in rendered
    assert "openrouter" not in rendered
    assert "localhost" not in rendered
    assert payload["model_chains"]["scout"] == ["deepseek-v4-flash", "deepseek-v4-pro"]

def test_presence_model_chain_hint_never_claims_catalog_availability_without_blocker():
    from web.api.nova_presence import _operational_projection

    payload = _operational_projection(
        managed_spaces=[{"space": "aquarium-zentrum"}],
        blockers=[],
        supervision={"running": True},
    )
    assert payload["model_chain_state"] == "not_checked"
    assert payload["model_provider"] == "ollama-cloud"

def test_presence_card_reads_redacted_space_inventory_without_mutating_controls():
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    assert "/api/nova/space-inventory" in ui_js
    assert "novaSpaceInventory" in index_html
    assert "_NOVA_INVENTORY_TARGETS" in ui_js
    assert "YOLO und Betreuung werden hier nicht aktiviert." in ui_js
    assert "project_dir" not in ui_js[ui_js.index("function _renderNovaSpaceInventory"):ui_js.index("function _renderNovaSpaceInventory")+5000]
    assert "method:'POST'" not in ui_js[ui_js.index("function _renderNovaSpaceInventory"):ui_js.index("function _renderNovaSpaceInventory")+5000]

def test_presence_focus_explicitly_distinguishes_entity_autonomy_state():
    """The entity voice must not imply autonomous work when the ticker is off."""
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")
    start = ui_js.index("const _NOVA_CARD_SPACE_RE=")
    end = ui_js.index("\nfunction renderMessages(", start)
    code = ui_js[start:end]
    program = r"""
const vm=require('node:vm'); const elements={};
function el(){return {children:[],hidden:false,textContent:'',dataset:{},style:{},replaceChildren(){this.children=[]},appendChild(c){this.children.push(c);return c}};}
for(const id of ['novaPresenceState','novaPresenceFocus','novaPresenceSupervision','novaPresencePolicy','novaPresencePending','novaPresenceUnread','novaPresenceOffline','novaPresenceSlot','novaPresenceReleaseSlot','novaManagedSpaces','novaAuditedResults','novaBlockers','novaActivity','novaTickerEvents','novaUnreadEvents']) elements[id]=el();
const context={window:{_activeSpace:'nova'},document:{createElement:el},$:id=>elements[id]||null,Promise,Set,String,Object,Array,Error,Math};
vm.runInNewContext(__CODE__,context,{filename:'ui.js'});
context._renderNovaPresenceCard({state:'available',managed_spaces:[{space:'aquarium-zentrum',state:'paused'}],focus:{kind:'supervision',space:'aquarium-zentrum',state:'paused'},operational:{management_mode:'space_yolo_only',managed_space_count:1,ticker:'inactive',runtime_status:'offline'},supervision:{running:false}});
const text=elements.novaPresenceFocus.textContent;
if(!text.includes('keine autonome Arbeit l?uft')) throw new Error(text);
if(text.includes('als Nova-Entität im Blick')) throw new Error(text);
""".replace('__CODE__',json.dumps(code))
    result=subprocess.run(['node','-e',program],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15)
    assert result.returncode==0, result.stderr or result.stdout
