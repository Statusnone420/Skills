# Doctor playbook

## Diagnose

Classify the explicit goal before general diagnosis: feature/change goal uses `update` via changed-path names/path-limited diff (no audit); cleanup, migration, and reader goals use selected evidence; bare `doctor` is read-only; same-message fix/apply is zero mutation. Bare `doctor` must report every compact checker finding in the declared scan scope. Goal text narrows diagnosis, but do not suppress related blockers required to complete that goal; report the excluded scope. A scoped result must never be described as repository-exhaustive.

Public explicit scope syntax is `$docs doctor --scope <repository-relative-directory> [goal text]`. For a missing or uncertain map, the first repository-evidence action is `<python> <installed-skill>/scripts/check.py <repository-root> --json --agent --init-discovery`, or the equivalent read-only API `discover_init_scope(root, explicit_scope=None)`, not both. Append `--scope <repository-relative-directory>` only for explicit user scope. This is bounded metadata-first discovery: it uses name/path metadata and does not blindly read repository-wide content. An explicit scope is honored as a confinement boundary, never permission to ingest every file.

Respect `choice-required`, truncation, any physical limit, and `requires_user_action`; stop for the requested choice, narrower scope, or explicit continuation. Selection of a bounded scope happens before content opens. Report `requested_scope`, `normalized_scope`, `selected_scope`, `inspected_scope`, exclusions, prunes, configured and observed limits, `content_batch`, unopened routes, and `content_reads`. Report selection reason, applied boundaries, and user action as well. A discovery result is scope-limited and is never repository-exhaustive.

When a valid map and its scope are already evidenced, the first mapped repository-evidence action is a direct read of the repository-relative map evidenced inside that selected scope; the conventional root-scope map is `docs/README.md`. Every pre-check and post-check content path stays inside the selected scope. Resolve relative links from the linking file's directory. With a map, forbid name-only inventories (`Get-ChildItem`, `ls`, `rg --files`, `git ls-files`); do not use repository-wide search. Report a missing target; do not list its parent. Missing map uses the discovery route above; do not recursively inventory. Measure selected map/current-state bytes against the provisional optimization target; it is not a maximum or health gate.

Consult the exact `map`/`check` entry in `commands.md`; after scope selection, run `scripts/check.py` exactly once as `<python> <installed-skill>/scripts/check.py <repository-root> --json --agent --map <repository-relative-map> --scope <selected-scope>`. `--hot` contains only existing current-state files selected from map evidence, never the map or a missing path; omit `--hot` when none exists. Never use repo-local checker, --help, bare-script invocation, availability preflight, or retry; consume its output. The checker may recursively analyze documentation inside the selected scope and return compact findings, so Doctor can present all findings without opening each repository file individually. Print `health.meter` once as a standalone plain Markdown line from checker evidence: exactly 20 literal cells, no code fence or backticks. No repository read is permitted after the checker except Doctor's bounded post-check evidence. Missing args/capability: report; do not run it; continue bounded conceptually.

Separate facts, inference, and candidates. Report actual loaded and unloaded material, every loaded path, and failed/preflight attempts. Exhaustive compact detection is separate from bounded semantic evidence loading. Post-check content opens remain bounded to at most four files and are used only for root-cause verification, priority, duplicate merging, and treatment design. A finding needing no content open consumes no opening. There is no compact-finding or treatment-count cap. Report every compact checker finding in the declared scan scope and group or merge duplicates into one or more correct evidence-backed treatments without suppressing individual finding coverage. Show which treatment covers each finding. Unverified semantic suspicions remain unresolved rather than becoming facts. Without explicit scope, keep untracked/unrelated material cold. Direct commands remain independently usable.

## Treatment manifest

Return a plain-English diagnosis with one or more correct evidence-backed treatments. Healthy repository: report health only when structure is clean and Trust is verified; no-memory: `init` preview with exact proposed tree; no empty Diátaxis folders. The user chooses whether to authorize the recommended architecture. There is no artificial treatment-size ceiling.

Every treatment uses this manifest shape; the rendered example uses the content-derived ID required by the public contract:

```text
ID: DOC-7F2A91C4
Fingerprint: sha256:<canonical-finding-json>
Priority: P0 | P1 | P2
Status: Proposed
Outcome:
Why this is the correct repair:
Evidence:
Scope:
Coverage:
Exact files:
Responsible command:
Tree/hot-path impact:
Dispositions:
Risk:
Verification:
Isolation:
Approval:
```

In an actual manifest, `Fingerprint:` contains the full 64-hex SHA-256 fingerprint, not a shortened digest or the placeholder. `Priority:` is exactly P0, P1, or P2; `Status:` is `Proposed`. `Coverage:` lists every compact finding covered by the treatment, including merged duplicates. `Tree/hot-path impact:` reports measured bytes with provenance; bytes with provenance are telemetry only and never create a finding, score pressure, or deletion pressure. Related child work uses `DOC-7F2A91C4.1` and remains attached to its parent.

Deterministic checker findings and verified semantic findings both derive their fingerprint on every run from normalized stable semantic identity. Exclude volatile locators/metadata, prose evidence, status/priority, and absolute checkout paths. Derive `DOC-*` from that fingerprint read-only with no reservation write. Start with the shortest collision-free eight-hex prefix; a short-prefix collision extends the displayed ID before presentation. Line movement preserves identity; a semantic identity change changes it. Later approval revalidates the exact ID and full fingerprint; changed evidence cannot silently retarget an old ID.

