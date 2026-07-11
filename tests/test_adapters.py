import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).parents[1]
BUILDER = ROOT / "tools" / "build_adapters.py"


def read_rgba_png(path):
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    position = 8
    compressed = []
    width = height = bit_depth = color_type = interlace = None
    while position < len(data):
        length = struct.unpack(">I", data[position:position + 4])[0]
        kind = data[position + 4:position + 8]
        payload = data[position + 8:position + 8 + length]
        position += length + 12
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            compressed.append(payload)
        elif kind == b"IEND":
            break
    if (bit_depth, color_type, interlace) != (8, 6, 0):
        raise ValueError("PNG must be non-interlaced 8-bit RGBA")
    raw = zlib.decompress(b"".join(compressed))
    stride = width * 4
    rows = []
    previous = bytearray(stride)
    offset = 0
    for _ in range(height):
        filter_type = raw[offset]
        scan = raw[offset + 1:offset + 1 + stride]
        offset += stride + 1
        row = bytearray(stride)
        for index, value in enumerate(scan):
            left = row[index - 4] if index >= 4 else 0
            above = previous[index]
            upper_left = previous[index - 4] if index >= 4 else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                base = left + above - upper_left
                distances = (abs(base - left), abs(base - above), abs(base - upper_left))
                predictor = (left, above, upper_left)[distances.index(min(distances))]
            else:
                raise ValueError(f"unsupported PNG filter: {filter_type}")
            row[index] = (value + predictor) & 0xFF
        rows.append(row)
        previous = row
    return width, height, rows


class AdapterBuilderTests(unittest.TestCase):
    def test_canonical_visual_assets_and_metadata(self):
        assets = ROOT / "skills" / "docs" / "assets"
        small = assets / "bounded-compass-small.svg"
        large = assets / "bounded-compass.png"
        self.assertTrue(small.is_file())
        self.assertTrue(large.is_file())

        svg = small.read_text(encoding="utf-8")
        root = ElementTree.fromstring(svg)
        self.assertEqual(root.attrib.get("viewBox"), "0 0 24 24")
        self.assertIn("#6657E8", svg)
        elements = [element.tag.rsplit("}", 1)[-1] for element in root.iter()]
        self.assertEqual(elements.count("path"), 4)
        self.assertFalse({"script", "text", "image", "foreignObject", "linearGradient", "radialGradient"} & set(elements))
        for element in root.iter():
            for key, value in element.attrib.items():
                self.assertNotEqual(key.rsplit("}", 1)[-1], "href")
                self.assertFalse(value.startswith(("http://", "https://", "data:")))

        width, height, rows = read_rgba_png(large)
        self.assertEqual((width, height), (512, 512))
        corner_alpha = (rows[0][3], rows[0][-1], rows[-1][3], rows[-1][-1])
        self.assertEqual(corner_alpha, (0, 0, 0, 0))

        metadata = (ROOT / "skills" / "docs" / "agents" / "openai.yaml").read_text(encoding="utf-8")
        for value in ("./assets/bounded-compass-small.svg", "./assets/bounded-compass.png", "#6657E8"):
            self.assertIn(value, metadata)

    def test_unowned_existing_output_directory_is_preserved(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as td:
            output = Path(td) / "existing"
            output.mkdir()
            sentinel = output / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")
            run = subprocess.run(
                [sys.executable, str(BUILDER), "generate", "--output", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_protected_repository_subtree_cannot_be_cleaned(self):
        with tempfile.TemporaryDirectory() as td:
            clone = Path(td) / "repo"
            (clone / "tools").mkdir(parents=True)
            shutil.copy2(BUILDER, clone / "tools" / "build_adapters.py")
            shutil.copytree(ROOT / "skills", clone / "skills")
            sentinel = clone / "skills" / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")
            run = subprocess.run(
                [sys.executable, str(clone / "tools" / "build_adapters.py"), "generate", "--output", str(clone / "skills")],
                cwd=clone,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(run.returncode, 0)
            self.assertTrue(sentinel.exists(), run.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

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

    def test_slash_frontmatter_requires_exact_canonical_syntax_and_values(self):
        import tools.build_adapters as builder
        canonical = (ROOT / "skills/docs/SKILL.md").read_text(encoding="utf-8")
        bads = (
            canonical.replace("name: docs", "name : docs", 1),
            canonical.replace("---\n\n#", "--- \n\n#", 1),
            canonical.replace("name: docs", "name: other", 1),
            canonical.replace("description:", "description :", 1),
            canonical.replace("name: docs", "name: docs\nname: docs", 1),
        )
        for bad in bads:
            with self.subTest():
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

    def test_checked_in_adapters_match_canonical_source(self):
        check = subprocess.run(
            [sys.executable, str(BUILDER), "--check", "--output", str(ROOT / "adapters")],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(check.returncode, 0, check.stderr)
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
            self.assertEqual(manifest["interface"]["capabilities"], ["Read", "Write"])
            self.assertEqual(manifest["interface"].get("brandColor"), "#6657E8")
            self.assertEqual(manifest["interface"].get("composerIcon"), "./assets/bounded-compass.png")
            self.assertEqual(manifest["interface"].get("logo"), "./assets/bounded-compass.png")
            for vendor in ("claude", "copilot", "grok", "cursor"):
                for name in ("bounded-compass-small.svg", "bounded-compass.png"):
                    self.assertEqual((out / vendor / "assets" / name).read_bytes(), (ROOT / "skills/docs/assets" / name).read_bytes())
            for name in ("bounded-compass-small.svg", "bounded-compass.png"):
                self.assertEqual((out / "plugin/skills/docs/assets" / name).read_bytes(), (ROOT / "skills/docs/assets" / name).read_bytes())
            self.assertEqual((out / "plugin/assets/bounded-compass.png").read_bytes(), (ROOT / "skills/docs/assets/bounded-compass.png").read_bytes())


if __name__ == "__main__":
    unittest.main()
