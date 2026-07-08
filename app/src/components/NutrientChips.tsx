// Tertiary nutrient chips (fiber / sugar / sodium) — shown behind a "more" tap
// on the dish overlay total bar, the barcode card, and day totals (DESIGN.md
// §6.7). Deliberately muted so they never crowd the big three. Values ≤ 0 or
// undefined are skipped.

import { fmtInt } from '../lib/format';

interface Props {
  fiber?: number;
  sugar?: number;
  sodium?: number; // mg
}

export function NutrientChips({ fiber, sugar, sodium }: Props) {
  const chips: { key: string; label: string }[] = [];
  if (fiber != null && fiber > 0) chips.push({ key: 'fiber', label: `Fiber ${fmtInt(fiber)}g` });
  if (sugar != null && sugar > 0) chips.push({ key: 'sugar', label: `Sugar ${fmtInt(sugar)}g` });
  if (sodium != null && sodium > 0) chips.push({ key: 'sodium', label: `Sodium ${fmtInt(sodium)}mg` });
  if (chips.length === 0) return <span className="chip chip-tertiary">No extra data</span>;
  return (
    <>
      {chips.map((c) => (
        <span key={c.key} className="chip chip-tertiary">
          {c.label}
        </span>
      ))}
    </>
  );
}
