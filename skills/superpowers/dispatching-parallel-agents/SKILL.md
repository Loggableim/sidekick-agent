---
name: dispatching-parallel-agents
description: Use when two or more independent investigations or implementation tasks can run without shared state.
---

# Dispatching Parallel Agents

Parallel work is for independent, bounded tasks; shared edits require sequencing.

1. Split only at clean boundaries with no overlapping files or decisions.
2. Give every delegate a concrete objective, scope, verification command, and return format.
3. Keep integration, final decisions, and writes to shared files with the primary Sidekick session.
4. Review each result against the task before accepting it.

Do not parallelize a task merely because it is large. Use one agent for coupled changes, debugging chains, or work that depends on another result.
