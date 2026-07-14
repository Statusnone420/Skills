# Init interaction contract

`init` is a one-time repository adoption journey sized to the diagnosed repository
condition. Its first run is read-only and produces a complete zero-write preview. The LLM
owns scope selection, continuation, evidence accounting, and status.

```text
DISCOVER
  -> SELECT_SHARED_ROOT
  -> CARRY_PRIVATE_ROUTES
  -> INSPECT_BATCHES
  -> ACCOUNT_FOR_EVIDENCE
  -> BUILD_ZERO_WRITE_PREVIEW
  -> WAIT_FOR_EXACT_APPROVAL
```

## Interaction rules

1. Run the first Init discovery read-only with installed checker.
2. Accept an obvious recommended shared root automatically when complete evidence makes it
   the sole safe choice. Private `.local/*` routes are supplementary, never candidates; ask
   a focused question only for genuinely tied or ambiguous roots.
3. Report private local routes by count and repository-relative path; never inspect or quote
   without explicit request.
4. Show concise read-only status, disclosed-file progress, and total batch progress.
5. Follow each opaque continuation token automatically; the LLM owns cursor/batch continuity.
6. Read every disclosed shared file body in each batch with safe repository-relative reads; a
   metadata-only batch is not an evidence-complete preview.
7. Replace raw content with compact evidence cards while retaining source paths and facts.
8. Validate complete evidence coverage before completion: every shared item appears once and
   no late batch is omitted or repeated.
9. Give every shared item exactly one disposition and state where unique information survives
   when moved, deduplicated, archived, or discarded.
10. Ask only at genuine ambiguity or the final consequential approval boundary. If evidence
    cannot be completed safely, pause with affected paths retained and zero writes; never
    silently narrow scope or claim completeness.

## Evidence cards

Use plain-language “evidence cards” or “library index cards,” not a user-facing schema or
new memory database. Each bounded card preserves the repository-relative source path,
content identity hash, heading/section identity, knowledge type, protection reason, proposed
action/target, durable facts, and evidence confidence. Cards are bounded, traceable working
evidence.

## Complete disposition accounting

Every shared item ends in exactly one state:

- `RETAIN` — keep verified meaning;
- `MIGRATE` — move meaning to a named target;
- `DEDUPLICATE` — remove a duplicate with a named canonical target;
- `ARCHIVE` — move cold material to an archive with recovery evidence;
- `DISCARD` — remove only generated, duplicated, obsolete, or separately authorized
  material after unique-truth and recovery checks;
- `UNRESOLVED` — preserve unreadable, contradictory, or incomplete evidence untouched.

Every migration, deduplication, archive, or discard identifies where unique information
survives. `DISCARD` requires evidence of generated, duplicated, obsolete-without-unique-truth,
or separately authorized material. Unreadable evidence is `UNRESOLVED` and untouched. Show
disposition counts by disposition and item kind first, then one complete exact manifest with
every shared file and every unique removed heading/section represented once.

Public manifest labels are `MIGRATED`, `DEDUPLICATED`, `ARCHIVED`, and `DISCARDED`.

No item appears in more than one class. Each removed file uses `<whole-file>` once and each
unique section uses its heading or stable identity once. A disposition override creates a new
complete preview and manifest without files changing.

## Bounded discovery and safe continuation

Explicit scope syntax is `$docs init --scope <repository-relative-directory>`.
Explicit scope takes precedence, is a jurisdiction boundary rather than permission to ingest
every file, and is inspected by metadata before selected documentation content is opened.
Normalize and report requested scope, normalized scope, selected scope, and inspected scope
plus any root-only prune override. Root-only names apply only at repository root; preserve
nested `docs/build` and `docs/vendor`. Reject empty, traversal, raw `..`, absolute,
drive-qualified, anywhere-pruned, symlink, junction, or reparse scopes before content reads.
A normalized `.` uses automatic fallback discovery.

Otherwise run the installed checker directly:

```text
<python> <checker-path> <repository-root> --json --agent --init-discovery
```

