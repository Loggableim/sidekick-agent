---
name: writing-skills
description: Use when creating, editing, or validating a reusable Sidekick skill package.
---

# Writing Skills

Treat a skill as tested operational guidance.

1. Define a realistic failure or pressure scenario before writing the guidance.
2. Observe the baseline gap and write only the guidance needed to close it.
3. Use valid frontmatter: a hyphenated name and a `Use when...` description.
4. Keep the skill self-contained, searchable, concise, and specific about required gates.
5. Verify discovery through `skills_list` and loading through `skill_view` after bundling.

Do not rely on machine-local paths or tools that are unavailable in Sidekick.
