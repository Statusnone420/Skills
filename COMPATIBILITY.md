# Compatibility

Evidence is dated to 2026-07-11 and grouped by enforcement tier:

| Surface | Tier | Status |
| --- | --- | --- |
| `skills/docs` canonical source | explicit invocation | source and locally tested |
| `adapters/plugin` | unpublished preview | generated; no marketplace install claimed |
| Claude repository sync | marketplace installation shim | generated and structurally tested; live sync pending |
| Copilot, Grok, Cursor | static adapter | generated and tested-static; live smoke not run |
| Gemini, OpenCode | wrapper | generated and tested-static; instruction-based enforcement |
| Generic web prompts | prompt only | generated; repository mutation unavailable |

The `skills/docs` directory is canonical; marketplace and generated files are installation adapters, not forks of the product. Live cross-harness pilots remain an alpha-roadmap gate, and the project does not claim universal compatibility.
