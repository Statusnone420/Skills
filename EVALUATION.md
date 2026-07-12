# Evaluation

The evaluation workflow uses deterministic fixtures, disposable repositories, recorded attempts, and sanitized visible artifacts. The public-alpha evidence currently includes five matched safety-pressure pairs, a preserved `init` approval-boundary failure and remediation, more than 100 deterministic tests, plugin validation, and a 5/5 live Windows junction probe.

## Shared-engine dogfood

Fresh isolated Codex agents ran the same canonical skill against the same repository state on Windows 11 on 2026-07-11:

| Command | Observed result | Remaining limitation |
| --- | --- | --- |
| `map` | Literal path tree, one checker execution, 1,686 / 16,384-byte current hot path, and both known unreachable planning files | None observed in the final probe |
| `check` | Same current hot-path size and findings from one checker execution; no source, help, Git, or finding-file detour | None observed in the final probe |
| `context` | Bounded explanation from three target-repository evidence files; generated contents, tests, and validation stayed cold | One unnecessary name-only adapter-directory listing remained |
| `update` | Disposable dirty-worktree fixture changed only two affected docs, preserved source anchors, unrelated dirty files, and an untracked user draft | One focused test-runner attempt stopped when the runner was unavailable |

Earlier probes are retained as failures rather than overwritten. They exposed absolute-versus-relative checker arguments, accidental skill-file hot-path promotion, broad context corroboration, repository inventory, and repeated missing-runner probes. Each accepted rule maps to one observed failure; the literal map tree remained unchanged.

The planned release matrix is six scenarios × skill/baseline × three repetitions × Codex/Claude/Grok (108 trajectories). It has not run. Cross-harness compatibility pilots have not run. Infrastructure failures remain failures and limitations, never positive results.

No hidden reasoning, credentials, private paths, or private repository material are part of the public record. Raw task identifiers and unsanitized traces remain private; only final outputs, diffs, concise tool events, and measured counters may be exported.
