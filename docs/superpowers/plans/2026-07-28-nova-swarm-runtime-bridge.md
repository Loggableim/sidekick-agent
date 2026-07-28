# Nova-to-Swarm Runtime Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route vetted Nova Mind intents through project-local Swarm and execute an allowed Nova action exactly once through Swarm policy, Nova governance, and Nova action dispatch.

**Architecture:** Add generic Core primitives for a typed pre-completion hook and atomic namespace-based integration admission, without importing Nova into Core. Sidekick resolves Nova-specific execution options only for durable Nova runs; the versioned Nova bridge owns canonical snapshots, read-only verification, admission, hook execution, and in-process dispatch. The separate live Nova deployment changes only its submission seam after disposable tests and an explicit Cloud canary pass.

**Tech Stack:** Python 3, SQLite, PyYAML, existing Swarm Core, Sidekick Ollama Cloud transport, Nova EntityKernel, pytest, PowerShell.

## Global Constraints

- Standard mode is exactly reviewed_execution, at most six newly admitted Nova runs in a rolling 24-hour window, one active Nova run (running or paused), at most 48 model calls per run, and the existing three-call concurrency ceiling.
- YOLO status comes only from the existing persisted EntityKernel runtime state. It sets autonomous, removes only the rolling daily cap, permits 128 calls, and retains one active Nova run.
- Neither mode bypasses trusted project roots, the static action allowlist, immutable snapshot/digest binding, Cloud-only routing, policy claim, Nova govern, Nova act, or required human approval for external, irreversible, or cost-increasing work.
- The automatic action allowlist is exactly mind_diary, agenda_update, and prioritize_thread. Reflection, ACES, Moltbook, blog_draft, and unknown actions are blocked before a run or Cloud request.
- Every action path is PolicyGate.authorize_and_claim(), then EntityKernel.govern(), then EntityKernel.act(). There is no legacy direct-action fallback.
- A run never refreshes models. The only provider is the existing canonical https://ollama.com/v1 Cloud path and its existing provider-slot guard.
- Durable bridge events contain only IDs, action names, hashes, mode, counters, and bounded reasons; never API keys, raw prompts, raw model responses, or full Nova state.
- Test Nova roots must be tmp_path / "spaces" / "nova" so trusted-workspace checks pass cross-platform.
- Do not touch C:\sidekick\home\spaces\nova\nova_mind.py, its .swarm state, or its process until the live rollout task. Do not restart Sidekick or the WebUI.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| swarm_core/engine.py | Generic typed pre-completion lifecycle hook and fail-closed required-hook enforcement. |
| swarm_core/store.py | Atomic namespace admission, duplicate mapping, rolling quota, active-slot release, and durable rejection audit. |
| swarm_core/types.py | Immutable generic admission request/result types. |
| swarm_core/config.py | Safe integration config read/write in .swarm/swarm.yaml. |
| cli/swarm_host.py | Per-run options resolver while retaining the existing Cloud transport and slots. |
| cli/swarm.py | Default registration of the Nova options resolver for CLI and HTTP resume. |
| nova/entity_kernel.py | Public read-only YOLO state accessor. |
| nova/swarm_runtime_bridge.py | Bridge config, snapshot, verifier, admission, dispatch, and completion hook. |
| nova/swarm_adapter.py | Existing sole policy/govern/act action boundary; only a minimal spec lookup may be exposed. |
| tests/test_swarm_workflow.py | Core hook ordering, pause, resume, and ordinary-run regressions. |
| tests/test_swarm_core.py and tests/test_swarm_config.py | Admission atomicity, limits, release, rejection, and configuration tests. |
| tests/test_swarm_host.py, tests/test_swarm_cli.py, tests/test_swarm_http.py | Host option, resume, Cloud-only, and read-only API tests. |
| tests/test_nova_swarm_runtime_bridge.py | Fake-transport bridge, verifier, mode, exactly-once, and recovery tests. |
| nova/test_entity_kernel.py | Public YOLO state accessor test. |
| docs/swarm-core.md | Operational contract and staged rollout documentation. |
| C:\sidekick\home\spaces\nova\nova_mind.py | Final unversioned deployment seam only. |

## Task 1: Add the generic pre-completion hook to Swarm Core

**Files:**

- Modify: swarm_core/engine.py
- Modify: tests/test_swarm_workflow.py

**Interfaces:**

- Add PreCompletionContext, PreCompletionResult, and PreCompletionHook to swarm_core.engine.
- Extend SwarmEngine.__init__(..., pre_completion_hook: PreCompletionHook | None = None).
- Extend SwarmEngine.start_run(..., host_metadata: Mapping[str, Any] | None = None).
- Reserve run metadata key required_pre_completion_hook; it requires a matching hook_id.

