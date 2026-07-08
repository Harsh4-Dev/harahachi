// Mifflin-St Jeor BMR/TDEE and macro targets. Mirrors the server's computation
// so demo mode and the onboarding preview agree with the server.

import type { Macros, Profile } from '../api/types';

const ACTIVITY_FACTOR: Record<Profile['activity'], number> = {
  sedentary: 1.2,
  light: 1.375,
  moderate: 1.55,
  active: 1.725,
};

export function computeTargets(p: Profile): Macros {
  // When body fat % is known, use Katch-McArdle (lean-mass based); else fall
  // back to Mifflin-St Jeor. Mirrors the server (API.md v2).
  let bmr: number;
  if (p.body_fat_pct != null && p.body_fat_pct > 0 && p.body_fat_pct < 60) {
    const leanMassKg = p.weight_kg * (1 - p.body_fat_pct / 100);
    bmr = 370 + 21.6 * leanMassKg;
  } else {
    const base = 10 * p.weight_kg + 6.25 * p.height_cm - 5 * p.age;
    bmr = p.sex === 'm' ? base + 5 : p.sex === 'f' ? base - 161 : base - 78;
  }
  const tdee = bmr * ACTIVITY_FACTOR[p.activity];

  let calories = tdee;
  // v3: when a weekly pace is set, drive the deficit/surplus from it
  // (7700 kcal ≈ 1 kg) instead of the flat ±20/10%, and clamp to ≥1200 kcal.
  // The pace's SIGN follows the goal: cut → deficit, bulk → surplus.
  if (p.pace_kg_per_week != null && p.pace_kg_per_week > 0 && p.goal !== 'maintain') {
    const dailyDelta = (p.pace_kg_per_week * 7700) / 7;
    calories = p.goal === 'cut' ? tdee - dailyDelta : tdee + dailyDelta;
    calories = Math.max(1200, calories);
  } else if (p.goal === 'cut') {
    calories = tdee * 0.8;
  } else if (p.goal === 'bulk') {
    calories = tdee * 1.1;
  }
  calories = Math.round(calories / 10) * 10;

  const protein_g = Math.round(1.6 * p.weight_kg);
  const fat_g = Math.round((calories * 0.25) / 9);
  const carbs_g = Math.max(0, Math.round((calories - protein_g * 4 - fat_g * 9) / 4));

  return { calories, protein_g, carbs_g, fat_g };
}

// v3: projected date to reach weight_goal_kg from the current weight at
// pace_kg_per_week. Returns an ISO date (YYYY-MM-DD) or null when there's no
// goal/pace or the goal is already met. Mirrors GET /profile's eta_date.
export function computeEtaDate(p: Profile): string | null {
  const goal = p.weight_goal_kg;
  const pace = p.pace_kg_per_week;
  if (goal == null || pace == null || pace <= 0) return null;
  const deltaKg = Math.abs(p.weight_kg - goal);
  if (deltaKg < 0.05) return null; // already there
  const weeks = deltaKg / pace;
  const d = new Date();
  d.setDate(d.getDate() + Math.round(weeks * 7));
  return d.toISOString().slice(0, 10);
}
