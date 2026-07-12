# Benchmark status

As of 2026-07-11, Tasks 1–6 provide deterministic fixtures, RED captures, five matched safety-pressure pairs plus the preserved init failure/remediation, 73 local tests, plugin validation, and a 5/5 live Windows junction probe.

## Map retrieval pilot

One same-repository, same-model Codex Desktop comparison measured whether a literal documentation tree or ambiguous retrieval instructions caused the high usage. The UI model label was `5.6 Luna Max` on Windows 11.

| Measured counter | Explicit-tree candidate | Bounded four-action candidate |
| --- | ---: | ---: |
| Duration | 241.0 s | 125.5 s |
| Repository tool calls | 8 | 4 |
| Cumulative tokens | 366,538 | 123,182 |
| Uncached input tokens | 61,159 | 12,053 |
| Reasoning tokens | 9,233 | 3,971 |
| Non-reasoning output tokens | 2,514 | 918 |

The bounded candidate preserved the literal tree and findings while reducing cumulative tokens by 66% and uncached input by 80% in this single comparison. These are host-reported cumulative counters, not visible-output tokens, direct cost, or proof of causation. The result selected a retrieval contract for further testing; it is not a release benchmark.

The 108-run matrix and cross-harness compatibility pilots have **not run**. No projected cost is published. Infrastructure failures remain failures/limitations and are not converted into positive results.
