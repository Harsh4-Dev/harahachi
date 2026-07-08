// Sumi-line weight trend chart (DESIGN.md §6.7): an SVG on a washi grid, sakura
// dots for each entry, a matcha dashed line for the 7-day moving-average trend,
// and a --shu marker line at the goal weight. Pure presentational — data comes
// from GET /weight ({entries, trend}) + the profile's weight_goal_kg.

import type { WeightEntry, WeightTrendPoint } from '../api/types';

interface Props {
  entries: WeightEntry[];
  trend: WeightTrendPoint[];
  goalKg?: number | null;
}

const W = 320;
const H = 180;
const PAD_L = 34;
const PAD_R = 10;
const PAD_T = 12;
const PAD_B = 22;

export function WeightChart({ entries, trend, goalKg }: Props) {
  if (entries.length === 0) {
    return <div className="empty">No weigh-ins yet — add today&apos;s above to start the line.</div>;
  }

  const dates = entries.map((e) => e.date);
  const xForDate = (date: string): number => {
    const i = dates.indexOf(date);
    const n = Math.max(1, dates.length - 1);
    return PAD_L + (i / n) * (W - PAD_L - PAD_R);
  };

  const values = entries.map((e) => e.weight_kg);
  const trendVals = trend.map((t) => t.avg_kg);
  const all = [...values, ...trendVals, ...(goalKg != null ? [goalKg] : [])];
  let min = Math.min(...all);
  let max = Math.max(...all);
  // Pad the range so points don't hug the edges; ensure a non-zero span.
  const span = Math.max(1, max - min);
  min -= span * 0.15;
  max += span * 0.15;
  const yFor = (v: number): number => PAD_T + (1 - (v - min) / (max - min)) * (H - PAD_T - PAD_B);

  // Horizontal grid lines at 4 steps (washi grid).
  const gridLines = 4;
  const gridYs = Array.from({ length: gridLines + 1 }, (_, i) => min + (i / gridLines) * (max - min));

  const entryPath = entries
    .map((e, i) => `${i === 0 ? 'M' : 'L'} ${xForDate(e.date).toFixed(1)} ${yFor(e.weight_kg).toFixed(1)}`)
    .join(' ');
  const trendPath = trend
    .map((t, i) => `${i === 0 ? 'M' : 'L'} ${xForDate(t.date).toFixed(1)} ${yFor(t.avg_kg).toFixed(1)}`)
    .join(' ');

  return (
    <svg className="weight-chart" viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Weight trend">
      {/* Washi grid */}
      {gridYs.map((gv, i) => (
        <g key={i}>
          <line
            x1={PAD_L}
            y1={yFor(gv)}
            x2={W - PAD_R}
            y2={yFor(gv)}
            stroke="var(--sumi)"
            strokeWidth={1}
            opacity={0.12}
          />
          <text x={PAD_L - 6} y={yFor(gv) + 3} textAnchor="end" fontSize={9} fill="var(--sumi-soft)" fontFamily="var(--font-display)">
            {gv.toFixed(1)}
          </text>
        </g>
      ))}

      {/* Goal marker (--shu dashed) */}
      {goalKg != null && goalKg >= min && goalKg <= max && (
        <g>
          <line
            x1={PAD_L}
            y1={yFor(goalKg)}
            x2={W - PAD_R}
            y2={yFor(goalKg)}
            stroke="var(--shu)"
            strokeWidth={1.4}
            strokeDasharray="4 3"
            opacity={0.85}
          />
          <text x={W - PAD_R} y={yFor(goalKg) - 4} textAnchor="end" fontSize={9} fill="var(--shu)" fontFamily="var(--font-display)">
            goal {goalKg}kg
          </text>
        </g>
      )}

      {/* Raw entry line (faint sumi) */}
      <path d={entryPath} fill="none" stroke="var(--sumi)" strokeWidth={1.4} opacity={0.4} />

      {/* 7-day trend (matcha dashed) */}
      <path d={trendPath} fill="none" stroke="var(--matcha)" strokeWidth={2} strokeDasharray="5 4" opacity={0.9} />

      {/* Sakura entry dots */}
      {entries.map((e) => (
        <circle
          key={e.date}
          cx={xForDate(e.date)}
          cy={yFor(e.weight_kg)}
          r={3}
          fill="var(--sakura-deep)"
          stroke="var(--washi)"
          strokeWidth={1}
        />
      ))}
    </svg>
  );
}
