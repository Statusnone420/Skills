# Getting started

This public-alpha tutorial uses a disposable repository and keeps the first pass bounded and read-only.

## Prerequisites

- A supported Codex surface (ChatGPT desktop app, Codex CLI, or supported IDE integration).
- Repository access and the installed `skills/docs` source skill; follow [INSTALL.md](INSTALL.md).
- Python 3 is optional for the network-free checker.

1. Install the source skill as described in [INSTALL.md](INSTALL.md).
2. Start with `$docs doctor make this repository's documentation trustworthy, bounded, and easy for humans and agents to use`; its initial result is read-only and explains repository health, evidence, and any proposed treatment IDs. Expected result: no file changes on this first pass.
3. If a treatment is needed, review and explicitly approve selected IDs and the proposed isolation before any edits.
4. Direct commands remain available: run `$docs map` for topology, `$docs context the API migration` for bounded recall, and `$docs check` for links, anchors, reachability, duplicate titles, and hot-path bytes.
5. Only then ask `$docs write an API migration guide`; verify the proposed claims before accepting edits.

For changes, `$docs update ...` revalidates affected evidence. Structural commands such as `init`, `migrate`, and `cleanup` preview first and require a later exact approval.

## Troubleshooting

If the skill is missing, verify `$HOME/.agents/skills/docs/SKILL.md`, restart the host, or start a new task. If file tools are unavailable, use `context` or draft supplied material without claiming repository inspection. If Python is unavailable, run the skill's checker conceptually and report that the executable check was skipped.
