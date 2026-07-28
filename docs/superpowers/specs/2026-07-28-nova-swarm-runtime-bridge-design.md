# Nova-to-Swarm Runtime Bridge Design

**Date:** 2026-07-28
**Status:** approved for planning; implementation remains separately gated
**Goal:** Route Nova Mind intents through the project-local Swarm workflow and
execute only an exactly-once, policy-approved Nova action.

## Decisions

- Standard mode creates at most six Nova-initiated runs in a rolling 24-hour
  window, with one active Nova run at a time, at most 48 model calls per run,
  and `reviewed_execution` autonomy.
- YOLO mode is derived only from Nova's existing runtime policy state. It
  retains the one-active-run rule, removes the rolling daily limit, permits at
  most 128 model calls per run, and uses `autonomous` autonomy.
- YOLO never bypasses the trusted-workspace boundary, action allowlist,
  immutable proposal binding, provider safety rules, exact-once claim, or the
  human gate for external, irreversible, or otherwise sensitive actions.
- A Swarm run may still use the existing bounded intra-run parallel review
  calls. "One active Nova run" means one Nova-originated durable run, not one
  model request globally.
- The first automatic action registry is deliberately narrow:
  `mind_diary`, `agenda_update`, and `prioritize_thread`. Current Nova Mind
  proposals for `reflection`, `aces_cycle`, `moltbook_post`, and every unknown
  action are rejected before a Cloud model call. They receive an auditable
  blocked result and never fall back to the legacy direct action path.

## Chosen architecture

Use a host-side completion bridge. Nova Mind calls a new versioned
`nova.swarm_runtime_bridge.NovaSwarmRuntimeBridge` at its existing
`submit_intent_proposal()` boundary. The bridge uses the existing Sidekick
Cloud transport and provider slots directly; it does not add an HTTP
round-trip or a second daemon.

The alternative HTTP route would duplicate trusted-workspace, identity,
pause/resume, and idempotency handling. A separate daemon would add a second
lifecycle, lease recovery, and crash surface. Neither adds safety over a
typed, in-process host bridge.

## Components

### `NovaSwarmRuntimeBridge`

This versioned Sidekick module owns only Nova-to-Swarm translation and
orchestration. It accepts an already-created `EntityKernel`, the active Nova
space root, and injected host dependencies for tests.

Before creating a run it:

1. canonicalizes the Nova suggestion and calculates a stable intent digest;
2. assigns a proposal ID bound to the durable run and digest;
3. verifies that the Nova space is the exact Swarm project root;
4. rejects an unsupported action before contacting a model;
5. coalesces an identical active intent and rejects a second active Nova run;
6. applies the mode-specific rolling-24-hour and model-call limits; and
7. records a durable integration event without logging credentials or raw
   model responses.

It creates the Nova project's `.swarm` state only on this explicit write path,
never from status reads. A local bridge configuration is stored beneath the
Nova space's `.swarm/` directory, defaults to disabled, and contains no
credentials. It is an explicit deployment setting, not an environment hint
supplied by a model request.

### Nova intent verifier

`NovaIntentReadOnlyVerifier` is a new `ReadOnlyVerifier` implementation. It
only examines the canonical intent snapshot, the trusted project root, the
static action registry, expected local output scope, and sensitive markers.
It emits verifier evidence containing the exact intent digest. It never
executes an action, adopts builder or critic evidence, sends network requests,
or trusts model-provided capability labels.

The generic default verifier intentionally reports unavailable state, so it
cannot authorize this integration. The bridge explicitly supplies the Nova
verifier for its own runs.

### Pre-completion hook

`SwarmEngine.execute_run()` gains a typed, optional completion hook. The hook
runs only after the durable verifier and independent review checkpoints are
written and before the run is changed from `running` to `completed`.

This ordering is required because `PolicyGate.authorize_and_claim()` accepts
only a running run. The hook reads durable checkpoints rather than in-memory
model output and writes namespaced integration events. Existing CLI, API, and
WebUI runs retain their current behavior when no hook is supplied.

