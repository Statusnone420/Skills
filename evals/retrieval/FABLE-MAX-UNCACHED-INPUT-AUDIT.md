# Fable Max causal audit: 0.1.7 candidate uncached-input regression

Date: 2026-07-20 (analysis of the 2026-07-20 campaigns). Scope: read-only causal audit of why candidate `47d6e76` measures +49.6% cumulative uncached input against the frozen July 11 bounded recipe despite a smaller hot path, a correct four-action route, and near-equal duration. No product code, tests, adapters, manifests, campaign constants, collectors, completed results, Git history, plugin installations, or Codex configuration were modified. This report is the only file created.

Sanitization: no task/thread IDs, no user-profile absolute paths, no private memory contents. Runs are referenced by their public `run_id`s. Raw sessions were located via the private manifests under `evals/results/drafts/` (Git-ignored) and are the same files whose `raw_session_sha256` digests appear in the public results.

---

## 1. Binary causal verdict

**NOT A PRODUCT DEFECT.** The +49.6% uncached-input regression against the frozen July recipe is dominated by host-default global-memory retrieval and uncontrolled provider prompt-cache warmth, not by candidate `47d6e76`'s bytes or route. The product-attributable share of the gap is ≈ +12% versus the July median — inside the frozen 25% gate on its own. Consequently the frozen Desktop uncached-input comparison, **as executed**, is causally invalid for release gating (proof in §6); the threshold itself is untouched and untouchable.

Under the head-scratcher's taxonomy the answer is **E**, with measured contributions to the +10,023-token median-vs-median gap:

| Cause | Contribution | Share |
| --- | ---: | ---: |
| C. Host global-memory retrieval (registry scan in the median candidate run) | +7,654 | 76.4% |
| B. Real installed-skill invocation (host injection +736; host-mandated full re-read +1,053) | +1,789 | 17.8% |
| A. Product route content (shared contract +1,208 − July contract load −1,230 + JSON checker receipt +1,016 + read noise +139) | +1,133 | 11.3% |
| D-adjacent mechanics (re-injected model output +222, cache-alignment slop −1,067, wrapper chrome +431) | −414 | −4.1% |
| **Total (exactly balances the host token counter)** | **+10,023** | 100% |

The specific hypothesis that `--json --agent` returns substantially more structure than map requires is **refuted in its `--agent` half**: `--agent` adds **zero** output bytes (measured: 3,363 bytes with and without it; it only converts exit 1-with-findings into exit 0). The receipt cost is `--json` (3,363 B) versus plain text (279 B), ≈ +1,016 tokens — real, second-order (≈10% of the gap, ≈5% of the July median), and partially self-funding because it eliminated July's separate byte-measurement action.

## 2. Executive summary

The candidate is real product progress: against released 0.1.6 under the *same* host policy (bare product invocation, memory on), it is 18.3% faster, 14.1% cheaper on uncached input, 10.5% cheaper on total tokens, improved correctness from 1 pass/1 partial/1 fail to 3/3, and eliminated wrong-checker attempts. That is the like-for-like comparison, and this audit confirmed it is like-for-like: **all six** bare-invocation runs (three 0.1.6, three candidate) performed host-memory operations, and both sets faced the same cache-warmth lottery.

The July recipe arm is not like-for-like. Three separately measured mechanisms inflate the candidate's number against it:

