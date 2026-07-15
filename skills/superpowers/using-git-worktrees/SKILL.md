---
name: using-git-worktrees
description: Use when feature work needs isolation from a dirty workspace or before executing a substantial implementation plan.
---

# Using Git Worktrees

Keep feature work isolated without disturbing existing user changes.

1. Inspect `git status`, branch, and worktree list before creating anything.
2. Choose a sibling or ignored local worktree directory and a descriptive feature branch.
3. Run the relevant baseline checks in that worktree before editing.
4. Never reset, checkout, or clean unrelated changes in the original workspace.
5. Verify exact branch and remote state before any integration action.

If a worktree cannot be created safely, explain the collision and ask for direction.
