---
name: docs-context
description: "Find the repository knowledge relevant to the current task."
user-invocable: true
disable-model-invocation: true
---

# Docs Context

This is the explicit thin route for the fixed command `context`. Treat all trailing text as that command's raw trailing text; never reinterpret it as another command.

Load and follow the sibling [Diátaxis Docs skill](../docs/SKILL.md), including its shared safety, evidence, health, and result contracts. The selected command contract below is the complete canonical `commands.md` contract for `context`; do not load `commands.md`, and load no additional playbook beyond those linked here. If a required shared resource is unavailable, stop and report that the command could not be executed; do not invent a fallback.

## Selected command contract (canonical)

- `context <what you are doing>`  Show where to start and what repository knowledge matters for the task. No edits.

`context <task>`: make no edits. Orient from the map/current state and follow only task-relevant routes. Read at most four repository files by default: map, current state, and up to two task-relevant canonical sources; if unresolved, name the next route without loading it. Generated copies remain cold unless explicitly targeted. A source-to-generated relationship targets the canonical source and generator, not representative generated copies, tests, or a validation run. For an explanation, read one most-direct canonical route; do not inspect tests or execute validation unless the user asks to verify current status. Report deliberately unloaded material. It must not run the checker solely to calculate health.
