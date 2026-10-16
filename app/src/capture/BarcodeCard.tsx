// Product glass card floating over the barcode feed (DESIGN.md §6.5): name,
// brand, per-serving kcal + macros, a serving stepper (×0.5/×1/×1.5/×2 that
// scales the values), and the vermillion hanko "add to today's goal" stamp.
// found:false → a glass card offering manual entry.

import { useMemo, useState } from 'react';
import type { BarcodeResult, MealInput } from '../api/types';
import { fmtInt } from '../lib/format';
import { NutrientChips } from '../components/NutrientChips';

interface Props {
  result: BarcodeResult;
  onRescan: () => void;
  onLog: (meal: MealInput) => Promise<void> | void;
  onManual: () => void; // hand off to Chaya's manual entry
}

const SERVING_STEPS = [0.5, 1, 1.5, 2];

export function BarcodeCard({ result, onRescan, onLog, onManual }: Props) {
  const [mult, setMult] = useState(1);
  const [stamped, setStamped] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showMore, setShowMore] = useState(false);

  const p = result.product;

  const scaled = useMemo(() => {
    const ns = p?.per_serving;
    if (!ns) return { calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0, fiber_g: 0, sugar_g: 0, sodium_mg: 0 };
    return {
      calories: Math.round(ns.calories * mult),
      protein_g: Math.round(ns.protein_g * mult),
      carbs_g: Math.round(ns.carbs_g * mult),
      fat_g: Math.round(ns.fat_g * mult),
      fiber_g: ns.fiber_g != null ? Math.round(ns.fiber_g * mult) : 0,
      sugar_g: ns.sugar_g != null ? Math.round(ns.sugar_g * mult) : 0,
      sodium_mg: ns.sodium_mg != null ? Math.round(ns.sodium_mg * mult) : 0,
    };
  }, [p, mult]);

  const hasExtras = !!(p?.per_serving && (p.per_serving.fiber_g != null || p.per_serving.sugar_g != null || p.per_serving.sodium_mg != null));

  if (!result.found || !p) {
    return (
      <div className="capture-center">
        <div className="glass overlay-card barcode-card">
          <div className="card-title">Barcode not recognised</div>
          <div className="small muted" style={{ margin: '6px 0 14px' }}>
            Code {result.code} isn’t in the catalogue yet. You can log it by hand instead.
          </div>
          <div className="row between">
            <button className="btn btn-ghost" onClick={onRescan}>
              Scan again
            </button>
            <button className="btn btn-primary" onClick={onManual}>
              Enter manually
            </button>
          </div>
        </div>
      </div>
    );
  }

  const stamp = () => {
    if (busy) return;
    setBusy(true);
    setStamped(true);
    if ('vibrate' in navigator) navigator.vibrate?.(30);
    const meal: MealInput = {
      dish: `${p.name}${mult !== 1 ? ` ×${mult}` : ''}`,
      calories: scaled.calories,
      protein_g: scaled.protein_g,
      carbs_g: scaled.carbs_g,
      fat_g: scaled.fat_g,
      source: 'barcode',
      ...(hasExtras
        ? { fiber_g: scaled.fiber_g, sugar_g: scaled.sugar_g, sodium_mg: scaled.sodium_mg }
        : {}),
    };
    window.setTimeout(() => void onLog(meal), 220);
  };

  return (
    <div className="capture-center">
      <div className="glass overlay-card barcode-card">
        <div className="barcode-head">
          <div>
            <div className="overlay-dish display">{p.name}</div>
            <div className="small muted">{p.brand} · {p.serving}</div>
          </div>
          <button className="item-del" aria-label="Scan another" onClick={onRescan}>
            ✕
          </button>
        </div>

        <div className="overlay-total-kcal tnum" style={{ marginTop: 10 }}>
          <span className="big">{fmtInt(scaled.calories)}</span> kcal
          <span className="small muted" style={{ marginLeft: 8 }}>
            per {mult}× serving
          </span>
        </div>

        <div className="row" style={{ gap: 6, flexWrap: 'wrap', margin: '10px 0' }}>
          <span className="chip chip-protein">P {fmtInt(scaled.protein_g)}g</span>
          <span className="chip chip-carbs">C {fmtInt(scaled.carbs_g)}g</span>
          <span className="chip chip-fat">F {fmtInt(scaled.fat_g)}g</span>
          {hasExtras && (
            <button className="chip chip-more" onClick={() => setShowMore((v) => !v)} aria-expanded={showMore}>
              {showMore ? 'less' : 'more'}
            </button>
          )}
        </div>
        {hasExtras && showMore && (
          <div className="row" style={{ gap: 6, flexWrap: 'wrap', margin: '0 0 10px' }}>
            <NutrientChips fiber={scaled.fiber_g} sugar={scaled.sugar_g} sodium={scaled.sodium_mg} />
          </div>
        )}

        <div className="serving-steps">
          {SERVING_STEPS.map((s) => (
            <button
              key={s}
              className={`pill ${mult === s ? 'active' : ''}`}
              onClick={() => setMult(s)}
            >
              ×{s}
            </button>
          ))}
        </div>

        <div className="overlay-actions" style={{ position: 'static', marginTop: 14 }}>
          <button className="btn btn-ghost" onClick={onRescan} disabled={busy}>
            Scan again
          </button>
          <button
            className={`hanko-stamp ${stamped ? 'stamped' : ''}`}
            onClick={stamp}
            disabled={busy}
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
