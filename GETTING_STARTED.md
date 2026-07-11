# Getting started

This tutorial uses a disposable repository and keeps the first pass bounded.

## Prerequisites

- A supported Codex surface (ChatGPT desktop app, Codex CLI, or supported IDE integration).
- Repository access and the installed `skills/docs` source skill; follow [INSTALL.md](INSTALL.md).
- Python 3 is optional for the network-free checker.

1. Install the source skill as described in [INSTALL.md](INSTALL.md).
2. Start read-only with `$docs help`; expected result: a compact list of supported commands and no file changes.
3. Run `$docs map`; expected result: documentation topology, hot path, and source-of-truth relationships.
4. Ask `$docs context the API migration`; expected result: sources, constraints, risks, and deliberately unloaded material.
5. Run `$docs check`; expected result: `clean`, or findings for links, anchors, reachability, duplicate titles, and hot-path bytes.
6. Only then ask `$docs write an API migration guide`; verify the proposed claims before accepting edits.

For changes, `$docs update ...` revalidates affected evidence. Structural commands such as `init`, `migrate`, and `cleanup` preview first and require a later exact approval.

## Troubleshooting

If the skill is missing, verify `$HOME/.agents/skills/docs/SKILL.md`, restart the host, or start a new task. If file tools are unavailable, use `context` or draft supplied material without claiming repository inspection. If Python is unavailable, run the skill's checker conceptually and report that the executable check was skipped.
