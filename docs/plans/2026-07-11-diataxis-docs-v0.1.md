# Statusnone Skills — Diátaxis Docs v0.1

## Summary

Create `Statusnone420/skills`, branded **Statusnone Skills**, with one flagship skill:

- Identifier: `docs`
- Display name: **Diátaxis Docs**
- Tagline: “Bounded repository memory. Evidence-backed documentation.”
- Invocation: `$docs …` in Codex; `/docs …` elsewhere.
- License: Apache-2.0.
- Release: polished `v0.1.0` evidence preview.
- Publication: build and benchmark locally, obtain final approval, then create the public repository.

The skill combines reader-facing [Diátaxis](https://diataxis.fr/) with a repository-native memory protocol: maps route retrieval, evidence promotes facts, candidates remain non-canonical, Git preserves history, and archives stay cold. No database, backend, embeddings, network dependency, or background process.

Skill authoring follows RED–GREEN–REFACTOR: record baseline failures before `SKILL.md` exists, write only enough guidance to address observed failures, and re-test in fresh contexts.

## Public interface

| Command | Contract |
|---|---|
| `init` | Inspect, propose the smallest useful structure, preview, then await approval. |
| `context <task>` | Read-only bounded recall with sources, constraints, risks, and deliberately unloaded material. |
| `write <need>` | Identify audience and Diátaxis type, verify claims, write one focused page, update its map entry. |
| `update <change>` | Verify against code/tests/config/diff and update only affected documentation. |
| `audit [scope]` | Strictly read-only, numbered, prioritized, evidence-backed findings. |
| `fix <IDs\|scope>` | Revalidate selected findings and make only the authorized repairs. |
| `map` | Read-only documentation topology, hot path, and source-of-truth report. |
| `classify` | Read-only diagnosis of user need and Diátaxis type. |
| `migrate` | Preview exact moves and resulting tree, preserve history, await approval. |
| `check` | Read-only links, reachability, duplicate-title, and context-budget integrity. |
| `cleanup` | Preview splits, merges, archives, removals, and estimated context savings; await approval. |
| `help [all]` | Compact command help without inspecting the repository. |

Unknown or missing commands produce help and no side effects. `write`, `update`, and selected `fix` operations are authorized by invocation; structural or destructive work always needs separate approval.

## Repository memory

For greenfield repositories, propose only what is needed:

- `docs/README.md`: human-readable retrieval map.
- `docs/STATE.md`: optional verified current truth.
- `docs/CANDIDATES.md`: optional bounded, explicitly non-canonical queue.
- `docs/archive/`: created only when retained history still teaches something.

Preserve existing conventions such as root `STATE.md`, `PRODUCT.md`, and `DESIGN.md`. Do not impose four empty Diátaxis directories. Use a soft 16 KiB combined budget for the map and current-state hot path. Candidate promotion requires corroboration from code, tests, configuration, or confirmed product intent. Contradicted or superseded material leaves the hot path; Git is the default history store.

## Skill packaging

- Keep canonical source under `skills/docs/`.
- Keep `SKILL.md` at or below 500 words and route directly to one command playbook plus, only when needed, one shared Diátaxis or memory reference.
- Keep every reference one level from `SKILL.md`.
- Include one optional Python-standard-library checker. It is read-only, repository-confined, follows no external symlinks, performs no network access, and has an agent-only fallback.
- Generate adapter bundles from the canonical body and verify parity:
  - Codex: `agents/openai.yaml`, `$docs`, `allow_implicit_invocation: false`.
  - Claude, Copilot, Grok, Cursor: `/docs`, `disable-model-invocation: true`.
  - Gemini and OpenCode: `/docs` command wrapper that explicitly activates the shared skill; label enforcement as instruction-based where the host lacks a native manual-only policy.
  - Generic web: one self-contained prompt per command, with repository-access limitations stated.
- Keep model names, vendor tool names, `$ARGUMENTS`, personas, and model-specific prompting out of the core.
- Installed skills never rewrite themselves. The source repository may deliberately dogfood the skill.

## Task 1: Evaluation foundation and RED baselines

- Create six synthetic scenario families and standard `evals/evals.json` before creating the skill.
- Generate a private-data-free legacy state fixture of exactly 290,542 bytes and 2,041 lines.
- Implement a safe, standard-library evaluation runner that uses disposable repositories, clean sessions, recorded attempts, and sanitized visible artifacts.
- Run one no-skill baseline per scenario on Codex, Claude, and Grok. Do not expose assertions or expected answers to target agents.

## Task 2: Canonical skill and checker

- Initialize `skills/docs` with the official skill scaffolder only after Task 1 RED artifacts exist.
- Write the lean router, direct command playbooks, Diátaxis compass, repository-memory lifecycle, and result contract.
- Add the optional read-only checker with scriptless fallback.
- Test audit immutability, preview-before-structural-mutation, prompt-injection resistance, path confinement, malformed Markdown, Unicode paths, anchors, hot-context budgets, JSON output, and exit codes.

## Task 3: Adapters, validation, and CI

- Generate installable variants from the canonical source rather than maintaining forks.
- Add harness-specific explicit-invocation controls and generic per-command prompts.
- Add adapter parity, schema, routing, and argument-forwarding checks.
- Run minimal-permission Windows and Linux CI with actions pinned by commit SHA.

## Task 4: Public documentation and dogfooding

- Add a proof-first README, getting-started tutorial, install guide, command reference, architecture, origin, evaluation method, compatibility matrix, benchmark report, changelog, contributing guide, security policy, Apache license, and NOTICE.
- Tell the Statusnone-led origin: vanished Smithery skill → Diátaxis → ADHD Matrix’s oversized state → Crimson Desert’s corroboration/quarantine insight → portable repository memory.
- Link the public Crimson Desert Report Hub, disclose Codex collaboration, and publish no ADHD Matrix code, prose, screenshots, paths, or private artifacts.
- Add an independent-project disclaimer and use fresh wording rather than adapting CC BY-SA prose.
- Dogfood the completed skill on its own repository and record the result.

## Task 5: Pilot, security review, and review snapshot

- Run deterministic validation and a final repository security review focused on subprocess safety, secret redaction, prompt injection, and external-path writes.
- Run the cross-harness pilot and compatibility smokes for Copilot, Gemini, OpenCode, Cursor, and the generic web prompts.
- Publish the pilot’s observed usage and projected cost before the separate 108-run release matrix approval gate.
- Produce a local review snapshot. Do not create, push, tag, release, or publish the GitHub repository before explicit approval.

## Task 6: Shared bounded-retrieval engine

- Preserve the literal, human-readable documentation tree proven by dogfooding; reduce retrieval cost rather than visual quality.
- Add focused failing contract tests before changing guidance for the observed `map`, `context`, and `check` over-retrieval failures.
- Treat the map and current-state files as orientation. Treat source anchors as optional routes, not automatic reads or hot-path members.
- For routine recall and diagnostics, take only the minimum evidence hop needed, then stop or report the relationship as unresolved.
- Execute a known bundled checker once with its documented arguments. Inspect its source or help only after an execution failure.
- Keep intentionally broad commands (`audit`, `migrate`, and `cleanup`) capable of deeper inspection; bounded retrieval must raise the floor without flattening useful judgment.
- Establish an isolated, private-data-free `update` baseline before changing its contract. Add guidance only for a reproduced failure.
- Rebuild adapters, refresh the isolated local installation, run deterministic validation, and rerun fresh-agent `map`, `context`, and `check` probes against the same repository state.

## Task 7: Cline Desktop 0.2.9 compatibility

- Keep `skills/docs/` canonical and vendor-neutral; never maintain a copied Cline-specific skill fork.
- Document the existing opt-in Cline installation targets: the global `%USERPROFILE%/.cline/data/settings/skills/docs` directory and a repository-local `.cline/skills/docs` directory.
- Verify explicit `/docs` discovery and loading from the exact canonical bundle in Cline Desktop 0.2.9.
- In the separate Cline Desktop repository, evaluate the smallest durable opt-in external-skill-root setting so the app can point at a canonical skill library without copying it.
- Require both Cline discovery paths to share the same configured roots, preserve path containment and size limits, and include focused tests before implementation.
- Do not make Cline Desktop, Electron, the Cline SDK, or any application runtime a dependency of Statusnone Skills.

## Release evaluation

The release matrix is six scenarios × skill/baseline × three repetitions × Codex/Claude/Grok = 108 fresh trajectories. Publish exact harness, model, version, date, prompts, final outputs, diffs, concise tool events, timing, tokens/cost when exposed, failures, and limitations. Never publish hidden reasoning. Record every attempt; infrastructure retries receive new IDs and never replace failures.

Hard gates:

- 100% compliance for read-only commands, approval boundaries, unrelated-change preservation, path confinement, secret safety, and non-promotion of unverified claims.
- Every adapter passes explicit invocation and negative implicit-invocation tests at its documented enforcement tier.
- At least 85% deterministic assertion pass rate and at least 15 percentage-point aggregate lift over baseline.
- No regression on hard assertions in any scenario family.
- Cleanup produces a hot path at or below 16 KiB; bounded-context runs avoid loading the complete synthetic state file.

After approval, create `Statusnone420/skills`, push the reviewed history, tag `v0.1.0`, and publish generated bundles and sanitized evaluation artifacts. Earn `v1.0.0` only after successful use in at least two independent repositories and a full compatibility rerun following a significant model or harness update.

## Assumptions

- Public branding is “Statusnone Skills”; repository address defaults to `Statusnone420/skills`.
- `docs` remains the concise command name.
- No custom website, marketplace/plugin packaging, logo, backend, SQL, or scheduled paid evaluations in v0.1.
- Compatibility means a vendor-neutral core, tested adapters, honest capability tiers, and dated evidence—not a claim covering every current and future interface.
- Web-only agents without repository or file tools can classify and draft supplied material but cannot initialize or mutate a repository.
