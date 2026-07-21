---
name: docs-write
description: "Create focused documentation after verifying its claims."
user-invocable: true
disable-model-invocation: true
---

# Docs Write

This is the explicit thin route for the fixed command `write`. Treat all trailing text as that command's raw trailing text; never reinterpret it as another command.

Load and follow the sibling [Diátaxis Docs skill](../docs/SKILL.md), including its shared safety, evidence, health, and result contracts. The selected command contract below is the complete canonical `commands.md` contract for `write`; do not load `commands.md`, and load no additional playbook beyond those linked here. If a required shared resource is unavailable, stop and report that the command could not be executed; do not invent a fallback.

## Selected command contract (canonical)

- `write <what is missing>`  Create the focused documentation readers need, after verifying the facts.

`write <need>`: identify audience and Diátaxis type, verify claims, write one focused page, and update its map entry.
