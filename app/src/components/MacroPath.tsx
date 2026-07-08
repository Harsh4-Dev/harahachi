// A small parallel road-path for one macro (protein=matcha, carbs=ai,
// fat=kin). Renders label, current/target grams, and a filling stroke.

import { useEffect, useRef } from 'react';
import { fmtInt } from '../lib/format';

interface Props {
  label: string;
  kanji: string;
  color: string;
  value: number;
  target: number;
}

const D = 'M 6 14 C 60 4, 120 22, 180 10 S 260 6, 300 12';

export function MacroPath({ label, kanji, color, value, target }: Props) {
  const base = useRef<SVGPathElement | null>(null);
  const fill = useRef<SVGPathElement | null>(null);

  useEffect(() => {
    const b = base.current;
    const f = fill.current;
    if (!b || !f) return;
    const len = b.getTotalLength();
    const frac = target > 0 ? Math.min(1, value / target) : 0;
    f.style.strokeDasharray = `${len}`;
    f.style.strokeDashoffset = `${len * (1 - frac)}`;
  }, [value, target]);

  return (
    <div className="macro-path">
      <div className="macro-head">
        <span className="macro-kanji" style={{ color }}>
          {kanji}
        </span>
        <span className="macro-label">{label}</span>
        <span className="macro-val tnum">
          {fmtInt(value)}<span className="muted">/{fmtInt(target)}g</span>
        </span>
      </div>
      <svg viewBox="0 0 306 22" width="100%" preserveAspectRatio="none">
        <path ref={base} d={D} fill="none" stroke="var(--sumi)" strokeWidth={2} strokeDasharray="4 3" opacity={0.28} />
        <path
          ref={fill}
          d={D}
          fill="none"
          stroke={color}
          strokeWidth={3.5}
          strokeLinecap="round"
          style={{ transition: 'stroke-dashoffset 600ms cubic-bezier(.22,.9,.31,1)' }}
        />
      </svg>
    </div>
  );
}
