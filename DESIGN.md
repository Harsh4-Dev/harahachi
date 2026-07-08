# Harahachi Design System — "Old Sakura Map"

Both the phone app and the Sekisho admin dashboard use this exact system.
The aesthetic: an aged Japanese woodblock/portolan map (think Edo-period
Tōkaidō road maps) rendered with isochrone contour rings, with all interactive
UI floating above it as glassmorphism panels.

## 1. Palette (CSS custom properties, define on `:root`)

```css
:root {
  --washi:        #F3ECDA;  /* aged paper base */
  --washi-deep:   #E9DFC6;  /* paper shadow / contour fill */
  --sumi:         #2B2A33;  /* ink — primary text & linework */
  --sumi-soft:    #55525E;  /* secondary text */
  --sakura:       #E8A7B8;  /* blossom fill */
  --sakura-deep:  #D96C8A;  /* blossom accent, active states */
  --shu:          #C73E3A;  /* vermillion — seals, alerts, overshoot */
  --matcha:       #7BA05B;  /* success, protein, "healthy" tags */
  --ai:           #2D4A63;  /* aizome indigo — water, links, carbs */
  --kin:          #C9A227;  /* gold — achievements, fat macro, seals */
  --glass-alpha:  0.55;     /* USER-ADJUSTABLE via settings slider */
  --glass-blur:   14px;
}
```

## 2. Glassmorphism (every panel, popup, button, sheet)

```css
.glass {
  background: rgba(243, 236, 218, var(--glass-alpha));
  backdrop-filter: blur(var(--glass-blur)) saturate(1.25);
  -webkit-backdrop-filter: blur(var(--glass-blur)) saturate(1.25);
  border: 1px solid rgba(255, 255, 255, 0.38);
  border-radius: 18px;
  box-shadow: 0 8px 32px rgba(43, 42, 51, 0.14);
}
```

- **User-defined transparency is a hard requirement**: a slider in settings
  writes `--glass-alpha` (range 0.15–0.9) to the root element and persists it
  (localStorage on app; cookie/localStorage on admin).
- Dark ink variant for popovers over bright map areas:
  `background: rgba(43,42,51, calc(var(--glass-alpha) * 0.85)); color: var(--washi);`

## 3. Typography (Google Fonts)

- Display / station names: **"Shippori Mincho"**, serif — use for headings,
  numerals on the intake bar, station labels (include kanji + romaji).
- Body / UI: **"Zen Maru Gothic"**, rounded sans.
- Numbers in stat readouts: Shippori Mincho, `font-feature-settings: "tnum"`.

## 4. The map (the centerpiece background)

A full-viewport inline **SVG** (not an image file) styled like an **actual
game world map** (reference: aged-parchment maps from open-world games — an
organic continent, not abstract rings):

- **Landmass**: one large irregular continent with a jagged, hand-drawn
  coastline (many small bays and peninsulas). Land fill `--washi`, coast
  outlined with 2–3 nested offset strokes in `--sumi` at falling opacity
  (old-map coastal hatching). The surrounding sea is `--washi-deep` with
  sparse seigaiha wave arcs in `--ai` at low opacity.
- **Terrain inside the landmass**: elevation patches (`--washi-deep` blobs
  with faint contour outlines), mountain ridges (rows of small ^ strokes in
  `--sumi`), forest clusters (matcha dot/blob clusters), one or two winding
  rivers (`--ai`, 1.5px, low opacity) running to the coast, and one or two
  softly-tinted regions (a `--sakura` tinted province, a `--matcha` tinted
  one) like faction territories on a game map.
- **Trails, not a single arc**: a branching network of dashed paths
  (`--sumi`, dashed 8 4, 2–2.5px) winding organically between the six
  stations, following the terrain (around mountains, along rivers), with a
  couple of short dead-end spurs for flavor. The active journey route
  highlights in `--sakura-deep`.
- **Stations**: circular hanko-seal markers — `--shu` ring, washi center,
  kanji glyph inside — each sitting in its own named region of the map, with
  its romaji name in small caps beneath. Active station's seal fills `--shu`.
- **Sakura**: blossom clusters near trails; 2–3 drifting petals animated with
  slow CSS keyframes.
- **Paper aging**: burnt/darkened irregular parchment border (like a worn
  game map), radial vignette, faint turbulence noise at ~4% opacity.
- Compass rose in one corner, `--sumi` linework, sakura blossom at its center.
- **Density (locked 2026-07-07)**: the map must feel like a dense ukiyo-e
  chart, not a sparse diagram — ~10 mountain ranges of overlapping shaded
  peaks (occasional snow caps), 8+ pine-forest clusters (rare `--kin` autumn
  trees), a pagoda/teahouse settlement beside every station seal, torii gates
  at spur ends, ink boats with washi sails and kasumi mist bands over open
  water, and a red-bordered vertical-text legend cartouche (腹八分 · 旅路の図)
  in a sea corner. All inline SVG in the existing palette.

