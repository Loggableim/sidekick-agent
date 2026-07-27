# Nova/Sidekick Swarm Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a project-local, Ollama-Cloud-first Swarm Core that Sidekick and Nova can use without making Nova a dependency.

**Architecture:** `swarm_core` owns durable project state, event sequencing, role workflow, policy, memory, model routing, and plugin protocols. Sidekick supplies CLI, trusted-workspace, provider-pool, WebUI, Kanban, and optional Nova adapters; it never becomes the source of truth for a swarm run.

**Tech Stack:** Python 3.11, SQLite, PyYAML, existing Sidekick provider transport, custom WebUI routes/static JavaScript, pytest.

## Global Constraints

- Default provider is `ollama-cloud`; default model is `deepseek-v4-flash`.
- A run is capped at 48 model calls and 3 simultaneous model calls, including retries.
- A cloud failure pauses the run after the configured Ollama-only fallback chain; never silently route to another provider or local model.
- Default autonomy is `reviewed_execution`: local reversible actions require a verifier result and two independent review approvals; external, irreversible, or cost-increasing actions always require a human approval.
- `.swarm/swarm.yaml` and user-selected pack overrides are versionable; `.swarm/runtime/` is ignored and contains all run records, prompts, responses, caches, and cost data.
- Core code must not import `nova`, `web`, a live Nova deployment path, or direct Ollama HTTP clients.
- New Swarm status GET endpoints are pure reads and must not call Nova status, migration, repair, cron, or provider-pool loading paths.
- No GPT-OSS model is present in a Swarm model route or fallback chain.
- Do not modify `C:\sidekick\home\spaces\nova`, restart Nova, or enable the Nova adapter.

---

### Task 1: Project-local core state and configuration

**Files:**
- Create: `swarm_core/__init__.py`, `swarm_core/types.py`, `swarm_core/config.py`, `swarm_core/store.py`, `swarm_core/events.py`
- Modify: `pyproject.toml`
- Test: `tests/test_swarm_core.py`

**Interfaces:**
- `initialize_project(project_root: Path) -> SwarmConfig` creates the versionable `.swarm/swarm.yaml`, nested `.swarm/.gitignore`, and ignored `.swarm/runtime/`.
- `ProjectSwarmStore(project_root: Path)` owns `.swarm/runtime/swarm.sqlite` and exposes `create_run`, `get_run`, `append_event`, `list_events`, `set_run_status`, and `resume_run`.
- `SwarmEvent` has a monotonic sequence, timestamp, type, run id, payload, and visibility.

- [ ] Write failing tests for initial layout, ignored runtime state, monotonic event ordering, run pause/resume, and reopening a persisted run.
- [ ] Run the focused test file and confirm failures are caused by absent Swarm Core APIs.
- [ ] Implement the smallest typed configuration, SQLite store, and event bus that satisfy those tests.
- [ ] Add `swarm_core` to package discovery and run focused tests plus existing Agent Pool/Kanban baseline.
- [ ] Commit the task.

### Task 2: Ollama Cloud registry, router, and role workflow

**Files:**
- Create: `swarm_core/models.py`, `swarm_core/transport.py`, `swarm_core/router.py`, `swarm_core/workflow.py`, `swarm_core/engine.py`
- Test: `tests/test_swarm_routing.py`, `tests/test_swarm_workflow.py`

**Interfaces:**
- `ModelTransport.complete(request: ModelRequest) -> ModelResponse` is the only model-call protocol in core.
- `OllamaCloudTransport` calls `runtime.auxiliary_client.call_llm` only through a Sidekick adapter and always supplies explicit `provider="ollama-cloud"` and model.
- `SwarmEngine.run(goal, project_root, pack="coding-team") -> RunSummary` emits structured work, evidence, decision, and pause events.
- `ModelRouter.select(role, requirements)` returns a model plus Ollama-only fallback chain.

- [ ] Write failing fake-transport tests for default/role routing, independent parallel review selection, capability discovery, no GPT-OSS fallback, schema/error fallback, 48-call budget, 3-call concurrency cap, and pause on exhausted chain.
- [ ] Run the focused routing/workflow tests and confirm they fail because the router/engine do not exist.
- [ ] Implement model capabilities and the specified routes: Flash, Pro, MiniMax M3, Kimi K2.6, GLM 5.2, Kimi K2.7 Code, Nemotron 3 Super, and catalog-gated Qwen/Gemma vision.
- [ ] Implement Scout → Planner → Builder/Critic → Verifier → two independent reviewers → Integrator/Referee workflow with context sharding and structured blackboard evidence.
- [ ] Run focused tests and commit the task.

### Task 3: Policy, approvals, and pluggable action gates

**Files:**
- Create: `swarm_core/policy.py`, `swarm_core/tools.py`, `swarm_core/sidekick_adapter.py`
- Test: `tests/test_swarm_policy.py`

