# Doctor playbook

## Diagnose

Classify the explicit goal before general diagnosis. Feature/change goal: `update` via changed-path names/path-limited diff; no audit. Cleanup, migration, and reader goals: selected evidence only. Bare `doctor`: bounded health. Same-message fix/apply: zero mutation.

The first repository-evidence action is a direct read of `docs/README.md`. Resolve relative links from the linking file's directory. With a map, forbid name-only inventories (`Get-ChildItem`, `ls`, `rg --files`, `git ls-files`); do not use repository-wide search. Missing link: report it; do not list its parent. Missing map alone activates the bounded conventional fallback; do not recursively inventory. Git status/diff only for dirty/isolation/explicit scope; never unrelated contents. Map/current-state maximum: 16,384 bytes.

Consult the exact `map`/`check` entry in `commands.md`. Run `scripts/check.py` exactly once as `<python> <installed-skill>/scripts/check.py <repository-root> --json --map <repository-relative-map>`. `--hot` contains only existing current-state files selected from map evidence, never the map or a missing path; omit `--hot` when none exists. Never use repo-local checker, --help, bare-script invocation, availability preflight, or retry. Consume its output. Missing args/capability: report; do not run it; continue bounded conceptually.

Separate verified facts, inference, and candidates. Report actual loaded and unloaded material, every loaded path, and failed/preflight attempts. A post-check evidence group is one finding plus up to two directly linked/paired corroboration files. Explicit goal: at most one goal-relevant group; bare `doctor`: at most two highest-priority actionable groups; total post-check opens: at most four files. A finding needing no read consumes no opening. Report all other checker diagnostics unresolved and unopened. Without explicit scope, keep untracked/unrelated material cold. Direct commands remain independently usable.

## Treatment manifest

Return a plain-English diagnosis with stable treatment IDs and the minimum sufficient treatment. Healthy repository: report health, stop. No-memory: `init` preview with the exact proposed tree; no empty Diátaxis folders. Every item uses `ID:`, `Outcome:`, `Evidence:`, `Exact files:`, `Responsible command:`, `Tree/hot-path impact:`, `Risk:`, `Verification:`, `Isolation:`, `Approval:`. `Isolation:` names verified selected root, exact destination/boundary and branch, or exact current-workspace risk/draft-only state. Before approval the manifest exists only in the response; none when healthy.

## Approval and isolation

Later approval selects exact IDs; declined, ambiguous, missing, or non-exact IDs produce zero writes. For a possible Git write, one bounded identity/status action binds to the host/user-selected repository root (`git -C <selected-root>` or equivalent). Normalize paths; the normalized `--show-toplevel` exactly equals that selected root. Reject parent-repository discovery. That action checks the destination's nearest existing ancestor; a different Git top-level rejects it before approval. No isolation creation before approval.

With worktree isolation, propose exact destination/boundary and branch outside selected/unrelated Git worktrees; reject symlink/junction/reparse chains before approval. If unprovable, ask for safe boundary. Current-workspace risk only if Git/safe isolation unavailable; require explicit acceptance.

When Git/isolation is unavailable, state this combined gate in the initial diagnosis: later writes require exact selected IDs plus explicit current-workspace risk acceptance; ordinary approval is insufficient. Name unrelated status and rollback limits. Without write capability, treatments remain draft-only. Persist a plan only after approval for multi-step, structural, review-heavy, or resumable work; follow repository convention. If none exists, preview the proposed path. A plan-only request authorizes only that plan file; simple repairs need no plan file.

## Execute minimum treatment

Route exact Doctor-approved IDs through `write`, `update`, `fix`, `migrate`, `cleanup`, or approved `init`. Do not broaden scope or load unrelated dirty contents. Feedback may refine only the accepted treatment scope; new structural or unrelated work returns to preview and approval.

## Verify and review

Run smallest relevant verification plus one documentation check. Report failures, partial work, or deviations. Show resulting tree, hot-path usage, complete affected-file list, and diff. Preserve unrelated changes; stop before commit/push.

## Close repository memory

Promote only verified truth backed by code/tests/configuration or confirmed intent. Keep unresolved candidates outside hot path. Update map/state only for completed route/truth changes; never add treatment IDs, process logs, transient status, or plan prose.

## Capability limits

Report unavailable Git isolation/write/execution/verification/rollback and missing capabilities. Vendor-neutral/network-free: no required database, no required embeddings, no required daemon, no background process, and no new dependency. Stop if clean or verification fails; do not broaden edits or claim success. Never commit/push/release/publish or modify outside selected treatments.