- [ ] **Step 1: Write the failing lifecycle tests**

  Add tests beside the existing final-transition tests. The hook must observe durable verifier, review_a, and review_b checkpoints while the run is still running; its event must precede run.completed.

  ~~~python
  def test_pre_completion_hook_runs_before_completed(tmp_path: Path):
      observed = []

      class Hook:
          hook_id = "test-hook-v1"

          def run(self, context: PreCompletionContext) -> PreCompletionResult:
              checkpoints = context.store.get_workflow_role_checkpoints(
                  context.run.run_id
              )
              assert {"verifier", "review_a", "review_b"} <= set(checkpoints)
              assert context.store.get_run(context.run.run_id).status == "running"
              context.store.append_event(
                  context.run.run_id, "test.pre_completion_hook", {}
              )
              observed.append(context.decision)
              return PreCompletionResult(continue_completion=True)

      engine = SwarmEngine(WorkflowTransport(), pre_completion_hook=Hook())
      run = engine.start_run(
          "verify hook order",
          tmp_path,
          host_metadata={"required_pre_completion_hook": "test-hook-v1"},
      )
      summary = engine.execute_run(run.run_id, tmp_path)
      event_types = [
          event.event_type
          for event in ProjectSwarmStore(tmp_path).list_events(run.run_id)
      ]
      assert summary.status == "completed"
      assert observed
      assert event_types.index("test.pre_completion_hook") < event_types.index(
          "run.completed"
      )
  ~~~

  Add a hook-pause test that returns PreCompletionResult(False, "awaiting_nova_approval"). It must leave no run.completed event and, after explicit resume, reuse all durable role output without another model call. Add a no-hook test proving ordinary runs retain their current terminal event sequence.

- [ ] **Step 2: Run the focused tests to verify they fail**

  Run:

  ~~~powershell
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q tests/test_swarm_workflow.py -k "pre_completion_hook"
  ~~~

  Expected: missing hook types, constructor argument, and host metadata argument.

- [ ] **Step 3: Implement the typed hook**

  Add these host-neutral definitions near RunSummary:

  ~~~python
  @dataclass(frozen=True)
  class PreCompletionContext:
      run: SwarmRun
      project_root: Path
      store: ProjectSwarmStore
      goal: str
      pack: str
      autonomy: str
      call_count: int
      decision: str
      evidence: Mapping[str, list[Any]]


  @dataclass(frozen=True)
  class PreCompletionResult:
      continue_completion: bool
      pause_reason: str | None = None

      def __post_init__(self) -> None:
          if not self.continue_completion and not (
              isinstance(self.pause_reason, str) and self.pause_reason.strip()
          ):
              raise ValueError("Paused pre-completion result requires a reason")
          if self.continue_completion and self.pause_reason is not None:
              raise ValueError("Continuing result cannot carry a pause reason")


  class PreCompletionHook(Protocol):
      hook_id: str

      def run(self, context: PreCompletionContext) -> PreCompletionResult: ...
  ~~~

  Validate host_metadata as JSON-safe and reject keys goal, pack, project_root, and autonomy. After _record_local_verifier_reputation(), but before _complete_after_checkpoint(), read the durable required hook ID. A missing/mismatched required hook pauses with required_pre_completion_hook_unavailable. Invoke checkpoint once, call the hook, and map a declining result to the existing paused summary. Catch ordinary Exception and pause with pre_completion_hook_failed without raw error text; do not catch BaseException so existing crash and lease semantics remain intact.

- [ ] **Step 4: Run the lifecycle regression suite**

  ~~~powershell
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q tests/test_swarm_workflow.py
  ~~~

  Expected: workflow tests pass, including hook order, pause, resume, and no-hook behavior.

- [ ] **Step 5: Commit the Core hook**

  ~~~powershell
  git add swarm_core/engine.py tests/test_swarm_workflow.py
  git commit -m "feat: add Swarm pre-completion hooks"
  ~~~

## Task 2: Persist generic integration admissions atomically

**Files:**

- Modify: swarm_core/types.py
- Modify: swarm_core/store.py
- Modify: swarm_core/config.py
- Modify: tests/test_swarm_core.py
- Modify: tests/test_swarm_config.py

**Interfaces:**

- Add IntegrationAdmissionRequest and IntegrationAdmission to swarm_core.types.
- Add ProjectSwarmStore.admit_integration_run(request, *, now=None).
- Add ProjectSwarmStore.record_integration_rejection(...) and get_integration_admission(namespace, idempotency_key).
- Add load_integration_config(project_root, namespace) and save_integration_config(project_root, namespace, config).

- [ ] **Step 1: Write failing admission and configuration tests**

  Use this fixture:

  ~~~python
  @pytest.fixture
  def nova_project(tmp_path: Path) -> Path:
      project = tmp_path / "spaces" / "nova"
      project.mkdir(parents=True)
      initialize_project(project)
      return project
  ~~~

  Test two concurrent equal-key Nova admissions: exactly one returns created and the other coalesced with the same run ID. Test two different keys: one created and one active_limit. Pausing the admitted run must retain the active slot; completion frees it but does not erase the rolling quota.

  Test six standard admissions inside a fixed 24-hour window and assert the seventh returns rolling_limit. Pass rolling_run_limit=None for the same request and assert admission. Test record_integration_rejection creates no run and records only a bounded reason. Test missing integrations.nova reads as an empty mapping, explicit save/load round-trips version and enabled, and an escaping .swarm link is still rejected before writing.

- [ ] **Step 2: Run the focused tests to verify they fail**

  ~~~powershell
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q tests/test_swarm_core.py tests/test_swarm_config.py -k "integration or admission"
  ~~~

  Expected: missing admission types and store/config methods.

