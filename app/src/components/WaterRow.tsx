// Water row (DESIGN.md §6.7): a row of cup glyphs. Tap an empty cup → +250 ml
// (POST /water); filled cups render in --ai. A small undo affordance removes the
// last 250 ml. Target derives from the day totals' water_target_ml (35 ml/kg,
// min 2000 → ~8+ cups). Cup count is capped so the row never overflows on phones;
// beyond the visible cups a "+N" note shows the overflow.

import { fmtInt } from '../lib/format';

interface Props {
  ml: number;
  targetMl: number;
  onAdd: () => void;
  onUndo: () => void;
}

const CUP_ML = 250;
const MAX_CUPS = 12; // visual cap

export function WaterRow({ ml, targetMl, onAdd, onUndo }: Props) {
  const targetCups = Math.max(1, Math.round(targetMl / CUP_ML));
  const filled = Math.floor(ml / CUP_ML);
  const shownCups = Math.min(MAX_CUPS, Math.max(targetCups, filled));
  const overflow = filled - shownCups;

  return (
    <div className="water-row">
      <div className="water-head">
        <span className="water-label">
          Water · 水{' '}
          <span className="small muted tnum">
            {fmtInt(ml)} / {fmtInt(targetMl)} ml
          </span>
        </span>
        <button
          className="water-undo"
          onClick={onUndo}
          disabled={ml <= 0}
          aria-label="Undo last cup"
          title="Undo a cup (−250 ml)"
        >
          −
        </button>
      </div>
      <div className="water-cups">
        {Array.from({ length: shownCups }, (_, i) => {
          const isFilled = i < filled;
          return (
            <button
              key={i}
              className={`water-cup ${isFilled ? 'filled' : ''}`}
              onClick={onAdd}
              aria-label={isFilled ? `Cup ${i + 1} filled` : `Add cup ${i + 1} (+250 ml)`}
            >
              <svg viewBox="0 0 24 28" width="20" height="24" aria-hidden>
                {/* cup outline */}
                <path
                  d="M4 4 L20 4 L18 24 Q18 26 16 26 L8 26 Q6 26 6 24 Z"
                  fill={isFilled ? 'var(--ai)' : 'transparent'}
                  stroke="var(--ai)"
                  strokeWidth="1.6"
                  opacity={isFilled ? 0.9 : 0.5}
                />
              </svg>
            </button>
          );
        })}
        {overflow > 0 && <span className="water-overflow tnum">+{overflow}</span>}
      </div>
    </div>
  );
}
