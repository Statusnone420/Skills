# Task 6A report

Base commit: `d0b2e2a9fdb50927aa6b38af8e6a262d59ca10bb`

## RED → GREEN

Focused regression tests were added before implementation and run RED:

```text
python -m unittest tests.test_docs_skill.DocsSkillContractTests.test_scope_symlink_fails_with_confinement_diagnostic tests.test_docs_skill.DocsSkillContractTests.test_json_missing_root_after_options_is_parseable tests.test_adapters.AdapterBuilderTests.test_slash_frontmatter_rejects_unknown_and_malformed_lines tests.test_public_docs.PublicDocumentationContractTests.test_windows_install_verification_fails_when_skill_missing
FAIL (4 tests; expected missing confinement/JSON/frontmatter/install behavior)
```

Minimal fixes were then implemented. The same focused command and artifact tests passed (`4/4`, `2/2`).

## Verification

- Full unit suite: `python -m unittest discover -s tests -v` — **55 passed**.
- Adapter validation: `python tools/build_adapters.py --check` — **clean**.
- Repository checker (human): `python skills/docs/scripts/check.py .` — **clean**.
- Repository checker (JSON): `python skills/docs/scripts/check.py . --json` — parseable, `findings: []`.
- Diff hygiene: `git diff --check` — **clean**.
- Public-doc contract, exact fixture dimensions, and pressure provenance — covered by full suite; provenance returned expected five fixture OIDs and two snapshots.
- Plugin submission-readiness packet validation — `tests.test_task6a_artifacts` passed; exact 5 positive/3 negative cases and schema/gap honesty enforced.

No 108-run matrix, compatibility pilots, security scan, UI/profile install, external model calls, cost projection, submission portal, or final review archive was run.

## Review follow-up

Added structural artifact tests for explicit case kind/type, stable IDs, required behavior/result fields, refusal rationale, recursive secret/local-path scanning, and visible-evidence/hidden-reasoning constraints. Tightened slash frontmatter to exact canonical bytes plus exact slash keys, rejecting whitespace drift, separator drift, malformed/duplicate/unknown keys, and value drift.

Follow-up RED observed the expected failures (`5` frontmatter/artifact failures). Follow-up GREEN and full suite pass: `python -m unittest discover -s tests` — **56 passed**. Adapter check, checker human/JSON, plugin/artifact validation, and `git diff --check` completed cleanly.

## Changes

Closed checker scope reparse handling, option-order JSON error handling, strict slash frontmatter validation, and Windows install verification. Added vendor-neutral external-review kit and skills-only plugin submission-readiness packet. All captures are repository-relative and omit hidden reasoning/secrets/private paths.