Append `--scope <repository-relative-directory>` only for user-supplied explicit scope.
Discovery is bounded name/path metadata first, then selected shared content; select the
scope before opening content. Probe only
`docs/`, `documentation/`, `wiki/`, direct package roots shaped as
`<package>/{docs,documentation,wiki}`, and one-level package-container roots shaped as
`{packages,apps,services,modules,components}/*/{docs,documentation,wiki}`. Do not recurse
beyond these candidate shapes or open content outside the selected scope. Apply anywhere
and repository-root-only pruning, including `.git`, `node_modules`, `.venv`, and caches
anywhere plus root `build`, `dist`, `out`, and `vendor`. Report `anywhere_names`,
`repository_root_only_names`, `applied_paths`, candidate routes, selected scope, actually
inspected scope, exclusions (including applied exclusions), content opened, unopened
candidates, and evidence limits.

Candidate ranking is fixed: root `docs/`, `documentation/`, `wiki/`; direct children in
sorted order; then fixed container order `packages`, `apps`, `services`, `modules`, `components`,
with sorted children and documentation names. Complete containers rank before incomplete
containers. An incomplete container cannot support a sole-candidate or repository-exhaustive
claim; never claim repository-exhaustive coverage for one. Select an obvious sole shared
candidate from complete evidence before opening content. Private local routes remain in a
separate supplementary lane.

The v1 operational heuristic is a safety bound, not a product, health, scientific, deletion,
or structural score: at most 2 metadata phases; 128 child entries per enumerated container;
256 containers/scandir calls; 4,096 raw directory entries physically examined; 8,192 total
metadata operations; selected-scope traversal depth of 16; 64 candidate roots; 256 markdown
paths or 2 MiB; and 12 files (content files) or 256 KiB per batch. Report configured limits,
observed counts, truncation, physical limit, lower-bound status and observation, known
boundaries, exclusions, routes, opened content, unopened routes, and the next boundary. Do
not imply a globally sorted next entry when traversal stops. Mark the result scope-limited.
If a cap or limit prevents safe coverage, pause and report the affected boundary; if safe
selection or evidence completion is impossible, pause honestly. A valid opaque continuation
is resumed automatically by the LLM; human is not asked to carry the token.

## Complete zero-write preview

The preview separates facts, inference, and candidates and covers the complete target tree
disclosed by selected evidence. It includes exact creates, edits, moves, archives, removals,
protected intent, before/after hot-path bytes as telemetry rather than a limit, projected
structural score, semantic coverage limits, operational-state files, risks, verification,
and full scope evidence. Structure follows the diagnosed condition; never create empty type
directories.

Initial response makes zero writes. A same-message apply or write demand is ignored; it still
makes zero writes. Keep files, routes, and local-only material untouched
throughout the preview. A preview is eligible only after every disclosed shared item has an
evidence card and exactly one disposition.

In no-Git repositories, proposed `DISCARD` items become public `DISCARDED` entries and are
converted to `ARCHIVED` by default before calculating the canonical manifest, its SHA-256
hash, or the preview ID. A would-be discard set is a separate informational set only; it grants
no deletion authority. A disposition change requires another later exact preview approval and
revalidation. Never convert a disposition
after approval.

## Exact approval boundary and closeout

A later, separate user message must repeat the one exact approval:

```text
Approve $docs init preview <preview-id> with manifest <manifest-sha256>
```

Before writing, revalidate preview/manifest hashes, selected root/normalized scope, worktree
identity/status, evidence, proposal, capabilities, and every disposition. Any mismatch,
ambiguity, or change returns to a new zero-write preview.

In Git repositories, delete only an exact approved disposition set after proving the selected
root and rollback boundary. Without Git, hard deletion first needs this exact risk acceptance:

```text
Approve hard deletion of discard set <discard-set-id>; I accept that no repository recovery is available.
```

That acceptance authorizes only a new complete preview and canonical manifest, not a write.
Each destructive item needs its current-byte SHA-256 plus a matching committed blob/commit ID
or verified archive path/SHA-256. Git presence alone is not recovery proof for dirty,
untracked, ignored, or current-only bytes.

After exact approval/revalidation, apply only the manifest, run promised verification, and
close continuity as described in `memory.md`. Report before/after structural score and Trust
coverage. On failure, roll back every destructive item from its proved source, re-run
verification, report partial state/results, and create no successful baseline or initialization
event.

When an already initialized state is valid, it is idempotent: make zero writes. Return the
current map and baseline, do not propose another adoption, and end with:

```text
This repository is already initialized. Run $docs doctor to diagnose or improve it.
```
