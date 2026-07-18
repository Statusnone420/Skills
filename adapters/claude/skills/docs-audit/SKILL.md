---
name: docs-audit
description: "Audit documentation and return prioritized evidence-backed findings."
user-invocable: true
disable-model-invocation: true
---

# Docs Audit

This is the explicit thin route for the fixed command `audit`. Treat all trailing text as that command's raw trailing text; never reinterpret it as another command.

Load and follow the sibling [Diátaxis Docs skill](../docs/SKILL.md), including its shared safety, evidence, health, and result contracts. Follow the selected command contract in [commands.md](../docs/references/commands.md). Do not load unrelated command playbooks. If a required shared resource is unavailable, stop and report that the command could not be executed; do not invent a fallback.
