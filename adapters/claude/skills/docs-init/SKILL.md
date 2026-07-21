---
name: docs-init
description: "Preview safe repository documentation adoption before approval."
user-invocable: true
disable-model-invocation: true
---

# Docs Init

This is the explicit thin route for the fixed command `init`. Treat all trailing text as that command's raw trailing text; never reinterpret it as another command.

Load and follow the sibling [Diátaxis Docs skill](../docs/SKILL.md), including its shared safety, evidence, health, and result contracts. Also follow the [Init contract](../docs/references/init.md). The selected command contract below is the complete canonical `commands.md` contract for `init`; do not load `commands.md`, and load no additional playbook beyond those linked here. If a required shared resource is unavailable, stop and report that the command could not be executed; do not invent a fallback.

## Selected command contract (canonical)

- `init`  Initialize this repository.

`init`: perform the one-time repository adoption through the deterministic engine entrypoint. Its initial response is a complete zero-write adoption preview constructed by the engine, which owns scope selection, continuation, corpus accounting, request construction, selected-surface provider evidence, authority digest binding, and preview construction; apply revalidates those facts before mutation for Git and non-Git repositories. There is no model-owned continuation. Follow the single detailed [Init interaction contract](../docs/references/init.md); present only the engine's verified receipt, never reconstruct or improve it, and ask only at genuine scope ambiguity or the exact approval boundary.