- [ ] **Step 3: Implement bounded generic persistence**

  Implement:

  ~~~python
  @dataclass(frozen=True)
  class IntegrationAdmissionRequest:
      namespace: str
      idempotency_key: str
      metadata: Mapping[str, Any]
      max_active: int
      rolling_window_seconds: int
      rolling_run_limit: int | None


  @dataclass(frozen=True)
  class IntegrationAdmission:
      status: str
      namespace: str
      idempotency_key: str
      run: SwarmRun | None
      reason: str | None
  ~~~

  Add one generic table/migration to the existing SQLite store:

  ~~~sql
  CREATE TABLE IF NOT EXISTS integration_admissions (
      namespace TEXT NOT NULL,
      idempotency_key TEXT NOT NULL,
      run_id TEXT UNIQUE REFERENCES runs(run_id),
      state TEXT NOT NULL,
      active_slot INTEGER,
      metadata_json TEXT NOT NULL,
      reason TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (namespace, idempotency_key)
  );
  CREATE UNIQUE INDEX IF NOT EXISTS idx_integration_admissions_active_slot
      ON integration_admissions(namespace, active_slot)
      WHERE active_slot IS NOT NULL;
  ~~~

  admit_integration_run must use one immediate transaction: validate request bounds and JSON-safe metadata, return an existing identity first, check active slots, check the rolling window across the namespace, insert the run plus admission plus run.started atomically, and return a typed result. Metadata must contain valid goal, pack, project_root, and autonomy. set_run_status(..., completed) clears the active slot in the same transaction; paused retains it. Rejected pre-run attempts store state rejected and run_id NULL.

  Implement config helpers through the existing pinned swarm.yaml mechanism. They operate only on an integrations mapping, validate an ASCII namespace and JSON/YAML-safe values, preserve unrelated configuration, and write only in explicit save calls.

- [ ] **Step 4: Run store, config, and lifecycle regressions**

  ~~~powershell
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q tests/test_swarm_core.py tests/test_swarm_config.py tests/test_swarm_workflow.py
  ~~~

  Expected: race, quota, symlink, ordinary-run, and lifecycle tests pass.

- [ ] **Step 5: Commit durable admission support**

  ~~~powershell
  git add swarm_core/types.py swarm_core/store.py swarm_core/config.py tests/test_swarm_core.py tests/test_swarm_config.py
  git commit -m "feat: persist generic Swarm integration admissions"
  ~~~

## Task 3: Make Sidekick resolve per-run bridge options

**Files:**

- Modify: cli/swarm_host.py
- Modify: nova/entity_kernel.py
- Modify: nova/test_entity_kernel.py
- Modify: tests/test_swarm_host.py

**Interfaces:**

- Add SwarmExecutionOptions and ExecutionOptionsResolver to cli.swarm_host.
- Extend SidekickSwarmService with execution_options_resolver, autonomy, and host_metadata support.
- Add EntityKernel.is_yolo_enabled() -> bool.

- [ ] **Step 1: Write failing host-option and YOLO tests**

  Add a resolver test that receives the durable run, returns a custom verified read-only verifier, a test hook, and max_calls=128. With the current fake verified catalog, assert every request still has provider ollama-cloud, every request passes through the existing provider-slot callback, and no catalog refresh occurs.

  In nova/test_entity_kernel.py, write .lifecycle/yolo.json below the temporary space, assert is_yolo_enabled() is true, then replace it with malformed JSON and assert false.

- [ ] **Step 2: Run the focused tests to verify they fail**

  ~~~powershell
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q tests/test_swarm_host.py -k "execution_options"
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q nova/test_entity_kernel.py -k "yolo"
  ~~~

  Expected: missing resolver and missing public accessor.

- [ ] **Step 3: Implement safe option resolution**

  Add:

  ~~~python
  @dataclass(frozen=True)
  class SwarmExecutionOptions:
      max_calls: int = 48
      max_concurrent: int = 3
      verifier: ReadOnlyVerifier | None = None
      pre_completion_hook: PreCompletionHook | None = None
      blocked_reason: str | None = None


  ExecutionOptionsResolver = Callable[
      [Path, SwarmRun], SwarmExecutionOptions | None
  ]
  ~~~

  execute_run reads the durable run, invokes the resolver once, and if blocked_reason is present pauses/records a bounded event before engine construction or a model request. Otherwise _engine_for builds SwarmEngine with the selected limits, verifier, and hook while retaining the current verified-catalog check, OllamaCloudTransport, canonical endpoint check, and provider-slot guard unchanged. Default services without a resolver preserve current behavior; Core required-hook enforcement protects marked integration runs when a resolver is absent.

  Extend start_run to forward validated autonomy and host_metadata. Add:

  ~~~python
  def is_yolo_enabled(self) -> bool:
      return self._yolo_enabled()
  ~~~

  The future bridge uses this method only; it must not inspect an environment variable, model output, or request body.

- [ ] **Step 4: Run host and kernel regressions**

  ~~~powershell
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q tests/test_swarm_host.py nova/test_entity_kernel.py
  ~~~

  Expected: budget injection works without weakening Cloud-only dispatch.

- [ ] **Step 5: Commit host option injection**

  ~~~powershell
  git add cli/swarm_host.py nova/entity_kernel.py nova/test_entity_kernel.py tests/test_swarm_host.py
  git commit -m "feat: support per-run Swarm execution options"
  ~~~

