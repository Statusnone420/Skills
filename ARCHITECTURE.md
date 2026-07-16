# Architecture

The canonical source is `skills/docs/SKILL.md`, routed to `references/commands.md` and, when needed, `references/memory.md`. The source defines explicit routing, evidence-first writing, adaptive memory, and a result contract. The product is a repository documentation operating system: Diátaxis supplies the organization compass, while the map/highway, scope evidence, Trust routes, freshness hashes, and lifecycle control plane make the behavior auditable.

`skills/docs/scripts/check.py` is an optional network-free orchestration façade over cohesive `_docs_checker` modules. The checker confines paths to the repository, rejects reparse points, parses Markdown links and anchors, reports unreachable pages and duplicate titles, evaluates state-declared hash freshness and Trust coverage, and reports provenance-tagged map/current-state bytes. The 16,384-byte value is only `provisional_target_bytes`, not an enforced budget, health input, or deletion mandate.

Discovery owns bounded metadata and selection policy; `paths.py` owns confinement, normalization, reparse checks, and prune primitives; `memory.py` is read-only inspection; `lifecycle.py` owns pure authorization/state policy; and `lifecycle_io.py` owns transactional filesystem I/O. The dependency direction is one-way and the CLI façade stays thin. The committed `.diataxis/` control plane stores normalized routes, stable findings, verified hashes, disposition identity, and event history—never document bodies, prompts, hidden reasoning, or local-only filenames.

Every generated adapter packages the complete canonical checker resource tree. Generic web prompts are composed per command from a shared safety core, one selected command contract, and required supporting rules; they do not concatenate the entire playbook. The observed prompt range is 3,412–26,596 UTF-8 bytes, with a 40,000-byte generator regression guard selected after measurement and 13,404 bytes of headroom.

Adapters under `adapters/` are generated outputs. Do not hand-edit them; regenerate from the canonical source and test their documented invocation tier.
