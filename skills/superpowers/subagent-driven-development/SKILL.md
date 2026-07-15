---
name: subagent-driven-development
description: Use when executing an approved plan whose independent tasks can be delegated within the current Sidekick session.
---

# Subagent-Driven Development

Delegate independent plan items while preserving one integration owner.

1. Give each subagent one isolated task, relevant paths, acceptance criteria, and tests.
2. Keep conflicting edits out of parallel tasks; use separate workspaces where needed.
3. Review the returned diff and verification before starting dependent work.
4. Integrate only after the primary session validates the combined behavior.

Use `dispatching-parallel-agents` to decide whether parallel work is actually safe.
