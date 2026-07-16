# Getting started

This public-alpha tutorial uses a disposable repository and keeps the first pass bounded and read-only. The product is a repository documentation operating system: Init adopts a selected highway once, Doctor keeps it healthy, and the map/context commands make retrieval deliberate.

## Prerequisites

- A supported Codex surface (ChatGPT desktop app, Codex CLI, or supported IDE integration).
- Repository access and the installed `skills/docs` source skill; follow [INSTALL.md](INSTALL.md).
- Python 3 is optional for the network-free checker.

1. Install the source skill as described in [INSTALL.md](INSTALL.md).
2. Start with `$docs doctor make this repository's documentation trustworthy, bounded, and easy for humans and agents to use`; its initial result is read-only and explains repository health, evidence, and any proposed treatment IDs. Expected result: no file changes on this first pass.
3. If a treatment is needed, review the scope-qualified structural score, Trust coverage, source-hash freshness, protected public surfaces, and complete disposition manifest. Explicitly approve the exact `DOC-*` IDs and full fingerprints plus the proposed isolation before any edits.
4. Direct commands remain available: run `$docs map` for topology, `$docs context the API migration` for bounded recall, and `$docs check` for links, anchors, reachability, duplicate titles, and hot-path bytes.
5. Only then ask `$docs write an API migration guide`; verify the proposed claims before accepting edits.

For changes, `$docs update ...` revalidates affected evidence. Structural commands such as `init`, `migrate`, and `cleanup` preview first and require a later exact approval.

Init is a one-time adoption preview produced by a deterministic engine—the model presents the engine's verified result rather than constructing one. It is zero-write until you approve the exact preview and manifest; subsequent verified closeout records routes, finding lifecycles, hashes, events, and disposition identity under committed `.diataxis/` state. No external database or daemon is required. Local-only knowledge remains local, and provider-facing public entrances remain at their established paths.

The 16 KiB repository hot-path figure is provisional telemetry only. It does not affect the structural percentage, Trust, health verdict, or deletion pressure. Web adapters use command-specific progressive disclosure and report measured prompt sizes rather than inheriting that repository heuristic.

## Troubleshooting

If the skill is missing, verify `$HOME/.agents/skills/docs/SKILL.md`, restart the host, or start a new task. If file tools are unavailable, use `context` or draft supplied material without claiming repository inspection. If Python is unavailable, run the skill's checker conceptually and report that the executable check was skipped.
