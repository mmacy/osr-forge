---
name: worktree-merge-no-stash
description: How to merge origin/main into a worktree branch that has uncommitted new files, without git stash and without a WIP commit in history
metadata:
  type: feedback
---

To take origin/main into a branch with uncommitted work: commit the work as a temporary commit, `git reset --hard` to the branch's base, `git merge origin/main` (a fast-forward), then `git checkout <temp sha> -- <paths>` to restore the files as uncommitted changes, and re-checkout from origin/main any file the temp commit held an older copy of. A `git reset --soft HEAD~1` after a merge commit undoes the merge and stages main's changes as your own, which is wrong.

**Why:** the suite forbids bare `git stash` in shared worktrees, and the final history should hold only real commits plus the merge.

**How to apply:** whenever the coordinator says to merge origin/main mid-task.
