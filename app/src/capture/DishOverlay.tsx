// Dish analysis that SURROUNDS the dish — the food stays visible, nothing sits
// on top of it (DESIGN.md §6.5, updated). Layout:
//   • Title ribbon at the very top (dish + confidence), clarifying-question chip
//     just under it (one-tap answers).
//   • One compact glass chip per items[] entry, pinned NEXT TO its component:
//     positioned from the item's normalized `point` (pushed outward toward the
//     nearest edge so it sits beside, not over, the food) with a 1px --shu
//     leader line back to the point. Items without a point alternate down the
//     left/right edges with a leader angled at the plate centre. Chips never
//     overlap (vertical collision-nudge) and keep a clear central zone (~55% of
//     the frame) empty. Tap a chip to cycle portion ×0.5/×1/×1.5/×2; ✕ removes.
//   • Full-width glass TOTAL BAR pinned at the bottom: big total kcal + three
//     macro chips + the hanko "add to today's goal" stamp at its right end.

import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { AnalyzeResult, MealInput } from '../api/types';
import { fmtInt } from '../lib/format';
import { NutrientChips } from '../components/NutrientChips';

interface Props {
  result: AnalyzeResult;
  reducedMotion: boolean;
  // Natural pixel size of the frozen photo (0 when unknown, e.g. gallery files
  // before load). Used to map image-space item points through the object-fit:
  // cover crop so chips/leader dots sit exactly on their food part.
  imgW?: number;
  imgH?: number;
  onRetake: () => void;
  onLog: (meal: MealInput) => Promise<void> | void;
}

// object-fit: cover maps an (imgW×imgH) image into a (w×h) box by scaling to
// COVER (max scale) and centre-cropping the overflow. Convert an image-space
// normalized point (0..1) to a container-space normalized point (0..1) so chips
// and leader dots land on the right food part at any viewport size / rotation.
function coverMap(
  nx: number,
  ny: number,
  imgW: number,
  imgH: number,
  w: number,
  h: number,
): { x: number; y: number } {
  if (!imgW || !imgH || !w || !h) return { x: nx, y: ny };
  const scale = Math.max(w / imgW, h / imgH);
  const dispW = imgW * scale;
  const dispH = imgH * scale;
  const offX = (w - dispW) / 2; // negative → cropped left/right
  const offY = (h - dispH) / 2;
  const px = offX + nx * dispW;
  const py = offY + ny * dispH;
  // Clamp into the visible box so a point cropped off-screen still yields a
  // sensible on-frame anchor rather than drawing off the edge.
  return {
    x: Math.min(1, Math.max(0, px / w)),
    y: Math.min(1, Math.max(0, py / h)),
  };
}

interface ItemState {
  mult: number;
  removed: boolean;
}

// A normalized item: the big three always present, extended nutrients optional.
type NormItem = {
  name: string;
  calories: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
  fiber_g?: number;
  sugar_g?: number;
  sodium_mg?: number;
  point?: { x: number; y: number };
};

// Fill in per-item macros (distributing top-level totals by calorie share when
// an item lacks its own) so removing/rescaling shifts totals sensibly. Extended
// nutrients (fiber/sugar/sodium) are distributed the same way when the item
// lacks its own but the dish carries a total.
function normalizeItems(result: AnalyzeResult): NormItem[] {
  const totalCals = result.items.reduce((a, it) => a + it.calories, 0) || result.calories || 1;
  return result.items.map((it) => {
    const share = it.calories / totalCals;
    const spread = (itemVal: number | undefined, total: number | undefined): number | undefined => {
      if (itemVal != null) return itemVal;
      if (total != null) return Math.round(total * share);
      return undefined;
    };
    return {
      name: it.name,
      calories: it.calories,
      protein_g: it.protein_g ?? Math.round(result.protein_g * share),
      carbs_g: it.carbs_g ?? Math.round(result.carbs_g * share),
      fat_g: it.fat_g ?? Math.round(result.fat_g * share),
      fiber_g: spread(it.fiber_g, result.fiber_g),
      sugar_g: spread(it.sugar_g, result.sugar_g),
      sodium_mg: spread(it.sodium_mg, result.sodium_mg),
      point: it.point,
    };
  });
}

