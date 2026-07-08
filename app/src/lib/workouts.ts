// Workout types + MET (metabolic equivalent) table for the Dōjō 道場 timer.
//
// Calories burned are CALCULATED, never hand-entered:
//   kcal = MET × weight_kg × hours
// (the standard MET formula, where 1 MET ≈ 1 kcal/kg/hour). Weight comes from
// the traveler's profile; elapsed time comes from the live timer. Minutes are
// derived from elapsed seconds — the user never types a number.

export type WorkoutType =
  | 'Strength'
  | 'Calisthenics'
  | 'Powerlifting'
  | 'Running'
  | 'Cycling'
  | 'Yoga'
  | 'Sports';

export interface WorkoutMet {
  type: WorkoutType;
  met: number;
  kanji: string;
  hint: string;
}

// Compendium-of-physical-activities-flavoured MET values (moderate/general
// effort per type). Kept intentionally round; the calorie count is an estimate.
export const WORKOUT_METS: WorkoutMet[] = [
  { type: 'Strength', met: 5.0, kanji: '力', hint: 'weights, general' },
  { type: 'Calisthenics', met: 6.0, kanji: '体', hint: 'bodyweight, vigorous' },
  { type: 'Powerlifting', met: 6.0, kanji: '重', hint: 'heavy, low reps' },
  { type: 'Running', met: 9.8, kanji: '走', hint: 'steady pace' },
  { type: 'Cycling', met: 7.5, kanji: '輪', hint: 'moderate' },
  { type: 'Yoga', met: 3.0, kanji: '禅', hint: 'flow / hatha' },
  { type: 'Sports', met: 7.0, kanji: '球', hint: 'general play' },
];

export function metForType(type: WorkoutType): number {
  return WORKOUT_METS.find((w) => w.type === type)?.met ?? 6.0;
}

// Live calorie count-up from elapsed seconds + body weight. Returns a rounded
// integer so the ticking number reads cleanly.
export function caloriesBurned(type: WorkoutType, weightKg: number, elapsedSec: number): number {
  const hours = elapsedSec / 3600;
  return Math.round(metForType(type) * weightKg * hours);
}

// Elapsed seconds → whole minutes (min 1 once any time has elapsed), for the
// logged workout's `minutes` field.
export function elapsedMinutes(elapsedSec: number): number {
  if (elapsedSec <= 0) return 0;
  return Math.max(1, Math.round(elapsedSec / 60));
}

// mm:ss (or h:mm:ss past an hour) for the timer read-out.
export function formatElapsed(elapsedSec: number): string {
  const s = Math.max(0, Math.floor(elapsedSec));
  const hrs = Math.floor(s / 3600);
  const min = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(min).padStart(2, '0');
  const ss = String(sec).padStart(2, '0');
  return hrs > 0 ? `${hrs}:${mm}:${ss}` : `${mm}:${ss}`;
}
