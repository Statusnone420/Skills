# Bounded Compass Identity Design

**Status:** Approved and implemented; visually accepted in Codex Desktop
**Date:** 2026-07-11
**Product:** Statusnone Skills — Diátaxis Docs
**Decision owner:** Statusnone

## Goal

Replace the generic Codex skill artwork with a distinctive, professional mark that belongs beside first-party-quality skills and plugins at 16–24 px. The identity must communicate four-part Diátaxis routing, bounded repository memory, and recursive retrieval without literal documentation or AI clichés.

## Approved identity

The mark is **Bounded Compass**: four equal abstract modules rotate around calm central negative space. Their directional geometry reads as a compass; their outer contour closes visually into a bounded loop. The mark contains no letters or text.

The design is small-size-first. A clean silhouette and controlled negative space matter more than illustrative detail.

## Geometry

- Construct on a 24×24 grid with four rotationally equivalent modules.
- Keep module gaps optically balanced after rasterization, not merely mathematically equal.
- Preserve a quiet central diamond or square as negative space.
- Let the outer contour imply a closed loop without drawing a generic circle around the symbol.
- Prefer rounded geometric corners with enough tension to feel precise rather than playful.
- Maintain recognition at 16, 20, and 24 px and when rendered in one color.
- Avoid hidden letters, arrows, page corners, infinity symbols, knots, camera apertures, or four-color pie-chart readings.

## Color system

- Primary brand color: deep periwinkle `#6657E8`.
- Required variants: primary color, pure black, and pure white.
- Small marks use flat color only: no gradients, shadows, glow, texture, transparency effects, or 3D treatment.
- All production assets use a transparent background.
- The large mark uses the same symbol and proportions as the small mark; it is not a separate illustration.

## Explicit exclusions

No sparkles, brains, database cylinders, mascots, document sheets, fake 3D cubes, decorative gradients, slogans, embedded wordmarks, or generated typography. Do not imitate the OpenAI knot, Microsoft Loop, an infinity symbol, a camera focus mark, or an existing vendor logo.

## Exploration

Image generation may explore up to six silhouette treatments within the approved Bounded Compass system:

1. modular orbit;
2. rounded compass petals;
3. restrained interlock;
4. squircle-derived loop;
5. notched compass modules;
6. negative-space routing.

Every exploration tile uses a white inspection background, generous padding, no text, and the same flat periwinkle direction. Generated imagery is a decision surface only. The selected route must be reconstructed deterministically as vector geometry before it can enter the repository.

The accepted route was the bottom-middle candidate: four restrained rounded modules, flat cardinal ends, and a compact central diamond. The generated tile was used only as a selection reference; the repository artwork was rebuilt as deterministic SVG geometry.

## Canonical assets and packaging

Canonical identity files live under `skills/docs/assets/`:

- `bounded-compass-small.svg`: transparent, simplified small mark, 24×24 view box;
- `bounded-compass.png`: transparent 512×512 large mark derived from the final SVG.

`skills/docs/agents/openai.yaml` references those assets with `icon_small`, `icon_large`, and `brand_color`.

The adapter generator copies canonical skill assets into every self-contained adapter that carries `agents/openai.yaml`. The plugin bundle also exposes presentation assets at its root and sets `brandColor`, `composerIcon`, and `logo` in `.codex-plugin/plugin.json`. Generated copies never become independent sources.

## Implementation flow

1. Generate and inspect the six bounded-compass exploration tiles.
2. Select one silhouette and record rejected directions.
3. Rebuild the selected silhouette as deterministic SVG on the 24×24 grid.
4. Render the large transparent PNG from the SVG without changing geometry.
5. Wire canonical skill metadata and plugin presentation metadata.
6. Regenerate all adapters from canonical source.
7. Reinstall the local skill, restart Codex Desktop, and inspect the real Skills picker and invocation chip.
8. Continue the `$docs map` dogfood run only after the final artwork is visible.

## Verification

Automated checks must prove:

- required canonical assets exist;
- SVG parses as XML and contains no scripts, external references, embedded fonts, or text;
- SVG uses the approved 24×24 view box and flat approved colors;
- PNG is 512×512 with an alpha channel and transparent corners;
- OpenAI skill metadata uses repository-relative asset paths and the approved brand color;
- plugin manifest asset paths remain inside the generated plugin root;
- generated adapter assets have byte parity with canonical sources;
- checked-in adapter generation remains reproducible and rejects stale or extra files;
- adding assets does not change `SKILL.md` or its context budget.

Manual acceptance must prove:

- the mark is recognizable and balanced at 16–24 px;
- the center and inter-module gaps do not collapse in dark or light UI;
- the mark looks intentional beside GitHub, Gmail, Figma, and Google Drive in the Codex picker;
- the invocation chip remains legible;
- no new repository changes appear after read-only dogfood commands.

## Failure handling

If Codex continues to show the generic icon, first validate metadata paths, installed asset presence, cache refresh, and host restart. Do not alter skill behavior to repair presentation. If rasterization damages the silhouette, correct the vector geometry and regenerate all derived assets rather than hand-editing the PNG.

## Scope boundaries

This design covers the Diátaxis Docs skill mark and the Statusnone Skills plugin presentation assets needed for v0.1. A website identity, full wordmark family, motion system, merchandise, trademark clearance, and broader Statusnone master brand remain separate future work.