For large moves or deletions, `Dispositions:` uses the Task 5 `MIGRATED`, `DEDUPLICATED`, `ARCHIVED`, and `DISCARDED` format. Show disposition counts first, including counts by disposition and item kind, then a complete file/section appendix in which every removed file and every unique removed section appears exactly once. Bind every destructive item to its current-byte digest and item-specific recovery boundary. The complete exact disposition manifest, verified recovery boundary, and explicit approval are required before any mutation. Git, no-Git archive-first behavior, hard-deletion risk acceptance, rollback, and failed-verification rules remain those in `commands.md` and `isolation.md`.

`Isolation:` names verified selected root, exact destination/boundary and branch, or exact current-workspace risk/draft-only state. Before approval the manifest exists only in the response; none when healthy.

## Approval and isolation

Exact approval syntax for one treatment is `Approve $docs treatment DOC-7F2A91C4 fingerprint sha256:<64-hex-fingerprint>`. Exact approval syntax for one or many treatments is explicit; for many use `Approve $docs treatments DOC-7F2A91C4 fingerprint sha256:<64-hex-fingerprint>; DOC-A1B2C3D4 fingerprint sha256:<64-hex-fingerprint>`. Substitute every emitted exact ID and full digest. Before any write, revalidate both every exact ID and its full fingerprint against fresh canonical evidence. Declined, ambiguous, missing, or non-exact IDs or fingerprints produce zero writes and a changed fingerprint requires a new proposal. A destructive approval must also name the exact disposition-manifest hash shown in `Dispositions:`.

For a possible Git write, one bounded identity/status action binds to host/user-selected repository root (`git -C <selected-root>` or equivalent). Normalize paths; normalized `--show-toplevel` exactly equals that selected root. Reject parent-repository discovery. Check the destination's nearest existing ancestor; a different Git top-level rejects it before approval. No isolation creation before approval.

With worktree isolation, propose exact destination/boundary and branch outside selected/unrelated Git worktrees; reject symlink/junction/reparse chains before approval. If unprovable, ask for safe boundary. Current-workspace risk only if Git/safe isolation unavailable; require explicit acceptance.

When Git/isolation is unavailable, state this combined gate in the initial diagnosis: later writes require exact selected IDs plus explicit current-workspace risk acceptance; full-fingerprint revalidation is also mandatory, and ordinary approval is insufficient. Name unrelated status and rollback limits. Without write capability, treatments remain draft-only. Persist a plan only after approval for multi-step, structural, review-heavy, or resumable work; follow repository convention. If none exists, preview the proposed path. A plan-only request authorizes only that plan file; simple repairs need no plan file.

## Execute approved treatment

Route exact Doctor-approved IDs and full fingerprints through `write`, `update`, `fix`, `migrate`, `cleanup`, or approved `init`; do not broaden scope or load unrelated dirty contents. Feedback may refine only the accepted treatment scope; new structural or unrelated work returns to preview and approval.

## Verify and review

Run the smallest relevant verification plus one documentation check. Report failures, partial work, or deviations. Show resulting tree, hot-path usage, complete affected-file list, and diff. Preserve unrelated changes; stop before commit/push.

## Close repository memory

Promote verified truth backed by code/tests/configuration or confirmed intent. Keep unresolved candidates outside the hot path. Update map/state only for completed route/truth changes; never add treatment IDs, process logs, transient status, or plan prose.

The diagnosing Doctor response is read-only and performs zero lifecycle writes. After a separate exact approval, the responsible mutating command must freshly revalidate every ID/fingerprint, starting digest, disposition, protected-surface effect, selected scope, and local-only boundary. If any evidence changed, return to a new proposal instead of retargeting the old approval.

Close only after the approved documentation result and protected public entrances pass their promised verification. Then use one compare-before-write transaction with same-directory reserved temporaries, verified bytes and schemas, atomic replacement, and the success event last. Failed verification writes no operational closeout. Transaction failure rolls back exact control bytes; an orphan temporary or torn state/findings/manifest/local-map/event combination is a P0 `state-conflict`, never a successful baseline.

For a state conflict, use canonical Markdown, code, configuration, tests, and confirmed intent as evidence. Recompute deterministic findings, preserve non-conflicting protected-intent and verified-source routes, and show a deterministic zero-write recovery preview plus discarded-conflict evidence. Only exact approval of that preview may apply recovery through the same lifecycle transaction.

When present, `.diataxis/local-map.json` remains local-only and must be mechanically verified as ignored before creation or update. Doctor may validate its bounded routing metadata and verified content hashes without copying private routes into shared state or events. A missing local map is reported as unavailable; it neither permits an absence claim nor changes shared health.

## Capability limits

Report unavailable Git isolation/write/execution/verification/rollback and missing capabilities. Vendor-neutral, network-free operation has no required database, no required embeddings, no required daemon, no background process, and no new dependency. Stop if clean or verification fails; do not broaden edits or claim success. Never commit/push/release/publish or modify outside selected treatments.
