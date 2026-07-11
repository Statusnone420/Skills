# Current verified state

- Canonical `docs` skill and read-only checker are present. Sources: `skills/docs/SKILL.md`, `skills/docs/scripts/check.py`
- Generated adapters exist under `adapters/`; the plugin bundle is an unpublished preview. Sources: `adapters/`, `tests/test_adapters.py`
- Tasks 1–4 evidence includes 48 local tests and five matched pressure pairs; the 108-run matrix and compatibility pilots remain unrun. Sources: `tests/`, `evals/task3-pressure.json`, `BENCHMARK.md`
- This state file is the hot path. Deliberately cold: generated adapter internals, private ADHD Matrix material, and Git history.