**Zoom behavior**: tapping a seal zooms **deep into that region** (scale
≈2.2–2.8, region centered in the map area left visible above the glass
panel) — like moving to a zone on a game world map; closing the panel zooms
back out to the full continent. 700ms cubic-bezier(.22,.9,.31,1) both ways.

## 5. Navigation = travel

There is **no tab bar**. The map IS the navigation:

- The six stations are laid out across the SVG. Tapping a seal **pans/zooms
  the map** to that region (CSS `transform: translate/scale` on the map group,
  `transition: transform 700ms cubic-bezier(.22,.9,.31,1)`), then the
  station's glass panel slides up over the lower 70% of the screen.
- A small traveler marker (walking figure or straw-hat icon) sits on the road
  and **animates along the path** (SVG `getPointAtLength`) — on the Honjin
  screen its position = today's calorie progress.
- Closing a panel zooms back out to the full map. A thin glass ribbon at the
  top always shows: current station name (kanji + romaji), today's kcal
  fraction, and a back-to-map button.

### Stations

| Seal kanji | Name | Screen |
|-----------|------|--------|
| 本 | Honjin 本陣 | Home: journey bar, macro paths, today's meals, suggestions |
| 茶 | Chaya 茶屋 | Meals: camera capture, analyze, confirm, history |
| 暦 | Koyomi 暦 | Calendar: month heat-map of adherence, day drill-down |
| 道 | Dōjō 道場 | Fitness: log workouts, kcal burned credits the journey |
| 市 | Ichiba 市場 | Store: merch cards + healthy delivery deep links |
| 手 | Tegata 手形 | Profile: travel permit (user details), targets, glass slider, pairing |

## 6. Signature components

- **Journey bar (the daily intake bar)**: not a rectangle — a horizontal
  excerpt of the road with origin and destination seals; a `--sakura-deep`
  stroke fills along the path as kcal accumulate; overshoot continues past the
  destination seal in `--shu`. Label: `1,430 / 2,200 kcal · 里 6.5 of 10`.
- **Confirm card** (after photo analysis): glass card with dish name, kcal,
  three macro chips (matcha/ai/kin), confidence %, an editable portion
  stepper, and a `--shu` hanko "stamp to log" button with a stamp-down
  animation on tap.
- **Onboarding tegata**: 4-step vertical scroll styled as an unrolling scroll;
  final step stamps a red seal with the user's initial. Big friendly steppers
  and sliders, no keyboard where avoidable.

## 6.5 The camera & scanner (Chaya's capture mode)

Opening capture from Chaya takes over the **full screen** with a live camera
feed (getUserMedia, rear camera). Everything drawn over the feed is glass in
our style — no washi page behind, the food IS the background:

- **Top ribbon** (glass): 茶屋 Chaya · Capture, close ✕. **Bottom bar**
  (glass): big shutter ring (washi ring, `--shu` center), a gallery/file
  fallback button, and a **mode toggle**: 料理 Dish / バーコード Barcode.
- **Dish mode**: tap shutter → frame freezes → "reading the dish…" glass
  shimmer → the LLM response renders as glass labels that **SURROUND the
  dish — the food must stay visible, nothing sits on top of it**:
  - **Per-component labels**: one compact glass chip per `items[]` entry
    ("Dal tadka · 180 kcal · P9 C22 F6"), pinned NEXT TO its part of the food
    using the item's `point` from the API, pushed outward from the point so
    the chip sits beside — not over — the component, with a thin 1px `--shu`
    **leader line** from chip to point. Without `point`s, arrange chips down
    the left/right edges alternately with leader lines angled toward the
    plate center. Chips never overlap each other (simple vertical
    collision-nudge) and keep a clear central zone (~55% of the frame) empty.
    Each chip: ✕ to remove, tap to cycle portion (×0.5/×1/×1.5/×2) scaling
    its values and the total live.
  - **Title ribbon** at the very top (dish name + confidence %), clarifying
    question chip just under it (one-tap answers).
  - **Total bar pinned at the bottom**: full-width glass bar — total kcal
    big (Shippori numerals) + the three macro chips (matcha/ai/kin) — with
    the vermillion hanko **"add to today's goal"** stamp button at its right
    end. Stamping fires the stamp animation, logs, and returns to Chaya with
    the journey advancing.
- **Barcode mode**: same full-screen feed with a glass **reticle frame**
  (rounded rect, corner ticks in `--shu`, faint scan-line sweep). On detect:
  product glass card over the feed (name, brand, per-serving kcal + macros,
  serving stepper ×0.5/×1/×2…) with the same hanko "add to today's goal"
  stamp. Unknown barcode → glass card offering manual entry.
