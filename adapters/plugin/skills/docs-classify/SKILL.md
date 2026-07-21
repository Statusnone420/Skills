---
name: docs-classify
description: "Choose the appropriate Diátaxis documentation type."
---

# Docs Classify

This is the explicit thin route for the fixed command `classify`. Treat all trailing text as that command's raw trailing text; never reinterpret it as another command.

Load and follow the sibling [Diátaxis Docs skill](../docs/SKILL.md), including its shared safety, evidence, health, and result contracts. The selected command contract below is the complete canonical `commands.md` contract for `classify`; do not load `commands.md`, and load no additional playbook beyond those linked here. If a required shared resource is unavailable, stop and report that the command could not be executed; do not invent a fallback.

## Selected command contract (canonical)

- `classify`  Classify documentation.

`classify`: diagnose the user's need and likely Diátaxis type without inspecting or changing files.
