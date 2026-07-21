---
name: docs-update
description: "Update documentation for a verified repository change."
user-invocable: true
disable-model-invocation: true
---

# Docs Update

This is the explicit thin route for the fixed command `update`. Treat all trailing text as that command's raw trailing text; never reinterpret it as another command.

Load and follow the sibling [Diátaxis Docs skill](../docs/SKILL.md), including its shared safety, evidence, health, and result contracts. The selected command contract below is the complete canonical `commands.md` contract for `update`; do not load `commands.md`, and load no additional playbook beyond those linked here. If a required shared resource is unavailable, stop and report that the command could not be executed; do not invent a fallback.

## Selected command contract (canonical)

- `update <what changed>`  Bring affected documentation in line with a code, configuration, product, or design change.

`update <change>`: orient from the map/current state and task-relevant `Sources:` anchors; inspect changed path names first, then path-limited diffs. Verify against code, tests, configuration, confirmed intent, and diff. Preserve unrelated dirty and untracked work without loading its contents. Do not inventory the repository or run the documentation checker when those routes are available. Run at most one available focused verification; do not probe multiple missing runners.
