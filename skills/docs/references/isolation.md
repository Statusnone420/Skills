# Approved treatment isolation

Load only after exact Doctor treatment IDs and full fingerprints are approved. Revalidate selected IDs, full fingerprints, evidence, scope, worktree, and capabilities before any write. A changed fingerprint invalidates the approval and cannot retarget it.

Prefer a safe worktree; use a feature branch only after verifying it excludes unrelated dirty changes.

- Resolve the approved selected repository root. Bind every Git command to it (`git -C <repository-root>` or host equivalent); never rely on ambient CWD or parent-repository discovery.
- Before `git worktree add`, reject any symlink, junction, or reparse point in the existing destination/boundary chain; stop and re-preview a physical path for separate approval.
- Before creation, inspect the proposed destination's nearest existing ancestor using metadata only. If it resolves inside a different Git worktree, stop and re-preview outside it; never dirty another repository.
- Before creation, normalize both paths and require `--show-toplevel` equals the intended root; capture HEAD and the common Git directory.
- Worktree post-create: normalized `git -C <new-path> rev-parse --show-toplevel` must equal the exact approved worktree destination. Also require it shares the expected common Git directory and HEAD, lies within the user-approved boundary (the exact approved boundary), and status is clean.
- Branch fallback uses the same root binding and identity proof; verify the exact approved branch name before writing.
- Any mismatch: stop with no copy, import, or write. Never import dirty or untracked files without separate exact authorization.
- In Git repositories, any approved removal is limited to its exact approved disposition set and requires a verified rollback boundary at the selected root. Require disposition counts first and the complete file/section appendix before explicit approval. For each destructive item, bind the current-byte SHA-256 digest to either a matching committed blob ID and commit ID whose bytes match, or a confined archive path and SHA-256 digest verified before destruction. Git presence alone is not recovery proof for dirty, untracked, ignored, or current-only bytes.
- In no-Git repositories, convert proposed `DISCARDED` items to `ARCHIVED` before the canonical manifest, hash, preview, or approval. Hard deletion requires the exact risk acceptance, then a new preview and manifest, then another later exact approval and revalidation.

Without proved isolation, use only the Doctor manifest's exact current-workspace risk gate or remain draft-only.

During verification, capture the underlying process exit code and relevant output explicitly; never substitute a wrapper or tool-call status. If verification fails after a destructive change, use the proved source to roll back every destructive item, re-run the previewed verification, and report any remaining partial state rather than claiming success.
