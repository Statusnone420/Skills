# Approved treatment isolation

Load only after exact Doctor treatment IDs are approved. Revalidate selected IDs, evidence, scope, worktree, and capabilities before any write.

Prefer a safe worktree; use a feature branch only after verifying it excludes unrelated dirty changes.

- Resolve the approved selected repository root. Bind every Git command to it (`git -C <repository-root>` or host equivalent); never rely on ambient CWD or parent-repository discovery.
- Before creation, normalize both paths and require `--show-toplevel` equals the intended root; capture HEAD and the common Git directory.
- Worktree post-create: normalized `git -C <new-path> rev-parse --show-toplevel` must equal the exact approved worktree destination. Also require it shares the expected common Git directory and HEAD, lies within the user-approved boundary (the exact approved boundary), and status is clean.
- Branch fallback uses the same root binding and identity proof; verify the exact approved branch name before writing.
- Any mismatch: stop with no copy, import, or write. Never import dirty or untracked files without separate exact authorization.

Without proved isolation, use only the Doctor manifest's exact current-workspace risk gate or remain draft-only.
