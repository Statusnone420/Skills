# Documentation Map Contract Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `$docs map` consistently produce a bounded, plain-English visual documentation map across supported capable agents.

**Architecture:** Keep routing and shared safety unchanged. Encode one positive, command-specific output recipe in the canonical command playbook, protect it with a focused deterministic contract test, regenerate all adapters, and forward-test the result with identical fresh-agent prompts. The optional checker supplies facts but does not own presentation.

**Tech Stack:** Markdown skill sources, Python 3 standard-library `unittest`, existing adapter generator, existing read-only checker, Git.

## Global Constraints

- Canonical behavior remains under `skills/docs/`; generated adapters are never hand-edited.
- `SKILL.md` remains at or below 500 words and contains no model or vendor names.
- `map` remains strictly read-only and never turns findings into authorization.
- Require semantic completeness and a compact visual hierarchy, not identical prose or a rigid full-response template.
- Keep `check` as the detailed diagnostic command.
- Add no runtime, dependency, backend, command, or model-specific prompting.
- Leave the two unreachable Bounded Compass documents unchanged for the later migration dogfood.
- Do not commit, push, tag, release, or publish without separate user authorization.

---

### Task 1: Protect the reader-facing map contract with a failing test

**Files:**
- Modify: `tests/test_docs_skill.py`
- Read: `skills/docs/references/commands.md`

**Interfaces:**
- Consumes: the `map` section bounded by `` `map` `` and `` `classify` `` in the canonical command playbook.
- Produces: `DocsSkillContractTests.test_map_command_has_visual_reader_contract`, the deterministic floor for later skill edits.

- [x] **Step 1: Add the focused contract test**

```python
    def test_map_command_has_visual_reader_contract(self):
        commands = (SKILL / "references" / "commands.md").read_text(encoding="utf-8")
        start = commands.index("`map`")
        end = commands.index("`classify`", start)
        contract = commands[start:end].lower()
        for phrase in (
            "documentation map",
            "plain english",
            "compact text hierarchy",
            "where to start",
            "current truth",
            "generated",
            "intentionally cold",
            "16,384 bytes",
            "needs attention",
            "outside the mapped routes",
            "deliberately not loaded",
            "presentation may vary",
        ):
            self.assertIn(phrase, contract)
        self.assertIn("make no edits", contract)
        self.assertIn("detailed diagnostics remain under `check`", contract)
```

- [x] **Step 2: Run the focused test and verify RED**

Run:

```powershell
python -m unittest tests.test_docs_skill.DocsSkillContractTests.test_map_command_has_visual_reader_contract -v
```

Expected: `FAIL`, first reporting that `documentation map` is absent from the current one-line contract. A pass means the test does not reproduce the observed Luna omission and must be corrected before continuing.

### Task 2: Add the smallest positive recipe and regenerate adapters

**Files:**
- Modify: `skills/docs/references/commands.md`
- Regenerate: `adapters/claude/references/commands.md`
- Regenerate: `adapters/copilot/references/commands.md`
- Regenerate: `adapters/cursor/references/commands.md`
- Regenerate: `adapters/grok/references/commands.md`
- Regenerate: `adapters/plugin/skills/docs/references/commands.md`

**Interfaces:**
- Consumes: the failing contract test from Task 1 and the existing optional `scripts/check.py` facts.
- Produces: one canonical `map` recipe copied byte-for-byte to generated adapters.

- [x] **Step 1: Replace only the canonical `map` entry with this positive recipe**

```markdown
`map`: make no edits. Title the result `Documentation map`, then explain in plain English where to start. Show a compact text hierarchy of the important documentation routes and source-of-truth relationships. Expand the hot path and current truth; collapse or summarize generated, intentionally cold, archived, test, and evaluation material instead of dumping the complete repository tree. Identify the entry point, current truth, canonical sources, generated material, and what was deliberately not loaded. Report the hot-path files and usage as bytes used / 16,384 bytes, plus a percentage when practical. Briefly report obvious documentation outside the mapped routes under `Needs attention`; use the optional checker when available or state the scriptless limitation. Detailed diagnostics remain under `check`. Presentation may vary, but the hierarchy and reader questions must remain complete.
```