## Task 4: Build canonical Nova snapshots and the local verifier

**Files:**

- Create: nova/swarm_runtime_bridge.py
- Modify: nova/swarm_adapter.py
- Modify: tests/test_nova_swarm_adapter.py
- Create: tests/test_nova_swarm_runtime_bridge.py

**Interfaces:**

- Add NovaBridgeConfig, NovaIntentSnapshot, NovaIntentReadOnlyVerifier, configure_nova_bridge(), and load_nova_bridge_config().
- Keep NOVA_AUTOMATIC_ACTIONS as exactly mind_diary, agenda_update, prioritize_thread in versioned code.

- [ ] **Step 1: Write failing snapshot and verifier tests**

  Test stable identity for equal action content and source slot, a changed digest for a changed slot, and the exact verifier evidence reference:

  ~~~python
  def test_snapshot_identity_is_stable_for_one_decision_slot(nova_project: Path):
      first = NovaIntentSnapshot.from_submission(
          _diary_suggestion(), source_slot=1234, project_root=nova_project
      )
      second = NovaIntentSnapshot.from_submission(
          _diary_suggestion(), source_slot=1234, project_root=nova_project
      )
      assert first.intent_digest == second.intent_digest
      assert first.proposal_id == second.proposal_id
      assert first.verifier_evidence_ref in first.to_suggestion()["evidence_refs"]
  ~~~

  Reject reflection, ACES, Moltbook, blog_draft, and unknown actions. Prove caller id, proposal_id, tier, capabilities, and evidence_refs cannot downgrade canonical output.

  Test the verifier returns decision verified, mode read_only, and exactly its snapshot evidence ref for a valid snapshot. Test wrong root, digest mismatch, output outside the expected local scope, and fields apply, command, secret, or url. Invalid input returns a non-positive result or InvalidVerifierResult and never writes a file, calls a kernel, or performs network I/O.

- [ ] **Step 2: Run the focused tests to verify they fail**

  ~~~powershell
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q tests/test_nova_swarm_runtime_bridge.py -k "snapshot or verifier"
  ~~~

  Expected: the bridge module is missing.

- [ ] **Step 3: Implement canonical input and verification**

  Store explicit bridge configuration in the generic integrations.nova section:

  ~~~yaml
  integrations:
    nova:
      version: 1
      enabled: false
  ~~~

  An absent project/section is disabled and load_nova_bridge_config performs no initialization. configure_nova_bridge(project_root, enabled=...) is the only write helper. It stores no secret, model endpoint, action allowlist, root override, or YOLO override.

  NovaIntentSnapshot normalises only action, need, title, why, target, payload, expected_outcome, priority, and code-owned source_slot. It calculates SHA-256 over canonical JSON, derives proposal_id as nova- plus the digest, and supplies exactly one reference nova:verifier:<digest>. Fixed expected output scopes are nova_data/mind_diary.jsonl for mind_diary and the Nova entity agenda path for agenda_update/prioritize_thread.

  NovaIntentReadOnlyVerifier validates the resolved project root, static allowlist, digest, expected scope, and sensitive markers. It ignores Builder/Critic evidence rather than copying it, uses no model/tool/action capability, and returns independent VerificationResult evidence with decision verified only for a valid snapshot. Add get_nova_action_spec(action: str) -> NovaActionSpec in nova/swarm_adapter.py and use that existing adapter-owned capability data before NovaSwarmAdapter.translate() forms the trusted ActionProposal.

- [ ] **Step 4: Run adapter and verifier regressions**

  ~~~powershell
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q tests/test_nova_swarm_adapter.py tests/test_nova_swarm_runtime_bridge.py -k "snapshot or verifier or translate or policy"
  ~~~

  Expected: existing root, mutation, policy-order protections and the new independent verifier pass.

- [ ] **Step 5: Commit canonical Nova inputs**

  ~~~powershell
  git add nova/swarm_runtime_bridge.py nova/swarm_adapter.py tests/test_nova_swarm_adapter.py tests/test_nova_swarm_runtime_bridge.py
  git commit -m "feat: add Nova Swarm intent verification"
  ~~~

## Task 5: Implement admission, execution, and exactly-once completion

**Files:**

- Modify: nova/swarm_runtime_bridge.py
- Modify: cli/swarm.py
- Modify: tests/test_nova_swarm_runtime_bridge.py
- Modify: tests/test_swarm_cli.py
- Modify: tests/test_swarm_http.py
- Modify: docs/swarm-core.md

**Interfaces:**

- Add NovaSwarmRuntimeBridge.submit(suggestion, source_slot=...) -> NovaBridgeResult.
- Add NovaPreCompletionHook with hook_id nova-runtime-v1.
- Add nova_execution_options_for_run(project_root, run) -> SwarmExecutionOptions | None.
- Persist integration_namespace, nova_intent_digest, nova_snapshot, nova_mode, proposal_digest, and required_pre_completion_hook in run metadata.

