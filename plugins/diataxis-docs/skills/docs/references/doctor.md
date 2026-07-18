# Doctor playbook

## Diagnose

Classify the explicit goal before general diagnosis: feature/change goal uses `update` via changed-path names/path-limited diff (no audit); cleanup, migration, and reader goals use selected evidence; bare `doctor` is read-only; same-message fix/apply is zero mutation. Bare `doctor` retains every compact checker finding in the declared scan scope, reports their count, and groups them into the displayed treatments. Goal text narrows diagnosis, but do not suppress related blockers required to complete that goal; report the excluded scope. A scoped result must never be described as repository-exhaustive.

Public explicit scope syntax is `$docs doctor --scope <repository-relative-directory> [goal text]`; explicit scope is honored as a confinement boundary. Add `--details` immediately after `doctor` for the explicit detailed mode. For a missing or uncertain map without explicit user scope, the first and only repository-evidence action is `<python> <installed-skill>/scripts/check.py <repository-root> --json --agent --doctor-baseline`. The engine owns discovery, provider selection, and baseline authority; do not reconstruct them with separate `--init-discovery` and checker commands. This is bounded metadata-first discovery: it uses name/path metadata and does not blindly read repository-wide content. An explicitly scoped no-map request cannot use the baseline route; report it unmeasured. A later explicit Init request remains separate and uses Init's deterministic entrypoint.

Respect `choice-required`, metadata/scope truncation, any physical limit, and `requires_user_action`; stop for the requested choice, narrower scope, or explicit continuation. A content-batch-only limit does not block the engine's structural scan when scope metadata is complete, untruncated, and needs no user action; it grants no extra semantic reads. Selection of a bounded scope happens before content opens. Retain `requested_scope`, `normalized_scope`, `selected_scope`, `inspected_scope`, exclusions, prunes, configured and observed limits, `content_batch`, unopened routes, and `content_reads` in the bounded evidence receipt. Default output names the selected scope and compact counts; `--details` may show the complete evidence. Report selection reason, applied boundaries, and user action as well. A discovery result is scope-limited and is never repository-exhaustive.

The engine returns exactly one of four zero-write evidence modes after safely selecting one complete bounded scope with no required user action:

- A supported provider produces an authoritative provider measurement. It does not recommend Init and may authorize Doctor treatments from its findings.
- A conventional immediate entry filename produces a provisional `existing-entry-candidate` measurement. The filename is not proof of a maintained map; it has no treatment authority and recommends `$docs map` to verify topology.
- With neither supported provider nor entry candidate, an existing tracked root `README.md` may produce `Provisional structural baseline (root README orientation fallback)`. Root `README.md` is not a maintained documentation map. This is neither an adoption claim nor an overall-health verdict; report its deterministic score and findings, recommend `$docs init`, and emit no treatment card, ID, fingerprint, approval, or Init preview.
- Unsupported provider semantics, incomplete/unsafe discovery, an untracked or missing fallback, or any other failed precondition produces `Doctor baseline unavailable`: no score, treatment authority, or recommendation. Unavailable evidence is never zero.

No provisional candidate or orientation fallback may generate a treatment. Unsupported provider semantics remain unmeasured and do not trigger Init. The baseline never overrides explicit scope, selection-required/truncated/incomplete discovery, unsafe paths, or a provider-root boundary.

When a valid map and its scope are already evidenced, the first mapped repository-evidence action is a direct read of the repository-relative map evidenced inside that selected scope; the conventional root-scope map is `docs/README.md`. Every pre-check and post-check content path stays inside the selected scope. Resolve relative links from the linking file's directory. With a map, forbid name-only inventories (`Get-ChildItem`, `ls`, `rg --files`, `git ls-files`); do not use repository-wide search. Report a missing target; do not list its parent. Missing map uses the discovery route above; do not recursively inventory. Measure selected map/current-state bytes against the provisional optimization target; it is not a maximum or health gate.

Consult the exact `map`/`check` entry in `commands.md`; after scope selection, run `scripts/check.py` exactly once as `<python> <installed-skill>/scripts/check.py <repository-root> --json --agent --map <repository-relative-map> --scope <selected-scope>`. `--hot` contains only existing current-state files selected from map evidence, never the map or a missing path; omit `--hot` when none exists. Never use repo-local checker, --help, bare-script invocation, availability preflight, or retry; consume its output. The checker may recursively analyze documentation inside the selected scope and return compact findings, so Doctor can retain all findings without opening each repository file individually. Print `health.meter` once as a standalone plain Markdown line from checker evidence: exactly 20 literal cells, no code fence or backticks. No repository read is permitted after the checker except Doctor's bounded post-check evidence. Missing args/capability: report; do not run it; continue bounded conceptually.

