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

Model discovery is deliberately explicit. A run never refreshes the catalog
by itself. If no healthy Ollama Cloud catalog is available, the run pauses with
an auditable reason instead of trying a local model or another provider.

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

## Approvals and controls

The default autonomy level is `reviewed_execution`.

- Reading is immediate.
- A local reversible project, Git or worktree action requires verifier
  evidence and two independent model-review approvals.
- External, irreversible or cost-increasing work always requires a human
  approval.
- `pause`, `resume` and `approve` are explicit commands. A completed run is
  terminal and cannot be resumed.

Approvals are bound to the exact immutable action proposal. The policy claim
and authorization happen atomically, and action capabilities are adapter-owned
instead of being trusted from a model or browser payload.

The WebUI Swarm tab uses the same API. Its status loads and event stream are
read-only; it passes the active Space's configured filesystem project path,
not the Space slug. Set a project directory on the Space before opening a
Swarm run there.

## Optional Kanban projection

Kanban is a visibility projection, never Swarm's source of truth. Projecting a
run is an explicit user action. The projection is idempotent, does not start a
dispatcher or worker, and failures are recorded as projection status without
pausing or rewriting the Swarm run.

## Nova adapter: prepared, disabled

`nova.swarm_adapter.NovaSwarmAdapter` is intentionally not registered with a
Nova startup path and defaults to `enabled=False`. Importing it does not create
a kernel or touch the live Nova deployment.

The initial adapter registry is deliberately narrow: `agenda_update`,
`mind_diary` and `prioritize_thread` are direct project-local actions;
`blog_draft` retains Nova's `external` tier and therefore always needs a human
Swarm approval. Script-backed actions such as `inner_voice` are not enabled in
this first slice. A later activation also requires the supplied
`EntityKernel.space_dir` to be the exact Swarm project root, and the kernel's
configured policy tier is used without adapter-side downgrades.

Before any later activation, verify all of the following in a separate,
explicit rollout:

1. Use a disposable/non-live Nova space and a project with a trusted
   `.swarm` configuration.
2. Review the small adapter-owned Nova action registry and its Swarm risk
   categories and Nova policy tiers.
3. Exercise disabled, Swarm-blocked, Nova-governance-blocked and allowed paths
   with a fake kernel first.
4. Confirm an allowed path calls `EntityKernel.govern()` before
   `EntityKernel.act()`, with no legacy fallback.
5. Obtain separate approval before changing any Nova deployment configuration,
   startup hook or runtime process.

No current Swarm command, API request or WebUI load enables that adapter or
restarts Nova.