- [x] **Step 2: Run the focused test and verify GREEN**

Run:

```powershell
python -m unittest tests.test_docs_skill.DocsSkillContractTests.test_map_command_has_visual_reader_contract -v
```

Expected: `OK` with one passing test.

- [x] **Step 3: Regenerate adapters from canonical source**

Run:

```powershell
python tools/build_adapters.py generate --output adapters
```

Expected: exit code `0`; only generated adapter copies of canonical resources change.

- [x] **Step 4: Verify byte-level adapter parity**

Run:

```powershell
python tools/build_adapters.py --check --output adapters
```

Expected: `clean` and exit code `0`.

### Task 3: Keep the repository memory honest and validate the complete patch

**Files:**
- Modify: `docs/README.md`
- Existing design: `docs/superpowers/specs/2026-07-11-map-contract-hardening-design.md`
- Existing plan: `docs/superpowers/plans/2026-07-11-map-contract-hardening.md`
- Update installed trial copy: `$HOME/.agents/skills/docs/references/commands.md`

**Interfaces:**
- Consumes: the completed canonical recipe and generated adapters.
- Produces: a reachable design/plan record, a matching private installed bundle, and fresh verification evidence.

- [x] **Step 1: Add the current design and plan to the documentation map**

Replace the final planning line in `docs/README.md` with:

```markdown
Planning record: [Diátaxis Docs v0.1](plans/2026-07-11-diataxis-docs-v0.1.md), [map contract design](superpowers/specs/2026-07-11-map-contract-hardening-design.md), and [map contract implementation plan](superpowers/plans/2026-07-11-map-contract-hardening.md).
```

This keeps the new work reachable while deliberately leaving the two older Bounded Compass pages as the only known topology findings.

- [x] **Step 2: Run focused and complete tests**

Run:

```powershell
python -m unittest tests.test_docs_skill -v
python -m unittest discover -s tests -v
```

Expected: all tests pass with no errors or warnings attributable to this patch.

- [x] **Step 3: Validate skill packaging and word budget**

Run:

```powershell
python "$env:USERPROFILE/.codex/skills/.system/skill-creator/scripts/quick_validate.py" skills/docs
python tools/build_adapters.py --check --output adapters
```

Expected: skill validation succeeds and adapter check reports `clean`.

- [x] **Step 4: Verify repository-memory findings**

Run:

```powershell
python skills/docs/scripts/check.py . --json
```

Expected: exit code `1` with exactly the two pre-existing unreachable Bounded Compass pages and no new finding. Those findings are retained for the separately approved `migrate` dogfood.

- [x] **Step 5: Refresh and verify the private installed trial copy**

Copy the canonical `skills/docs/references/commands.md` over `$HOME/.agents/skills/docs/references/commands.md`, then compare SHA-256 hashes for every canonical/installed file in `SKILL.md`, `agents/`, `assets/`, `references/`, and `scripts/`.

Expected: identical hashes and no files outside the installed `docs` skill changed.

- [x] **Step 6: Review the final diff and secret/path surface**

Run:

```powershell
git diff --check
git status --short
git diff -- skills/docs tests adapters docs
```

Expected: only the approved spec, plan, test, canonical command playbook, generated adapter copies, and documentation-map entry appear. No session IDs, local repository paths, credentials, hidden reasoning, or unrelated files are added.

- [ ] **Step 7: Forward-test without changing another variable**

In fresh tasks on the same repository and commit, attach Diátaxis Docs and send only `map` to the selected Codex, Anthropic, and Grok models. Record visible final output, timing, exposed usage, repository diff evidence, and limitations. Score the five reader questions, visual hierarchy, read-only behavior, and deliberately unloaded material; do not score stylistic identity.

Expected: each sufficiently capable supported model reaches the same semantic floor; stronger models may add depth. Any failure remains evidence for another RED-GREEN iteration rather than being rationalized away.
