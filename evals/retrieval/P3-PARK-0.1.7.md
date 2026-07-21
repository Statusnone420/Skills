# 0.1.7 adversarial-review P3 park

Date: 2026-07-21

Branch: `release/0.1.7`

Rule: this file contains only non-blocking P3 follow-up. No P0, P1, or P2 finding is parked.

## Closed before the pull request

- P1: focused Check and Doctor no longer depend on a `commands.md` contract they are explicitly told not to load; both routes are self-contained and keep bounded prompt headroom.
- P1: collection now binds the exact condition prompt and selector, requires declared candidate provenance, rejects reused sessions, and requires the complete repetition set.
- P1: memory-unavailable campaigns reject both memory tool reads and host memory-summary injection markers.
- P1: the complete public result is privacy-scanned before write, including manifest-controlled fields and task-ID-shaped values.
- P2: host context, token/timestamp consistency, neutral comparison naming, and duration semantics are now validated or documented.

No P0 finding was identified.

## Release evidence boundary

The three-run correctness result is provenance-bound to package commit `a000fe8`. Afterward, the adversarial review changed focused Check/Doctor dependency closure and the evaluation collector. It did not change the three files exercised by that campaign:

- `skills/docs-map/SKILL.md`: `6208aa57bfa5f40f9bd00b94d50cc644a58efb9133420c6e23af81a83a721d1a`
- `skills/docs/SKILL.md`: `83718a0f20ff61671e4038fb72c63e8907d5e1f8ea00e45f54653f5ebaeef92d`
- `skills/docs/scripts/check.py`: `5c95d5ff57e782ae7b4a6f87655100c093c931c1cd014d25f1fc7b4ae732792c`

Those hashes still match the private provenance receipt. The final package tree was not subjected to another model campaign, so release text must describe the 3/3 result as Map-route evidence, not as an exact-final-tree rerun. The Check/Doctor review changes are covered by canonical-contract, focused-distribution, adapter-parity, and prompt-size tests. Reopen this item if any of the three hashes change or if an exact-final-tree model claim is desired.

## Windows CLI containment

Codex CLI 0.144.5 could not start its Windows read-only sandbox helper during the campaign. The scored runs therefore used `danger-full-access` only on a disposable detached worktree, with read-only prompts, serial execution, and a clean-worktree assertion after every run. This limits evaluation containment, not product behavior. Revisit when the Windows helper changes or before benchmarking a repository that is not disposable.

## Collector host-schema coupling

The collector intentionally fails closed while parsing local Codex JSONL events and the version-keyed plugin cache. A future Codex event/cache schema change can stop collection until fixtures and parsers are updated. It must never be handled by accepting missing telemetry. Revisit on a Codex CLI/Desktop schema or cache-layout change.

## Human semantic scoring

Constant-answer correctness remains human-reviewed from the visible final answer. The collector binds the exact prompt, selector, candidate bytes, raw-session hash, final-output hash, model, effort, repository commit, memory isolation, and token telemetry, but it does not claim to understand answer meaning. A future deterministic answer-schema scorer would reduce reviewer work; until then, preserve the raw private session and public hashes and require a second reviewer for disputed scoring.

## Campaign role names

The summarizer recognizes the frozen July reference/candidate condition names rather than a general schema-level role field. That is correct for the committed campaign but limits reuse. Add explicit condition roles before introducing a differently named comparison campaign; do not silently guess roles from labels.
