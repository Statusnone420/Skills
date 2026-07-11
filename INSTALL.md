# Installation

In the ChatGPT desktop app, Codex CLI, and supported IDE integrations, user skills are available from `$HOME/.agents/skills`. From a clone of this repository, install the canonical `skills/docs` directory without overwriting an existing destination.

PowerShell (Windows 11):

```powershell
$dest = Join-Path $HOME '.agents/skills/docs'
if (Test-Path $dest) { Write-Error "Destination exists; inspect or update it deliberately: $dest" } else {
  New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
  Copy-Item -Recurse -Path .\skills\docs -Destination $dest
}
Test-Path (Join-Path $dest 'SKILL.md')
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

The canonical OpenAI references are [Skills](https://developers.openai.com/codex/skills) and [Plugins](https://developers.openai.com/codex/plugins/build). Plugins are the distribution layer; this repository does not claim a marketplace or plugin installation exists yet.

Set `policy.allow_implicit_invocation: false` to keep invocation explicit. Other harnesses use the generated adapters and their documented enforcement tiers; see [compatibility](COMPATIBILITY.md).
