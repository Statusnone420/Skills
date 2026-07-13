# Evaluation

The evaluation workflow uses deterministic fixtures, disposable repositories, recorded attempts, and sanitized visible artifacts. The public-alpha evidence currently includes five matched safety-pressure pairs, a preserved `init` approval-boundary failure and remediation, more than 100 deterministic tests, plugin validation, and a 5/5 live Windows junction probe.

## Layered regression gates

The public alpha uses three gates so quality evidence does not become an uncontrolled model-usage campaign:

1. **Deterministic contract gate:** every change runs standard-library tests for safety, checker behavior, adapter parity, and command contracts.
2. **Sanitized trajectory gate:** host-neutral receipts record semantic answers, documentation-owned actions, host/external overhead, visible diagnostics, and exposed usage counters. Raw traces, hidden reasoning, private paths, and credentials are never public inputs.
3. **Capped live canary:** release candidates may run a small, explicitly approved campaign against stable mapped, missing-map, and hostile fixtures. A campaign is limited to 12 runs; the checked-in example authorizes none.

Validate a receipt locally:

```text
python tools/trajectory_gate.py evals/trajectory/bulwark-map-accepted.json
```

The gate checks reader outcomes rather than exact prose: where to start, what to trust, current truth, generated/cold material, needs attention, and deliberately unloaded material. It separately counts documentation-owned and host/external actions. Cumulative token totals without a paired host control are labeled unattributed rather than charged entirely to Diátaxis Docs.

## Documentation-health rubric v1

The canonical checker emits a versioned `health` object with raw counts, earned weight, available weight, a deterministic percentage, and the plain-text meter. This is a reproducible structural baseline according to `$docs`, not a universal Diátaxis score and not evidence of factual accuracy.

| Category | Weight | Evidence |
| --- | ---: | --- |
| Maintained entry point | 20 | selected map exists and is readable |
| Path safety | 15 | maintained paths remain confined and avoid reparse or outside-link findings |
| Link integrity | 15 | valid local targets / checked local targets; no checked links earns zero |
| Anchor integrity | 10 | valid referenced anchors / checked anchors; no references is neutral/full |
| Reachability | 20 | maintained documents reachable from the selected map / maintained documents |
| Title clarity | 10 | usable, unique primary titles / maintained documents |
| Current-truth hot path | 10 | proportional credit while selected map/current-state bytes stay within 16 KiB |

Every division is zero-guarded, category weights sum to 100, and the percentage rounds by `int(earned_weight + 0.5)`. The meter fills `floor(percentage / 5)` of exactly 20 literal cells. Health repairs must improve the measured evidence; model judgment does not change the number.

Route tests use generated one-change mutations, invariant checks, deterministic cases, and retained named regressions. This applies Hypothesis/property-based testing ideas without adding Hypothesis, copying its source, or adding any runtime dependency.

## Shared-engine dogfood

Fresh isolated Codex agents ran the same canonical skill against the same repository state on Windows 11 on 2026-07-11:

| Command | Observed result | Remaining limitation |
| --- | --- | --- |
| `map` | Literal path tree, one checker execution, 1,686 / 16,384-byte current hot path, and both known unreachable planning files | None observed in the final probe |
| `check` | Same current hot-path size and findings from one checker execution; no source, help, Git, or finding-file detour | None observed in the final probe |
| `context` | Bounded explanation from three target-repository evidence files; generated contents, tests, and validation stayed cold | One unnecessary name-only adapter-directory listing remained |
| `update` | Disposable dirty-worktree fixture changed only two affected docs, preserved source anchors, unrelated dirty files, and an untracked user draft | One focused test-runner attempt stopped when the runner was unavailable |

Earlier probes are retained as failures rather than overwritten. They exposed absolute-versus-relative checker arguments, accidental skill-file hot-path promotion, broad context corroboration, repository inventory, and repeated missing-runner probes. Each accepted rule maps to one observed failure; the literal map tree remained unchanged.

The original release proposal included six scenarios × skill/baseline × three repetitions × Codex/Claude/Grok (108 trajectories). It has not run and is not the public-alpha default. Cross-harness compatibility pilots remain incomplete. Infrastructure failures remain failures and limitations, never positive results.

No hidden reasoning, credentials, private paths, or private repository material are part of the public record. Raw task identifiers and unsanitized traces remain private; only final outputs, diffs, concise tool events, and measured counters may be exported.
