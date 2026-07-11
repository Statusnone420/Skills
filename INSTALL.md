# Installation

In the ChatGPT desktop app, Codex CLI, and supported IDE integrations, user skills are available from `$HOME/.agents/skills`. Install this repository's `skills/docs` directory there, preserving its layout, then invoke it explicitly with `$docs`.

The canonical OpenAI references are [Skills](https://developers.openai.com/codex/skills) and [Plugins](https://developers.openai.com/codex/plugins/build). Plugins are the distribution layer; this repository does not claim a marketplace or plugin installation exists yet.

Set `policy.allow_implicit_invocation: false` to keep invocation explicit. Other harnesses use the generated adapters and their documented enforcement tiers; see [compatibility](COMPATIBILITY.md).

