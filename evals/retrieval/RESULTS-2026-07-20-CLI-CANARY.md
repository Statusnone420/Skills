# 0.1.7 candidate Codex CLI canary

Date: 2026-07-20 local time. Candidate commit: `47d6e76`. Target repository commit: `7609b76`. Host: Codex CLI `0.144.5`. Model: Luna High. This is diagnostic evidence only; it is not one of the frozen Luna Max acceptance pairs.

Duration values below are host session telemetry from turn context through the final token-count event, not end-to-end CLI process wall time.

## Verdict

The memory-isolation mechanism works, but the formal paired campaign was not ready to launch unchanged. No release score is assigned.

- The completed canary recorded zero memory reads.
- Candidate cache provenance passed immediately before the run: package `a62da84f…`, focused skill `a6739faf…`.
- The temporary target worktree stayed clean and was removed after inspection.
- No third Luna task was launched.

## Attempt 1: invalid host run

The exact base prompt did not select the plugin skill in non-interactive `codex exec`. Every shell action then failed before execution because the Windows read-only sandbox helper returned `orchestrator_helper_launch_failed` / `Access is denied`. The visible answer correctly reported that it could not run the map. This is a host/setup failure, not a product result.

## Attempt 2: invocation diagnostic

After refreshing the installed local plugin, the retry used memory-disabled CLI execution on a disposable clean worktree. It prefixed the base prompt with `$docs-map`, which was believed to be the focused selector at launch. Current official Codex examples instead use the fully qualified `$plugin-name:skill-name` form. Host telemetry and the answer show that the unqualified selector did not prove focused `docs-map` routing.

Measured telemetry:

| Metric | Value |
| --- | ---: |
| Duration | 69.534 seconds |
| Tool-call wrappers | 8 |
| Shell commands | 8 |
| Memory reads | 0 |
| Memory-read output | 0 characters |
| First-turn cached input | 8,960 tokens |
| Cumulative uncached input | 43,968 tokens |
| Cumulative total tokens | 334,248 tokens |

The answer recovered most expected findings: the canonical/generated split, current truth, exact 1,589-byte hot path, 93% health, and both known topology gaps. It still failed the constant contract by starting at root `README.md` instead of `docs/README.md` and by rendering absolute local links instead of repository-relative labels. Because the wrong selector was used, these are not scored against the focused candidate.

## Harness correction

The paired campaign now keeps the frozen candidate base prompt byte-for-byte and records `diataxis-docs:docs-map` as a separate, fully qualified CLI selector. The launcher prefixes only the candidate request with `$diataxis-docs:docs-map` on its own line. CLI provenance is reported honestly as an exact qualified request plus a pre/post verified cache snapshot; it is not mislabeled as Desktop-style embedded `<skill>` bytes, which CLI sessions do not expose.

## Next release action

Fix or deliberately bypass the local read-only sandbox-helper failure on disposable read-only benchmark worktrees, then run the three sequential Luna Max pairs from `luna-max-cli-paired-v1.json`. A single qualified-skill canary should precede the scored campaign and must show focused routing, zero memory reads, correct repository-relative output, and a clean worktree. Do not change product bytes unless a valid paired campaign produces a product-attributed failure.
