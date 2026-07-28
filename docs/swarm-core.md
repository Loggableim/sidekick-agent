# Swarm Core

Swarm Core is a reusable, project-local coordination layer. It works with
Sidekick, but does not require Nova and keeps all durable run state in the
project that owns the work.

## Start a project

```powershell
sidekick swarm --project C:\work\my-project init
sidekick swarm --project C:\work\my-project models refresh
sidekick swarm --project C:\work\my-project packs list
sidekick swarm --project C:\work\my-project run "Audit the release readiness" --pack release-audit
sidekick swarm --project C:\work\my-project status
```

`init` creates the versionable `.swarm/swarm.yaml` and a nested `.gitignore`.
Runtime data, SQLite state, prompts, model responses and cost records live
under `.swarm/runtime/` and remain local/ignored.

On POSIX, mutating Swarm access repairs `.swarm` and `.swarm/runtime` to
owner-only mode `0700` and the SQLite database, journal, WAL and SHM files to
mode `0600`. Read-only status access never changes permissions; it rejects a
too-broad directory or SQLite artifact instead. This is deliberately the local
OS trust boundary: a process running as the same OS user as the project owner
is already trusted to modify that user's project files. Python's stock SQLite
VFS cannot atomically bind a hostile same-user rename to a pre-opened database
descriptor while retaining correct journal/WAL sidecars. Swarm therefore also
checks the pinned database child's identity immediately before and after its
SQLite open and fails closed before Swarm SQL if it changed; that is a
best-effort detection layer, not a stronger same-user isolation guarantee.

Model discovery is deliberately explicit. A run never refreshes the catalog
by itself. If no healthy Ollama Cloud catalog is available, the run pauses with
an auditable reason instead of trying a local model or another provider.
Swarm accepts catalog refreshes and default Sidekick dispatches only through
the canonical `https://ollama.com/v1` HTTPS endpoint. A local or custom
`OLLAMA_BASE_URL` is never treated as Cloud; it produces a pause, and the
endpoint is checked again immediately before every default provider hand-off.

## Packs and routing

Available packs are `coding-team`, `bug-hunt`, `research-team` and
`release-audit`. The normal flow is Scout, Planner, Builder/Critic, Verifier,
two independent reviewers, Integrator/Referee and a policy gate.

The default/scout model is `deepseek-v4-flash`. Planning uses
`deepseek-v4-pro` with `kimi-k2.6` as an independent challenger; builder and
first critic use `minimax-m3`; coding/review A uses `glm-5.2`; long review B
uses `kimi-k2.7-code`; integration uses `nemotron-3-super`. Vision uses a
live-catalog `qwen3.5`, otherwise `gemma4:31b`. GPT-OSS is never a Swarm
fallback.

Every run is capped at 48 model calls and three concurrent calls, including
retries. The two required reviews run independently; the integrator receives
their structured findings and verifier evidence, not an unrestricted shared
conversation.

## Local role market and reputation

`RoleMarket` is a read-only, project-local explanation layer: it lists the
curated Ollama Cloud candidates for a role with their catalog health, static
`role_quality`, and any existing local role/capability reputation. Only healthy
catalog candidates are recommendable. Its score is transparent (80% static
quality, 20% local reputation when one exists), but it has no execution
authority: Default, review, vision, and every other safety-locked router chain
retain their prescribed order and fallbacks.

The planner challenger and planner arbitrator transparently use the planner's
quality and reputation basis for comparison, while still exposing only their
own prescribed Kimi and DeepSeek/Kimi candidate routes.

Reputation is never inferred from a model response or merely because a run
completed. A trusted local verifier must explicitly submit a structured
assessment with a role, capability, score, safety result, and verifier-owned
source reference; unsafe assessments contribute zero. Golden-task results
remain explicit and separate from automatic workflow execution.

## Local verifier boundary

The Verifier is local code, never an Ollama Cloud role. Its tool-free
`ReadOnlyVerifier` contract receives an immutable goal, project path and the
Builder/Critic outputs; it has no action-execution capability. A host may
inject a project-specific adapter that performs read-only inspection and emits
its own evidence and provenance. Directly reusing Builder or Critic evidence
references is rejected.

Without an injected adapter, `DefaultReadOnlyVerifier` performs no project
I/O and records an auditable local result with
`decision: "verification_unavailable"`, `operation: "no_project_io"`, its
own `verifier:local:*` evidence reference, and no reputation assessments. It
is intentionally fail-closed: this record documents why verification was not
available, but never certifies a project action.