- [ ] **Step 1: Write failing bridge, mode, and recovery tests**

  Use fake Sidekick transport/catalog/slot and an injectable dispatcher. Cover these outcomes:

  1. Disabled returns bridge_disabled without creating .swarm, a run, a dispatch thread, or a model call.
  2. Unsupported actions create only a durable unsupported_action rejection; they create no Swarm run, Cloud request, govern call, or act call.
  3. Equal digest/slot through a new bridge instance returns the original run ID without a second worker.
  4. A different intent is rejected while the first admission is running and while paused.
  5. Standard metadata is reviewed_execution plus 48 calls and reaches six per 24 hours; a true kernel is_yolo_enabled result produces autonomous plus 128 calls with no daily cap.
  6. YOLO still blocks non-allowlisted actions, root mismatch, external work lacking human approval, and any caller attempt to claim YOLO in proposal data.
  7. Positive verifier plus both reviewers produces nova.bridge.action_proposed, policy before govern, exactly one act, nova.bridge.action_result, then run.completed.
  8. Negative/unavailable verifier, review denial, evidence mismatch, snapshot mutation, root mismatch, or Nova denial never calls act and pauses auditably.
  9. Provider pause creates no replacement worker or automatic resume.
  10. A crash after atomic policy claim produces execution_already_claimed on explicit recovery, records nova.bridge.recovery_required, and never replays govern or act.

- [ ] **Step 2: Run the bridge tests to verify they fail**

  ~~~powershell
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q tests/test_nova_swarm_runtime_bridge.py -k "bridge or admission or yolo or recovery"
  ~~~

  Expected: missing runtime bridge, completion hook, and options resolver behavior.

- [ ] **Step 3: Implement the exact bridge sequence**

  NovaSwarmRuntimeBridge.submit must do this in order:

  1. Read configuration only. If disabled, return before creating a store.
  2. Canonicalise source_slot and suggestion. Check static allowlist before ProjectSwarmStore, NovaSwarmAdapter, or Cloud transport. If enabled, persist only a bounded pre-run rejection.
  3. Verify kernel.space_dir, kernel.actions.space_dir, and project root are the same resolved Nova root.
  4. Read mode only through kernel.is_yolo_enabled() and choose (reviewed_execution, 48, 6) or (autonomous, 128, None).
  5. Create NovaSwarmAdapter(kernel, PolicyGate(store), enabled=True), translate the canonical snapshot, calculate exact proposal_digest, and call admit_integration_run atomically.
  6. Persist a new run with required_pre_completion_hook set to nova-runtime-v1. Append nova.bridge.admitted and start one named in-process worker thread only for a newly admitted run. For coalesced, active-limit, quota-limit, or paused outcomes append a bounded event and do not dispatch another worker or retry.

  NovaPreCompletionHook reopens durable state, reconstructs/revalidates the snapshot, reconstructs the proposal, and checks its digest matches the admission. It appends nova.bridge.action_proposed, calls NovaSwarmAdapter.execute_suggestion(), then maps outcomes to PreCompletionResult. Success continues completion. Policy/quorum/approval/root/governance failures pause with a bounded nova_ reason. execution_already_claimed appends nova.bridge.recovery_required and pauses with nova_action_claimed_requires_human_recovery. No method outside NovaSwarmAdapter may call act.

  nova_execution_options_for_run recognises only integration_namespace == nova. It rebuilds verifier/hook from durable metadata, uses the persisted 48/128 limit, and returns blocked_reason=nova_bridge_disabled for an explicitly disabled admitted run. Update cli.swarm.get_swarm_service() to install this resolver, which makes normal sidekick swarm resume and WebUI resume attach the same hook. Status GET/SSE only constructs the service; it must not resolve options, initialize .swarm, or dispatch work.

- [ ] **Step 4: Run bridge, policy, CLI, and HTTP regressions**

  ~~~powershell
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q tests/test_nova_swarm_runtime_bridge.py tests/test_nova_swarm_adapter.py tests/test_swarm_policy.py
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q tests/test_swarm_cli.py tests/test_swarm_http.py tests/test_fastapi_route_bridge.py
  ~~~

  Expected: fake bridge flows, exact policy claims, and read-only status routes all pass.

- [ ] **Step 5: Document and commit the versioned bridge**

  Update docs/swarm-core.md to replace the prepared-disabled section with default-disabled configuration, standard/YOLO limits, static allowlist, Cloud catalog requirement, hook order, no automatic retry after provider pause/action claim, and explicit sidekick swarm resume/recover behavior.

  ~~~powershell
  git add nova/swarm_runtime_bridge.py cli/swarm.py tests/test_nova_swarm_runtime_bridge.py tests/test_swarm_cli.py tests/test_swarm_http.py docs/swarm-core.md
  git commit -m "feat: route Nova intents through Swarm"
  ~~~

## Task 8: Gate Task 6 with the mandatory Nova bridge behavior matrix

**Files:**

- Modify: tests/test_nova_swarm_runtime_bridge.py
- Modify only if a focused test exposes a bridge defect: nova/swarm_runtime_bridge.py
- Modify: tests/test_swarm_cli.py
- Modify: tests/test_swarm_http.py

**Purpose:**

This is a verification remediation created before the live seam because Task 5's
initial implementation did not include the approved end-to-end bridge matrix.
It uses only a disposable Nova project, fake transport/catalog/slot, and a fake
kernel; it must not read or modify the live Nova space, start a process, or call
Ollama.

