# Bounded Compass Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the generic Codex skill cube with a polished Bounded Compass mark and carry the same canonical assets through every generated adapter and plugin surface.

**Status:** Complete; visually accepted in Codex Desktop on 2026-07-11.

**Architecture:** Generate one 2×3 exploration sheet, select one silhouette, then rebuild it as a deterministic 24×24 SVG. Render one 512×512 transparent PNG from that SVG, keep both files canonical under `skills/docs/assets/`, and make the adapter builder copy and verify them.

**Tech Stack:** SVG, PNG, Python standard library tests, existing adapter generator, Chromium one-time SVG rendering.

## Global Constraints

- Approved route: four-part abstract Bounded Compass with calm central negative space.
- Primary color: `#6657E8`; required black and white rendering compatibility.
- No letters, text, gradients, shadows, 3D, sparkles, brains, documents, databases, or mascots.
- Generated imagery is exploratory only; repository assets are deterministic.
- Do not change `SKILL.md`, add runtime dependencies, push, tag, or publish.

---

### Task 1: Select the silhouette

**Files:**
- Read: `docs/superpowers/specs/2026-07-11-bounded-compass-identity-design.md`
- Produce: one preview-only ImageGen result; no repository file

**Interfaces:**
- Consumes: approved Bounded Compass constraints
- Produces: one user-selected grid position and a concise preserve/avoid note

- [x] **Step 1: Generate one exploration sheet**

Use one ImageGen call with this exact brief:

```text
Create a professional logo exploration sheet containing exactly six distinct abstract symbol candidates in a clean 2×3 grid on a pure white inspection background. Each candidate is a four-part Bounded Compass: four equal rounded geometric modules rotate around a calm diamond-or-square negative-space center, while the outer silhouette implies a closed bounded-memory loop. Flat deep periwinkle #6657E8 only, no text, no labels, no letters, no numbers, no gradients, no shadows, no 3D, no sparkles, no document pages, no brain, no database, no mascot, no infinity symbol, no camera aperture, no OpenAI-style knot. Generous equal padding, crisp vector-like edges, optically balanced gaps, first-party software-plugin polish, and strong recognition when reduced to 16–24 pixels. Vary only module silhouette and interlock logic. Keep all six candidates isolated and fully visible.
```

- [x] **Step 2: Select one candidate**

Present the sheet without implementation claims. Record the selected grid position and any rejection note. If none survives at 24 px, run one targeted revision rather than another broad batch.

Selected: bottom-middle. Preserve the four restrained rounded modules, flat cardinal ends, and compact central diamond; reject decorative interlocks and softer petal-like alternatives.

---

### Task 2: Build, package, and verify the identity

**Files:**
- Create: `skills/docs/assets/bounded-compass-small.svg`
- Create: `skills/docs/assets/bounded-compass.png`
- Modify: `skills/docs/agents/openai.yaml`
- Modify: `tools/build_adapters.py`
- Modify: `tests/test_adapters.py`
- Regenerate: `adapters/`

**Interfaces:**
- Consumes: selected silhouette from Task 1
- Produces: canonical skill assets, complete OpenAI metadata, and byte-identical generated copies

- [x] **Step 1: Write failing asset and metadata tests**

Add standard-library assertions to `tests/test_adapters.py` that:

```python
import struct
from xml.etree import ElementTree

assets = ROOT / "skills/docs/assets"
small = assets / "bounded-compass-small.svg"
large = assets / "bounded-compass.png"
self.assertTrue(small.is_file())
self.assertTrue(large.is_file())
root = ElementTree.parse(small).getroot()
self.assertEqual(root.attrib["viewBox"], "0 0 24 24")
self.assertNotRegex(small.read_text(encoding="utf-8"), r"<(?:script|text)\b|https?://|(?:href|xlink:href)=")
width, height, bit_depth, color_type = struct.unpack(">IIBB", large.read_bytes()[16:26])
self.assertEqual((width, height, bit_depth, color_type), (512, 512, 8, 6))
metadata = (ROOT / "skills/docs/agents/openai.yaml").read_text(encoding="utf-8")
for value in ("./assets/bounded-compass-small.svg", "./assets/bounded-compass.png", "#6657E8"):
    self.assertIn(value, metadata)
```

Extend generated-contract assertions for plugin `brandColor`, `composerIcon`, and `logo`, and require byte parity for every copied asset.

- [x] **Step 2: Run the focused tests and confirm RED**

Run:

```powershell
python -m unittest tests.test_adapters -v
```

Expected: failures for missing canonical assets and metadata.

- [x] **Step 3: Create deterministic assets**

Reconstruct the selected silhouette on a 24×24 SVG grid using four rotationally equivalent path groups, flat `#6657E8`, and transparent background. Use `width="16" height="16" viewBox="0 0 24 24"`; include no metadata, embedded raster, external reference, script, text, or font.

Render the same SVG to a 512×512 RGBA PNG with transparent background using installed Chromium:

```powershell
& 'C:\Program Files\Google\Chrome\Application\chrome.exe' --headless --disable-gpu --hide-scrollbars --default-background-color=00000000 --window-size=16,16 --force-device-scale-factor=32 --screenshot='skills/docs/assets/bounded-compass.png' 'file:///D:/Statusnone%20Skills/skills/docs/assets/bounded-compass-small.svg'
```

- [x] **Step 4: Wire metadata and generation**

Add to `skills/docs/agents/openai.yaml`:

```yaml
  icon_small: "./assets/bounded-compass-small.svg"
  icon_large: "./assets/bounded-compass.png"
  brand_color: "#6657E8"
```

Update `tools/build_adapters.py` so `assets/` is copied beside `agents/` for every self-contained skill adapter. Copy the canonical PNG into `adapters/plugin/assets/` and set:

```json
"brandColor": "#6657E8",
"composerIcon": "./assets/bounded-compass.png",
"logo": "./assets/bounded-compass.png"
```

Add the assets to the exact expected-file set and parity loops, then regenerate only the verified default output:

```powershell
python tools/build_adapters.py generate --output adapters
```

- [x] **Step 5: Verify GREEN and the real UI**

Run:

```powershell
python -m unittest discover -s tests -v
python tools/build_adapters.py --check --output adapters
git diff --check
```

Expected: all tests pass, adapter check prints `clean`, and diff check exits 0.

Replace the isolated installed copy at `$HOME/.agents/skills/docs` from canonical source, restart Codex Desktop, and inspect the Skills picker plus invocation chip at normal scale. If the generic cube remains, verify installed asset paths before changing artwork.

- [x] **Step 6: Freeze locally after approval**

After the user approves the real Codex rendering, stage only the spec, plan, canonical assets, metadata, generator, tests, and regenerated adapter files. Create one local commit; do not push.