For `reviewed_execution`, an explicit read-only inspection adapter must
persist an independent local result with the exact decision `"verified"`,
verifier-owned evidence/provenance, and a `model: null` verifier checkpoint.
Only that result can participate in the local verifier portion of the action
quorum; it still requires the two independent review votes below. A verifier
adapter must not use Cloud models, tools, project writes, Git writes, or
external side effects.

## Approvals and controls

The default autonomy level is `reviewed_execution`.

- Reading is immediate.
- A local reversible project, Git or worktree action requires independent,
  positively verified local verifier evidence and two independent model-review
  approvals. The default `verification_unavailable` record never qualifies.
- That model quorum is derived only from immutable run-local checkpoints:
  `review_a` on `glm-5.2` and `review_b` on `kimi-k2.7-code` must each carry
  evidence plus `approved: true` and the canonical positive decision
  `approve` or `approved`; arbitrary approval IDs, family labels, negative or
  ambiguous decisions never satisfy it. Proposal evidence must intersect the
  verifier evidence, while either reviewer may use its own supporting refs.
- External, irreversible or cost-increasing work always requires a human
  approval.
- `pause`, `resume` and `approve` are explicit commands. A completed run is
  terminal and cannot be resumed.
- A process that dies while holding an execution lease is never taken over
  automatically. After confirming the old host has stopped, a human may run
  `sidekick swarm recover RUN_ID`; this records the trusted host actor, leaves the run
  paused, and requires a separate `resume` command. Recovery never launches
  a model call by itself. It records a separate audit event for each exact,
  previously uncertain attempt (original event sequence, role and model), so
  only those calls may be retried during that later resume. Malformed,
  duplicate or non-matching replay audit events fail closed and keep the run
  paused.

Approvals are bound to the exact immutable action proposal. The policy claim
and authorization happen atomically, and action capabilities are adapter-owned
instead of being trusted from a model or browser payload.

The WebUI Swarm tab uses the same API. Its status loads and event stream are
read-only; it passes the active Space's configured filesystem project path,
not the Space slug. Set a project directory on the Space before opening a
Swarm run there.

## Optional Kanban projection

Kanban is a visibility projection, never Swarm's source of truth. Projecting a
run is an explicit user action. The route derives a trusted dashboard actor on
the server and records that human request in the Swarm audit trail before the
cross-surface write. The projection is idempotent, does not start a dispatcher
or worker, and failures are recorded as projection status without pausing or
rewriting the Swarm run.

## Nova runtime bridge

Nova admission is default-disabled through `integrations.nova.enabled`; reading
that setting is read-only and creates neither `.swarm` state nor a worker. An
enabled host must supply one trusted Nova root shared by the project, kernel,
and action registry. Only `mind_diary`, `agenda_update`, and
`prioritize_thread` are admitted. Cloud catalog/model execution is still the
normal Swarm host path; unsupported Nova actions are recorded as bounded
pre-run rejections before an adapter, model, or Cloud request exists.

Each admitted run has immutable canonical snapshot and proposal digests, is
single-active, and requires `nova-runtime-v1` before completion. Standard mode
is `reviewed_execution` (48 calls and six admissions per rolling 24 hours).
Only `kernel.is_yolo_enabled()` may select autonomous YOLO mode (128 calls and
no rolling quota); proposal/config input cannot claim YOLO. YOLO does not relax
the allowlist, root check, reviewer/verifier evidence, or human approval for
external/human-sensitive work.

Immediately before completion, the hook rereads the durable admission,
revalidates its snapshot/root/evidence and exact proposal digest, records
`nova.bridge.action_proposed`, and invokes only
`NovaSwarmAdapter.execute_suggestion()`. The adapter preserves
PolicyGate-to-govern-to-act ordering and the atomic policy claim; no other
bridge method calls `kernel.act`. A denial, missing evidence, mismatch, or
provider pause is auditably paused and never auto-retried. A post-claim crash
records `nova.bridge.recovery_required` and requires explicit human recovery;
it never replays govern or act.

`sidekick swarm resume` and the WebUI use the same per-run resolver. A Nova
resume is executable only in the same process after the explicit host-owned
bridge has registered its kernel, trusted root, verifier, and hook. The
versioned `NovaSwarmRuntimeBridge.attach_admitted_run()` is the Task-6 host
attach seam; it accepts an already-owned kernel/root capability and does not
discover, start, or patch a live Nova process. A fresh process never restores
that trust from metadata: it fails closed with `nova_bridge_unavailable` before
engine construction or a Cloud call. Status GET/SSE construction stays
read-only: it neither resolves Nova execution options, initializes `.swarm`,
nor dispatches a worker.
