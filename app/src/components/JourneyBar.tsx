// The signature daily intake bar: a horizontal excerpt of the road with origin
// and destination hanko seals. A sakura-deep stroke fills along the path as
// kcal accumulate; overshoot past the destination seal renders in vermillion.
// Label: "1,430 / 2,200 kcal · 里 6.5 of 10".

import { useEffect, useRef } from 'react';
import { fmtInt, ri } from '../lib/format';

interface Props {
  consumed: number; // net kcal (meals - burned) or raw, caller decides
  target: number;
  burned?: number;
}

// Gentle S-curve road excerpt across a 320x64 box.
const PATH_D = 'M 24 44 C 90 20, 150 60, 210 38 S 300 20, 300 30';

export function JourneyBar({ consumed, target, burned = 0 }: Props) {
  const pathRef = useRef<SVGPathElement | null>(null);
  const fillRef = useRef<SVGPathElement | null>(null);
  const overRef = useRef<SVGPathElement | null>(null);

  useEffect(() => {
    const path = pathRef.current;
    const fill = fillRef.current;
    const over = overRef.current;
    if (!path || !fill || !over) return;
    const len = path.getTotalLength();
    const frac = target > 0 ? consumed / target : 0;
    const mainFrac = Math.min(1, Math.max(0, frac));
    // Main fill up to min(1, frac)
    fill.style.strokeDasharray = `${len}`;
    fill.style.strokeDashoffset = `${len * (1 - mainFrac)}`;
    // Overshoot: draw a short vermillion continuation past the destination seal.
    const overFrac = Math.max(0, frac - 1);
    over.style.opacity = overFrac > 0 ? '1' : '0';
    over.style.transform = `scaleX(${Math.min(1, overFrac * 3)})`;
  }, [consumed, target]);

  const riVal = ri(consumed, target);

  return (
    <div className="journey-bar">
      <svg viewBox="0 0 324 72" width="100%" preserveAspectRatio="xMidYMid meet">
        {/* base road */}
        <path
          ref={pathRef}
          d={PATH_D}
          fill="none"
          stroke="var(--sumi)"
          strokeWidth={3}
          strokeDasharray="6 4"
          strokeLinecap="round"
          opacity={0.35}
        />
        {/* progress fill */}
        <path
          ref={fillRef}
          d={PATH_D}
          fill="none"
          stroke="var(--sakura-deep)"
          strokeWidth={5}
          strokeLinecap="round"
          style={{ transition: 'stroke-dashoffset 600ms cubic-bezier(.22,.9,.31,1)' }}
        />
        {/* overshoot stub past destination */}
        <g style={{ transformOrigin: '300px 30px', transition: 'transform 500ms, opacity 300ms' }} ref={overRef as never}>
          <path d="M 300 30 C 312 26, 320 30, 330 26" fill="none" stroke="var(--shu)" strokeWidth={5} strokeLinecap="round" />
        </g>
        {/* origin seal */}
        <g transform="translate(24 44)">
          <circle r={12} fill="var(--washi)" stroke="var(--shu)" strokeWidth={3} />
          <text textAnchor="middle" dominantBaseline="central" fontFamily="var(--font-display)" fontSize={13} fill="var(--shu)">
            出
          </text>
        </g>
        {/* destination seal */}
        <g transform="translate(300 30)">
          <circle r={12} fill={consumed >= target ? 'var(--shu)' : 'var(--washi)'} stroke="var(--shu)" strokeWidth={3} />
          <text
            textAnchor="middle"
            dominantBaseline="central"
            fontFamily="var(--font-display)"
            fontSize={13}
            fill={consumed >= target ? 'var(--washi)' : 'var(--shu)'}
          >
            着
          </text>
        </g>
      </svg>
      <div className="journey-label tnum">
        <strong>{fmtInt(consumed)}</strong> / {fmtInt(target)} kcal
        <span className="ri"> · 里 {riVal} of 10</span>
        {burned > 0 && <span className="burned"> · +{fmtInt(burned)} burned</span>}
      </div>
    </div>
  );
}