- [ ] **Step 1: Build reusable fake-host fixtures**

  Create a fake kernel, fake action recorder, verified fake transport/catalog,
  dispatcher recorder, and explicit host-context attachment helper.  A test must
  drive `NovaSwarmRuntimeBridge.submit()` and the real Swarm engine/hook rather
  than calling isolated adapter/policy methods directly.

- [ ] **Step 2: Cover every approved bridge outcome**

  Add executable tests for all of these outcomes:

  1. Disabled reads configuration only: no `.swarm`, run, worker, or model call.
  2. Unsupported action records one bounded rejection and performs no run,
     Cloud, govern, act, or worker call.
  3. Equal digest/slot submitted through a new bridge instance coalesces to one
     run and one worker.
  4. Different intent is rejected while the first run is `running` and while it
     is `paused`, with no replacement worker.
  5. Standard runs persist `reviewed_execution`/48 and enforce six rolling
     admissions; literal kernel YOLO persists `autonomous`/128 and has no daily
     quota while still retaining the one-active limit.
  6. YOLO rejects non-allowlisted actions, root mismatch, external/human-gated
     work, and a caller-supplied YOLO flag.
  7. A verified fake workflow reaches `nova.bridge.action_proposed`, policy
     before govern, exactly one act, `nova.bridge.action_result`, and then
     `run.completed`.
  8. Negative/unavailable verifier, review denial, evidence/digest/snapshot/root
     mismatch, and Nova denial pause auditably with zero act calls.
  9. Provider pause creates no replacement worker and no automatic resume.
  10. A post-claim crash has one recovery-required event and explicit recovery
      never replays govern or act.

  Also cover `attach_admitted_run`: only a paused, matching durable run may
  attach; running/completed/root/proposal mismatch must preserve an existing
  binding.  Cover same-process CLI resume with attached context and a fresh
  process/no-context resume blocking before model dispatch.  Confirm HTTP status
  and SSE remain read-only (no context resolution, project initialization, or
  worker dispatch).

- [ ] **Step 3: Run focused and full non-live gates**

  ~~~powershell
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q tests/test_nova_swarm_runtime_bridge.py tests/test_nova_swarm_adapter.py tests/test_swarm_policy.py
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q tests/test_swarm_cli.py tests/test_swarm_http.py tests/test_fastapi_route_bridge.py
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m ruff check nova\swarm_runtime_bridge.py tests\test_nova_swarm_runtime_bridge.py tests\test_swarm_cli.py tests\test_swarm_http.py
  git diff --check
  ~~~

- [ ] **Step 4: Commit the executable behavior gate**

  ~~~powershell
  git add tests/test_nova_swarm_runtime_bridge.py tests/test_swarm_cli.py tests/test_swarm_http.py nova/swarm_runtime_bridge.py
  git commit -m "test: cover Nova Swarm runtime bridge behavior"
  ~~~

## Task 9: Add the versioned live-entry API and harden the deployment contract

**Files:**

- Modify: nova/swarm_runtime_bridge.py
- Modify: tests/test_nova_swarm_runtime_bridge.py
- Modify: tests/test_nova_swarm_live_seam.py

**Purpose:**

Task 6's preflight found that the planned live seam calls a missing
`submit_nova_intent()` API.  This remediation must be complete before any live
file changes.  It remains entirely versioned/non-live.

- [ ] **Step 1: Write failing public-entry and AST-contract tests**

  Test `submit_nova_intent(kernel, proposal, source_slot)` with a disposable
  trusted fake kernel.  It must create the host-owned runtime context from the
  code-owned kernel roots, delegate only through `NovaSwarmRuntimeBridge`, and
  return a plain JSON-safe mapping with exactly bounded `run_id`, `accepted`,
  `executed`, `reason`, and `decision.policy` fields.  Disabled, unsupported,
  coalesced, and admitted results must never leak raw exception/policy text.

  Strengthen the gated AST test so it skips unless *both*
  `NOVA_LIVE_BRIDGE_CONTRACT=1` and `NOVA_LIVE_SPACE` are supplied; requires
  exactly one effective top-level `submit_intent_proposal`; requires an actual
  call to `submit_nova_intent`; and detects direct or simple alias calls to
  `govern`/`act` in that function.

- [ ] **Step 2: Implement the bounded entry point**

  `submit_nova_intent` must derive its project roots only from the supplied
  code-owned kernel and its action registry, require all resolved roots to
  agree, create the internal host trust context without importing WebUI/config
  modules or accepting a caller root/resolver, then submit through the existing
  bridge.  Root disagreement returns a bounded rejected mapping before a store,
  worker, model, govern, or act call.  It must not directly call `govern` or
  `act`.

- [ ] **Step 3: Run the non-live entry gate**

  ~~~powershell
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q tests/test_nova_swarm_runtime_bridge.py tests/test_nova_swarm_live_seam.py
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m ruff check nova\swarm_runtime_bridge.py tests\test_nova_swarm_runtime_bridge.py tests\test_nova_swarm_live_seam.py
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m compileall -q nova
  git diff --check
  ~~~

- [ ] **Step 4: Commit the versioned deployment API**

  ~~~powershell
  git add nova/swarm_runtime_bridge.py tests/test_nova_swarm_runtime_bridge.py tests/test_nova_swarm_live_seam.py
  git commit -m "feat: add Nova Swarm live entry point"
  ~~~