const PORTION_STEPS = [0.5, 1, 1.5, 2];

// Vertical band the chips live in (fractions of the frame height), leaving room
// for the title ribbon at the top and the total bar at the bottom.
// Chips live between the title ribbon (top) and the total bar (bottom); values
// leave clearance for both so labels never sit under them.
const BAND_TOP = 0.24;
const BAND_BOTTOM = 0.76;
const CHIP_H_FRAC = 0.09; // approx chip height as a fraction of the frame
const EDGE_X_LEFT = 0.03;
const EDGE_X_RIGHT = 0.97; // right anchor (chip is right-aligned there)

interface Placed {
  side: 'left' | 'right';
  y: number; // top of chip, fraction of frame height
  point: { x: number; y: number }; // leader target, fraction of frame
}

// Deterministically place chips beside the food: use each item's point to pick
// a side (left half → left edge, right half → right edge) and a vertical slot,
// then nudge apart so none overlap. Items without a point alternate sides.
function placeChips(
  items: { point?: { x: number; y: number } }[],
): Placed[] {
  const placed: Placed[] = items.map((it, i) => {
    const p = it.point;
    if (p) {
      const side: 'left' | 'right' = p.x < 0.5 ? 'left' : 'right';
      const y = Math.min(BAND_BOTTOM - CHIP_H_FRAC, Math.max(BAND_TOP, p.y - CHIP_H_FRAC / 2));
      return { side, y, point: { x: p.x, y: p.y } };
    }
    // No point: alternate edges, target the plate centre.
    const side: 'left' | 'right' = i % 2 === 0 ? 'left' : 'right';
    const y = BAND_TOP + ((i + 0.5) / (items.length || 1)) * (BAND_BOTTOM - BAND_TOP - CHIP_H_FRAC);
    return { side, y, point: { x: 0.5, y: Math.min(0.7, Math.max(0.3, y + CHIP_H_FRAC / 2)) } };
  });

  // Collision-nudge within each side: sort by y and push overlaps down, then
  // clamp back up if they run past the band.
  (['left', 'right'] as const).forEach((side) => {
    const idx = placed
      .map((p, i) => ({ p, i }))
      .filter((x) => x.p.side === side)
      .sort((a, b) => a.p.y - b.p.y);
    let cursor = BAND_TOP;
    for (const { p } of idx) {
      if (p.y < cursor) p.y = cursor;
      cursor = p.y + CHIP_H_FRAC + 0.01;
    }
    // If we overflowed the bottom, shift the whole column up.
    const overflow = cursor - 0.01 - BAND_BOTTOM;
    if (overflow > 0) for (const { p } of idx) p.y = Math.max(BAND_TOP, p.y - overflow);
  });

  return placed;
}

