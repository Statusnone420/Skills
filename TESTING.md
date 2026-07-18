# Testing

The repository uses the Python standard library's `unittest` framework. One observable orchestrator partitions the existing modules into meaningful groups, runs the same commands on Windows, WSL Ubuntu, and GitHub Actions, prints live progress and elapsed time, and fails when the partition does not cover every `tests/test_*.py` module exactly once.

```mermaid
flowchart LR
    A["Existing test suite"] --> B["Observable test orchestrator"]
    B --> C["Windows"]
    B --> D["WSL Ubuntu"]
    B --> E["GitHub CI"]
    F["Canonical format policy"] --> G["Markdown"]
    F --> H["MDX safe text parsing"]
    G --> I["Docs commands"]
    H --> I
    J["Selected-surface engine"] --> K["Markdown map or bounded Mintlify provider"]
    K --> I
    L["Malformed or unsupported provider input"] --> M["Unmeasured, fail closed"]
    N["Pinned corpus manifest"] --> O["Read-only corpus runner"]
    O --> P["Sanitized evidence receipt v1"]
```

## Commands

Run the narrowest relevant group first:

```text
python -B tools/run_tests.py core
python -B tools/run_tests.py lifecycle
python -B tools/run_tests.py trajectory
```

Run every group before completion:

```text
python -B tools/run_tests.py all
```

Inspect or verify the partition without executing tests:

```text
python -B tools/run_tests.py list
python -B tools/run_tests.py verify
```

Validate the receipt and corpus harness with local fixtures:

```text
python -B -m unittest -v tests.test_docs_evidence
```

Public corpus acquisition is explicit and writes only new ignored checkouts. The runner itself performs no acquisition or target writes:

```text
python -B tools/prepare_docs_corpus.py --manifest evals/docs-corpus-v1.json
python -B tools/run_docs_corpus.py --manifest evals/docs-corpus-v1.json --output evals/docs-corpus-baseline-v1.json
```

Preparation never deletes, updates, or reuses an existing corpus directory. The runner requires the official remote, exact detached commit, clean before/after Git status, and every declared entry/configuration probe. It never installs dependencies, builds sites, or executes MDX, JSX, JavaScript, TypeScript, TOML, YAML, Hugo shortcodes, imports, expressions, or components.

The orchestrator prints each group start and finish, module/test progress from verbose `unittest`, elapsed time, and a heartbeat every 30 seconds while work is still running. `--heartbeat-seconds` changes that interval and `--failfast` stops at the first failure.

## WSL performance

Run the Ubuntu proof from a Linux-native checkout under `$HOME`, not directly from `/mnt/c`, `/mnt/d`, or another Windows-mounted path. The Windows bridge is correct but makes metadata-heavy lifecycle tests much slower; the backing WSL virtual disk can still live on the chosen Windows drive. Keep the Windows checkout authoritative, copy or clone it into a clearly named disposable Linux directory for verification, and remove only that verified copy afterward.

## Proof order

1. Add or run the smallest regression that proves the changed behavior.
2. Run its owning group on Windows.
3. Run the same group in WSL Ubuntu.
4. Regenerate and verify generated adapters when canonical skill content changed.
5. Run the repository documentation checker.
6. Run `all` on Windows and WSL once the narrower gates pass.
7. Let CI repeat the same grouped commands; CI confirms local evidence rather than discovering basic failures.

Provider regressions also prove that Map, Check, Doctor, Audit, and Init use the same selected-surface evidence, including root-manifest authority, root README score isolation, tracked Git visibility, provider findings, and authority-digest Init revalidation on Git and non-Git fixtures. Semantic candidates remain labeled and bounded.

Corpus regressions additionally prove exact pins, inert configuration probes, unsupported-provider `not_assessed` states, orientation evidence that does not affect scoring, and zero target-repository writes. Rubric v2 and its category weights remain the comparison baseline until a separate calibration change is justified.

No valid test may be skipped, deleted, or weakened to pass a gate. A completion claim requires fresh output, a reviewed diff, and explicit separation of change-caused failures from verified pre-existing failures.
