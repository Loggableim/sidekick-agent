# Nova Scheduler Marker Design

**Date:** 2026-07-29
**Status:** approved for planning; implementation and activation remain separately gated
**Goal:** Let Nova show, for each enrolled YOLO-Space, whether the trusted
scheduler observed a new signal or deliberately found the same scheduler
reference and did not spend a model call.

## Decision

Use a scheduler-owned marker in the existing Nova supervision ledger. It is
bound to the current `space_id`, canonical-root fingerprint, and governance
revision. It holds only opaque digests and fixed status codes; neither the
presence API nor the WebUI receives an event ID, source payload, repository
path, raw hash, model text, or audit detail.

This is not a project-content fingerprint. It only compares the code-owned
Git, Kanban, and CI references already admitted by the scheduler. A missing,
legacy, malformed, WAL-backed, or mismatched reference is **unknown**, never
"unchanged".

## Alternatives considered

1. **Scheduler marker in the durable state row (chosen).** It proves a
   scheduler comparison, retains exact binding, and can skip an unnecessary
   periodic model admission.
2. **UI-only timestamp.** It is cheap but cannot prove that no new signal
   exists, so it must not say "Nix Neues".
3. **Full working-tree fingerprint.** It would need a new trusted host
   detector, filesystem policy, cost/latency boundaries, and production host
   wiring. It is explicitly out of scope.

## State and data flow

`NovaSpaceSupervisionRuntime.ingest_signal()` remains the sole writer for a
new scheduler reference. `current_reference_digest` is exactly the canonical
coalesced `pending_digest` snapshot already computed by the runtime: it hashes
the target, source, event ID, and fixed reason code, then includes each merged
signal in arrival order. An exact duplicate is already rejected and changes no
reference. A different source or reason is a distinct, bounded scheduler
identity under the existing exactly-once rule. Ingest clears any older
`unchanged` code. The durable
`nova_supervision_space_state` row gains:

- `current_reference_digest` — most recent opaque scheduler reference;
- `last_evaluated_reference_digest` — reference admitted for the latest
  started run;
- `last_checked_at` — timestamp of a scheduler-only equality check; and
- `last_check_code` — fixed value `unchanged` or empty.

All columns are private. Schema migration must add safe defaults; a row
without valid 64-hex current and evaluated references is unknown and cannot
start a periodic model check merely to repair itself. Rebinding a row to a
new Space ID, root fingerprint, or governance revision resets both references,
the check timestamp, and check code atomically before it accepts the fresh
signal under that new authority.

On a pending signal, `pulse()` keeps the existing admission, exactly-once,
one-active-run, and governance revalidation path. A successful start advances
the evaluated reference only through a compare-and-swap that includes the
target, Space ID, root fingerprint, governance revision, pending digest, and
current reference digest. A racing new signal makes that update affect zero
rows; the old run can finish under its already granted capability, while the
new reference remains pending and wins.

After the 15-minute floor measured exactly as
`now - max(last_started_at, last_checked_at) >= 900`, a non-pending row with
equal non-empty references performs a scheduler-only equality check. Its
write is a compare-and-swap containing the same authority binding,
`pending_digest = ''`, and both reference digests. It writes
`last_checked_at` and the fixed `unchanged` code, returns a bounded
`unchanged` outcome, and does not call admission, dispatch, or a model. The
timestamp rate-limits repeated quiet checks. A failed compare-and-swap emits
no quiet outcome; the next pulse reads the newer state. If equality cannot be
proven, it skips automatic work and exposes no optimistic status.

## Public projection and WebUI

`GET /api/nova/presence-card` stays a pure, read-only projection. It adds a
bounded `change_markers` list with at most one valid marker per managed Space:

```json
{
  "space": "example",
  "state_code": "change_detected | reference_unchanged",
  "checked_at": "2026-07-29T20:30:00+00:00 | null"
}
```

The API emits a marker only after validating the exact Space binding. When
the immutable read cannot safely observe the ledger, it omits the marker. The
timestamp is present only for `reference_unchanged`; an active changed marker
uses `null` rather than disclosing an event timestamp or source. The
projection gives a pending or unequal valid reference precedence and emits
only `change_detected`; it emits `reference_unchanged` only for equal valid
references with a valid `unchanged` code and timestamp. Any other condition
omits the marker. The reader checks the expected marker columns and validates
digest, code, and timestamp shapes without initializing or migrating schema;
a partial or malformed marker row omits only that marker, not the rest of the
card. The
UI maps only the fixed codes to text such as `Änderung erkannt.` and
`Stand geprüft. Nix Neues.` It renders the marker as metadata in the existing
managed YOLO-Space list, not as arbitrary feed text.

## Security and failure behavior

- A presence GET never creates schema, writes a timestamp, probes a model, or
  changes scheduler state.
- Revocation, root change, Space recreation, or governance revision change
  invalidates old markers. A fresh code-owned signal is required afterward.
- Duplicate signals retain exactly-once behavior; no deduplication tombstone
  is evicted to make an old event new again.
- Concurrent signal arrival wins over an equality write. A stale pulse cannot
  overwrite a newer reference or claim "unchanged".
- The marker adds no authority: it cannot bypass trusted-workspace checks,
  supervisor admission, action gates, worktree isolation, or hard denies.
- The deliberately inert runtime remains unconnected to the production host.
  This slice neither enables live project supervision nor changes the separate
  live Nova deployment.

## Verification

Focused tests must prove:

1. a signal creates a changed reference and still follows normal admission;
2. equal references after the 15-minute floor produce `unchanged` with no
   admission, dispatcher call, or model call;
3. a fresh signal after an equality check is pending and dispatchable;
4. duplicate, merged, and concurrent signals preserve the latest reference
   and exactly-once semantics;
5. malformed/legacy references, WAL snapshots, binding mismatches,
   revocation, root changes, and recreated Spaces do not publish
   `reference_unchanged` or dispatch periodic work;
6. the presence endpoint remains read-only/redacted and never returns a raw
   digest; and
7. the dashboard contract renders fixed marker copy and rejects arbitrary
   status strings.

## Non-goals and rollout

This design does not fingerprint files, poll repositories, call providers,
start the inert runtime, restart Sidekick or Nova, change credentials, or
publish/deploy anything. Implementation will first run fake-dispatch and
browser-contract tests in this worktree. Any later production host wiring,
canary, or live Nova rollout requires a separate explicit design and approval.