export function DishOverlay({ result, reducedMotion, imgW = 0, imgH = 0, onRetake, onLog }: Props) {
  const items = useMemo(() => normalizeItems(result), [result]);
  const [states, setStates] = useState<ItemState[]>(() => items.map(() => ({ mult: 1, removed: false })));
  const [dish] = useState(result.dish);
  const [answered, setAnswered] = useState(false);
  const [stamped, setStamped] = useState(false);
  const [busy, setBusy] = useState(false);

  const frameRef = useRef<HTMLDivElement | null>(null);
  const [frameSize, setFrameSize] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const measure = () => {
      const el = frameRef.current;
      if (el) setFrameSize({ w: el.clientWidth, h: el.clientHeight });
    };
    measure();
    window.addEventListener('resize', measure);
    // Re-measure on device rotation of the frozen view so chips stay anchored.
    window.addEventListener('orientationchange', measure);
    return () => {
      window.removeEventListener('resize', measure);
      window.removeEventListener('orientationchange', measure);
    };
  }, []);

  // Map each item's image-space point through the cover-crop into container
  // space FIRST, then place chips off those corrected points. Recomputed when
  // the frame size (or rotation) changes.
  const mappedItems = useMemo(
    () =>
      items.map((it) => ({
        ...it,
        point: it.point
          ? coverMap(it.point.x, it.point.y, imgW, imgH, frameSize.w, frameSize.h)
          : undefined,
      })),
    [items, imgW, imgH, frameSize.w, frameSize.h],
  );

  const placements = useMemo(() => placeChips(mappedItems), [mappedItems]);

  const totals = useMemo(() => {
    return items.reduce(
      (acc, it, i) => {
        const s = states[i];
        if (s.removed) return acc;
        return {
          calories: acc.calories + it.calories * s.mult,
          protein_g: acc.protein_g + it.protein_g * s.mult,
          carbs_g: acc.carbs_g + it.carbs_g * s.mult,
          fat_g: acc.fat_g + it.fat_g * s.mult,
          fiber_g: acc.fiber_g + (it.fiber_g ?? 0) * s.mult,
          sugar_g: acc.sugar_g + (it.sugar_g ?? 0) * s.mult,
          sodium_mg: acc.sodium_mg + (it.sodium_mg ?? 0) * s.mult,
        };
      },
      { calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0, fiber_g: 0, sugar_g: 0, sodium_mg: 0 },
    );
  }, [items, states]);

  // Whether any extended nutrient is present at all (so "more" only appears when
  // there's something to show).
  const hasExtras = useMemo(
    () => items.some((it) => it.fiber_g != null || it.sugar_g != null || it.sodium_mg != null),
    [items],
  );
  const [showMore, setShowMore] = useState(false);

  const conf = Math.round(result.confidence * 100);
  const anyLeft = states.some((s) => !s.removed);

  const cyclePortion = (i: number) => {
    setStates((prev) =>
      prev.map((s, j) => {
        if (j !== i) return s;
        const idx = PORTION_STEPS.indexOf(s.mult);
        return { ...s, mult: PORTION_STEPS[(idx + 1) % PORTION_STEPS.length] };
      }),
    );
  };

  const removeItem = (i: number) => {
    setStates((prev) => prev.map((s, j) => (j === i ? { ...s, removed: true } : s)));
  };

  const answerQuestion = (bump: boolean) => {
    setAnswered(true);
    if (!bump) return;
    setStates((prev) => prev.map((s) => (s.removed ? s : { ...s, mult: Math.min(2, +(s.mult + 0.25).toFixed(2)) })));
  };

  const stamp = () => {
    if (busy || !anyLeft) return;
    setBusy(true);
    setStamped(true);
    if ('vibrate' in navigator) navigator.vibrate?.(30);
    const meal: MealInput = {
      dish,
      calories: Math.round(totals.calories),
      protein_g: Math.round(totals.protein_g),
      carbs_g: Math.round(totals.carbs_g),
      fat_g: Math.round(totals.fat_g),
      source: 'photo',
      ...(hasExtras
        ? {
            fiber_g: Math.round(totals.fiber_g),
            sugar_g: Math.round(totals.sugar_g),
            sodium_mg: Math.round(totals.sodium_mg),
          }
        : {}),
    };
    const commit = () => void onLog(meal);
    if (reducedMotion) commit();
    else window.setTimeout(commit, 220);
  };

  const { w, h } = frameSize;

  return (
    <div className="dish-overlay" ref={frameRef}>
      {/* Leader lines (SVG under the chips) */}
      {w > 0 && (
        <svg className="leader-svg" width={w} height={h} aria-hidden>
          {items.map((_, i) => {
            if (states[i].removed) return null;
            const pl = placements[i];
            const px = pl.point.x * w;
            const py = pl.point.y * h;
            // Chip anchor: the inner edge of the chip at its vertical middle.
            const chipY = pl.y * h + (CHIP_H_FRAC * h) / 2;
            const chipX = pl.side === 'left' ? EDGE_X_LEFT * w + 4 : EDGE_X_RIGHT * w - 4;
            return (
              <g key={i}>
                <line x1={chipX} y1={chipY} x2={px} y2={py} stroke="var(--shu)" strokeWidth={1} opacity={0.85} />
                <circle cx={px} cy={py} r={3} fill="var(--shu)" />
              </g>
            );
          })}
        </svg>
      )}

      {/* Title ribbon + clarifying question */}
      <div className="dish-top">
        <div className="glass overlay-card overlay-title">
          <span className="overlay-dish display">{dish}</span>
          <span className={`conf ${conf >= 85 ? 'conf-ok' : 'conf-low'}`}>{conf}% sure</span>
        </div>
        {result.question && !answered && (
          <div className="glass-ink overlay-question">
            <span className="oq-text">{result.question}</span>
            <div className="oq-answers">
              <button className="oq-btn" onClick={() => answerQuestion(true)}>
                Yes / more
              </button>
              <button className="oq-btn" onClick={() => answerQuestion(false)}>
                No / as-is
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Pinned per-component chips */}
      {items.map((it, i) => {
        const s = states[i];
        if (s.removed) return null;
        const pl = placements[i];
        const style: React.CSSProperties =
          pl.side === 'left'
            ? { left: `${EDGE_X_LEFT * 100}%`, top: `${pl.y * 100}%` }
            : { right: `${(1 - EDGE_X_RIGHT) * 100}%`, top: `${pl.y * 100}%` };
        return (
          <div className={`glass item-chip item-chip-${pl.side}`} key={i} style={style}>
            <button
              className="item-portion tnum"
              onClick={() => cyclePortion(i)}
              aria-label={`Adjust portion of ${it.name}`}
              title="Tap to change portion"
            >
              ×{s.mult}
            </button>
            <div className="item-body">
              <span className="item-name">{it.name}</span>
              <span className="item-macros small">
                {fmtInt(it.calories * s.mult)} kcal · P{fmtInt(it.protein_g * s.mult)} C
                {fmtInt(it.carbs_g * s.mult)} F{fmtInt(it.fat_g * s.mult)}
              </span>
            </div>
            <button className="item-del" aria-label={`Remove ${it.name}`} onClick={() => removeItem(i)}>
              ✕
            </button>
          </div>
        );
      })}

      {/* Bottom total bar */}
      <div className="glass total-bar">
        <div className="total-figures">
          <div className="total-kcal tnum">
            <span className="big">{fmtInt(totals.calories)}</span> kcal
          </div>
          <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
            <span className="chip chip-protein">P {fmtInt(totals.protein_g)}g</span>
            <span className="chip chip-carbs">C {fmtInt(totals.carbs_g)}g</span>
            <span className="chip chip-fat">F {fmtInt(totals.fat_g)}g</span>
            {hasExtras && (
              <button
                className="chip chip-more"
                onClick={() => setShowMore((v) => !v)}
                aria-expanded={showMore}
              >
                {showMore ? 'less' : 'more'}
              </button>
            )}
          </div>
          {hasExtras && showMore && (
            <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
              <NutrientChips fiber={totals.fiber_g} sugar={totals.sugar_g} sodium={totals.sodium_mg} />
            </div>
          )}
        </div>
        <div className="total-actions">
          <button className="total-retake" onClick={onRetake} disabled={busy} aria-label="Retake photo">
            ↺
          </button>
          <button
            className={`hanko-stamp ${stamped ? 'stamped' : ''}`}
            onClick={stamp}
            disabled={busy || !anyLeft}
            aria-label="Add to today's goal"
          >
            <span className="hanko-inner">
              <span className="hanko-glyph">記</span>
            </span>
            <span className="hanko-label">add to today’s goal</span>
          </button>
        </div>
      </div>
    </div>
  );
}
