---
name: verification-before-completion
description: Use when about to claim a change is complete, fixed, passing, or ready to integrate.
---

# Verification Before Completion

Claims require current evidence.

1. Run the focused test that drove the change.
2. Run the relevant regression suite and a runtime-visible check when the task affects UI, service, or live behavior.
3. Inspect the final diff and status for accidental scope expansion.
4. Report commands, outcomes, and any pre-existing failures separately.
5. Only then state that the requested work is complete.

Do not infer success from code inspection, a previous test run, or a green unrelated suite.