- Permission denied / no camera → graceful glass card offering the file-input
  path. Respect `prefers-reduced-motion` (no shimmer/sweep).

## 6.6 Accounts, body diagram, and the Annai chat

- **Account flow**: onboarding ends by creating the account (username + 4-8
  digit PIN on a glass numpad) right before the hanko stamp; the stamp seals
  the account. Tegata gains an "account" card: username, logout (returns to a
  login screen: username + PIN pad + "new traveler" link), and switch account.
  Login screen carries the same tegata-scroll styling.
- **Body diagram picker** (manual path, in onboarding step 2.5 and editable
  in Tegata): two rows — "your body now" and "your goal" — each a horizontal
  scroll of 7 glass tiles containing simple sumi-line body silhouettes at
  ~10 / 15 / 20 / 25 / 30 / 35 / 40% body fat (sex-appropriate variants drawn
  as inline SVG line art, not photos), label under each ("~15% · lean").
  Selected tile gets the `--shu` ring. A small "or estimate from a photo"
  button switches to the photo path: full-screen camera (same style as 6.5),
  full-body capture → LLM estimate → an EDITABLE glass result screen
  (estimated %, range slider to adjust, confidence note) → confirm saves to
  profile. The photo is analyzed and discarded — say so on screen.
- **Annai chat (案内人)**: a small floating glass button (chat bubble with a
  torii glyph) bottom-right on the map and inside panels. Opens a full-height
  glass chat overlay: messages as washi bubbles (user right/sumi, Annai left
  with a tiny hanko avatar), suggestion chips above the input ("What's left
  today?", "Log my dinner", "What should I eat tonight?", "How does this app
  work?"). When the server executes an action (meal/workout logged), render a
  confirmation chip inline with the stamp animation and advance the journey
  bar live. Input bar is glass with a send button in `--shu`.
- **Reminders**: push notifications reuse the hanko icon; in-app fallback is
  a glass banner sliding from the top ribbon at 18:00/22:00 with the same
  text and a one-tap "log it" for the suggested regular meal.

## 6.7 The feedback loop (v3) + richer worlds

- **Honjin adds**: a water row (tap a cup glyph = +250 ml, long-press/− to
  undo; N cups fill in `--ai`) and a streak chip (current streak as a small
  hanko count, e.g. "7日 streak").
- **Koyomi adds two tabs** (glass pills under the heat-map): **Weight** — big
  stepper entry + a sumi-line trend chart (SVG path on washi grid, sakura
  dots for entries, matcha dashed line for the 7-day trend, `--shu` marker at
  goal weight) — and **Stamp book (御朱印帳)** — a goshuin grid, one ornate
  red stamp per on-target day, gold-ringed milestone stamps (7/30 days).
- **Chaya adds "From your road"**: a horizontal quick-log carousel above
  manual entry — recent/favorite dishes as glass cards (dish, kcal, ♥ toggle)
  with one-tap log.
- **Tegata adds**: goal weight + pace picker (0.25/0.5/0.75 kg-week tiles)
  with journey copy: "At this pace you arrive at 65 kg near the end of
  September." Data export link ("carry your records with you" → CSV).
- **Weekly insights**: a scroll-styled card at the top of Koyomi — averages,
  adherence %, best/worst day seals, weight delta, and Annai's short summary.
- **Nutrient detail**: fiber/sugar/sodium as small tertiary chips behind a
  "more" tap on confirm cards and day totals — never crowding the big three.
- **Server admin background**: upgrade to the SAME dense scene language as
  the phone map (§4 density block) — mountains, forests, villages, boats,
  mist, cartouche — but a DIFFERENT composition per admin tab (vary seed/
  layout so Status/Users/Backends each feel like another province).
- **Phone map — villages & borders**: add 3-4 village clusters (groups of
  3-6 tiny hip-roofed houses in sumi line + washi fill) along the road
  between stations, and hand-drawn **province borders**: irregular dashed
  `--sumi` boundary lines (1px, dash 3 4, opacity .3) enclosing each tinted
  province and 2-3 more implied regions, like domain borders on Edo maps.

## 7. Motion rules

- Panels: slide + fade, 280ms ease-out. Map pans: 700ms.
- Stamp animation: scale 1.4→1 with slight rotate, 200ms, haptic if available.
- Petals: 20–30s linear drift loops. Respect `prefers-reduced-motion`.

## 8. Tone

Copy is warm and journey-flavored but never cryptic: "You've walked 6.5 of 10
ri today" always sits next to the plain numbers. Kanji is decoration; romaji +
English carry the meaning.
