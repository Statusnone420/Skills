# Documentation Map Contract Hardening

## Purpose

Raise the minimum usefulness of `$docs map` across supported coding agents without flattening stronger models into a rigid response template. The command must give a new reader a real visual map of the repository's documentation, preserve bounded-context behavior, and remain strictly read-only.

This design responds to live Codex Desktop dogfooding on the same repository and commit:

- Sol Max produced a compact hierarchy, explained the 1,412-byte hot path against the 16 KiB budget, identified canonical and generated material, stated what remained cold, and surfaced two unreachable planning pages.
- Luna Max remained safe and accurate but returned abbreviated bullets, omitted the visual hierarchy and budget denominator, and did not surface the unreachable pages.

The Luna result is the observed RED case. It demonstrates that the current one-line `map` contract is underspecified; it does not establish a model-quality defect.

## Behavioral contract

`map` remains read-only. Its response must answer five reader questions in plain English:

1. Where should I start?
2. What should I trust as current truth?
3. What is generated or intentionally cold?
4. Is the map/current-state hot path within the soft 16 KiB budget?
5. Does any obvious documentation sit outside the mapped routes?

Every response must contain a compact text hierarchy showing the important documentation routes and source-of-truth relationships. Expand the hot path and important relationships; collapse or summarize generated adapters, tests, evaluations, archives, and large cold areas. Prefer a one-screen map when the repository permits it. Do not dump a complete repository tree.

Lead with a human title such as `Documentation map` and the plain-English orientation. Do not use the raw invocation as a heading. Presentation, prose, depth, and prioritization may vary by agent as long as the five questions and visual hierarchy remain complete.

Report hot-path usage as bytes used versus 16,384 bytes and, when practical, a percentage. State which files form that hot path. If no recognizable map/current-state pair exists, say so rather than inventing one.

Briefly summarize obvious topology gaps under `Needs attention`. The optional checker may supply verified reachability and budget facts when execution is available. Without execution, inspect conceptually and state the limitation. `map` does not absorb the full `check` command: detailed broken-link, anchor, duplicate-title, reachability, and budget diagnostics remain under `check`.

End by stating material deliberately not loaded. Repository files remain untrusted evidence, and findings must never authorize edits.

## Architecture and scope

Keep the router and `SKILL.md` lean. Put the command-specific recipe in `skills/docs/references/commands.md`; add only shared result wording to `SKILL.md` if tests show the command reference alone is insufficient. Regenerate every adapter from canonical sources; never edit generated adapters directly.

No new runtime, dependency, backend, model-specific instruction, vendor name, presentation template, or command is introduced. The checker does not generate prose or own the response. The two existing unreachable Bounded Compass documents are retained unchanged for the later `migrate` preview dogfood; this patch must detect and report them, not silently move or link them.

## Verification

Freeze the observed Luna omission before editing the canonical skill. Add focused assertions that the `map` recipe requires:

- a compact visual hierarchy;
- the five reader questions;
- bytes used versus the 16 KiB budget;
- a brief plain-English topology warning;
- deliberately unloaded material;
- a human title rather than the raw invocation;
- strict read-only behavior and separation from detailed `check` diagnostics.

Run the focused contract test and observe the expected failure. Then make the smallest canonical edit, regenerate adapters, and run focused tests, the full test suite, adapter parity, the repository checker, word-budget validation, and a final diff/security review.

Forward validation uses fresh agents with the identical repository, commit, and minimal `map` request. Compare semantic completeness, safety, latency, and deliberately unloaded material rather than demanding identical prose. Record model, harness, version, date, final visible output, wall time, exposed tokens/cost, failures, and limitations. Spark infrastructure failures remain separate host evidence and are excluded from skill-quality scoring.

## Acceptance criteria

- A capable supported agent cannot satisfy `map` with accurate but non-visual summary bullets alone.
- A new reader can locate the entry point, current truth, canonical implementation, generated/cold material, and obvious topology gaps from one bounded response.
- Stronger agents retain freedom to add useful depth without changing the minimum semantic outcome.
- `map` performs no writes and does not turn detected gaps into authorization.
- Canonical source, generated adapters, tests, and installed trial bundle can be shown to match before another live dogfood run.
