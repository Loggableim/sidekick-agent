# GPU card rotation design

## Goal

The Hub's top GPU summary card alternates between GPU temperature and GPU utilization every six seconds. It must never render utilization as a permanently stacked secondary value below temperature.

## UI behavior

- The card contains one prominent metric value.
- The card label identifies the active metric as `GPU TEMP` or `GPU LOAD`.
- The initial state is temperature; after six seconds it changes to utilization and continues alternating every six seconds.
- Missing temperature data is represented as `--°`; missing utilization data is represented as `--%`.
- The separate temperature and utilization cards in the machine detail scene remain unchanged.

## Implementation

- Remove the secondary `sGpuL` value from `dashboard/index.html`.
- Give the top GPU label a stable element ID so its text can change with the active metric.
- In `dashboard/app.js`, retain the latest temperature and utilization readings in local state.
- Add one six-second interval that toggles the active GPU metric and renders the label/value pair.
- Continue updating the detailed `mTmp` and `mGpuL` fields directly from incoming metric data.

## Failure handling

The rotation continues when either counter is unavailable. The unavailable metric uses its existing placeholder, making missing telemetry explicit without changing the timing or layout.

## Verification

- A regression test checks that the old stacked `sGpuL` markup is absent and the six-second timer exists.
- Existing metric tests remain green.
- Browser QA verifies both label/value states across one rotation, confirms there is only one visible top-card value, and checks for relevant console errors.
