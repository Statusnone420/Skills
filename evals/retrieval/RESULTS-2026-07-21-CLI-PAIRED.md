# Luna Max memory-isolated paired acceptance result

Date: 2026-07-21 local time. Candidate commit: `47d6e76`. Target repository commit: `7609b76`. Host: Codex CLI `0.144.5` with memories disabled. Model: `gpt-5.6-luna`, Max reasoning.

Duration values below are host session telemetry from turn context through the final token-count event, not end-to-end CLI process wall time.

## Verdict

**The provenance-bound candidate passes both performance gates and all execution-validity controls, but the exact candidate does not pass release acceptance because correctness was 2/3 rather than the required 3/3.** Do not discard or retry the incomplete run.

The incomplete candidate run found the correct current-truth route but omitted `docs/STATE.md` from the selected hot path, so it reported only 970 bytes instead of the required 1,589 bytes. The other two candidate runs were complete. This is a narrow product-contract reliability defect, not evidence that the bounded retrieval architecture regressed.

## Median results

| Condition | Duration | Tool wrappers | Uncached input | Total tokens | Correctness |
| --- | ---: | ---: | ---: | ---: | --- |
| July 11 bounded recipe | 143.377 s | 4 | 37,324 | 111,040 | 3 pass |
| Candidate `47d6e76` | 164.060 s | 4 | 17,637 | 115,294 | 2 pass, 1 partial |

Compared with the July arm, the candidate was 14.4% slower, used the same four tool wrappers, and used 52.7% less uncached input. The predeclared limit was at most 25% regression for duration and uncached input, so both performance gates pass.

## Validity and routing

- All six runs recorded zero memory reads and zero memory-read characters.
- First-turn cache states were symmetric: each condition had two runs at 8,960 cached tokens and one at zero.
- Every candidate run used the fully qualified `diataxis-docs:docs-map` selector and was bound to the preflighted cache snapshot.
- Candidate package SHA-256: `a62da84fbdf39f5e0d80a8237493a0cec05b10ca129e0e582c89c852fba9aeeb`.
- Focused skill SHA-256: `a6739fafaad15c3e67c689a299e6396b1b42e784f435d5ac5fa5c26286563f24`.
- Candidate repository-local checker attempts: 0/3. Installed bundled checker executions: 3/3.
- All six tasks ran serially against the detached target worktree, and the worktree was clean after every task.

Per-pair uncached-input deltas were -56.2%, -52.7%, and +54.9%. That spread confirms why the predeclared decision uses condition medians and records pair deltas as a cache-weather sanity check rather than scoring any single pair.

## Release decision

Do not cut the exact `47d6e76` bytes as 0.1.7. Make one targeted contract correction: an explicit current-state/current-truth/status route named by the map must be selected, read without a separate existence preflight, and passed to the bundled checker as `--hot` after a successful read. Add focused regression coverage, regenerate adapters, and run a new provenance-bound correctness acceptance check without weakening or rescoring this result.

The sanitized six-run evidence is in `results/luna-max-0.1.7-candidate-cli-memory-isolated.json`. Raw task IDs and final outputs remain in the Git-ignored private manifest directory.
