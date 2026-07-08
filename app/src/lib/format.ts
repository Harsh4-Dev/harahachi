// Small formatting helpers.

export function fmtInt(n: number): string {
  return Math.round(n).toLocaleString('en-US');
}

export function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')}`;
}

export function monthStr(d = new Date()): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

export function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  } catch {
    return '';
  }
}

// kcal -> "ri" (里) for the journey copy: 10 ri == a full day's target.
export function ri(consumed: number, target: number): number {
  if (target <= 0) return 0;
  return Math.round((consumed / target) * 10 * 10) / 10;
}
