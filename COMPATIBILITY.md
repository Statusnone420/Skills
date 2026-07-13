# Compatibility

Evidence is dated to 2026-07-12 and grouped by enforcement tier:

| Surface | Tier | Status |
| --- | --- | --- |
| `skills/docs` canonical source | explicit invocation | source and locally tested |
| `adapters/plugin` | unpublished preview | generated; no marketplace install claimed |
| Claude Desktop | marketplace installation shim | live-tested through the plugin picker; typed namespaced invocation is not supported by Desktop |
| Claude Code terminal | namespaced skill | generated and structurally tested; terminal invocation not yet live-tested |
| Copilot, Grok, Cursor | static adapter | generated and tested-static; live smoke not run |
| Gemini, OpenCode | wrapper | generated and tested-static; instruction-based enforcement |
| Generic web prompts | prompt only | generated; repository mutation unavailable |

The `skills/docs` directory is canonical; marketplace and generated files are installation adapters, not forks of the product. Live cross-harness pilots remain an alpha-roadmap gate, and the project does not claim universal compatibility.
