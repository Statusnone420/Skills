# Statusnone Skills

**Diátaxis Docs** — Bounded repository memory. Evidence-backed documentation.

Statusnone Skills presents Doctor as the guided front door for documentation work: assess repository health, verify claims against evidence, and propose the minimum safe treatment before any edits. Direct commands remain independently usable for experienced users. It is independent from Diátaxis and uses fresh prose.

## 60-second use

1. Read the [getting started guide](GETTING_STARTED.md).
2. Invoke `$docs context release notes` in Codex.
3. Use `$docs write a troubleshooting page for the parser`.
4. Run `$docs check` and review the resulting sources, risks, and deliberately unloaded material.

## Architecture

The canonical skill lives in [`skills/docs/`](skills/docs/SKILL.md). Generated adapters are previews or host-specific wrappers; the source remains authoritative. The optional checker is read-only and standard-library only. See [architecture](ARCHITECTURE.md).

## Install and commands

See [installation](INSTALL.md) and the [command reference](COMMANDS.md). Codex supports explicit `$docs`; implicit invocation is disabled by policy.

## Benchmark status

Tasks 1–4 provide deterministic fixtures, RED captures, five matched safety-pressure pairs, preserved init failure/remediation, 48 local tests, plugin validation, and a 5/5 Windows junction probe. The 108-run matrix has not run; see [benchmark](BENCHMARK.md).

## Compatibility

Compatibility is evidence-tiered, not universal. The generated `adapters/plugin` bundle is an unpublished preview. See the dated [compatibility matrix](COMPATIBILITY.md).

## Origin

Read the [origin note](ORIGIN.md), including the public [Crimson Desert Report Hub](https://github.com/Statusnone420/Crimson-Desert-Report-Hub). Statusnone conceived and directed this project; Codex collaborated on planning, implementation, adversarial testing, and review.
