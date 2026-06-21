# GPU Card Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stacked GPU temperature/load summary with one prominent value that alternates between temperature and utilization every six seconds.

**Architecture:** The top GPU card owns one label and one value. `dashboard/app.js` retains the latest two readings, renders the selected reading, and changes selection on a six-second interval; the existing detailed machine metrics remain independent.

**Tech Stack:** Static HTML/CSS, browser JavaScript, pytest source-regression tests, in-app Browser QA.

---

### Task 1: Add a failing GPU summary rotation regression test

**Files:**
- Modify: `tests/test_nova_hub_metrics.py`
- Inspect: `C:\HermesPortable\home\cockpit\dashboard\index.html`
- Inspect: `C:\HermesPortable\home\cockpit\dashboard\app.js`

- [ ] **Step 1: Write the failing test**

Append this test to `tests/test_nova_hub_metrics.py`:

```python
DASHBOARD_DIR = COCKPIT_DIR / "dashboard"


def test_gpu_summary_card_uses_one_value_and_rotates_every_six_seconds():
    index_html = (DASHBOARD_DIR / "index.html").read_text(encoding="utf-8")
    app_js = (DASHBOARD_DIR / "app.js").read_text(encoding="utf-8")

    gpu_card = index_html[index_html.index('<div class="sc gpu">') : index_html.index('<div class="sc dsk">')]

    assert 'id="sGpuLabel"' in gpu_card
    assert 'id="sGpu"' in gpu_card
    assert 'id="sGpuL"' not in gpu_card
    assert "const GPU_SUMMARY_ROTATION_MS=6000;" in app_js
    assert "setInterval(toggleGpuSummary,GPU_SUMMARY_ROTATION_MS);" in app_js
    assert "function renderGpuSummary()" in app_js
    assert "function toggleGpuSummary()" in app_js
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_nova_hub_metrics.py::test_gpu_summary_card_uses_one_value_and_rotates_every_six_seconds -q
```

Expected: FAIL because the card still contains `sGpuL` and no six-second rotation functions exist.

### Task 2: Implement the single-value six-second rotation

**Files:**
- Modify: `C:\HermesPortable\home\cockpit\dashboard\index.html:197`
- Modify: `C:\HermesPortable\home\cockpit\dashboard\app.js:604-631`
- Test: `tests/test_nova_hub_metrics.py`

- [ ] **Step 1: Replace the stacked GPU card markup**

Replace the top GPU card in `index.html` with:

```html
<div class="sc gpu"><div class="sl" id="sGpuLabel">🎮 GPU TEMP</div><div class="sv" id="sGpu">--°</div></div>
```

- [ ] **Step 2: Add state and rendering functions before `rStats`**

Add to `app.js`:

```javascript
const GPU_SUMMARY_ROTATION_MS=6000;
let gpuSummaryMode='temp';
let latestGpuTemp=null;
let latestGpuLoad=null;

function renderGpuSummary(){
  const showingLoad=gpuSummaryMode==='load';
  st($('#sGpuLabel'),showingLoad?'🎮 GPU LOAD':'🎮 GPU TEMP');
  st($('#sGpu'),showingLoad
    ? (latestGpuLoad==null?'--%':`${Math.round(latestGpuLoad)}%`)
    : (latestGpuTemp==null?'--°':`${Math.round(latestGpuTemp)}°`));
}

function toggleGpuSummary(){
  gpuSummaryMode=gpuSummaryMode==='temp'?'load':'temp';
  renderGpuSummary();
}
setInterval(toggleGpuSummary,GPU_SUMMARY_ROTATION_MS);
```

- [ ] **Step 3: Feed the renderer from `rStats` without changing detailed metrics**

At the start of the GPU portion of `rStats`, assign both latest values:

```javascript
const gpu=d.gpu_temp_c;
latestGpuTemp=gpu;
```

Remove both direct writes to `sGpu`. Keep the existing `mTmp`, `mTmpF`, and `mTmpS` updates.

For utilization, use:

```javascript
const gpuL=d.gpu_util_pct;
latestGpuLoad=gpuL;
if(gpuL!=null){
  st($('#mGpuL'),`${Math.round(gpuL)}%`);
} else {
  st($('#mGpuL'),'--%');
}
renderGpuSummary();
```

This removes all writes to the deleted `sGpuL` element while preserving the detailed load card.

- [ ] **Step 4: Run the focused test and JavaScript syntax check**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_nova_hub_metrics.py -q
node --check C:\HermesPortable\home\cockpit\dashboard\app.js
```

Expected: all GPU metric tests PASS and `node --check` exits 0.

- [ ] **Step 5: Commit the repository-owned regression test only**

The active dashboard files are outside the Sidekick Git repository. Stage only the repository-owned test:

```powershell
git add tests/test_nova_hub_metrics.py
git commit -m "test: cover Hub GPU card rotation"
```

Expected: the commit contains only `tests/test_nova_hub_metrics.py`; existing unrelated working-tree changes remain unstaged.

### Task 3: Verify the live rotation and regression suite

**Files:**
- Verify: `C:\HermesPortable\home\cockpit\dashboard\index.html`
- Verify: `C:\HermesPortable\home\cockpit\dashboard\app.js`

- [ ] **Step 1: Run automated verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_nova_hub_metrics.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: all tests PASS.

- [ ] **Step 2: Reload and inspect the existing Hub tab**

Using the in-app Browser, reload `http://127.0.0.1:8765/`, record URL/title, take a fresh DOM snapshot, check error/warning console logs, and capture the first viewport.

Expected initial top-card state:

```text
🎮 GPU TEMP
--° or a numeric temperature
```

The top card must contain no permanently stacked utilization value.

- [ ] **Step 3: Verify one six-second transition**

Wait slightly longer than six seconds and take a targeted DOM read or fresh snapshot.

Expected state:

```text
🎮 GPU LOAD
<numeric utilization>% or --%
```

Wait slightly longer than six additional seconds and confirm the label returns to `GPU TEMP`.

- [ ] **Step 4: Verify detailed metrics remain unchanged**

Confirm the machine scene still exposes separate `GPU TEMP` and `GPU LOAD` cards with their existing secondary text and values.

- [ ] **Step 5: Review repository state**

Run:

```powershell
git status --short
git show --stat --oneline HEAD
```

Expected: only the intended test commit was created; pre-existing changes in `cli/models.py`, `web/api/config.py`, and other Hub work remain present and unmodified.
