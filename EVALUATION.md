# Evaluation

The evaluation workflow uses deterministic fixtures, disposable repositories, recorded attempts, and sanitized visible artifacts. The public-alpha evidence currently includes five matched safety-pressure pairs, a preserved `init` approval-boundary failure and remediation, more than 700 deterministic tests, plugin validation, and a 5/5 live Windows junction probe.

## Layered regression gates

The public alpha uses four gates so quality evidence does not become an uncontrolled model-usage campaign:

1. **Deterministic contract gate:** every change runs standard-library tests for safety, checker behavior, adapter parity, and command contracts.
2. **Marketplace assembly gate:** release packaging must prove that a real Codex marketplace entry resolves to an identity-aligned package whose umbrella and 13 focused skills are present, explicit-only, and generator-owned.
3. **Sanitized trajectory gate:** host-neutral receipts record semantic answers, documentation-owned actions, host/external overhead, visible diagnostics, and exposed usage counters. Raw traces, hidden reasoning, private paths, and credentials are never public inputs.
4. **Capped live canary:** release candidates may run a small, explicitly approved campaign against stable mapped, missing-map, and hostile fixtures. A campaign is limited to 12 runs; the checked-in example authorizes none.

## Product contract and research provenance

This is a repository documentation operating system, not a prompt concatenator. Init establishes a bounded map/highway once; Doctor diagnoses that highway read-only; context routes task-relevant truth; check reports structural evidence; and separate approvals drive any lifecycle mutation. Findings use content-derived stable IDs and full fingerprints. State, findings, verified hashes, events, and complete disposition manifests provide committed operational memory without an external database or daemon. Protected public entrances and local-only knowledge remain distinct routes with distinct authorization.

The design uses established observations as motivation, not endorsement of this repository's exact rubric or weights:

