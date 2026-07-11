# Task 4 implementation report

## Scope and decisions

Implemented a repository-local, standard-library-only adapter builder at `tools/build_adapters.py`. The canonical source remains `skills/docs`; generated outputs are under `adapters/`. The plugin is a thin generated wrapper and no marketplace/profile/publish operation was performed.

## TDD evidence

1. Added `tests/test_adapters.py` before implementation.
2. RED: `python -m unittest tests.test_adapters` failed because `tools/build_adapters.py` did not exist (expected missing-feature failure).
3. GREEN: implemented generator and validator; the focused suite passed (`Ran 2 tests ... OK`).
4. Full verification passed: `python -m unittest discover -s tests -p 'test_*.py'` (`Ran 41 tests ... OK`).

## Generated and validated artifacts

- Slash bundles for Claude, Copilot, Grok, and Cursor add explicit invocation metadata while preserving the canonical body/resources.
- Gemini and OpenCode `/docs` wrappers explicitly activate `docs` and forward raw trailing text without shell interpolation; invocation is documented as instruction-enforced.
- Generic web prompts exist for all twelve commands and state filesystem/tool capability limits.
- `adapters/plugin/.codex-plugin/plugin.json` uses identifier `statusnone-skills`, display name `Statusnone Skills`, developer `Statusnone`, version `0.1.0`, Apache-2.0, and the specified repository; the bundled `docs` skill is parity-checked against canonical source.
- `python tools/build_adapters.py --check --output adapters` passed.
- Official local plugin validator passed: `validate_plugin.py adapters/plugin`.
- GitHub Actions workflow uses read-only contents permission, first-party checkout pinned to a full commit SHA, and no package installation.

## Limitations

No Codex Desktop UI observation or macOS execution was claimed; those remain Task 6/user-pilot evidence. No remote, marketplace, profile, network, publish, or release mutation was performed.

## Self-review

Generated output is deterministic (the focused test hashes all files before and after regeneration). Builder output is derived from the canonical skill and resources, and validation rejects missing parity/commands. Existing Tasks 1–3 files were not edited.

## Review fixes (TDD evidence)

Added focused regression tests for repository-confined output, semantic frontmatter placement, stale/modified resources, isolated user-level install/uninstall, and portable CI commands. RED run: `python -m unittest tests.test_adapters` failed with three expected failures (outside output was deleted/accepted, metadata was outside frontmatter, and stale/resource drift passed validation).

Implemented root-cause fixes: output paths must be repository-confined before deletion; slash metadata is inserted in YAML frontmatter; validation checks one-hop references, forbidden vendor/model terms, wrapper interpolation safety, all copied resource byte parity, stale extras, and expected output set; copy operations exclude bytecode; CI uses `python` on both Windows and Linux.

Fix verification:

- `python -m unittest tests.test_adapters` — 7 passed.
- `python -m unittest discover -s tests -p 'test_*.py'` — 46 passed.
- `python tools/build_adapters.py --check --output adapters` — clean.
- Official `validate_plugin.py adapters/plugin` — passed.
- `git diff --check` — passed.

The isolated install test uses a temporary repository-local home and simulates filesystem installation, metadata inspection, explicit `$docs` presence, and uninstall; it does not claim Desktop UI or macOS execution.
