<p align="center">
  <img src="skills/docs/assets/bounded-compass-small.svg" width="88" height="88" alt="Bounded Compass mark">
</p>

<h1 align="center">Diátaxis Docs</h1>

<p align="center"><strong>Part of Statusnone Skills</strong></p>

<p align="center"><strong>Your repository's documentation should help agents—not consume their context window.</strong></p>

<p align="center">Bounded repository memory. Evidence-backed documentation.</p>

<p align="center">
  <a href="https://github.com/Statusnone420/Skills/actions/workflows/validate.yml"><img alt="CI" src="https://github.com/Statusnone420/Skills/actions/workflows/validate.yml/badge.svg"></a>
  <img alt="Public alpha" src="https://img.shields.io/badge/status-public%20alpha-6657E8">
  <a href="LICENSE"><img alt="Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-3B82F6"></a>
</p>

**Diátaxis Docs** is bounded repository memory and evidence-backed documentation for coding agents. **Doctor is the guided front door**; direct commands remain available when you already know the treatment. The system gives humans clean documentation while giving agents a small, trustworthy retrieval path instead of another context firehose.

> **Public alpha:** useful today and actively tested. Review proposed changes before approval and use the same Git safeguards you use for any coding agent.

## 60-second use

Install the canonical [`skills/docs`](skills/docs/SKILL.md) skill, restart your host if needed, and open a repository:

```text
$docs doctor make this repository's documentation trustworthy, bounded, and easy for humans and agents to use
```

Doctor's first pass is read-only. It maps what exists, checks obvious documentation health, separates evidence from inference, proposes the minimum treatment, and stops for your approval.

- **New here?** Follow [Getting started](GETTING_STARTED.md).
- **Ready to install?** Use the [installation guide](INSTALL.md).
- **Know what you need?** Open the [command reference](COMMANDS.md).

## What makes it different

- **Bounded memory:** a human-readable map and current-state route keep retrieval deliberate; measured bytes are telemetry against a provisional 16 KiB optimization target, not a product limit.
- **Evidence before claims:** code, tests, configuration, and confirmed intent outrank stale prose.
- **Quarantine instead of contamination:** uncertain candidates remain non-canonical until corroborated.
- **Cold history:** generated adapters, archives, evaluation payloads, and Git history stay unloaded unless needed.
- **Human documentation first:** Diátaxis helps each page serve a real reader need—not an agent-only memory database.
- **Safe treatment:** structural work previews first; Doctor requires explicit treatment approval and prefers isolated branches or worktrees.

## The workflow

```text
Ask and scope
    ↓
Bounded map + check + diagnosis
    ↓
Evidence-backed treatment manifest
    ↓
STOP — you select and approve treatments
    ↓
Isolated write / update / fix / migrate / cleanup
    ↓
Re-check, show the diff, and stop before commit
```

## Commands

| Command | Purpose | First invocation |
| --- | --- | --- |
| `doctor [goal]` | Guided diagnosis and treatment | Read-only |
| `map` | Show documentation topology and hot path | Read-only |
| `context <task>` | Recall only relevant repository memory | Read-only |
| `check` | Check structure, declared Trust coverage, freshness, and byte telemetry | Read-only |
| `audit [scope]` | Prioritized, evidence-backed findings | Read-only |
| `classify` | Identify the reader need and Diátaxis type | Read-only |
| `write <need>` | Create one verified, focused page | Writes authorized page |
| `update <change>` | Update only documentation affected by a verified change | Writes affected docs |
| `fix <IDs\|scope>` | Revalidate and repair selected findings | Selected repairs only |
| `init`, `migrate`, `cleanup` | Propose structural work | Preview only; later approval required |
| `help [all]` | Show compact command help | No repository inspection |

## Install and compatibility

Codex is the primary tested path and invokes the skill explicitly as `$docs …`. The core follows the Agent Skills structure. Claude can install the same product through the repository's thin marketplace shim; generated adapters also exist for Grok, Copilot, Cursor, Gemini, OpenCode, generic web prompts, and a Codex plugin preview.

Compatibility is evidence-tiered—not universal. Static adapter validation is not the same as a live harness test. See the dated [compatibility matrix](COMPATIBILITY.md) before relying on a preview adapter.

## Safety and evidence

- Network-free, read-only checker built with Python's standard library.
- Repository confinement with symlink, junction, and reparse-point defenses.
- Explicit-only skill invocation and prompt-injection-resistant repository handling.
- Dirty-worktree preservation and approval gates before structural changes.
- **100+ deterministic tests** across Windows and Linux CI.
- Canonical/generated parity checks and sanitized, reproducible evaluation fixtures.

## Benchmark status

The broader 108-trajectory model matrix and complete cross-harness pilots have not run. That limitation is published rather than hidden. See [Benchmark](BENCHMARK.md), [Evaluation](EVALUATION.md), and [Security](SECURITY.md).

## Project status

Diátaxis Docs is an actively developed public alpha. The current priorities are independent repository use, live Claude/Grok/Cline compatibility evidence, installation polish, and a measured beta gate. See the [roadmap](ROADMAP.md) and [changelog](CHANGELOG.md).

## Origin and independence

The project grew from a vanished documentation skill, the [Diátaxis](https://diataxis.fr/) framework, documentation pressure in ADHD Matrix, and the corroboration/quarantine pattern explored in the public [Crimson Desert Report Hub](https://github.com/Statusnone420/Crimson-Desert-Report-Hub). Read the full [origin note](ORIGIN.md).

Statusnone conceived and directed the project; Codex collaborated on planning, implementation, adversarial testing, and review. This is an independent project and is not affiliated with or endorsed by the Diátaxis project, OpenAI, Anthropic, xAI, GitHub, or other harness vendors.

## Contributing

Feedback from real repositories is welcome. Please read [Contributing](CONTRIBUTING.md), use the issue forms, and report security concerns through the private process in [Security](SECURITY.md).

Apache-2.0 licensed. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