The Nova hook:

1. reconstructs the immutable Nova proposal from the stored digest;
2. accepts only positive verifier evidence for that exact digest;
3. calls `NovaSwarmAdapter.execute_suggestion()`;
4. lets the adapter perform Swarm policy, `EntityKernel.govern()`, and
   `EntityKernel.act()` in that order; and
5. records one terminal execution, block, or approval-needed event.

There is no direct `EntityKernel.act()` fallback anywhere in the bridge.

## Mode behavior

| Property | Standard | YOLO |
| --- | --- | --- |
| Autonomy | `reviewed_execution` | `autonomous` |
| New runs | 6 in trailing 24 hours | no daily cap |
| Active Nova runs | 1 | 1 |
| Calls per run | 48 | 128 |
| Intra-run model parallelism | existing Swarm limit (3) | existing Swarm limit (3) |
| External or irreversible action | human approval required | human approval required |
| Local fallback / other provider | never | never |

YOLO changes only the reviewed-execution quorum requirement for vetted local,
reversible actions. It is detected from the existing Nova policy/runtime state;
no suggestion, browser payload, or model output can claim YOLO status.

## Run lifecycle and recovery

```text
Nova intent
  -> canonical bridge admission
  -> durable running Swarm run
  -> Scout / Plan / Build / Critic / Verifier / independent reviews
  -> pre-completion Nova hook
  -> Swarm claim -> Nova govern -> Nova act
  -> durable terminal event and completed run
```

Provider errors, timeouts, schema failures, missing positive verification,
missing review quorum, or a human-approval requirement leave an auditable
paused or blocked result. The bridge never creates an automatic retry loop and
never starts another Nova run while the prior Nova run is running or paused.
A restart reconstructs the active intent from durable events and attaches to
the existing run instead of creating a duplicate.

An action claim is held before `EntityKernel.act()`; a crash after claim but
before action is not replayed automatically. It requires the existing explicit
human recovery path, preserving exactly-once semantics over availability.

## Live integration seam

The unversioned live Nova deployment changes at one narrow point:
`C:\\sidekick\\home\\spaces\\nova\\nova_mind.py` replaces its direct
`EntityKernel.govern()` / `act()` pair with an explicit bridge call. The
versioned bridge code remains in the Sidekick repository. No Sidekick WebUI
restart is required for this source change; Nova is restarted only after a
non-live canary passes.

## Verification plan

Automated tests must cover:

- disabled bridge makes no run or model call;
- unsupported actions are blocked before any Cloud request;
- active-intent deduplication, one-active-run locking, restart recovery, and
  rolling daily budgets are atomic under concurrent callers;
- standard mode requires positive local verifier evidence plus the two fixed
  independent review checkpoints;
- YOLO reads only the actual Nova runtime state, uses 128 calls, removes only
  the daily cap, and retains one-active-run / exact-once / human-sensitive
  gates;
- verifier-unavailable, negative verification, review denial, evidence digest
  mismatch, proposal mutation, and action-root mismatch never call `act()`;
- `govern()` precedes `act()` exactly once on an allowed path and no legacy
  fallback exists;
- provider pauses and crashes do not cause automated re-dispatch;
- fake-transport integration exercises the completion hook; and
- an explicit Ollama Cloud canary is opt-in, uses no secrets in output, and is
  run only after the disposable Nova-space checks pass.

## Rollout sequence

1. Implement and run the focused unit and fake-transport suites in an isolated
   worktree.
2. Run the bridge against a disposable Nova space with a trusted `.swarm`
   configuration, including allowed and blocked paths.
3. Review the live configuration, initialize the live Nova `.swarm` directory
   explicitly, and enable the bridge in standard mode.
4. Execute one opt-in Cloud canary.
5. Patch the one live Nova import seam and restart only Nova Mind.
6. Confirm one run at a time, fresh status, durable events, and no automatic
   retry or duplicate action.

No deployment step changes provider credentials, restarts Sidekick/WebUI, or
enables external Nova actions.
