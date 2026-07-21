# Retrieval regression campaigns

These campaigns answer one narrow question: did the installed documentation skill make repository mapping faster and cheaper without losing the correct map?

`luna-max-july11-constant.json` freezes the repository commit, model, effort, prompts, expected routes, metrics, and decision rule. The July 11 condition is the successful four-call candidate recipe, not a claim that every pre-alpha invocation achieved that result.

## Run the constant

1. Ensure the target commit and installed skill version named by the campaign are available locally.
2. Start every run as a fresh Codex task on the target commit. Never reuse conversation history. Use batches of at most three runs; concurrency is not an experimental variable and should not overload the host.
3. Use the exact model, effort, and prompt for the condition. For `docs-map-0.1.6`, invoke the installed `docs-map` command-skill rather than copying its body into the prompt.
4. Repeat every condition three times. Do not retry or discard a slow or incorrect completed run.
5. Record the task IDs in a private manifest under `evals/results/drafts/`; that directory is Git-ignored.
6. Locate the corresponding local Codex JSONL session files and collect them:

   ```powershell
   python -B tools/codex_campaign.py collect `
     evals/retrieval/luna-max-july11-constant.json `
     evals/results/drafts/luna-max-july11-private.json `
     evals/retrieval/results/luna-max-july11-0.1.6.json
   ```

7. Score the constant assertions from the visible final answers. This semantic scoring is a human-reviewed step, not a claim that the collector understands answer meaning; raw answer hashes keep it traceable. Keep raw task IDs, absolute paths, prompts injected by the host, and full session traces out of the versioned result.
8. Run `python -B tools/codex_campaign.py summarize <result>` and apply the campaign's declared decision rule.

`duration_seconds` is host session telemetry from the fresh turn-context timestamp through the final cumulative token-count event. It is comparable within a same-host campaign, but it is not end-to-end CLI process wall time and must not be presented as such. `uncached_input_tokens` is cumulative host-reported input minus cumulative cached input.

## Candidate provenance

Codex Desktop serves installed plugins from a version-keyed snapshot cache, not from the repository, and the injected skill message in each raw session records the snapshot path and the exact skill bytes. Because a candidate build may keep the released manifest version, the version string alone can never prove which bytes ran. Bind every candidate batch with the provenance receipt instead:

1. Refresh the installed plugin from the local `statusnone-skills` marketplace so the cache snapshot is rebuilt from the working tree. This is a host action; the harness never mutates the cache.
2. Run the preflight immediately before launching each batch:

   ```powershell
   python -B tools/codex_campaign.py provenance `
     evals/retrieval/luna-max-july11-constant.json `
     evals/results/drafts/luna-max-candidate-provenance.json `
     --expected-commit <candidate-commit> `
     --conditions docs-map-0.1.6
   ```

   It fails closed unless the repository HEAD equals the expected candidate commit, `plugins/` and `skills/` are clean, and the cache snapshot tree is byte-identical to `plugins/diataxis-docs`. The receipt stores the package-tree digest, key-file hashes, snapshot version, and sanitized cache-relative source only.
3. Launch the batch only after the preflight passes. If any plugin file changes afterward, rerun the preflight before the next batch.
4. Collect with the receipt:

   ```powershell
   python -B tools/codex_campaign.py collect `
     evals/retrieval/luna-max-july11-constant.json `
     evals/results/drafts/luna-max-private.json `
     evals/retrieval/results/<result>.json `
     --provenance evals/results/drafts/luna-max-candidate-provenance.json
   ```

   Collection re-verifies the commit, the repository tree, and the cache snapshot (drift fails closed), then binds every bound-condition run by extracting the host-injected skill message: its embedded bytes must hash-match the candidate file and its recorded path must end with the pinned snapshot-relative route. Each bound run gains `injected_skill_sha256` and `injected_skill_source`, and the public result gains a sanitized `candidate_provenance` block. A session that predates the receipt, embeds different bytes, or was served from another snapshot aborts collection.

For causal performance claims, run through Codex CLI with isolated host context and record that memory was unavailable. Desktop runs with memory enabled are still valid product dogfood because that is the normal daily-driver environment, but label them `desktop-memory-on-pilot`; do not compare their absolute counters directly with a different host configuration. Do not toggle memory between repetitions.

## Memory-isolated paired CLI acceptance

`luna-max-cli-paired-v1.json` is the release acceptance campaign for the provenance-bound 0.1.7 candidate. It does not replace or edit the frozen July constant. It pairs the two unchanged prompts so provider-cache warmth is distributed across the reference and candidate arms instead of silently favoring one batch.

1. Use a detached worktree at the campaign target commit. Refresh the local plugin snapshot from candidate commit `47d6e76`, then bind it before the first pair:

   ```powershell
   python -B tools/codex_campaign.py provenance `
     evals/retrieval/luna-max-cli-paired-v1.json `
     evals/results/drafts/luna-max-cli-paired-provenance.json `
     --expected-commit 47d6e76db08ffcbd599aef3f24e2ae2b66417852 `
     --conditions docs-map-candidate
   ```

2. Before launching a pair, coin-flip its order and write both private-manifest entries with `pair`, `pair_order`, and `repetition` equal to the pair number. Never choose the order after seeing a result.
3. Launch each run serially from the target worktree. Use the reference prompt unchanged. For the candidate, prefix the unchanged base prompt with the campaign's fully qualified `$diataxis-docs:docs-map` selector on its own line; `$docs-map` is not a qualified plugin-skill selector. Do not use `--ephemeral`; the collector needs the persisted session. `--disable memories` is mandatory:

   ```powershell
   $campaign = Get-Content -Raw evals/retrieval/luna-max-cli-paired-v1.json | ConvertFrom-Json
   $condition = $campaign.conditions | Where-Object id -eq <condition-id>
   $prompt = if ($condition.skill) {
     '$' + $condition.skill + [Environment]::NewLine + $condition.prompt
   } else {
     $condition.prompt
   }
   codex -a never exec --disable memories `
     -m gpt-5.6-luna -c 'model_reasoning_effort="max"' `
     -s read-only -C <target-worktree> --json $prompt
   ```

   On Codex CLI 0.144.5, approval policy is a top-level option (`codex -a never exec ...`) and the Windows read-only sandbox may fail before commands with `orchestrator_helper_launch_failed`. Treat that as an invalid host run, not a product result; fix the host before launching a scored pair.

4. Record the emitted task ID in the private manifest. Wait for the first run to finish before starting its mate, and wait at least 15 minutes between pairs. Maximum concurrency is one.
5. Collect with the required provenance receipt. Collection fails closed if manifest order/repetitions/session identities are incomplete or reused, candidate bytes drift, the exact condition request differs, or either run reads a Codex memory path or contains a host memory-summary marker. A memory-isolation failure invalidates the entire pair; no other completed run may be discarded.
6. After three valid pairs, summarize the result. Release acceptance requires candidate medians no more than 25% above the paired July arm for duration and uncached input, 3/3 correct candidate answers, and zero repository-local checker attempts. Luna High or Light runs are useful canaries but do not satisfy this frozen Luna Max gate.

## 0.1.7 targeted correctness confirmation

`luna-low-0.1.7-correctness-v1.json` is the predeclared, three-run follow-up for the one product defect exposed by the completed paired gate: one candidate run named `docs/STATE.md` but silently omitted it from `--hot`. It tests the exact 0.1.7 package after the contract correction, not the pre-fix `47d6e76` bytes.

Run three fresh tasks serially with the file's qualified selector, Luna Low reasoning, `--disable memories`, and the same disposable target checkout. Bind the installed package with a fresh provenance receipt after the 0.1.7 candidate is committed. No completed run may be retried or discarded. Passing requires 3/3 complete constant assertions, zero memory reads, zero repository-local checker attempts, and a clean checkout after every run.

This is a correctness confirmation, not a new performance comparison. It does not rescore or replace `luna-max-cli-paired-v1.json`; that frozen result remains the performance evidence and its 2/3 pre-fix correctness verdict remains recorded.

## Private manifest

The private manifest is deliberately small:

```json
{
  "validity": "desktop-memory-on-pilot",
  "host_context": {"host": "Codex Desktop", "memory": "enabled"},
  "runs": [
    {"run_id": "no-skill-1", "condition": "no-skill", "repetition": 1, "thread_id": "local-task-id"}
  ]
}
```

The collector finds the raw local session by exact task ID. It fails on missing, reused, malformed, multi-turn, wrong-model, wrong-effort, wrong-repository, wrong-commit, wrong-condition-prompt, duplicate/missing repetition, inconsistent token/timestamp telemetry, host-context mismatch, or declared memory-isolation failure instead of manufacturing a measurement. Qualified candidate campaigns require a provenance receipt and the exact campaign skill selector; unqualified reference conditions reject a selector. Before writing, the complete public result is rejected if any private task ID, local absolute path, or private session path escaped from the manifest. The public result retains a SHA-256 provenance digest for each raw session without exposing its path or task ID.

The 0.1.7 adversarial-review closeout and deliberately parked non-blockers are recorded in [`P3-PARK-0.1.7.md`](P3-PARK-0.1.7.md). No P0, P1, or P2 finding is parked there.

Archive completed benchmark tasks and remove their clean worktrees after collection. A temporary local branch used only to seed the pinned commit should also be removed after Git proves the commit remains reachable. Never push the campaign branch or temporary constant branch as part of a run.

## Extend without moving the goalposts

Create a new campaign file when changing the target repository commit, model, effort, prompts, expected assertions, repetitions, or regression threshold. Do not edit a completed campaign constant in place. Model comparisons use separate result files and are never pooled into one median.
