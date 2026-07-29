# Nova Space Supervision: disabled-by-default rollout checklist

This release adds the governed supervisor, but deliberately does **not** bind a
production scheduler, dispatcher, model transport, GitHub/deploy worker, or
Telegram sender.  It does not modify `C:\sidekick\home\spaces\nova`.

## Release invariant

The canonical `nova` Space is the one visible Nova entity.  A target Space is
eligible only when its own validated `nova_management` record has both
`yolo: true` and `enrolled: true`.  The global Nova YOLO switch and session
YOLO state are not target-Space authority.

Until a separate, reviewed activation change supplies an injected host
dispatcher, an eligible Space still cannot autonomously start work.  Merely
opening a page, reading presence/status, or changing a Space setting must not
start a model, create a run, schedule a heartbeat, or initialize Nova state.

## Preconditions before any activation

1. Run the fake-transport, gateway, supervisor, pure-GET, and browser suites
   in a disposable Sidekick home.  Record the exact commit and results.
2. Confirm the host remains unbound: no supervision heartbeat registration,
   no injected production dispatcher, no Telegram sender, and no GitHub or
   deployment credential provider.
3. Choose one non-Nova test Space with a recoverable project root.  Confirm
   the root is a trusted workspace and that the Space ID/root fingerprint shown
   by Space Settings match the intended project.
4. Enable YOLO and Nova management only through the dedicated confirmation UI
   or API.  Do not patch `space.yaml`, generic Space config, global Nova YOLO,
   or environment flags to simulate enrollment.
5. Use only fake GitHub, deployment, and Telegram adapters first.  Inspect the
   supervisor audit and notification claim data for allowlisted codes only;
   raw secrets, URLs, model text, and raw run IDs must be absent.

## Separate activation gate

A future production change requires explicit approval and all of the following
review artifacts before binding anything live:

- an injected, host-owned dispatcher that receives only canonical root and run
  ID, with the supervisor capability retained privately;
- a fake-composition test covering event ingestion, the heartbeat, exactly-once
  admission, host-bound worktree/GitHub/deploy routing, and redacted blocker
  notification claims;
- an operator-selected private Telegram chat and a sender that cannot send
  free-form model content;
- separately approved, least-privilege GitHub/deploy credentials kept inside
  their target workers, never in model context or Nova-readable files;
- one explicitly enrolled test Space, followed by a human review of audit,
  verifier, and test evidence before any broader rollout.

No real GitHub push, release, deploy, Telegram credential, scheduler binding,
or Nova process restart is authorized by this checklist.

## Stop and rollback

Disable enrollment/YOLO, change the root, or delete the Space only through the
governed Space lifecycle.  The supervisor must first pause the ledger-owned
child and confirm its durable child state.  If that pause cannot be confirmed,
the lifecycle write must fail closed; fix the storage/worker problem rather
than bypassing governance.  `abandoned` remains auditable and terminal, never
silently resumes, while freeing the global active-run slot for a distinct
intent.
