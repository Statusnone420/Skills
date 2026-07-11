# Architecture

The canonical source is `skills/docs/SKILL.md`, routed to `references/commands.md` and, when needed, `references/memory.md`. The source defines explicit routing, evidence-first writing, adaptive memory, and a result contract.

`skills/docs/scripts/check.py` is an optional network-free checker. It confines paths to the repository, rejects reparse points, parses Markdown links and anchors, reports unreachable pages and duplicate titles, and enforces a 16 KiB combined map/state hot path.

Adapters under `adapters/` are generated outputs. Do not hand-edit them; regenerate from the canonical source and test their documented invocation tier.

