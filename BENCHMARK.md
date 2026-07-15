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

## Task 9 prompt-composition measurement

The generated web adapters now disclose only the selected command contract plus the shared safety/result core and required supporting rules. This is a packaging measurement, not a claim about model quality:

| Observed range | Result |
| --- | ---: |
| Smallest command prompt | 3,214 bytes (`audit`) |
| Largest command prompt | 21,840 bytes (`doctor`) |
| Regression guard | 32,000 bytes, selected after that measurement with 10,160 bytes of headroom |

The former exact 16,000-byte concatenated-prompt check was retired. It compressed canonical behavior without product evidence. Repository map/current-truth hot-path bytes remain separate provisional telemetry and are not a score or health gate.

## Task 10 local deterministic dogfood (2026-07-14)

The first dogfood pass used only the completed read-only checker against 13 disposable repositories. It did not invoke an external model, mutate a fixture, or modify this checkout.

| Observation | Result |
| --- | ---: |
| Scenario matrix | 13/13 JSON results, deterministic on a repeated run |
| Initial Doctor/Init-style previews | 13/13 zero-write |
| Privacy checks | 13/13 no absolute fixture path or synthetic private sentinel leaked |
| No-doc route | `adoption-preview`, `content_reads: 0` |
| Large slop route | `batch-limited`, `content_reads: 0`; unique truth remained unopened |
| Stale/merged state | `blocked` with findings, never Healthy |
| Local-only authority | `choice-required`, local candidates present-uninspected, absence claim disallowed, `content_reads: 0` |
| Protected-surface inventory | 9 synthetic protected surfaces; 10 in the safe local public checkout, with incomplete evidence honestly reported |
| Vendor symlink | Created out-of-scope vendor symlink; checker remained structure-healthy and did not traverse it |
| Context-cost telemetry | Maximum generated web prompt 26,596 bytes; repository hot-path bytes remained provenance-tagged provisional telemetry |

The authorized Cline local-authority check reproduced the routing boundary: automatic discovery returned a choice boundary with two local candidates and no absence claim; an explicit local scope planned 12 files/94,889 bytes with `content_reads: 0`; selected read-only retrieval verified nine staged plan entries, including Chat Calm and performance work. The route/body stayed private and no local map or orientation hook was written. A safe local checkout with a configured remote provided the public-repository evidence; no network clone was performed. These are deterministic contract observations, not a claim about arbitrary model navigation.

## What remains to measure

External-model navigation quality, live cross-harness behavior, and a network-backed public-repository comparison remain unmeasured. The 16 KiB hot-path value and 26,596-byte prompt maximum remain telemetry/provisional targets; Task 10 does not justify a final hard threshold or weighting.
