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
    def test_generate_and_check_are_reproducible(self):
        with tempfile.TemporaryDirectory() as td:
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
        with tempfile.TemporaryDirectory() as td:
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
