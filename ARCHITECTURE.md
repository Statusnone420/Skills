# Architecture

The canonical source is `skills/docs/SKILL.md`, routed to `references/commands.md` and, when needed, `references/memory.md`. The source defines explicit routing, evidence-first writing, adaptive memory, and a result contract.

`skills/docs/scripts/check.py` is an optional network-free orchestration façade over cohesive `_docs_checker` modules. The checker confines paths to the repository, rejects reparse points, parses Markdown links and anchors, reports unreachable pages and duplicate titles, evaluates state-declared hash freshness and Trust coverage, and reports provenance-tagged map/current-state bytes. The 16,384-byte value is only `provisional_target_bytes`, not an enforced budget, health input, or deletion mandate.

Adapters under `adapters/` are generated outputs. Do not hand-edit them; regenerate from the canonical source and test their documented invocation tier.
