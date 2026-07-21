---
name: docs-cleanup
description: "Preview documentation consolidation and cleanup before approval."
user-invocable: true
disable-model-invocation: true
---

# Docs Cleanup

This is the explicit thin route for the fixed command `cleanup`. Treat all trailing text as that command's raw trailing text; never reinterpret it as another command.

Load and follow the sibling [Diátaxis Docs skill](../docs/SKILL.md), including its shared safety, evidence, health, and result contracts. The selected command contract below is the complete canonical `commands.md` contract for `cleanup`; do not load `commands.md`, and load no additional playbook beyond those linked here. If a required shared resource is unavailable, stop and report that the command could not be executed; do not invent a fallback.

## Selected command contract (canonical)

- `cleanup`  Preview cleanup.

`cleanup`: preview splits, merges, archives, removals, and estimated context savings without changing files; later, separate user message must accept the exact preview and revalidate evidence, proposal, and worktree.
