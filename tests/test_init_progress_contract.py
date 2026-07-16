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

    def test_init_uses_one_named_nonnumeric_milestone_channel(self):
        rules = self._init_rules()

        self.assertIn("one short named status channel for both preview and apply", rules)
        self.assertIn("reuse a compatible host status channel", rules)
        self.assertIn("`docs init — <milestone>`", rules)
        self.assertIn("never emit a competing bar or a second init channel", rules)
        for milestone in (
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
            with self.subTest(milestone=milestone):
                self.assertIn(milestone, rules)
        self.assertIn("emit only milestones the engine has actually completed", rules)
        self.assertIn("the structural score is health evidence, not progress", rules)
        for retired_rule in (
            "total_units =",
            "percentage = floor",
            "20 cells",
            "hold the last completed-unit percentage",
        ):
            with self.subTest(retired_rule=retired_rule):
                self.assertNotIn(retired_rule, rules)

    def test_init_presents_the_engine_score_receipt_without_inventing_deductions(self):
        init = self._init_rules()
        skill = " ".join(self._text(SKILL / "SKILL.md").lower().split())

        self.assertIn("the verified engine response is the source of truth", init)
        self.assertIn("present it in plain english", init)
        self.assertIn("returned category earned/available receipt", init)
        self.assertIn("subjective deductions", init)
        self.assertIn("attention signals are informational rather than scored", init)
        self.assertIn("never substitute a generic docs health meter for init status", init)
        self.assertIn("report the engine's structural score receipt separately", skill)
        self.assertIn("never render the generic `docs` health meter as init progress", skill)

    def test_retain_means_left_unchanged_not_quality_approval(self):
        rules = self._init_rules()

        self.assertIn("`retain` means left unchanged during init", rules)
        self.assertIn("it is not a quality endorsement", rules)
        for action in ("move", "rename", "rewrite", "archive", "delete"):
            with self.subTest(action=action):
                self.assertIn(f"will not {action}", rules)
        self.assertIn("this is an adoption decision, not a filing judgment", rules)
        self.assertIn("`retain` is shown as **left unchanged**", rules)
        for false_label in ('"approved,"', '"healthy,"', '"well organized."'):
            with self.subTest(false_label=false_label):
                self.assertIn(false_label, rules)
        self.assertIn("only the human can authorize that treatment", rules)

    def test_engine_owns_preview_receipt_manifest_and_exact_approval(self):
        rules = self._init_rules()

        for phrase in (
            "invoke the deterministic init adoption entrypoint",
            "present its verified response",
            "never construct a preview, approval, or disposition manifest yourself",
            "the engine owns scope selection, continuation, corpus accounting, request construction, and preview construction",
            "adopt-preview --receipt-file <outside-repository-receipt.json>",
            "the receipt is engine-owned",
            "do not open, edit, translate, or reconstruct it",
            "fail closed without a model fallback",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, rules)
        self.assertIn("a later, separate user message must repeat the engine-emitted line exactly", rules)
        self.assertIn(
            "approve $docs init preview <preview-id> with manifest <manifest-sha256>",
            rules,
        )
        self.assertIn("a same-message apply or write demand receives the preview only", rules)

    def test_apply_revalidates_the_same_receipt_and_fails_closed_to_recovery(self):
        rules = self._init_rules()

        for phrase in (
            "adopt-apply --receipt-file <same-outside-repository-receipt.json>",
            "pass the same untouched receipt and the user's exact approval string",
            "the engine revalidates the receipt, exact approval, selected scope, shared corpus, current bytes, repository identity, worktree, and transaction boundary",
            "records the successful event last",
            "retains truthful recovery evidence",
            "failed verification records no successful initialization event",
            "route to `$docs doctor` when the engine requests diagnosis",
            "only recovery cleanup may run; no target or operational-state mutation follows",
            "torn or orphaned recovery evidence is a p0 state conflict for doctor",
            "never manually delete `.diataxis/` to make init proceed",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, rules)


if __name__ == "__main__":
    unittest.main()
