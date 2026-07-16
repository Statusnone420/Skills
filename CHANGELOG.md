# Changelog

## Unreleased (2026-07-16)

- Made Init adoption deterministic and engine-owned: the installed `init_closeout.py` entrypoint constructs the schema-3 request, preview, manifest, and receipt, and the model presents the verified result without reconstructing it (#14).
- Repaired the first-run Init journey, including Windows short-path corpus discovery and explicit-scope path identity (#13, #14).
- Grew the deterministic suite to more than 700 tests, adding Init adoption CLI and shared-corpus visibility coverage.

## 0.1.0 — Public alpha (2026-07-13)

- Established one canonical semantic version across Agent Skills metadata, native plugin manifests, generated wrappers, and help output.
- Added a thin Claude marketplace installation shim that routes to the generated adapter without forking the canonical skill.
- Added Doctor as the guided read-only diagnosis and approval-gated treatment workflow.
- Hardened repository identity, worktree isolation, junction/reparse confinement, dirty-worktree preservation, and verification failure reporting.
- Expanded the deterministic suite to more than 100 tests and added Windows/Linux CI.
- Added public-alpha positioning, onboarding, repository safeguards, and community templates.
- Kept cross-harness live pilots and the 108-trajectory matrix explicitly unclaimed.

## Unreleased (2026-07-11)

- Added public proof-first documentation and bounded repository-memory map/state.
- Documented evidence tiers, origin, compatibility limits, and benchmark gaps.