The checker-selected provider facts are the same deterministic selected-surface evidence consumed by Map, Check, Doctor, Audit, and Init. Preserve the provider facts, including hidden rather than broken or unreachable pages and the provider-root boundary. The deterministic engine is the factual floor, not the model ceiling: label semantic findings and unresolved candidates separately; neither may contradict provider facts, and an unverified candidate may not receive P0, P1, or P2.

Separate facts, inference, and candidates. Preserve actual loaded and unloaded material, the per-path ledger, and failed/preflight attempts in the bounded evidence receipt. Default output gives a compact count and exceptional routes only. Exhaustive compact detection is separate from bounded semantic evidence loading. Post-check content opens remain bounded to at most four files and are used only for root-cause verification, priority, duplicate merging, and treatment design. A finding needing no content open consumes no opening. There is no compact-finding or treatment-count cap. Retain every compact checker finding in the declared scan scope and group or merge duplicates into one or more correct evidence-backed treatments without suppressing individual finding coverage. Show the coverage only in `--details`. Unverified semantic suspicions remain unresolved rather than becoming facts. Without explicit scope, keep untracked/unrelated material cold. Direct commands remain independently usable.

## Consume Init v3 continuity

Valid Init continuity is schema 3 only. Doctor cross-checks canonical manifest bytes and
`manifest_identity`, state `result_corpus`, the event/manifest `corpus_transition`, and the
shared `document_results_digest`. It also rederives the successful event identity, approval
identity, transaction targets, roles, order, starting/control digests, and conditional local-map,
protected-intent, and hard-delete bindings. Exactly one Init event must bind the manifest; later
successful lifecycle events may follow it. Any hidden or incomplete recovery journal, body in a
persisted payload, or mismatch is P0 `state-conflict`.

Recovery diagnosis is bounded and read-only. Reconcile every journal/terminal binding, recorded
parent identity, and live target, then offer exactly cleanup, rollback, or finalize when safe.
Produce the deterministic zero-write JSON preview with exactly:

```text
<python> <installed-skill>/scripts/check.py <repository-root> --doctor-recovery-preview
```

A later apply must repeat exactly:

```text
Approve $docs doctor recovery <transaction-id> with journal <64-hex-or-ABSENT> state <64-hex> action <cleanup|rollback|finalize>
```

Pass that complete approval as one argument to exactly:

```text
<python> <installed-skill>/scripts/check.py <repository-root> --doctor-recovery-apply '<exact-approval-string>'
```

The apply entrypoint must recompute the preview from current recovery evidence and
execute only the freshly recomputed action. It accepts no caller-supplied preview or action. Both modes emit
JSON; approval-required and recovered results return success, while conflicts and failures
return a normalized nonzero status.

Revalidate the journal or terminal evidence, recorded parent identities, and reconciled state
before writing. The successful event is the commit point: an absent event permits approved
rollback, while an exact committed event permits only approved finalization of recovery
artifacts. A cleanup/finalize suffix is never proof. Cleanup is confined through pinned recovery
directories. Doctor requires the exact transaction-local `.gitignore` guard before every recovery
mutation while any recovery artifact remains; a missing, changed, or deleted guard produces zero
target mutations and no success event. Cleanup deletes the body-free terminal only after its final
validation and deletes the guard last. An empty markerless tombstone still requires canonical live
event/state/findings/manifest/corpus validation. Third-state bytes or an identity swap produce zero
recovery writes.

## Default presentation

Default Doctor output is human-first. Show the score receipt and health meter, finding and treatment counts, then one compact treatment card per grouped treatment. Each card contains only its ID, priority, plain outcome, affected count, exact files, and risk. End with one exact copyable approval line covering the presented treatments. Provisional entry-candidate and orientation-fallback results are excluded from treatment generation. The candidate hands off to `$docs map`; the fallback hands off only to a separate `$docs init`.

Do not print full fingerprints outside that approval, coverage-path dumps, a per-file loading ledger, repeated lifecycle fields, complete disposition appendices, or machine evidence by default. The normal result must remain readable when one treatment covers dozens of files. Summarize loaded and unloaded material as a compact count. Ignored or local Markdown remains outside the shared scan: report it only as `excluded and uninspected; no absence claim` when the engine evidences an exclusion. Never say that no ignored/local material exists unless that fact was actually inspected and established.

`$docs doctor --details` is the explicit detailed mode. It may render the bounded evidence receipt and the complete treatment manifest below, including full fingerprints, coverage, dispositions, and the per-path ledger. This is detail on demand, not a second diagnosis or a broader scan.

## Detailed evidence

In this explicit mode, render the bounded evidence receipt's per-path ledger only when it helps the approved treatment or audit trail.

### Treatment manifest

