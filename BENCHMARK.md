# Benchmark status

As of 2026-07-12, the public-alpha evidence includes deterministic fixtures, RED captures, five matched safety-pressure pairs plus the preserved init failure/remediation, more than 100 deterministic tests, plugin validation, and a 5/5 live Windows junction probe.

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

## Host-context regression observation

A later same-repository Luna Max map run on 2026-07-12 preserved the tree, findings, read-only behavior, and bounded repository reads, but reported 407,376 cumulative tokens versus 368,229 in an earlier Bulwark run. It used fewer tool wrappers (9 versus 12) while average cumulative input per response rose from 27,693 to 39,974 tokens. Uncached input rose from 34,638 to 45,952 tokens.

Both runs loaded the host-required Superpowers startup guidance; the later run also attempted one obsolete path before finding it. Because the host injects global instructions, tool definitions, and installed-skill catalogs, this increase is **not attributable to Diátaxis Docs alone**. The trajectory gate now separates documentation-owned actions from host/external overhead and requires a paired host control before attributing context growth.

The 108-run matrix and cross-harness compatibility pilots have **not run**. No projected cost is published. Infrastructure failures remain failures/limitations and are not converted into positive results.
