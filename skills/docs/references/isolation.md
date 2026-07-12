# Approved treatment isolation

Load only after exact treatment IDs are approved and before any write. Revalidate selected IDs, evidence, scope, worktree, and capabilities before any write.

Prefer a safe worktree; use a feature branch only after verifying it excludes unrelated dirty changes.

- Resolve the intended repository root. Bind every Git command to it (`git -C <repository-root>` or host equivalent); never rely on ambient CWD or parent-repository discovery.
- Before creation, require `--show-toplevel` equals the intended root; capture HEAD and the common Git directory.
- After creation and before writes, require the new isolation shares the expected common Git directory and HEAD, lies within the user-approved boundary, and status is clean.
- Any mismatch: stop with no copy, import, or write. Never import dirty or untracked files without separate exact authorization.

Branch fallback uses the same root binding and identity proof. Without a proved isolation, use only the Doctor manifest's exact current-workspace risk gate or remain draft-only.
