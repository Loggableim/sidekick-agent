import json
import subprocess
from pathlib import Path

from tests.test_dashboard_health import _run_node_script


def test_nova_space_inventory_browser_smoke_filters_unknown_rows_and_projects_empty_offline_states():
    """The Nova entity inventory remains bounded, redacted and read-only."""
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")
    presence_start = ui_js.index("const _NOVA_CARD_SPACE_RE=")
    inventory_start = ui_js.index("const _NOVA_INVENTORY_TARGETS=")
    end = ui_js.index("\nfunction renderMessages(", presence_start)
    inventory_code = ui_js[presence_start:end] + ui_js[inventory_start:]
    node_program = r"""
const vm=require('node:vm'); const elements={};
function element(){return {children:[],hidden:false,style:{display:''},textContent:'',dataset:{},replaceChildren(){this.children=[]},appendChild(c){this.children.push(c);return c}};}
elements.novaSpaceInventory=element(); elements.novaSpaceInventoryNote=element();
const context={window:{},document:{createElement:()=>element()},$:id=>elements[id]||null,String,Object,Array,Set,Math};
vm.runInNewContext(__INVENTORY_CODE__,context,{filename:'ui.js'});
const readiness=(state)=>({state,ready:state==='ready',yolo:state==='ready',enrolled:state==='ready',space_id_persisted:state==='ready'});
context._renderNovaSpaceInventory({spaces:[
  {slug:'unrelated-one',enrollment_readiness:readiness('blocked')},
  {slug:'unrelated-two',enrollment_readiness:readiness('blocked')},
  {slug:'unrelated-three',enrollment_readiness:readiness('blocked')},
  {slug:'nova',enrollment_readiness:readiness('ready')},
  {slug:'finanz-junkie',enrollment_readiness:readiness('needs_review')},
  {slug:'aquarium-zentrum',enrollment_readiness:readiness('blocked')},
  {slug:'../../secret',enrollment_readiness:readiness('ready')},
  {slug:'nova',enrollment_readiness:readiness('ready')}
]});
const rows=elements.novaSpaceInventory.children.map(item=>item.children.map(child=>child.textContent).join(' | '));
if(rows.length!==3 || !rows.some(row=>row.includes('Nova')) || !rows.some(row=>row.includes('Finanz Junkie')) || !rows.some(row=>row.includes('Aquarium Zentrum'))) throw new Error('canonical inventory rows were hidden: '+rows.join(' || '));
if(rows.join(' ').includes('unrelated')||rows.join(' ').includes('secret')) throw new Error('untrusted inventory row leaked: '+rows.join(' || '));
if(!elements.novaSpaceInventoryNote.textContent.includes('redigierte')) throw new Error('read-only note missing');
context._renderNovaSpaceInventory({spaces:[]});
if(elements.novaSpaceInventory.children.length!==1 || !elements.novaSpaceInventory.children[0].textContent.includes('Keine')) throw new Error('empty inventory state missing');
context._renderNovaSpaceInventory({offline:true});
if(elements.novaSpaceInventory.children.length!==1 || !elements.novaSpaceInventory.children[0].textContent.includes('offline')) throw new Error('offline inventory state missing');
""".replace("__INVENTORY_CODE__", json.dumps(inventory_code))
    result = _run_node_script(node_program)
    assert result.returncode == 0, result.stderr or result.stdout