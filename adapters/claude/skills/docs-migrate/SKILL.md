---
name: docs-migrate
description: "Preview exact documentation moves before separate approval."
user-invocable: true
disable-model-invocation: true
---

# Docs Migrate

This is the explicit thin route for the fixed command `migrate`. Treat all trailing text as that command's raw trailing text; never reinterpret it as another command.

Load and follow the sibling [Diátaxis Docs skill](../docs/SKILL.md), including its shared safety, evidence, health, and result contracts. The selected command contract below is the complete canonical `commands.md` contract for `migrate`; do not load `commands.md`, and load no additional playbook beyond those linked here. If a required shared resource is unavailable, stop and report that the command could not be executed; do not invent a fallback.

## Selected command contract (canonical)

- `migrate`  Preview moves.

`migrate`: preview exact moves and the resulting tree without moving, writing, or deleting; later, separate user message must accept the exact preview and revalidate evidence, proposal, and worktree.
