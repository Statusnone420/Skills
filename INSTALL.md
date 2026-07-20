# Installation

Diátaxis Docs is a public alpha. Install from a revision you trust, review proposed changes before approval, and use normal Git safeguards.

## Codex marketplace

Diátaxis Docs 0.1.6 publishes as a Codex repository marketplace plugin. Add the marketplace, install the plugin, and verify that Codex reports it:

```text
codex plugin marketplace add Statusnone420/Skills
codex plugin add diataxis-docs@statusnone-skills
codex plugin list
```

Start a new task after installation so the task catalog loads the plugin's skills. Use `$docs-help` or `$docs-doctor` directly, or keep using the compatible umbrella form `$docs help` and `$docs doctor`. The plugin exposes the umbrella plus 13 focused `$docs-*` skills in the marketplace.

## Manual skill fallback

In the ChatGPT desktop app, Codex CLI, and supported IDE integrations, user skills are also available from `$HOME/.agents/skills`. From a clone of this repository, install the canonical `skills/docs` directory without overwriting an existing destination.

PowerShell (Windows 11):

```powershell
$dest = Join-Path $HOME '.agents/skills/docs'
if (Test-Path $dest) { Write-Error "Destination exists; inspect or update it deliberately: $dest" } else {
  New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
  Copy-Item -Recurse -Path .\skills\docs -Destination $dest
}
$skillPath = Join-Path $dest 'SKILL.md'
if (-not (Test-Path (Join-Path $dest 'SKILL.md') -PathType Leaf)) { throw "Installation verification failed: missing $skillPath" }
Write-Output "Installed: $skillPath"
```

POSIX shell (macOS/Linux):

```sh
dest="$HOME/.agents/skills/docs"
if [ -e "$dest" ]; then echo "Destination exists; inspect or update it deliberately: $dest" >&2; exit 1; fi
mkdir -p "$(dirname "$dest")"
cp -R skills/docs "$dest"
test -f "$dest/SKILL.md" && printf '%s\n' "Installed: $dest/SKILL.md"
```

The exact layout check is `$HOME/.agents/skills/docs/SKILL.md`. Restart the host or start a new task if the skill list is cached, then run `$docs help` and confirm it returns the command list.

The canonical OpenAI references are [Skills](https://developers.openai.com/codex/skills) and [Plugins](https://developers.openai.com/codex/plugins/build). The checked-in Codex package is generated from `skills/docs`; the manual copy remains a compatibility fallback, not a separate edition.

## Claude

Claude's repository-sync interface installs from a marketplace manifest. Statusnone Skills includes that thin installation metadata while keeping `skills/docs` authoritative; this does not create a separate Claude edition of Diátaxis Docs.

In Claude Code, add this repository and install the adapter:

```text
/plugin marketplace add Statusnone420/Skills
/plugin install diataxis-docs@statusnone-skills
```

In Claude Desktop, restart if the plugin list is cached, then attach the skill through `+ → Plugins → Diátaxis Docs → docs` and add `help`, `map`, or another command after the inserted token. The typed namespaced command is not recognized in Claude Desktop; the plugin picker is the supported Desktop invocation path.

In a Claude Code terminal, verify the namespaced skill:

```text
/diataxis-docs:docs help
/diataxis-docs:docs-doctor
/diataxis-docs:docs-help
```

Claude's built-in `/documentation` command remains separate. Use Diátaxis Docs when you want its repository map, bounded memory, evidence rules, health checks, or approval-gated Doctor workflow. Marketplace sync and Claude Desktop picker invocation are live-tested; the Claude Code terminal form remains structurally tested but has not completed a live terminal pilot.

Set `policy.allow_implicit_invocation: false` to keep invocation explicit. Other harnesses use the generated adapters and their documented enforcement tiers; see [compatibility](COMPATIBILITY.md).

After installation, the safest first trial is:

```text
$docs doctor make this repository's documentation trustworthy, bounded, and easy for humans and agents to use
```

The first Doctor response must be read-only and stop before treatment.
