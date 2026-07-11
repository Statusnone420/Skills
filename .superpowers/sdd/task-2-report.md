# Task 2 RED baseline report

Date: 2026-07-11; branch `feat/diataxis-docs-v0.1`; foundation `421f3fa`.

## Committed campaign

`evals/red-results.json` has exactly 18 current rows: six each for Codex, Claude, and Grok, with one row for each of `minimal-init`, `bounded-context`, `diataxis-write`, `evidence-update`, `preview-cleanup`, and `audit-repair`.

Outcome counts are exact: Codex valid 6; Claude infrastructure_failure 6 (raw metadata supplied a sanitized HTTP 401 authentication error); Grok timeout 5 and evaluator_invalid 1 (exit 0 with empty capture). No Grok row is treated as valid model evidence. Valid counts are Codex 6, Claude 0, Grok 0.

Every current row carries the complete provenance contract: UUID-scoped attempt/workspace, harness/scenario/outcome/status, invocation and safe command provenance, relative cwd provenance, timestamps/duration, git status/diff, usage, model/version/CLI version, visible final or safe error, and `unavailable_fields` explanations for every null. Codex invocation is `collaboration.spawn_agent` with `fork_turns=none`; unavailable Codex timing, usage, model, and version are explicitly explained, never invented.

Twenty-six invalidated attempts are retained by recovered UUID, harness, scenario, reason, replacement ID where known, and null payload: five Codex, twelve Claude, seven Grok, and two runner probes. The runner probes are explicitly classified as `runner`/`probe`; no probe is model evidence. Unsupported cwd invocations, authenticated Claude attempts, and Grok raw capture failures are separately classified.

## Sanitization and verification evidence

The JSON is recursively sanitized: no thought/reasoning, ANSI, absolute/home paths, credentials, raw logs, expected answers, plan text, or eval-schema leakage. `invalidated_attempts` contain no payload. `skills/docs` and `SKILL.md` are absent.

RED/GREEN commands and results:

- `python -m unittest discover -s tests -p 'test_*.py'` — GREEN, 14 tests passed.
- `rg -n 'thought|C:/|D:/|\\Users\\|/Users/|/home/|expected.?answer|docs/plans|evals\\.json|\\x1b' evals/red-results.json` — no matches.
- `git ls-tree -r --name-only HEAD | rg '(^|/)SKILL\\.md$|^skills/docs'` — no matches.
- `git remote -v` — no remote configured.

The sanitized artifacts amend the unpushed baseline commit in place; no payloads, skills, or production data were changed.