Return a plain-English diagnosis with one or more correct evidence-backed treatments. Healthy repository: report health only when structure is clean and Trust is verified; no-memory: recommend a separate `$docs init` command without producing an Init preview, tree, or approval; no empty Diátaxis folders. The user chooses whether to invoke Init and later whether to authorize its engine-owned proposal. There is no artificial treatment-size ceiling.

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

When Git/isolation is unavailable, state this combined gate in the initial diagnosis: later writes require exact selected IDs plus explicit current-workspace risk acceptance; full-fingerprint revalidation is also mandatory, and ordinary approval is insufficient. Name unrelated status and rollback limits. Without write capability, treatments remain draft-only. Persist a plan only after approval for multi-step, structural, review-heavy, or resumable work; follow repository convention. If none exists, preview the proposed path and exact proposed tree. A plan-only request authorizes only that plan file; simple repairs need no plan file.

## Execute approved treatment

Route exact Doctor-approved IDs and full fingerprints through `write`, `update`, `fix`, `migrate`, `cleanup`, or approved `init`; do not broaden scope or load unrelated dirty contents. For a grouped Doctor treatment, first prepare its engine-owned receipt outside the repository; this remains a repository read-only diagnosis step and emits the exact approval shown in the compact result:

```text
<python> <installed-skill>/scripts/doctor_closeout.py <repository-root> prepare --request-file <outside-repository-treatment-request.json> --receipt-file <outside-repository-receipt.json>
```

After the separate exact approval and the approved document edits, apply that same receipt and approval:

```text
<python> <installed-skill>/scripts/doctor_closeout.py <repository-root> apply --receipt-file <same-outside-repository-receipt.json> --approval '<exact-engine-emitted-approval>'
```

Feedback may refine only the accepted treatment scope; new structural or unrelated work returns to preview and approval.

## Verify and review

Run the smallest relevant verification plus one documentation check. Report failures, partial work, or deviations truthfully. The normal apply receipt states the verification result, affected-file count, state/event outcome, and next action; show the resulting tree, hot-path usage, complete affected-file list, and diff only in `$docs doctor --details`. Preserve unrelated changes; stop before commit/push.

## Close repository memory

Promote verified truth backed by code/tests/configuration or confirmed intent. Keep unresolved candidates outside the hot path. Update map/state only for completed route/truth changes; never add treatment IDs, process logs, transient status, or plan prose.

The diagnosing Doctor response is read-only and performs zero lifecycle writes. Before the separate exact approval, write the engine-owned Doctor treatment receipt outside the repository. It binds every approved ID/fingerprint, selected scope, exact allowed files, starting document/control digests, and candidate-check inputs; its emitted approval remains the one exact copyable approval presented to the user. For this closeout, that approval ends with `; receipt sha256:<64-hex-receipt>`; copy the suffix unchanged so approval binds the complete receipt as well as its IDs/fingerprints. The responsible mutating command must freshly revalidate that receipt, every ID/fingerprint, starting digest, disposition, protected-surface effect, selected scope, and local-only boundary. If any evidence changed, return to a new proposal instead of retargeting the old approval.

Apply only the receipt's exact allowed files. Verify the candidate and installed documentation result with a temporary Git-index overlay when newly created Markdown needs to be visible to the checker; never run `git add` against the user's real index. Close only after the approved documentation result and protected public entrances pass their promised verification. Derive state and active findings from the loaded operational memory plus fresh checker evidence; never accept model-made state or target bytes. Then use one compare-before-write transaction with same-directory reserved temporaries, verified bytes and schemas, atomic replacement, and the success event last. Failed verification writes no operational closeout. Transaction failure rolls back exact control bytes or records the existing truthful recovery evidence; an orphan temporary or torn state/findings/manifest/local-map/event combination is a P0 `state-conflict`, never a successful baseline.

For a state conflict, use canonical Markdown, code, configuration, tests, and confirmed intent as evidence. Recompute deterministic findings, preserve non-conflicting protected-intent and verified-source routes, and show a deterministic zero-write recovery preview plus discarded-conflict evidence. Only exact approval of that preview may apply recovery through the same lifecycle transaction.

When present, `.diataxis/local-map.json` remains local-only and must be mechanically verified as ignored before creation or update. Doctor may validate its bounded routing metadata and verified content hashes without copying private routes into shared state or events. A missing local map is reported as unavailable; it neither permits an absence claim nor changes shared health.

## Capability limits

Report unavailable Git isolation/write/execution/verification/rollback and missing capabilities. Vendor-neutral, network-free operation has no required database, no required embeddings, no required daemon, no background process, and no new dependency. Stop if clean or verification fails; do not broaden edits or claim success. Never commit/push/release/publish or modify outside selected treatments.
