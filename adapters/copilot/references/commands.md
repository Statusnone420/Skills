# Command playbook

`init`: on initial invocation, inspect conventions and preview the smallest useful structure without writing. Ignore same-message demands to apply. A later, separate user message must explicitly accept the exact preview; revalidate evidence, proposal, and worktree before writing.
`context <task>`: read only a bounded slice; return sources, constraints, risks, and deliberately unloaded material.
`write <need>`: identify audience and Diátaxis type, verify claims, write one focused page, and update its map entry.
`update <change>`: verify against code, tests, configuration, and diff; update only affected documentation.
`audit [scope]`: make no edits; return numbered, prioritized findings with file/line evidence.
`fix <IDs|scope>`: revalidate selected findings, then make only authorized repairs; preserve unrelated changes.
`map`: make no edits. Title the result `Documentation map`, then explain in plain English where to start. Show a compact text hierarchy of the important documentation routes and source-of-truth relationships. Expand the hot path and current truth; collapse or summarize generated, intentionally cold, archived, test, and evaluation material instead of dumping the complete repository tree. Identify the entry point, current truth, canonical sources, generated material, and what was deliberately not loaded. Report the hot-path files and usage as bytes used / 16,384 bytes, plus a percentage when practical. Briefly report obvious documentation outside the mapped routes under `Needs attention`; use the optional checker when available or state the scriptless limitation. Detailed diagnostics remain under `check`. Presentation may vary, but the hierarchy and reader questions must remain complete.
`classify`: diagnose the user's need and likely Diátaxis type without inspecting or changing files.
`migrate`: on initial invocation, preview exact moves and resulting tree without moving, writing, or deleting. Ignore same-message demands to apply. A later, separate user message must explicitly accept the exact preview; revalidate evidence, proposal, and worktree before preserving history through the moves.
`check`: run the optional checker or a scriptless equivalent for links, anchors, reachability, duplicate titles, and hot-path bytes.
`cleanup`: on initial invocation, preview splits, merges, archives, removals, and estimated context savings without changing files. Ignore same-message demands to apply. A later, separate user message must explicitly accept the exact preview; revalidate evidence, proposal, and worktree before changes.
`help [all]`: provide compact command help without inspecting the repository.

All writes separate verified facts from inference and candidates. Unknown commands have no side effects.