**Interfaces:**
- `ActionProposal` includes category, reversibility, external/cost flags, evidence references, and requested adapter action.
- `PolicyGate.evaluate(proposal, run) -> PolicyDecision` returns `allowed`, `needs_model_quorum`, `needs_human_approval`, or `blocked`.
- `ToolAdapter.preview` and `ToolAdapter.execute` are injected; core never executes shell commands directly.
- `SidekickToolAdapter` obtains trusted projects and optional worktree handling through existing Sidekick APIs.

- [ ] Write failing tests for all autonomy levels, the reviewed-execution default, independent-family quorum, verifier requirement, human-only external/irreversible/cost actions, and an adapter that is never invoked before approval.
- [ ] Run the focused policy test and confirm expected missing-interface failures.
- [ ] Implement policy classification, persisted approvals, model-family diversity checks, and a safe Sidekick adapter that cannot bypass the trusted-workspace check.
- [ ] Run focused tests plus existing worktree and Agent Pool tests; commit the task.

### Task 4: Memory, reputation, prompt candidates, and packs

**Files:**
- Create: `swarm_core/memory.py`, `swarm_core/learning.py`, `swarm_core/packs.py`, `swarm_core/packs/*.yaml`
- Test: `tests/test_swarm_memory.py`, `tests/test_swarm_learning.py`

**Interfaces:**
- `remember(kind, statement, evidence_refs, ...)` supports `fact`, `opinion`, `decision`, and `evidence`.
- `record_outcome(role, capability, score)` maintains per-role reputation.
- `evaluate_prompt_candidate(candidate_id, golden_results)` never promotes automatically without passing safety checks and explicit human approval.
- Packs are `coding-team`, `bug-hunt`, `research-team`, and `release-audit`.

- [ ] Write failing tests for stale/revalidation state, contradictory facts, role-specific reputation, opt-in redacted lesson export, candidate-prompt promotion gates, and pack lookup.
- [ ] Run the focused tests and confirm the APIs are absent.
- [ ] Implement isolated project memory, no automatic cross-project sync, Golden Task scoring, prompt candidate state, and default packs.
- [ ] Run focused tests and commit the task.

### Task 5: CLI and pure-read Swarm HTTP API

**Files:**
- Modify: `cli/main.py`, `web/api/routes.py`
- Create: `web/api/swarm.py`
- Test: `tests/test_swarm_cli.py`, `tests/test_swarm_api.py`

**Interfaces:**
- CLI supports `sidekick swarm init`, `run`, `status`, `approve`, `pause`, `resume`, `models refresh`, and `packs list`.
- API supports run creation, status, events/SSE, approval, pause/resume, packs, and explicit catalog refresh beneath `/api/swarm/`.
- Every API response is JSON-safe; GET status/events are pure reads and use the active trusted workspace.

- [ ] Write failing CLI and handler-level tests for commands, trusted workspace rejection, run lifecycle, pure-read status, SSE event ordering, approval action, pack list, and explicit-only refresh.
- [ ] Run focused tests and confirm CLI routes/endpoints are unregistered.
- [ ] Implement commands and API with no direct Nova route access and no automatic catalog refresh on status/read.
- [ ] Run focused tests plus selected WebUI regression tests; commit the task.

### Task 6: Sidekick WebUI and Kanban projection

**Files:**
- Modify: `web/static/index.html`, WebUI panel routing/bootstrap assets
- Create: `web/static/swarm.js`, `web/static/swarm.css`
- Test: `tests/test_swarm_dashboard.py`, browser smoke coverage

**Interfaces:**
- The Swarm panel displays runs, role/model state, evidence/conflicts, budget, approvals, and packs.
- UI commands use `/api/swarm/*`; display polling/SSE never creates a run or mutates a run.
- Kanban projection mirrors tasks/events only after a run exists; the Swarm SQLite store remains authoritative.

- [ ] Write failing browser/DOM and API-contract tests for panel navigation, run start, live state, approval, pause, and pack selection.
- [ ] Run focused tests and confirm the panel/API bindings are absent.
- [ ] Implement the panel with accessible controls and a separate Swarm namespace, then add optional Kanban projection without dispatcher auto-spawn.
- [ ] Run focused tests, browser smoke, and commit the task.

### Task 7: Prepared Nova adapter and final integration verification

**Files:**
- Create: `nova/swarm_adapter.py`
- Test: `tests/test_nova_swarm_adapter.py`
- Modify: user-facing documentation for install, packs, approval semantics, and deferred Nova activation

**Interfaces:**
- `NovaSwarmAdapter` translates a Nova proposal into an `ActionProposal` and invokes `EntityKernel.govern()` before `EntityKernel.act()`.
- A policy block never triggers a legacy fallback from the adapter.
- The adapter has no startup hook and remains disabled until a separate rollout explicitly enables it.

- [ ] Write failing tests with a fake entity kernel proving proposal translation, govern-before-act ordering, policy-block behavior, and no live deployment access.
- [ ] Run focused tests and confirm the adapter is absent.
- [ ] Implement the isolated adapter and documentation only; do not modify the live Nova deployment.
- [ ] Run all Swarm tests, relevant existing regression suites, lint/compile checks, and commit the task.
