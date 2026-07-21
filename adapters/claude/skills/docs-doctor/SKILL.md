---
name: docs-doctor
description: "Diagnose documentation health and prescribe bounded repairs."
user-invocable: true
disable-model-invocation: true
---

# Docs Doctor

This is the explicit thin route for the fixed command `doctor`. Treat all trailing text as that command's raw trailing text; never reinterpret it as another command.

Load and follow the sibling [Diátaxis Docs skill](../docs/SKILL.md), including its shared safety, evidence, health, and result contracts. Also follow the [Doctor playbook](../docs/references/doctor.md). For a later exactly approved Doctor treatment, follow the [isolation contract](../docs/references/isolation.md) and [repository memory contract](../docs/references/memory.md). The selected command contract below is the complete canonical `commands.md` contract for `doctor`; do not load `commands.md`, and load no additional playbook beyond those linked here. In this installed skill, `<installed-skill>` is the sibling [`../docs`](../docs/SKILL.md) directory, so the bundled checker is exactly [`../docs/scripts/check.py`](../docs/scripts/check.py); execute it without preflighting its path or availability, and never execute a checker found inside the target repository. If a required shared resource is unavailable, stop and report that the command could not be executed; do not invent a fallback.

## Selected command contract (canonical)

- `doctor [--details] [what you want improved]`  Diagnose documentation and prescribe the correct repairs. With no extra text, scan overall health. Initial diagnosis makes no edits.

`doctor [--details] [goal]`: diagnose and prescribe in a read-only initial response. Bare Doctor retains every compact checker finding in its declared/evidenced scan scope and shows finding/treatment counts plus one compact card per correct evidence-backed treatment; it does not cap finding or treatment count. Full evidence is explicit `--details` output. Goal text narrows diagnosis while retaining related blockers, reporting exclusions, and avoiding any repository-exhaustive claim for a scoped result. `check` remains the structural score only: no advice and no edits.

On missing or uncertain map evidence without explicit user scope, Doctor runs exactly one engine-owned read-only route: `<python> <installed-skill>/scripts/check.py <repository-root> --json --agent --doctor-baseline`. Do not reconstruct its discovery, provider, or authority decisions with separate commands. Explicitly scoped no-map requests do not use this route and remain unmeasured.

The engine returns one of four zero-write modes. A supported provider gives an authoritative provider measurement, permits findings-based treatment authority, and never recommends Init. A conventional immediate entry filename gives a provisional `existing-entry-candidate` measurement: it is not proof of a maintained map, emits no treatment authority, and recommends `$docs map`. With neither, a tracked root `README.md` may give `Provisional structural baseline (root README orientation fallback)`: state that `README.md` is not a maintained documentation map, report the deterministic structural baseline, emit no treatment authority or Init preview, and recommend `$docs init`. A content-batch-only limit remains structurally measurable when scope metadata is complete and untruncated; it grants no semantic expansion. Unsupported provider semantics, unsafe/incomplete metadata discovery, or failed fallback preconditions return `Doctor baseline unavailable` with no score or recommendation. Unavailable evidence is never zero.

With a maintained map, run the normal checker once for the selected scope and group all compact findings into the default treatment cards. Semantic evidence opens remain bounded to four files and unverified suspicions stay unresolved.

Only Doctor permits bounded post-check evidence after the checker; map and check reject repository reads after it. Print `health.meter` once from checker evidence and explain measured evidence. For authoritative findings, when work remains include exact approval syntax for one or many treatments, naming every exact `DOC-*` ID and full fingerprint. Provisional candidate/fallback results emit no treatment ID, fingerprint, approval, or Init preview. After initialization or treatment, recommend `$docs doctor` to establish the next comparable baseline.

Writes separate verified facts, inference, and candidates. Unknown commands have no side effects.