- [OpenAI Evals API](https://platform.openai.com/docs/api-reference/evals/run-output-item-object) documents reproducible evaluation artifacts and run outputs.
- Anthropic's [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) describes verifiable end states and interaction-quality rubrics for multi-turn agents.
- Aider publishes [code-editing leaderboard methodology](https://aider.chat/docs/leaderboards/edit.html), including task completion and edit-format compliance.
- The [SWE-agent paper](https://arxiv.org/abs/2405.15793) evaluates agent-computer interfaces for software engineering tasks.
- [Lost in the Middle](https://arxiv.org/abs/2307.03172) motivates measuring retrieval and context placement rather than assuming a larger context is always better.
- IBM's [content-quality guidance](https://www.ibm.com/docs/en/technical-content?topic=standards-content-quality) and the [OASIS DITA standard](https://www.oasis-open.org/standard/dita/) motivate task-oriented, structured documentation.
- [Diátaxis](https://diataxis.fr/) motivates separating reader needs and documentation types.

These references do not validate the local 100-point operationalization. The structural percentage, Trust precedence, scope rules, and byte telemetry are versioned repository contracts tested here. Freshness changes Trust, never the structural score. The historical no-schema/no-hash stance was a sensible alpha safeguard against speculative infrastructure; cross-session identity and drift evidence now justify the small committed control plane described above.

Validate a receipt locally:

```text
python tools/trajectory_gate.py evals/trajectory/bulwark-map-accepted.json
```

The gate checks reader outcomes rather than exact prose: where to start, what to trust, current truth, generated/cold material, needs attention, and deliberately unloaded material. It separately counts documentation-owned and host/external actions, but rejects repository-evidence actions mislabeled as external overhead. The checker action carries sanitized rubric version, percentage, and meter evidence; the displayed health meter must match it. Cumulative token totals without a paired host control are labeled unattributed rather than charged entirely to Diátaxis Docs.

## Documentation-health rubric v2

The canonical checker emits a versioned `health` object with raw counts, earned weight, available weight, a deterministic structural percentage, and the plain-text meter. This is a reproducible structural baseline according to `$docs`, not a universal Diátaxis score and not evidence of factual accuracy.

The quality dimensions come from established documentation and agent-engineering practice. The exact weights are Diátaxis Docs rubric v2: a versioned, testable local operationalization for comparison, not an externally validated scientific or universal constant. The structural percentage does not prove factual accuracy. Scope, semantic coverage, and hash freshness are separate evidence with explicit provenance. Freshness is implemented in v2 as a Trust gate, not assigned numeric weight until that weight has independent evidence.

| Category | Weight | Evidence |
| --- | ---: | --- |
| Maintained entry point | 20 | selected map exists and is readable |
| Path safety | 15 | maintained paths remain confined and avoid reparse or outside-link findings |
| Link integrity | 20 | valid local targets / checked local targets, gated by a useful entry |
| Anchor integrity | 10 | valid referenced anchors / checked anchors; no references is neutral/full |
| Reachability | 25 | maintained documents reachable from a useful selected map / maintained documents |
| Title clarity | 10 | usable, unique primary titles / maintained documents |

Entry credit is five points for a readable map, five for a usable H1, and ten for a valid navigation route. A complete single maintained document (H1, body paragraph, and secondary heading) is the explicit alternative. Self-only stubs receive no link, anchor, or reachability credit. Every division is zero-guarded, category weights sum to 100, and the percentage rounds by `int(earned_weight + 0.5)`. The meter fills `floor(percentage / 5)` of exactly 20 literal cells. Health repairs must improve the measured evidence; model judgment does not change the number.

`structure_status` and `trust_status` are separate. Trust reports the normalized, deduplicated union of configured current-truth routes, valid state hot/verified document and source routes, and map links carrying the exact same-line `<!-- docs:current -->` or `<!-- docs:authoritative -->` marker. It reports numerator, denominator, every route, and per-route provenance. Empty coverage is unverified. Precedence is blocked (open P0), stale, partial, then verified; an open P1 still prevents an overall healthy verdict.

State-declared verified document/source routes use newline- and NFC-normalized SHA-256 text digests with a bytes fallback. Freshness changes Trust, never the structural score. Selected map/current-state size is provenance-tagged telemetry only: `provisional_target_bytes: 16384` records an optimization hypothesis, not a product limit, compliance rule, health input, or reason to delete/compress truth.

Route tests use generated one-change mutations, invariant checks, deterministic cases, and retained named regressions. This applies Hypothesis/property-based testing ideas without adding Hypothesis, copying its source, or adding any runtime dependency.

## Evidence receipt and documentation corpus v1

Evidence receipt v1 is a sanitized product-evidence contract, not a transcript format. It records repository and checker identity, selected-surface facts, category-level rubric evidence, score gates, deterministic findings, semantic findings, unresolved candidates, Doctor evidence, Git/write state, and a complete index of unavailable evidence. `completed` with zero semantic findings is distinct from `not_assessed`; unavailable values are null and never become a product failure or numeric zero.

The calibration corpus pins Cline, Supabase, Docusaurus, Vite, uv, and Kubernetes Website to immutable commits. Explicit preparation creates sparse ignored checkouts without dependencies or site builds. The runner then requires each checkout to be clean, detached, and at the exact commit; hashes bounded configuration bytes and reads inert entry text; and verifies Git status is unchanged. Only the existing Mintlify contract produces a structural score. Custom MDX, Docusaurus, VitePress, MkDocs, and Hugo evidence remains deterministic but structurally `not_assessed` until an inert provider contract exists.

Literal H1 and scalar frontmatter-title observations are collected for calibration but do not affect rubric v2. The corpus baseline is evidence for a later scoring decision, not permission to tune `HEALTH_WEIGHTS`, useful-entry gating, or provider behavior in this release. No external model or API is required; an optional semantic lane must record its evaluator and cannot calculate or change the deterministic score.

## Shared-engine dogfood

Fresh isolated Codex agents ran the same canonical skill against the same repository state on Windows 11 on 2026-07-11:

| Command | Observed result | Remaining limitation |
| --- | --- | --- |
| `map` | Literal path tree, one checker execution, 1,686 measured current-route bytes against the then-provisional 16,384-byte optimization target, and both known unreachable planning files | None observed in the final probe |
| `check` | Same current hot-path size and findings from one checker execution; no source, help, Git, or finding-file detour | None observed in the final probe |
| `context` | Bounded explanation from three target-repository evidence files; generated contents, tests, and validation stayed cold | One unnecessary name-only adapter-directory listing remained |
| `update` | Disposable dirty-worktree fixture changed only two affected docs, preserved source anchors, unrelated dirty files, and an untracked user draft | One focused test-runner attempt stopped when the runner was unavailable |

Earlier probes are retained as failures rather than overwritten. They exposed absolute-versus-relative checker arguments, accidental skill-file hot-path promotion, broad context corroboration, repository inventory, and repeated missing-runner probes. Each accepted rule maps to one observed failure; the literal map tree remained unchanged.

The original release proposal included six scenarios × skill/baseline × three repetitions × Codex/Claude/Grok (108 trajectories). It has not run and is not the public-alpha default. Cross-harness compatibility pilots remain incomplete. Infrastructure failures remain failures and limitations, never positive results.

### Adapter prompt telemetry

Task 9 replaced the legacy all-command concatenation with command-specific progressive disclosure. The generator measured these UTF-8 prompt sizes before regeneration:

| Command | Bytes |
| --- | ---: |
| doctor | 21,840 |
| init | 20,658 |
| context | 4,776 |
| write | 10,484 |
| update | 10,862 |
| audit | 3,214 |
| fix | 10,412 |
| map | 9,705 |
| classify | 3,227 |
| migrate | 10,492 |
| check | 8,135 |
| cleanup | 10,511 |
| help | 4,081 |

The 32,000-byte value was the Task 9 packaging regression guard with 10,160 bytes of headroom over that run's observed maximum. It was not a product limit, health input, or evidence-backed industry standard. The separate 16,384-byte repository hot-path figure remains provisional, provenance-tagged optimization telemetry only.

No hidden reasoning, credentials, private paths, or private repository material are part of the public record. Raw task identifiers and unsanitized traces remain private; only final outputs, diffs, concise tool events, and measured counters may be exported.

### Task 10 observed local dogfood

On 2026-07-14, a local-only checker harness exercised 13 disposable repository conditions: healthy, no-docs, large slop with unique truth, conflicting intent, stale source, archive-heavy, stub map, vendor symlink, merged-state conflict, dirty worktree, no-Git, local-only authority, and protected public surfaces. All 13 repeated JSON results were deterministic, all 13 initial previews were zero-write, and all 13 privacy checks found no absolute fixture path or synthetic private sentinel in output. No external model was invoked, so these measurements cover deterministic routing and safety contracts, not model retrieval quality.

The no-doc case returned an adoption preview with `content_reads: 0`; the large-slop case returned a bounded batch-limited preview without opening the unique fact; stale and merged state returned blocked Trust outcomes; the local-only case returned choice-required with present-uninspected candidates and absence claims disallowed; and protected-surface previews reported 9 synthetic surfaces. The authorized Cline local-authority run found a choice boundary with two local candidates, then an explicit local scope planned 12 files/94,889 bytes with zero content reads; selected read-only retrieval verified nine staged plan entries, including Chat Calm and performance work. No local body, private route, or map was copied into shared state or public output.

A safe local checkout with a configured remote supplied the public-repository evidence: 10 protected surfaces were inventoried conservatively, the evidence was incomplete and scope-limited, and no relocation or mutation was attempted. No network clone was run. The 16,384-byte hot-path value and the measured prompt maximum remain telemetry/provisional targets; Task 10 does not justify a final hard threshold or weighting. After the deterministic Init rework, the regenerated maximum is 24,679 bytes (`doctor`), down from the 26,596 bytes observed here.

### Claude/Cline MDX canary

On 2026-07-17, Claude with Sonnet 5 High exercised Diataxis Docs v0.1.1 against a synced local fork of the public `cline/cline` repository. The checkout was clean and the session remained read-only. The target `docs/` surface contained 129 tracked files, including 110 authored `.mdx` pages and a Mintlify `docs.json` navigation manifest.

`help` and `help all` reported v0.1.1. `map` and `check` both selected the root marketing `README.md` and returned the same 20% structural score. Root-scoped `doctor` expanded the Markdown scan, returned 28%, and grouped 182 findings into four treatments. Several reported relative-link and anchor defects were independently reproduced, but 164 reachability findings included agent and tooling Markdown that may intentionally sit outside the reader-facing map.

The decisive canary used explicit scope: `doctor --details --scope docs`. Its bounded receipt honored `docs`, scanned 34 immediate entries at the metadata level, selected zero Markdown candidates, read zero content files, returned 0%, classified the corpus as having no memory, and recommended `init --scope docs`. It also reported the scoped public documentation surface as unprotected internal documentation eligible for disposition. A following `audit docs` returned zero findings and explicitly stated that `.mdx` was outside the skill's Markdown-only audit surface. No Init preview or treatment was approved.

This is a P1 public-alpha release blocker: a common framework-native documentation corpus can be misdiagnosed as empty, receive a meaningless health score, and be steered toward adoption. The evidence does not establish P0 because every observed command stayed read-only, disclosed zero content reads, and required a separate approval before mutation. Escalate to P0 if an Init preview can propose moving, replacing, publishing, archiving, or deleting an unsupported MDX corpus.

The smallest safe repair is fail-closed format detection before broad MDX support: recognize an unsupported documentation corpus, report health as unmeasured rather than 0%, protect it from disposition, require an explicit user action, and do not recommend Init as though the scope were empty. First-class MDX/Mintlify scanning, navigation, lifecycle, memory, and closeout support is a separate compatibility change with its own regression matrix.

The 2026-07-17 stabilization implements that boundary plus generic inert-text MDX compatibility without adding a Mintlify dependency. One canonical format policy now covers `.md`, `.markdown`, and `.mdx` across discovery, scanning, reachability, protection, lifecycle validation, and closeout. MDX imports, exports, JSX, JavaScript, and components are never executed. A valid Mintlify `docs.json` without the requested maintained map now fails closed as `unsupported documentation navigation manifest`: no score, clean/empty verdict, or Init recommendation is produced. Direct Init adoption also stops as waiting before emitting a preview or approval receipt. An explicit MDX map remains measurable through ordinary Markdown headings and links, while the manifest and MDX pages default to protected/retain. Full `docs.json` navigation interpretation remains unsupported.

A synthetic Cline-shaped regression records the original failures and their repair. Fresh group proof covers all 771 current tests: 200 core, 392 lifecycle, and 179 trajectory tests passed on both Windows and WSL Ubuntu. The latest Windows timings were 38 seconds for core, 465 seconds for lifecycle, and 25 seconds for trajectory; native ext4 Ubuntu took 4, 38, and 14 seconds respectively. The same observable runner, exact 34-module partition, Python 3.14 pin, and three test groups now drive GitHub Actions. Adapter regeneration/parity and the repository checker remain required closeout gates.