1. **Host memory protocol.** The identical 41,542-character developer message (SHA-256-identical across all six audited runs) instructs the agent to "use memory by default" whenever the query concerns a repository named in the embedded memory summary — and this repository is named there. A bare `docs-map` invocation therefore triggers a registry scan (~24–38 K chars of tool output, ≈7.7–10.7 K tokens) as *prescribed host behavior*; it did so in 3/3 candidate runs and 3/3 released-0.1.6 runs. The July recipe prompt's bounding clause ("Make no repository calls beyond the contract load and those three evidence actions…") suppressed that protocol in 2 of 3 July runs. The two arms effectively ran under different host policies.
2. **Provider cache warmth.** Every run starts from a ~23.1–23.9 K-token context. Whether the ~11,264-token host preamble segment was already in the provider cache swings a run's uncached total by exactly that amount — 56% of the July median. Warm/cold state was uncontrolled and asymmetric: the July batch launched three tasks 1–3 s apart (two warm starts), the candidate batch launched one solo task (warm, seeded by an earlier rejected batch) and two simultaneous tasks (both cold). The cold/warm arithmetic reproduces across all twelve sessions inspected (six audited in depth; the two clean July runs cross-validate to within 90 tokens: 20,201 cold vs 8,847 + 11,264 = 20,111 cold-equivalent).
3. **Invocation mechanics.** A real installed-skill run pays the host-injected skill block (+736 tokens net on turn 1) and the host rule that the agent read the selected `SKILL.md` completely even though its body was injected (+1,053). The July arm pastes a recipe and pays neither.

Strip only the memory-scan bytes from the median candidate run and it lands at 22,570 uncached ≈ **+11.7%** versus the July median — passing the frozen gate. An independent bottom-up build-up of all product + invocation costs lands at the same ≈ +12%. The product is not the problem; the measurement design is. The already-declared release-gate item — a memory-isolated Codex CLI campaign — is the correct next step, now specified as a paired design (§10).

## 3. Per-run operation and token table

