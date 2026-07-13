# Doctor playbook

## Diagnose

Classify the explicit goal before general diagnosis: feature/change goal uses `update` via changed-path names/path-limited diff (no audit); cleanup, migration, and reader goals use selected evidence; bare `doctor` is bounded health; same-message fix/apply is zero mutation.

The first repository-evidence action is a direct read of `docs/README.md`. Resolve relative links from the linking file's directory. With a map, forbid name-only inventories (`Get-ChildItem`, `ls`, `rg --files`, `git ls-files`); do not use repository-wide search. Report missing target; do not list its parent. Missing map activates the bounded conventional fallback; do not recursively inventory. 16,384 bytes max.

Consult the exact `map`/`check` entry in `commands.md`; run `scripts/check.py` exactly once as `<python> <installed-skill>/scripts/check.py <repository-root> --json --agent --map <repository-relative-map>`. `--hot` contains only existing current-state files selected from map evidence, never the map or a missing path; omit `--hot` when none exists. Never use repo-local checker, --help, bare-script invocation, availability preflight, or retry; consume its output. Print `health.meter` once as a standalone plain Markdown line from checker evidence: exactly 20 literal cells, no code fence or backticks. No repository read is permitted after the checker except Doctor's bounded post-check evidence. Missing args/capability: report; do not run it; continue bounded conceptually.

Separate facts, inference, and candidates. Report actual loaded and unloaded material, every loaded path, and failed/preflight attempts. A post-check evidence group is one finding plus up to two directly linked/paired corroboration files; explicit goal: at most one goal-relevant group; bare `doctor`: at most two highest-priority actionable groups; total post-check opens: at most four files. A finding needing no read consumes no opening. Report all other checker diagnostics unresolved and unopened. Without explicit scope, keep untracked/unrelated material cold. Direct commands remain independently usable.

## Treatment manifest

Return a plain-English diagnosis with stable treatment IDs and minimum sufficient treatment. Healthy repository: report health; no-memory: `init` preview with exact proposed tree; no empty Diátaxis folders. Every item uses `ID:`, `Outcome:`, `Evidence:`, `Exact files:`, `Responsible command:`, `Tree/hot-path impact:`, `Risk:`, `Verification:`, `Isolation:`, `Approval:`. `Isolation:` names verified selected root, exact destination/boundary and branch, or exact current-workspace risk/draft-only state. Before approval the manifest exists only in the response; none when healthy.

## Approval and isolation

Later approval selects exact IDs; declined, ambiguous, missing, or non-exact IDs produce zero writes. For a possible Git write, one bounded identity/status action binds to host/user-selected repository root (`git -c <selected-root>` or equivalent). Normalize paths; normalized `--show-toplevel` exactly equals that selected root. Reject parent-repository discovery. Check the destination's nearest existing ancestor; a different Git top-level rejects it before approval. No isolation creation before approval.

With worktree isolation, propose exact destination/boundary and branch outside selected/unrelated Git worktrees; reject symlink/junction/reparse chains before approval. If unprovable, ask for safe boundary. Current-workspace risk only if Git/safe isolation unavailable; require explicit acceptance.

When Git/isolation is unavailable, state this combined gate in the initial diagnosis: later writes require exact selected IDs plus explicit current-workspace risk acceptance; ordinary approval is insufficient. Name unrelated status and rollback limits. Without write capability, treatments remain draft-only. Persist a plan only after approval for multi-step, structural, review-heavy, or resumable work; follow repository convention. If none exists, preview the proposed path. A plan-only request authorizes only that plan file; simple repairs need no plan file.

## Execute minimum treatment

Route exact Doctor-approved IDs through `write`, `update`, `fix`, `migrate`, `cleanup`, or approved `init`; do not broaden scope or load unrelated dirty contents. Feedback may refine only the accepted treatment scope; new structural or unrelated work returns to preview and approval.

## Verify and review

Run the smallest relevant verification plus one documentation check. Report failures, partial work, or deviations. Show resulting tree, hot-path usage, complete affected-file list, and diff. Preserve unrelated changes; stop before commit/push.

## Close repository memory

Promote verified truth backed by code/tests/configuration or confirmed intent. Keep unresolved candidates outside the hot path. Update map/state only for completed route/truth changes; never add treatment IDs, process logs, transient status, or plan prose.

## Capability limits

Report unavailable Git isolation/write/execution/verification/rollback and missing capabilities. Vendor-neutral, network-free operation has no required database, no required embeddings, no required daemon, no background process, and no new dependency. Stop if clean or verification fails; do not broaden edits or claim success. Never commit/push/release/publish or modify outside selected treatments.
