import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILL = ROOT / "skills" / "docs"


class InitProgressContractTests(unittest.TestCase):
    @staticmethod
    def _text(path):
        return path.read_text(encoding="utf-8")

    @classmethod
    def _init_text(cls):
        return cls._text(SKILL / "references" / "init.md")

    @classmethod
    def _init_rules(cls):
        return " ".join(cls._init_text().lower().split())

    def test_fallback_covers_preview_and_apply_without_host_rule(self):
        init = self._init_text()
        rules = self._init_rules()

        self.assertIn("one logical init progress channel", rules)
        self.assertIn("docs init [<20 cells>] <percent-or-status> — <phase>", init.lower())
        self.assertRegex(
            rules,
            r"no compatible applicable host progress.{0,160}docs-owned fallback",
        )
        self.assertRegex(
            rules,
            r"zero-write preview.{0,180}post-approval apply|post-approval apply.{0,180}zero-write preview",
        )
        for phase in (
            "discovery",
            "batch x/y",
            "evidence complete",
            "preview ready",
            "waiting for exact approval",
            "approval revalidation",
            "apply/staging",
            "verification",
            "completed",
        ):
            with self.subTest(phase=phase):
                self.assertIn(phase, rules)

    def test_compatible_host_channel_is_reused_without_a_competitor(self):
        rules = self._init_rules()

        self.assertRegex(
            rules,
            r"compatible applicable host progress.{0,180}reuse.{0,180}(?:same|host).{0,80}(?:presentation|channel)",
        )
        self.assertRegex(
            rules,
            r"do not emit.{0,100}`?docs init.{0,100}(?:second|competing|competitor)",
        )
        self.assertRegex(
            rules,
            r"incompatible.{0,140}(?:host text|host instruction|presentation).{0,180}(?:does not suppress|use).{0,100}(?:fallback|`docs init`)",
        )
        self.assertIn("docs init: <phase>", rules)

    def test_strict_host_bar_uses_an_adjacent_phase_status_line(self):
        init = self._init_text()
        rules = self._init_rules()

        self.assertIn(
            "a strict host bar that accepts only a numeric percentage or `pass` is still compatible",
            rules,
        )
        self.assertIn("immediately adjacent plain-text line", rules)
        self.assertIn("one logical channel, not a second bar", rules)
        self.assertIn("hold the last completed-unit percentage", rules)
        self.assertIn("final host bar may use `pass`", rules)
        self.assertIn(
            "task [<20 cells>] 77%\ndocs init: waiting — waiting for exact approval",
            init.lower(),
        )
        self.assertIn(
            "task [<20 cells>] 77%\ndocs init: blocked — <reason>",
            init.lower(),
        )
        self.assertNotIn("Docs init [<20 cells>] 77%", init)

    def test_strict_host_pre_denominator_pause_defers_the_bar(self):
        init = self._init_text()
        rules = self._init_rules()

        self.assertRegex(
            rules,
            r"before `?total_batches`?.{0,100}denominator.{0,80}exist.{0,180}strict host.{0,180}(?:only|solely).{0,100}(?:adjacent )?plain-text",
        )
        self.assertIn("docs init: waiting — discovery", rules)
        self.assertIn("docs init: blocked — discovery", rules)
        self.assertRegex(
            rules,
            r"(?:defer|omit).{0,80}host bar.{0,100}(?:until|before).{0,100}denominator",
        )
        self.assertIn("single logical host-reused channel", rules)
        self.assertIn("emit no docs fallback or bar", rules)
        self.assertIn("never fabricate `0%`", rules)
        self.assertIn("no truthful percentage is computable", rules)
        self.assertIn("safe compatible rendering under the docs contract", rules)
        self.assertRegex(rules, r"after.{0,80}denominator.{0,80}(?:exist|known).{0,120}task \[<20 cells>\] 77%")
        self.assertNotIn("Task [<20 cells>] 0%", init)

    def test_eleven_batch_trajectory_uses_completed_units_only(self):
        rules = self._init_rules()
        batches = 11
        total_units = batches + 7
        batch_percentages = [
            (100 * (1 + completed_batches)) // total_units
            for completed_batches in range(1, batches + 1)
        ]

        self.assertIn("total_units = total_batches + 7", rules)
        self.assertIn("percentage = floor(100 * completed_units / total_units)", rules)
        self.assertEqual(total_units, 18)
        self.assertEqual(
            batch_percentages,
            [11, 16, 22, 27, 33, 38, 44, 50, 55, 61, 66],
        )
        for checkpoint in (
            "11 batches use 18 total units",
            "discovery 5%",
            "batches 1–11: 11%, 16%, 22%, 27%, 33%, 38%, 44%, 50%, 55%, 61%, 66%",
            "evidence complete 72%",
            "preview ready 77%",
            "approval revalidation 83%",
            "apply/staging complete 88%",
            "verification complete 94%",
            "completed 100%",
        ):
            with self.subTest(checkpoint=checkpoint):
                self.assertIn(checkpoint, rules)
        self.assertRegex(
            rules,
            r"before.{0,80}total batch count.{0,80}known.{0,120}(?:no|without).{0,40}percentage",
        )
        self.assertRegex(rules, r"staging x/y.{0,160}(?:hold|same).{0,100}percentage")
        self.assertIn("never use elapsed time", rules)
        for status in ("waiting", "pass", "blocked"):
            self.assertIn(status, rules)
        self.assertRegex(rules, r"preview.{0,100}`?waiting`?")
        self.assertRegex(rules, r"verification.{0,140}(?:before|then).{0,80}`?pass`?")

    def test_pre_denominator_waiting_or_blocked_fallback_has_no_cells(self):
        init = self._init_text()
        rules = self._init_rules()

        self.assertIn("docs init blocked — discovery", rules)
        self.assertIn("docs init waiting — discovery", rules)
        self.assertRegex(
            rules,
            r"before `?total_batches`?.{0,100}denominator.{0,80}(?:exist|known).{0,140}no (?:bar, )?cells.{0,80}percentage",
        )
        self.assertNotIn("Docs init [<20 cells>] BLOCKED — discovery", init)
        self.assertNotIn("Docs init [<20 cells>] WAITING — discovery", init)

    def test_init_health_is_prose_and_not_the_progress_channel(self):
        init = self._init_text()
        skill = self._text(SKILL / "SKILL.md")
        rules = " ".join((skill + "\n" + init).lower().split())

        self.assertIn("structural score: 83%", init.lower())
        self.assertRegex(
            rules,
            r"init.{0,180}(?:never|do not).{0,100}(?:generic )?`?docs`? health meter|(?:generic )?`?docs`? health meter.{0,180}(?:never|do not).{0,100}init",
        )
        self.assertIn("health.meter", skill)
        self.assertIn("for `map`, `check`, and `doctor`", skill.lower())
        self.assertRegex(skill.lower(), r"for `init`.{0,160}structural score.{0,120}prose")

    def test_all_retain_preview_is_concise_but_exactly_bound(self):
        rules = self._init_rules()

        self.assertIn("homogeneous all-`retain`", rules)
        self.assertIn("summarize it by default", rules)
        self.assertIn("public init manifest labels include `retain`", rules)
        self.assertIn("`retain` is initialization-only", rules)
        for field in (
            "selected scope",
            "total item count",
            "disposition counts",
            "preview id",
            "complete canonical manifest sha-256",
            "exact creates and edits",
            "risks",
            "verification",
        ):
            with self.subTest(field=field):
                self.assertIn(field, rules)
        self.assertRegex(rules, r"do not render.{0,80}(?:103|one line per).{0,100}`?retain`?")
        self.assertRegex(
            rules,
            r"mixed or destructive.{0,160}(?:complete|full).{0,80}(?:manifest|lines)",
        )
        self.assertIn("explicit user request", rules)
        self.assertRegex(
            rules,
            r"exact complete manifest.{0,160}transactionally persisted.{0,140}(?:state|event|transaction)",
        )
        self.assertRegex(
            rules,
            r"approval.{0,160}complete canonical manifest bytes.{0,160}(?:not|rather than).{0,100}(?:displayed )?summary",
        )

    def test_init_contract_is_strict_v3_only_and_names_exact_capacity_bounds(self):
        rules = self._init_rules()

        for phrase in (
            "init request, response, manifest, installed state, successful event, transaction, and recovery contracts are schema 3 only",
            "unknown fields, duplicate json fields, wrong exact types, and init schema 1 or 2 fail closed",
            "8 mib request",
            "256 corpus paths",
            "64 document operations",
            "32 destructive document operations",
            "16 source item ids per operation",
            "2 mib per source document",
            "4 mib aggregate result document bytes",
            "8 mib aggregate recovery backups",
            "1 mib manifest",
            "1 mib journal",
            "512 utf-8 bytes per reason",
            "64 backup entries",
            "80 result entries",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, rules)

    def test_apply_revalidation_is_direct_and_does_not_replay_preview_batches(self):
        rules = self._init_rules()

        self.assertIn("do not replay continuation batches", rules)
        self.assertIn("do not repeat model evidence analysis", rules)
        for stale_claim in (
            "one direct bounded digest read per starting document",
            "one final stable digest read per unchanged retain document",
            "one result-corpus metadata scan",
            "two bounded metadata corpus scans during apply",
        ):
            with self.subTest(stale_claim=stale_claim):
                self.assertNotIn(stale_claim, rules)
        for phrase in (
            "one bounded starting-corpus metadata scan",
            "one reconstruction read per starting document",
            "one independent pretransaction source-receipt read per starting document",
            "bounded recovery/compare reads for changed operations",
            "transaction preparation",
            "bounded result-corpus metadata scans at pre-event, event-mutation, and finalization boundaries",
            "unchanged `retain` documents are rechecked at the pre-event, event-mutation, and both finalization validation boundaries",
            "one corpus scan during preview",
            "o(files + operations)",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, rules)

    def test_v3_manifest_sections_and_event_commit_are_publicly_described(self):
        init = self._init_rules()
        memory = " ".join(
            self._text(SKILL / "references" / "memory.md").lower().split()
        )

        for phrase in (
            "corpusv3",
            "starting-to-result",
            "documentchangev3",
            "sectionv3",
            "atx-section-v1",
            "one external canonical manifest",
            "document_results",
            "body-free",
            "init-closeout-v3",
            ".diataxis/recovery/<transaction-id>",
            "prepared journal",
            "successful event is installed last and is the commit point",
            "transaction-local `.gitignore` guard",
            "before any recovery journal, backup, or staged-result payload",
            "immediately before every later journal, install, and doctor recovery mutation boundary",
            "final recovery cleanup deletion",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, init)

        self.assertIn(
            "always persists the complete body-free disposition manifest separately",
            memory,
        )

    def test_doctor_contract_consumes_v3_bindings_and_requires_exact_recovery_approval(self):
        doctor = self._text(SKILL / "references" / "doctor.md").lower()
        memory = self._text(SKILL / "references" / "memory.md").lower()
        combined = " ".join((doctor + "\n" + memory).split())

        for phrase in (
            "manifest_identity",
            "result_corpus",
            "document_results_digest",
            "corpus_transition",
            "transaction targets",
            "recovery journal",
            "cleanup, rollback, or finalize",
            "approve $docs doctor recovery <transaction-id> with journal <64-hex-or-absent> state <64-hex> action <cleanup|rollback|finalize>",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, combined)

        self.assertIn("schema 3 only", combined)
        self.assertIn("successful event is the commit point", combined)
        self.assertIn("transaction-local `.gitignore` guard", combined)
        self.assertIn("missing, changed, or deleted guard", combined)
        self.assertIn("deletes the guard last", combined)


if __name__ == "__main__":
    unittest.main()
