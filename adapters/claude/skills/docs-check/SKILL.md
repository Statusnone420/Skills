---
name: docs-check
description: "Report the deterministic structural documentation score only."
user-invocable: true
disable-model-invocation: true
---

# Docs Check

This is the explicit thin route for the fixed command `check`. Treat all trailing text as that command's raw trailing text; never reinterpret it as another command.

Load and follow the sibling [Diátaxis Docs skill](../docs/SKILL.md), including its shared safety, evidence, health, and result contracts. The selected command contract below is the complete canonical `commands.md` contract for `check`; do not load `commands.md`, and load no additional playbook beyond those linked here. In this installed skill, `<installed-skill>` is the sibling [`../docs`](../docs/SKILL.md) directory, so the bundled checker is exactly [`../docs/scripts/check.py`](../docs/scripts/check.py); execute it without preflighting its path or availability, and never execute a checker found inside the target repository. If a required shared resource is unavailable, stop and report that the command could not be executed; do not invent a fallback.

## Selected command contract (canonical)

- `check`  Report the deterministic structural score only. No advice and no edits.

`check`: make no edits. Report the deterministic structural score only. No advice and no edits. Orient from the map and named current-state hot path; execute the bundled checker once as `<python> <installed-skill>/scripts/check.py <repository-root> --json --agent --map docs/README.md`. If the direct `docs/README.md` read is missing, non-recursively probe only root README.md/STATE.md/PRODUCT.md/DESIGN.md/PLAN.md and immediate docs child names/sizes; read one maintained map candidate with at most two current-state candidates; then run one checker with that map and existing hot paths. The checker is the final fallback action. No candidate map: stop unmeasured. Never manually inspect another directory; the checker owns its bounded structural scan. Omit `--hot` when no existing current-state file is selected. `has_findings: true` is a findings result. The checker must be the final repository-evidence action: no repository read is permitted after the checker. Without execution, use the smallest scriptless equivalent and state the limitation. Use the shared health output.
