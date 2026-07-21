---
name: docs-help
description: "Show the Diátaxis Docs command tree without repository access."
---

# Docs Help

This is the explicit thin route for the fixed command `help`. Treat all trailing text as that command's raw trailing text; never reinterpret it as another command.

Load and follow the sibling [Diátaxis Docs skill](../docs/SKILL.md), including its shared safety, evidence, health, and result contracts. The selected command contract below is the complete canonical `commands.md` contract for `help`; do not load `commands.md`, and load no additional playbook beyond those linked here. If a required shared resource is unavailable, stop and report that the command could not be executed; do not invent a fallback.

## Selected command contract (canonical)

`help [all]`: `Diátaxis Docs v<metadata.version>`; `help` returns Daily help; `help all` returns Daily help plus Help all; no repo I/O. Always render this command tree before the matching descriptions so Help remains recognizable across hosts:

```text
Diátaxis Docs
├── doctor
├── init
├── context
├── write
├── update
├── audit
├── fix
├── map
├── classify
├── migrate
├── check
├── cleanup
└── help
```

- `doctor [--details] [what you want improved]`  Diagnose documentation and prescribe the correct repairs. With no extra text, scan overall health. Initial diagnosis makes no edits.

- `context <what you are doing>`  Show where to start and what repository knowledge matters for the task. No edits.

- `write <what is missing>`  Create the focused documentation readers need, after verifying the facts.

- `update <what changed>`  Bring affected documentation in line with a code, configuration, product, or design change.

- `check`  Report the deterministic structural score only. No advice and no edits.

- `classify`  Classify documentation.

- `init`  Initialize this repository.

- `audit [scope]`  Audit a scope.

- `fix <finding IDs>`  Fix finding IDs.

- `map`  Map documentation.

- `migrate`  Preview moves.

- `cleanup`  Preview cleanup.