## Task 6: Verify and stage the live Nova seam

**Files:**

- Create: tests/test_nova_swarm_live_seam.py
- Modify only in final rollout: C:\sidekick\home\spaces\nova\nova_mind.py

**Interfaces:**

- The live submit_intent_proposal delegates to submit_nova_intent(EntityKernel(), proposal, source_slot=...).
- It returns a plain result mapping with run_id, accepted, executed, reason, and bounded decision.policy data for Nova Mind logging.

- [ ] **Step 1: Add a non-mutating live-seam contract test**

  Gate tests/test_nova_swarm_live_seam.py behind NOVA_LIVE_BRIDGE_CONTRACT=1. It reads NOVA_LIVE_SPACE source, parses AST, locates submit_intent_proposal, and asserts:

  ~~~python
  source = ast.unparse(function_node)
  assert "submit_nova_intent" in source
  assert ".act(" not in source
  assert ".govern(" not in source
  ~~~

  Also call py_compile.compile(..., doraise=True). The test must not import the live daemon, start a process, use a key, or make a model request.

- [ ] **Step 2: Run the complete non-live gate**

  ~~~powershell
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q tests/test_swarm_core.py tests/test_swarm_config.py tests/test_swarm_workflow.py tests/test_swarm_host.py tests/test_swarm_policy.py tests/test_nova_swarm_adapter.py tests/test_nova_swarm_runtime_bridge.py tests/test_swarm_cli.py tests/test_swarm_http.py tests/test_fastapi_route_bridge.py nova/test_entity_kernel.py
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m ruff check swarm_core cli nova tests
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m compileall -q swarm_core cli nova
  git diff --check
  ~~~

  Expected: all commands exit zero. Then run the full pytest -q suite and record unrelated baseline failures separately rather than weakening bridge tests.

- [ ] **Step 3: Commit the deployment contract**

  ~~~powershell
  git add tests/test_nova_swarm_live_seam.py
  git commit -m "test: verify the Nova bridge deployment seam"
  ~~~

- [ ] **Step 4: Exercise an enabled disposable Nova space**

  In a disposable test root, initialize Swarm, configure_nova_bridge(enabled=True), persist a fake verified catalog, and submit one diary plus one blocked action. Assert the diary reaches the hook and the blocked action sends no model request. Do not use the live Nova root or a real API key.

