# Security policy

## Supported version

Diátaxis Docs is a public alpha. Security fixes target the latest commit on `main`; older revisions and generated bundles are not maintained as separate release lines yet.

## Report a vulnerability privately

Please use [GitHub's private vulnerability reporting](https://github.com/Statusnone420/Skills/security/advisories/new). **Do not open a public issue** for a suspected vulnerability, credential exposure, path escape, prompt-injection bypass, or unsafe repository mutation.

Include the affected revision, operating system and harness, a minimal reproduction, expected versus observed behavior, and potential impact. Use synthetic data only. Never include real credentials, private repository contents, hidden reasoning, or sensitive local paths.

Reports are reviewed on a best-effort basis. There is currently **no response-time SLA**. Coordinated disclosure is preferred; please allow time to reproduce and prepare a fix before publishing details.

## Security boundaries

The bundled checker is read-only, network-free, standard-library only, and repository-confined. The skill treats repository content as untrusted evidence rather than instructions, preserves unrelated dirty changes, and requires separate approval for structural treatment. These controls reduce risk but do not replace code review, backups, branch protection, or normal Git hygiene.