All token numbers are host-reported (`token_count` events; cumulative uncached = final `input_tokens − cached_input_tokens`, identical to the collector's metric). "Turn-1 prefix" is the cached token count of the first request: **9,984 = cold** (only the static developer message was warm), **21,248 = warm** (developer message + 11,264-token host preamble segment warm). Char→token conversions inside a wrapper are scaled to the exact per-run counters (bucket totals exact; intra-wrapper splits ±5%).

### Campaign runs (model turns = requests; wrappers = `custom_tool_call` items; commands = shell invocations inside wrappers)

| Run | Dur (s) | Turns | Wrappers | Shell cmds | Turn-1 prefix | Turn-1 uncached | New tool-output | Re-injected output | Cache slop | **Total uncached** | Memory tool-output |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| july11-bounded-recipe-1 | 144.0 | 5 | 4 | 4 | cold | 13,143 | 1,746 | 2,066 | 3,246 | **20,201** | 0 |
| july11-bounded-recipe-2 | 168.7 | 7 | 6 | 6 | warm | 1,879 | 11,619 | 4,416 | 4,581 | **22,495** | ≈8,979 (29,626 ch) |
| july11-bounded-recipe-3 | 94.6 | 5 | 4 | 4 | warm | 1,882 | 1,756 | 2,023 | 3,186 | **8,847** | 0 |
| docs-map-0.1.7-candidate-1 | 189.1 | 7 | 6 | 9 | warm | 2,618 | 15,272 | 5,112 | 3,579 | **26,581** | ≈10,720 (37,653 ch) |
| docs-map-0.1.7-candidate-2 | 136.9 | 5 | 4 | 6 | cold | 13,879 | 11,878 | 2,288 | 2,179 | **30,224** | ≈7,654 (27,190 ch) |
| docs-map-0.1.7-candidate-3 | 155.7 | 7 | 6 | 7 | cold | 13,877 | 12,742 | 5,167 | 3,813 | **35,599** | ≈8,394 (28,783 ch) |

Every row balances exactly: total = turn-1 + new tool-output + re-injected output + slop.

Context rows (call sequences audited; token ledgers not re-derived): the three released-0.1.6 runs (uncached 35,202 / 35,175 / 36,016; 8/8/6 wrappers) each performed **1–3 host-memory operations including registry reads**, and had turn-1 prefixes cold/cold/warm. The three no-skill runs were all cold (turn-1 uncached ≈13,060 each).

### Operation sequences (what each wrapper did)

- **july-1** (PARTIAL): contracts read (repo `SKILL.md`+`commands.md`, one command, 5,836 ch) → `README` (1,167) → `STATE` (801) → repo checker plain, exit 1 (482) → final 924 ch. Skipped byte measurement and named no findings — its PARTIAL scoring reflects doing *less* than the contract requires.
- **july-2** (PASS): **memory registry scan (`rg` over the registry, 29,626 ch)** → contracts (5,836) → `README` → `STATE` → repo checker (482) → repeat checker + byte stats (665) → final 1,434 ch.
- **july-3** (PASS): contracts (5,750) → `README` → `STATE` → checker + two byte stats in one command (562) → final 1,581 ch.
- **cand-1** (PASS): one wrapper = **3 parallel commands**: focused-skill re-read + shared skill + **memory registry read** (28,591 ch total) → one wrapper = rollout-summary reads (8,676 ch, July-11 map-session recaps) → `README` → `STATE` → installed checker `--json --agent` (3,970) → **`rg` across rollout summaries (10,015 ch)** → final 3,096 ch.
- **cand-2** (PASS, median): one wrapper = **3 parallel commands**: focused-skill re-read (3,740 ch) + shared skill (4,290 ch) + **memory registry scan (27,190 ch)** → `README` → `STATE` → installed checker `--json --agent` (3,970) → final 2,797 ch.
- **cand-3** (PASS): one wrapper = 2 parallel commands: both skills combined (7,986 ch) + **memory registry scan (24,137 ch)** → rollout-summary read (3,799) → `README` → `STATE` → installed checker (3,970) → memory `Select-String` (847) → final 2,426 ch.

### Per-turn ledgers for the two median runs (uncached per request)

| Turn | july-1 (cold) | cand-2 (cold) | What entered context (cand-2) |
| --- | ---: | ---: | --- |
| 1 | 13,143 | 13,879 | base context + prompt (+ injected skill block, 3,859 ch — the +736) |
| 2 | 2,910 | 11,846 | wrapper #1 output: two skills **+ 27,190-ch memory scan** |
| 3 | 1,418 | 1,158 | `README` |
| 4 | 1,135 | 896 | `STATE` |
| 5 | 1,595 | 2,445 | checker JSON receipt (3,970 ch) |
| **Σ** | **20,201** | **30,224** | |

## 4. Quantified attribution of the regression

Median vs median (cand-2 30,224 vs july-1 20,201; both cold-start, both 4 wrappers, both 5 turns — an unusually clean pairing):

| # | Component | Tokens | Evidence |
| --- | --- | ---: | --- |
| 1 | Host-injected focused-skill block (turn 1), net of the candidate's shorter user prompt | **+736** | turn-1 uncached 13,879 vs 13,143; injected block 3,859 ch measured in-session |
| 2 | Host-mandated full re-read of the already-injected focused `SKILL.md` | **+1,053** | 3,740-ch segment in wrapper #1 |
| 3 | Shared `docs/SKILL.md` load (product route) | **+1,208** | 4,290-ch segment |
| 4 | July contract load the candidate avoided (frozen-commit `SKILL.md` 2,727 B + `commands.md` 2,783 B) | **−1,230** | 5,836-ch July output |
| 5 | Host global-memory registry scan | **+7,654** | 27,190-ch segment inside wrapper #1 |
| 6 | Checker receipt: `--json --agent` (3,970 ch) vs July plain exit-1 output (482 ch) | **+1,016** | both measured in-session; local reruns: JSON 3,363 B, plain 279 B, old frozen checker plain 285 B |
| 7 | `README`/`STATE` + wrapper chrome residual | **+431** | identical file bytes (970/619); exec-wrapper text differs |
| 8 | Re-injected model output (reasoning/interstitials of non-final turns) | **+222** | 2,288 vs 2,066 |
| 9 | Cache-alignment slop (previously seen tokens re-billed) | **−1,067** | 2,179 vs 3,246 |
| | **Total** | **+10,023** | matches 30,224 − 20,201 exactly |

Notes.

- Point 6 decomposed: `--agent` contributes **0 bytes** (JSON byte-identical with/without; exit-code semantics only; `--agent` without `--json` is a usage error). The 3,363-B receipt directly supplies four required map elements (findings, hot-path files+bytes, `provisional_target_bytes`, shared health output) that July had to obtain with a *separate* byte-measurement action (july-2 spent one extra wrapper + turn ≈ +1.6 K uncached on it; july-1 skipped it and scored PARTIAL).
- Points 1–2 are invocation mechanics (host injects the skill, host rules require reading the selected `SKILL.md` completely anyway). Point 3 is the product's designed one-hop route ("Load and follow the sibling Diátaxis Docs skill, including its shared safety, evidence, health, and result contracts"). Net product contract bytes vs July (3+4): **−22 tokens** — the candidate's contract content is not larger than July's; it is differently shaped.
- Point 5 is host behavior under the host's own memory protocol (§5). In cand-1/cand-3 it is larger (10,720 / 8,394 tokens) and also added extra wrappers and turns, which is why their re-injection and slop are ~2–3 K higher than cand-2's.
- Final-answer length (candidate 2,426–3,096 ch vs July 924–1,581 ch) costs **zero** uncached input: the final message never re-enters context. It costs output tokens only. The +91.6% total-token gap is dominated by *cached* re-prefill (turn count × context size), which is the cheap token class; the gate correctly targets uncached input.

Counterfactual gates (no threshold change; arithmetic only):

| Scenario | Candidate median | July median | Ratio | 25% gate |
| --- | ---: | ---: | ---: | --- |
| As measured | 30,224 | 20,201 | +49.6% | FAIL |
| Median run minus memory-scan bytes only | 22,570 | 20,201 | +11.7% | PASS |
| Bottom-up product+invocation build-up, cold base | 20,201 + 2,370 | 20,201 | +11.7% | PASS |
| Same build-up on a warm base | 8,847 + 2,370 | 8,847 | +26.8% | FAIL |

The last two rows are the same product delta; only cache weather differs. A ratio gate over a base dominated by a fixed ~13.1 K cold-start term is not warmth-invariant — see §6.

## 5. Product cost versus host/memory cost

Hot-path byte inventory (measured file bytes; tool-output chars include exec chrome):

| Content | July recipe arm | Candidate arm | Owner |
| --- | ---: | ---: | --- |
| Contract text loaded | 5,510 B (frozen `SKILL.md` + `commands.md`) | 3,694 B injected + 3,694 B re-read + 4,286 B shared | product shape; the duplicate read is host-mandated |
| Checker output | 285 B plain (old checker; new checker plain is 279 B) | 3,363 B JSON receipt | product choice (`--json --agent` pinned in the map contract) |
| Byte measurement | separate action (or skipped → PARTIAL) | included in receipt | product improvement |
| `README` + `STATE` | 1,589 B | 1,589 B | equal |
| Host skill injection | none | 3,859 ch user message | host |
| Host memory retrieval | 0 / 0 / 29,626 ch | 37,653 / 27,190 / 28,783 ch | host protocol |
| Cache warmth swing | ±11,264 tokens per run | ±11,264 tokens per run | provider; uncontrolled |

Bottom line per bare-invocation run: product + invocation overhead ≈ **+2.4–2.9 K tokens** over the July arm; host memory pass ≈ **+7.7–10.7 K tokens**; cache-warmth lottery ≈ **±11.3 K tokens**. The host-side terms are 3–5× the product term and are the entire reason the gate fails.

Why the memory pass is host-default, not model whim: the developer message (byte-identical in all six runs, hash-verified) contains a memory protocol — skip memory only for clearly self-contained requests; "Use memory by default when ANY of these are true: the query mentions workspace/repo/module/path/files in MEMORY_SUMMARY below…"; then search the registry (`MEMORY.md` under the Codex profile) and open at most 1–2 rollout summaries it points to. The embedded memory summary names this repository and its Diátaxis Docs work specifically. A bare "map this repository" invocation is squarely inside "use memory by default" — and 6/6 bare-invocation runs (candidate + released 0.1.6) complied, versus 1/3 July-recipe runs whose prompt bounded retrieval away. The registry itself was byte-stable all evening (mtime July 19; both batches saw identical memory bytes), so this is policy exposure, not memory growth. The rollout summaries the candidates opened are July-11 map-session recaps — prior sessions about this repository — which makes memory consultation look *more* relevant, not less.

Also answered here: **the `tool_call_wrappers` metric hides parallel filesystem operations.** Measured: cand-2's wrapper #1 contained three parallel shell commands (two skill reads + one 27 K-char memory scan); cand-1's six wrappers contained nine commands; july-3's wrapper #4 ran the checker plus two byte-stats in one command. Wrapper counts are comparable only within a condition and are not an operation count; "4 wrappers" told us nothing about whether memory was read. Interpretation of the earlier "8 → 6 wrappers" improvement claim survives directionally (same-condition shape), but cross-condition wrapper comparisons and any "N wrappers ⇒ no memory expansion" inference are invalid.

## 6. Is the frozen comparison still valid?

**For duration: usable.** The candidate passed it (+8.1%) despite carrying the memory reads, and duration is not warmth-coupled to first order.

**For uncached input: causally invalid as executed**, on four measured grounds:

1. **Asymmetric host policy.** The July prompt's bounding clause suppresses the host's own memory protocol; the candidate prompt is a bare real invocation and cannot contain such language without ceasing to measure the product route. Measured exposure: memory reads in 1/3 July runs vs 6/6 bare-invocation runs. This is a between-arms treatment difference unrelated to product bytes.
2. **Uncontrolled cache warmth worth 56% of the July median.** Turn-1 warm-vs-cold is exactly ±11,264 tokens, decided by launch topology and provider cache lifetime, neither specified by the campaign. July's batch got two warm starts; the candidate's batch got one. The condition medians happened to pair two cold runs, but nothing in the design ensures that; the two clean July runs prove the arithmetic (20,201 cold ≈ 8,847 warm + 11,264).
3. **The ratio gate is not warmth-invariant.** The identical product delta reads +11.7% on a cold base and +26.8% on a warm base (§4). A gate whose verdict flips with cache weather, holding product bytes fixed, is not measuring the product.
4. **Reference-arm inconsistency.** The July median run (july11-bounded-recipe-1) is the PARTIAL run that skipped the byte measurement and named no findings — the cost bar was set by a run that did less than the contract requires (its own campaign noted the omitted exit-1 clause and restored it in the frozen constant for future runs).

None of this weakens the threshold or rescores anything: the 25% gate, the 3/3 correctness requirement, and the completed results stand as recorded. The conclusion is that the *Desktop memory-on* execution of the uncached-input arm cannot carry release weight — which the original campaign already anticipated: RESULTS-2026-07-20.md lists Desktop memory as a limitation and its release gate already requires "a separate memory-isolated Codex CLI campaign for causal performance comparison." That campaign, specified as a paired design in §10, is the replacement — a pre-existing gate item, not a new criterion.

## 7. Smallest recommended next action

Author and run the already-mandated memory-isolated paired CLI campaign (§10), with three small collector additions and their tests (§9). **No product changes.** Estimated effort: one campaign file + ~40 collector lines + 4 tests, then six benchmark runs.

## 8. Exact product change, if justified

**None is justified by this evidence.** The candidate's contract content is net cheaper than the July recipe's (−22 tokens); the checker receipt (+1,016) is second-order and partially pays for itself by eliminating a separate measurement action plus its extra turn; `--agent` is free. Do not trim required map elements; do not add skill text fighting host memory policy (that adds bytes to every run to influence behavior the product does not own).

Documented conditional lever, to be used **only** if the §10 paired campaign fails its uncached-input gate with the regression attributed to product bytes: a deterministic map-profile receipt — same JSON minus the fields map never consumes (`prunes` name lists, `navigation.limits`, per-category `raw` detail inside `health`), preserving `findings`, `hot_path` (files, bytes, `provisional_target_bytes`), the shared health meter/verdict/statuses, and coverage. Estimated saving ≈ 1.5–2.0 KB ≈ 400–550 tokens ≈ 2–3 points of gate ratio. It must keep byte-determinism, keep `--agent` exit semantics, change no required element, and land with regenerated adapters plus updated receipt-shape pins. One iteration maximum (§11).

## 9. Exact focused regression tests required (harness only, with the §10 work)

In `tests/test_codex_campaign.py`:

1. `test_collect_reports_shell_commands_and_memory_reads_per_wrapper` — synthetic session whose first wrapper batches three shell commands (two file reads + one read under a `memories` path); assert new per-run fields `shell_commands == 3`, `memory_read_ops == 1`, `memory_read_output_chars` equals the fixture's memory segment length, and that wrapper count stays 1.
2. `test_collect_reports_first_turn_cached_prefix` — fixture `token_count` events; assert `first_turn_cached_input_tokens` is recorded verbatim (no warm/cold judgment baked into scoring).
3. `test_summarize_flags_asymmetric_memory_exposure_and_cache_states` — `summarize` gains a `comparability` block reporting, per condition, the count of runs with `memory_read_ops > 0` and the distribution of first-turn cached prefixes; assert medians and the decision-rule output are byte-identical to before (flag-only; the gate is never altered by the flag).
4. `test_paired_campaign_requires_memory_isolation_and_recorded_pair_order` — validates the new campaign file: `host_context.memory == "unavailable"`, paired-execution fields present, conditions exactly the frozen two prompts; and that `collect` fails closed on any paired run whose session shows `memory_read_ops > 0` (execution-validity failure, distinct from scoring).

All four are collector/harness tests; no product test changes.

## 10. Exact next experiment design

New file `evals/retrieval/luna-max-cli-paired-v1.json` (the frozen constant is never edited; this follows the constant's own "extend without moving the goalposts" rule):

- **Target**: same repository at `7609b76da4b2ea6845c5b9f38dabfbd17487f673`. **Model**: `gpt-5.6-luna`, Max reasoning. Fresh task per run; read-only; zero writes.
- **Host**: Codex CLI with memory unavailable (no memories directory available to the session); record host and memory state in the manifest. This is the memory-isolated campaign the 2026-07-20 release gate already requires.
- **Conditions** (prompts copied verbatim from `luna-max-july11-constant.json`, including the restored exit-1 clause): `july11-bounded-recipe` and `docs-map-candidate` (the bare installed-skill invocation).
- **Pairing**: 3 repetitions; each repetition is one pair; within a pair the two conditions run back-to-back serially (second launches after the first completes) in an order decided by a coin flip recorded in the private manifest *before* launch; ≥15 minutes between pairs. Serial pairing plus randomized order makes cache-warmth exposure symmetric in expectation; no cache-control infrastructure is built.
- **Provenance**: refresh the installed plugin, run the `provenance` preflight at `47d6e76` before the first pair and after any plugin-file change; `collect --provenance` afterward; candidate runs must bind to the receipt hashes (package `a62da84f…`, focused skill `a6739faf…`).
- **New recorded fields** (measurement only): `first_turn_cached_input_tokens`, `shell_commands`, `memory_read_ops`, `memory_read_output_chars`.
- **Validity rule**: any run with `memory_read_ops > 0` proves memory isolation failed → fix the host configuration and rerun that entire pair; no other discards; nothing toggled between repetitions.
- **Decision rule (unchanged in substance)**: medians per condition; candidate passes when median duration and median uncached input are each ≤ +25% of the July arm **within this campaign**, with candidate correctness 3/3 (all constant assertions) and zero repository-checker attempts. Report the per-pair uncached-input differences alongside the medians as the paired sanity check.

Prediction registered now, falsifiably: from §4, the candidate arm should land ≈ +10–15% on uncached input and pass; if it exceeds +25% under memory isolation, the product term is larger than this audit measured and §8's lever becomes eligible.

## 11. Stop conditions (against overengineering)

1. **No product edits from this audit.** `skills/`, `plugins/`, `adapters/`, `tools/build_adapters.py`, manifests, and version numbers stay untouched until §10 produces a product-attributed failure.
2. **No edits to the frozen constant, its threshold, its scored results, or this evening's collected results.** Desktop memory-on runs remain valid dogfood telemetry labeled as such; they no longer carry the uncached-input release decision.
3. **Collector scope cap**: exactly the four fields and four tests in §9–§10; no session-parsing framework, no new abstractions, no cache-control tooling, no retry logic.
4. **If the paired campaign passes**: cut 0.1.7 from `47d6e76` (plus the release-authorized version bump) with no further optimization — no receipt trimming, no injection dedup, no memory countermeasures.
5. **If it fails product-attributed**: implement only the §8 receipt lever, rerun the same paired campaign once, and stop for a human decision regardless of outcome.
6. **Budget**: one campaign file, ≤ ~40 collector lines, 4 tests, 6 paired benchmark runs (+ any pair rerun forced by the validity rule).

## 12. Release recommendation

**READY FOR ACCEPTANCE RETEST.** The release itself stays uncut and the frozen gate stays unmoved — but no product work stands between the candidate and the decisive test. The blocking measurement is proven causally confounded (§6); the deciding evidence is the §10 memory-isolated paired campaign, which was already a declared release-gate requirement. Candidate `47d6e76` enters that retest as-is: 3/3 correct, zero wrong-checker attempts, provenance-bound bytes, duration gate passed, and a product-attributable uncached-input delta measured at ≈ +12%.

## 13. Confidence and unresolved unknowns

**High confidence (measured, exact):** the per-run ledgers (each balances the host token counter to the token); developer-message byte-identity across runs; memory-scan presence and sizes per run (segment-level); `--agent` adding zero bytes; receipt/plain/old-checker sizes (3,363 / 279 / 285 B); the 9,984/21,248 cached-prefix structure and the 11,264-token warm/cold swing (consistent across all twelve sessions inspected); memory-store byte-stability across both batches; 6/6 bare-invocation memory compliance vs 1/3 under the July prompt.

**Defensible inference:** intra-wrapper char→token splits (scaled to exact counters; ±5%); the cause of cand-2/cand-3's cold starts being provider cache eviction/lifetime rather than content change (content-identity is proven; eviction is not locally observable); write-propagation delay explaining july-1's cold start seconds after the no-skill batch.

**Unknown:** provider cache TTL/eviction policy specifics; whether `cached_input_tokens` granularity (1,024-token buckets) shifts a few hundred tokens between "slop" and other rows (bounded by the measured slop totals, 2.2–4.6 K/run); whether Codex Desktop can disable memory per-task (irrelevant to §10, which uses the CLI); exact composition of the hidden 11,264-token host preamble segment (constant across conditions, so it cancels except through warmth).

Reproduction without this session's scratch tooling: locate the nine raw sessions via the private manifests; for each, take the `token_count` events' `last_token_usage` per request (uncached = `input_tokens − cached_input_tokens`, summed), and the ordered `response_item` payloads for per-wrapper commands and output sizes; every number in §3–§4 re-derives from those two streams plus `git show` byte counts at `7609b76`/`47d6e76` and the checker mode reruns described in §4.

---

## Tomorrow morning — first five actions

1. Read §4 (ledger) and §10 (design) of this report; skim §6 for why the Desktop uncached-input arm is not decision-bearing.
2. Verify the environment is untouched: `git log --oneline -2` shows `47d6e76` then `4107f88`; `git status --porcelain` shows only `evals/retrieval/`, `tests/test_codex_campaign.py`, `tools/codex_campaign.py` untracked; this report sits inside `evals/retrieval/`.
3. Create `evals/retrieval/luna-max-cli-paired-v1.json` per §10, copying both prompts verbatim from `evals/retrieval/luna-max-july11-constant.json`.
4. Add the four collector fields and four tests from §9 to `tools/codex_campaign.py` / `tests/test_codex_campaign.py`; run `python -B -m pytest tests/test_codex_campaign.py -q`, then the core group via `python -B tools/run_tests.py core`.
5. Refresh the installed plugin from the local marketplace, run the provenance preflight at `47d6e76` (expect exit 0; package `a62da84f…`, skill `a6739faf…`), then launch pair 1 in Codex CLI with memory unavailable and confirm the collected runs show `memory_read_ops == 0`.
