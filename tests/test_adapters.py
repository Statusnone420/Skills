import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
BUILDER = ROOT / "tools" / "build_adapters.py"


class AdapterBuilderTests(unittest.TestCase):
    def test_reparse_parent_output_cannot_escape(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as td, tempfile.TemporaryDirectory() as outside_td:
            base = Path(td); outside = Path(outside_td); sentinel = outside / "sentinel"; sentinel.write_text("keep")
            link = base / "link"
            try: link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError): self.skipTest("symlink unavailable")
            run = subprocess.run([sys.executable, str(BUILDER), "generate", "--output", str(link / "new")], cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0); self.assertTrue(sentinel.exists())

    def test_frontmatter_conflict_and_stale_empty_dir_fail_check(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as td:
            out = Path(td) / "out"; subprocess.run([sys.executable, str(BUILDER), "generate", "--output", str(out)], cwd=ROOT, check=True)
            p = out / "claude/SKILL.md"; p.write_text(p.read_text().replace("user-invocable: true", "user-invocable: false\nuser-invocable: true", 1))
            (out / "web/stale-empty").mkdir()
            check = subprocess.run([sys.executable, str(BUILDER), "--check", "--output", str(out)], cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(check.returncode, 0); self.assertIn("frontmatter", check.stderr); self.assertIn("directory", check.stderr)
    def test_rejects_output_outside_repo_without_deleting_sentinel(self):
        with tempfile.TemporaryDirectory() as td:
            outside = Path(td); sentinel = outside / "sentinel"; sentinel.write_text("keep")
            run = subprocess.run([sys.executable, str(BUILDER), "generate", "--output", str(outside)], cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0); self.assertTrue(sentinel.exists())

    def test_slash_metadata_is_inside_frontmatter(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as td:
            out = Path(td) / "out"; subprocess.run([sys.executable, str(BUILDER), "generate", "--output", str(out)], cwd=ROOT, check=True)
            text = (out / "claude/SKILL.md").read_text()
            self.assertEqual(text.split("---", 2)[1].count("user-invocable:"), 1)
            self.assertNotIn("user-invocable:", text.split("---", 2)[2])

    def test_slash_frontmatter_rejects_unknown_and_malformed_lines(self):
        import tools.build_adapters as builder
        canonical = (ROOT / "skills/docs/SKILL.md").read_text(encoding="utf-8")
        for bad in (canonical.replace("name: docs", "unknown: value\nname: docs", 1), canonical.replace("name: docs", "malformed line\nname: docs", 1)):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError): builder.slash_skill(bad)

    def test_check_detects_stale_extra_and_resource_drift(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as td:
            out = Path(td) / "out"; subprocess.run([sys.executable, str(BUILDER), "generate", "--output", str(out)], cwd=ROOT, check=True)
            (out / "web/extra.txt").write_text("stale")
            (out / "plugin/skills/docs/references/memory.md").write_text("drift")
            check = subprocess.run([sys.executable, str(BUILDER), "--check", "--output", str(out)], cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(check.returncode, 0); self.assertIn("extra", check.stderr); self.assertIn("parity", check.stderr)

    def test_isolated_user_install_metadata_invocation_and_uninstall(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as td:
            out = Path(td) / "out"; home = Path(td) / "home"
            subprocess.run([sys.executable, str(BUILDER), "generate", "--output", str(out)], cwd=ROOT, check=True)
            target = home / ".codex" / "skills" / "docs"; target.parent.mkdir(parents=True)
            import shutil; shutil.copytree(out / "plugin/skills/docs", target)
            self.assertEqual((target / "SKILL.md").read_text().split("---", 2)[0], "")
            self.assertIn("$docs", (target / "agents/openai.yaml").read_text())
            shutil.rmtree(target); self.assertFalse(target.exists())

    def test_ci_uses_portable_python_command(self):
        workflow = (ROOT / ".github/workflows/validate.yml").read_text()
        self.assertIn("windows-latest", workflow); self.assertNotIn("python3 -m", workflow)
    def test_generate_and_check_are_reproducible(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as td:
            out = Path(td) / "out"
            run = subprocess.run([sys.executable, str(BUILDER), "generate", "--output", str(out)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            check = subprocess.run([sys.executable, str(BUILDER), "--check", "--output", str(out)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)
            first = {p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob("*") if p.is_file()}
            subprocess.run([sys.executable, str(BUILDER), "generate", "--output", str(out)], cwd=ROOT, check=True)
            second = {p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob("*") if p.is_file()}
            self.assertEqual(first, second)

    def test_generated_contracts(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as td:
            out = Path(td) / "out"
            subprocess.run([sys.executable, str(BUILDER), "generate", "--output", str(out)], cwd=ROOT, check=True)
            canonical = (ROOT / "skills/docs/SKILL.md").read_text(encoding="utf-8")
            for vendor in ("claude", "copilot", "grok", "cursor"):
                text = (out / vendor / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn("user-invocable: true", text)
                self.assertIn("disable-model-invocation: true", text)
                self.assertEqual(text.split("---", 2)[-1].replace("\nuser-invocable: true\ndisable-model-invocation: true", "", 1), canonical.split("---", 2)[-1])
            for vendor in ("gemini", "opencode"):
                wrapper = (out / vendor / "docs.md").read_text(encoding="utf-8")
                self.assertIn("docs", wrapper.lower()); self.assertIn("raw trailing text", wrapper.lower())
            web = (out / "web" / "docs-help.txt").read_text(encoding="utf-8")
            self.assertIn("capabilit", web.lower())
            manifest = json.loads((out / "plugin/.codex-plugin/plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], "statusnone-skills")


if __name__ == "__main__":
    unittest.main()
