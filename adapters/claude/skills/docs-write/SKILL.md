---
name: docs-write
description: "Create focused documentation after verifying its claims."
user-invocable: true
disable-model-invocation: true
---

# Docs Write

This is the explicit thin route for the fixed command `write`. Treat all trailing text as that command's raw trailing text; never reinterpret it as another command.

Load and follow the sibling [Diátaxis Docs skill](../docs/SKILL.md), including its shared safety, evidence, health, and result contracts. The selected command contract below is the complete canonical `commands.md` contract for `write`; do not load `commands.md`, and load no additional playbook beyond those linked here. For a later exactly approved mutating closeout, follow the embedded closeout boundary below and the [repository memory contract](../docs/references/memory.md). If a required shared resource is unavailable, stop and report that the command could not be executed; do not invent a fallback.

## Selected command contract (canonical)

- `write <what is missing>`  Create the focused documentation readers need, after verifying the facts.

`write <need>`: identify audience and Diátaxis type, verify claims, write one focused page, and update its map entry.

## Command closeout boundary

`doctor`, `check`, `map`, `context`, `audit`, and `classify` are read-only: they write neither documentation nor `.diataxis/` operational memory. A same-message request to diagnose and apply does not broaden that boundary. Doctor's outside-repository treatment receipt is an engine-owned approval artifact, not a repository mutation; only its later exact approved closeout may enter lifecycle closeout. Only an exact, separately approved `init`, `write`, `update`, `fix`, `migrate`, or `cleanup` result may enter lifecycle closeout.

Before a mutating closeout, revalidate every approved `DOC-*` ID against its full fingerprint, the selected repository and scope, starting control-file digests, the exact disposition set, protected-surface authorization and nonempty compatibility evidence, and any local-only route. Write the approved documentation result first and run its promised verification. A failed or unavailable verification makes zero state, findings, event, local-map, or manifest closeout writes and records no successful baseline.

After verification, use the single transaction defined in `memory.md`: compare-before-write, stage and verify same-directory reserved transaction temporaries, atomically replace state and findings, install any external disposition manifest or mechanically ignored `.diataxis/local-map.json`, and record the success event last. A stale target, interruption, cross-device replacement, sharing violation, or staged-validation failure rolls back or becomes an honest P0 state conflict; it never becomes success.

Finding lifecycle is `Proposed → Approved → Applied` or `Proposed → Parked`. Approval invalidation may return `Approved → Proposed`; the same recurring fingerprint may return `Applied → Proposed` while linking its prior event; materially changed parked evidence or priority may return `Parked → Proposed`. Priority may change without changing identity. Applied findings leave the active registry and remain in immutable event history.

Protected public entrances retain their exact paths and compatibility behavior unless that exact effect is authorized and verified. Local-only routes retain local-only visibility. Never move, publish, archive, delete, or expose either class through a general documentation approval. Missing local material is unavailable in this workspace, not proof of absence and not a shared-health penalty.
