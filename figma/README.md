# Spanner in Figma: the kit

The product is the design source. This folder turns the running hologram viewer into a Figma design system
and a clickable storyboard, so the Figma file and the app share one set of tokens and every state in the
file is captured from the live product. Say it that way in the pitch: "generated from the product, kept in
sync", never "designed first".

Target file: Spanner — Pitch Deck (https://www.figma.com/design/9t8Cp1iX4xGTL48QcRHsr8). Add two pages to it:
**Design system** and **Storyboard**. The deck stays on its own page and gets restyled with the tokens.

What's here:
- `tokens.json`: colours, type, spacing, sizes, radii, borders, glows, exported from the viewer's CSS.
- `renders/`: 14 PNGs (1600×1000) of the product in every state, captured from the live app.
- The state URLs below, for capturing editable layers instead of pictures.

Time: about 2 hours for one person with Figma desktop. Steps 1–3 are the minimum; 4–5 make it a system.

## 1. Tokens → Figma variables (15 min)

1. Install the **Tokens Studio for Figma** plugin (free tier is enough).
2. Plugin → Tools → *Load from file* → pick `figma/tokens.json`.
3. Click *Create variables* (colours, numbers) and *Create styles* (typography, effects).
4. Result: a `spanner` collection with `color/cyan`, `color/bg`, `type/instruction`, `effect/panel-glow`, etc.
   These are the same values as `training/viewer/index.html`.

Fonts: install **JetBrains Mono** and **Inter** on the machine (Google Fonts) so the text styles resolve.

## 2. Storyboard page: the states as frames (30 min)

Fast route (pictures): drag the 14 PNGs from `renders/` onto the Storyboard page, one per frame, in order.
Name each frame after the file. This alone gives you the prototype and the backup demo.

Better route (editable layers): install the **html.to.design** Chrome extension, open each URL below in
Chrome with the backend running (`uvicorn backend.main:app --port 8000`), and capture the tab into the
Storyboard page. The HUD, step card, camera panel and shop panel arrive as real Figma layers; the WebGL
hologram arrives as an image behind them. Then the states can be edited and turned into components.

| Frame | URL (prefix `http://127.0.0.1:8000/training/viewer/?instant=1&wake=0&`) | What it shows |
|---|---|---|
| 01 assembled | *(nothing)* | Idle hologram, status bar |
| 02 exploded | `explode=1` | All 255 parts apart |
| 03 x-ray | `xray=1` | Hologram see-through |
| 04 step 1 | `step=1` | Step card: cable out |
| 05 step 2 | `step=2` | Keycap puller in place |
| 06 step 4 | `step=4` | Switch puller, switch out |
| 07 step 5 | `step=5` | Socket close-up with OK tag |
| 08 step 7 | `step=7` | Align the new switch |
| 09 mistake | `step=8&mistake=1` | Rotated switch, red warning |
| 10 step 11 | `step=11` | Cable back, test |
| 11 inspector | `select=switch_F&open=1` | Part inspector, switch opened |
| 12 shop, autonomous | `fx=0&auto=1&shop=replacement%20switch%20for%20my%20RK61` | Verified parts, "started on its own" |
| 13 other keyboards | `fx=0&shop=show%20me%20other%20keyboards` | Alternatives, pros/cons, repair-instead banner |
| 14 specs panel | `panel=specs` | Sourced specifications |

Extra states worth a frame if time allows: the wake screen (drop `wake=0`), the camera panel (`camera=1`,
needs a webcam), and `fx=0` versions for cleaner layers.

## 3. Prototype: link the frames (15 min)

Storyboard page, prototype tab: 01 → 04 → 05 → 06 → 12 → 07 → 08 → 09 → 10 → 13, "On click", instant.
Present it full-screen. This is the backup if the live demo fails: the pitch continues frame by frame.

## 4. Design system page: components with variants (45 min)

From the captured layers (route B in step 2), make these components. Each variant is a real state of the
app, not an invention:

- **Step card**: `in progress` (frame 04), `with tools` (05), `done` (dots filled), `mistake` (09, red line),
  `learned warning` ("⚠ Learned from earlier sessions: people often …").
- **Camera panel**: `watching`, `step detected`, `mistake` (text and confidence bar states).
- **Shop panel**: `searching`, `quick results`, `verified` (green box with the decision), `other keyboards`.
- **Tag**: `ok` (green), `red`, `learned`, `autonomous` (amber).
- **Chip**, **status bar** (`standby`, `repair · step n/11`, `live · watching`), **wake screen**.

Bind their fills, strokes and text styles to the variables from step 1.

## 5. Restyle the deck (30 min)

Apply the variables to the deck: `color/bg` backgrounds, `color/cyan` headings in `type/brand` tracking,
`color/text` body, `effect/panel-glow` on panels. Use the renders as the hero images. Deck, product and
Databricks dashboard then read as one system.

## What to say in the pitch (section 10)

"Everything on screen comes from one set of tokens. The Figma file is generated from the running product:
same variables as the CSS, every state captured from the live app and turned into components. It's also our
backup demo: the storyboard page plays the whole repair frame by frame."