- [ ] **Step 5: Patch only the live seam after disposable success**

  Inspect the exact live function and retain an explicitly named recoverable copy. Replace only submit_intent_proposal in C:\sidekick\home\spaces\nova\nova_mind.py with:

  ~~~python
  def submit_intent_proposal(proposal: dict) -> dict:
      """Submit one Nova Mind intent through the versioned Swarm bridge."""
      try:
          from nova.entity_kernel import EntityKernel
      except ImportError:
          from entity_kernel import EntityKernel
      from nova.swarm_runtime_bridge import submit_nova_intent

      return submit_nova_intent(
          EntityKernel(),
          proposal,
          source_slot=int(time.time() // DECIDE_INTERVAL),
      )
  ~~~

  Update the adjacent print path to report Swarm run <run_id> admitted when run_id exists. Do not leave a direct govern or act fallback.

  Initialize and enable only the live Nova project:

  ~~~powershell
  & C:\sidekick\sidekick\.venv\Scripts\sidekick.exe swarm --project C:\sidekick\home\spaces\nova init
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -c "from pathlib import Path; from nova.swarm_runtime_bridge import configure_nova_bridge; configure_nova_bridge(Path(r'C:\sidekick\home\spaces\nova'), enabled=True)"
  ~~~

  Then run:

  ~~~powershell
  $env:NOVA_LIVE_BRIDGE_CONTRACT = '1'
  $env:NOVA_LIVE_SPACE = 'C:\sidekick\home\spaces\nova'
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q tests/test_nova_swarm_live_seam.py
  Remove-Item Env:\NOVA_LIVE_BRIDGE_CONTRACT
  Remove-Item Env:\NOVA_LIVE_SPACE
  ~~~

- [ ] **Step 6: Run one explicit Cloud canary and restart Nova only**

  Explicitly refresh the live catalog:

  ~~~powershell
  & C:\sidekick\sidekick\.venv\Scripts\sidekick.exe swarm --project C:\sidekick\home\spaces\nova models refresh
  ~~~

  Submit exactly one mind_diary intent and use the returned run ID with sidekick swarm --project C:\sidekick\home\spaces\nova status. Confirm one active admission, verifier plus two reviewer checkpoints, one nova.bridge.action_proposed, one nova.bridge.action_result, and no secret/raw response event content. If no verified catalog/key exists, the correct outcome is an auditable pause; do not add a fallback provider.

  After canary success, stop only the verified Nova Mind process tree by exact PID and start/check it with:

  ~~~powershell
  & C:\sidekick\sidekick\.venv\Scripts\python.exe C:\sidekick\home\spaces\nova\nova_mind_watchdog.py --ensure
  & C:\sidekick\sidekick\.venv\Scripts\python.exe C:\sidekick\home\spaces\nova\nova_mind_watchdog.py --status
  ~~~

  Confirm a fresh nova-site\nova-status.json reports a running mind. Never terminate all Python processes and never restart Sidekick or WebUI.

- [ ] **Step 7: Roll back safely on seam or canary failure**

  Set only bridge configuration back to enabled=false, restore the retained live source copy, and restart only the verified Nova process if it was restarted. Preserve .swarm/runtime and all audit rows; do not delete records or clear action claims.

## Task 7: Review, verify, and integrate locally

**Files:**

- Modify only files already listed.

**Interfaces:**

- No new interfaces; this task proves the prior ones satisfy the approved design together.

- [ ] **Step 1: Review required coverage**

  Confirm each design point has implementation and a regression: one active run; 6/24h plus 48 standard; unlimited-daily plus 128 YOLO; static allowlist; pre-Cloud rejection; Cloud-only catalog/slots; local verifier plus two reviewers; immutable digest and policy claim; Nova govern then act; no automatic retry; and only a scoped Nova process change.

- [ ] **Step 2: Run final verification**

  ~~~powershell
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m ruff check swarm_core cli nova tests
  git diff --check
  git status --short
  ~~~

  Expected: bridge tests, lint, and diff checks pass. Preserve and report any unrelated pre-existing full-suite failure without skipping or weakening bridge assertions.

- [ ] **Step 3: Merge the reviewed versioned bridge locally**

  After a clean branch and approved local integration, merge only bridge commits into local master. Verify:

  ~~~powershell
  git -C C:\sidekick\sidekick merge --ff-only feat/nova-swarm-runtime-bridge
  git -C C:\sidekick\sidekick merge-base --is-ancestor feat/nova-swarm-runtime-bridge master
  git -C C:\sidekick\sidekick log --oneline master -8
  git -C C:\sidekick\sidekick status --short
  ~~~

  Do not fetch, pull, push, release, or alter unrelated untracked files as part of this local merge.

## Task 10: Resolve final review security boundaries before local integration

**Files:**

- Modify as needed: `cli/swarm_host.py`, `runtime/auxiliary_client.py`,
  `nova/actions.py`, `nova/swarm_adapter.py`, `nova/swarm_runtime_bridge.py`,
  `swarm_core/workflow.py`, and their focused tests.
- Do not modify: `C:\sidekick\home\spaces\nova\*`, live processes, provider
  configuration, or any network state.

**Purpose:**

The final whole-branch review found three merge-blocking security defects.  All
three must be closed in one versioned, non-live remediation before the branch
can merge or the live seam can change.

- [ ] **Step 1: Bind every Swarm request to the canonical Cloud client**

  Ensure that both reviewed and YOLO Swarm requests cannot reuse an existing
  auxiliary `ollama-cloud` client created for a local or third-party endpoint.
  Bind the call and cache identity to the exact canonical
  `https://ollama.com/v1` endpoint, or prove the cached client's actual
  endpoint immediately before dispatch and reject/evict noncanonical clients.
  Add a regression that seeds a noncanonical auxiliary cache entry, restores
  the canonical environment, and proves no Swarm prompt uses the stale client.

- [ ] **Step 2: Contain automatic Nova output effects at the real filesystem boundary**

  The allowlisted `mind_diary`, `agenda_update`, and `prioritize_thread`
  handlers must fail closed before an external effect whenever an output file
  or any path component is a symlink, junction, reparse point, or resolves
  outside the code-owned Nova root.  Validate at the final write/open boundary,
  not only when translating/verifying the intent; do not classify an escaped
  target as local/reversible.  Cover a symlinked diary file, a parent
  directory symlink/junction when supported, and a deterministic component-swap
  or final-boundary regression without relying on the live Nova space.

- [ ] **Step 3: Make reviewed reviewers authorize the exact canonical action**

  Provide the workflow's two independent reviewers an immutable, bounded,
  canonical authorization context containing the action, target, payload,
  expected output scope, intent digest, and proposal digest.  Preserve that
  context durably, bind each reviewer checkpoint explicitly to the same digest,
  and require that binding in the Nova review quorum before the pre-completion
  hook may execute `govern()` then `act()`.  The context must not make public
  status/SSE endpoints mutate state or reveal raw model responses.  Add a
  regression using a benign title with a materially different allowlisted
  payload and prove execution pauses unless both reviewers reviewed and
  approved the exact canonical context.

- [ ] **Step 4: Tighten the action-result boundary**

  Treat an action as executed only when its `executed` field is literally
  boolean `True`; truthy non-booleans must fail closed.  Add a focused
  regression.

- [ ] **Step 5: Verify and commit the remediation**

  ~~~powershell
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m pytest -q tests/test_swarm_host.py tests/test_nova_swarm_adapter.py tests/test_nova_swarm_runtime_bridge.py tests/test_swarm_workflow.py tests/test_swarm_policy.py
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m ruff check cli\swarm_host.py runtime\auxiliary_client.py nova\actions.py nova\swarm_adapter.py nova\swarm_runtime_bridge.py swarm_core\workflow.py tests
  & C:\sidekick\sidekick\.venv\Scripts\python.exe -m compileall -q cli runtime nova swarm_core
  git diff --check
  ~~~

  Commit only scoped versioned files with:

  ~~~powershell
  git commit -m "fix: harden Swarm runtime security boundaries"
  ~~~
