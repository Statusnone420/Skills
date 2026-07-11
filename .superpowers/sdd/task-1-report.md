# Task 1 implementation report

## Implementation

Built the RED evaluation foundation before any skill directory or `SKILL.md` exists. The standard `evals/evals.json` schema contains six baseline scenario families: minimal initialization, bounded legacy-state context, Diátaxis writing/classification, evidence-backed updates and candidates, read-only audit with selected repair, and preview-first cleanup under hostile instructions. `tools/run_evals.py` provides deterministic fixture generation, disposable Git attempts, path checks, secret/path redaction, timeout/error capture, immutable JSON attempt records, and `list`, `prepare`, `run`, `summarize`, and dry-run CLI behavior.

## Files

- `evals/evals.json` — single scenario source.
- `tools/run_evals.py` — standard-library-only harness and fixture builder.
- `tools/__init__.py` — package marker.
- `tests/test_foundation.py` — focused foundation tests.

The fixture is synthetic, deterministic LF bytes, exactly 290,542 bytes and 2,041 lines on every platform.

## Test commands and results

- RED: `python -m unittest tests.test_foundation` failed at collection with `ModuleNotFoundError: No module named 'tools'` before implementation existed.
- GREEN: `python -m unittest tests.test_foundation -v` — 6 tests passed.

Coverage includes schema loading, exact fixture dimensions, clean-attempt isolation, confinement, redaction, timeout/error recording, immutable records, and dry-run non-invocation.

## Self-review

The implementation uses subprocess argument arrays with `shell=False`, disposable attempt directories below `evals/workspace`, bounded timeouts, filtered credential-shaped environment variables, and visible-output-only records. It does not create skills, access a network, invoke model CLIs, publish artifacts, or expose hidden reasoning. No unrelated files were changed.
