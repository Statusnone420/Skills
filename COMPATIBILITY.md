# Compatibility

Evidence is dated to 2026-07-18 and grouped by enforcement tier:

| Surface | Tier | Status |
| --- | --- | --- |
| `skills/docs` canonical source | explicit invocation | source and locally tested |
| Codex marketplace plugin | explicit focused or umbrella skill | generated and structurally tested; fresh-task live canary is the release gate |
| Claude Desktop | marketplace installation shim | live-tested through the plugin picker; typed namespaced invocation is not supported by Desktop |
| Claude Code terminal | namespaced skill | generated and structurally tested; terminal invocation not yet live-tested |
| Copilot, Grok, Cursor | static adapter | generated and tested-static; live smoke not run |
| Gemini, OpenCode | wrapper | generated and tested-static; instruction-based enforcement |
| Generic web prompts | prompt only | generated; repository mutation unavailable |

The `skills/docs` directory is canonical; `plugins/diataxis-docs` and all generated adapters are installation outputs, not forks of the product. Codex and Claude both expose the umbrella plus 13 focused command skills while preserving their native `$` and `/diataxis-docs:` invocation forms. Live cross-harness pilots remain an alpha-roadmap gate, and the project does not claim universal compatibility.
