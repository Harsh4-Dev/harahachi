// A tiny rule-based "I ate X" parser for demo mode's Annai chat, mirroring the
// server's mock (API.md v2: "can still parse 'I ate <food>' for common Indian
// foods"). Not exhaustive — just enough that demo chat can log a meal.

import type { MealInput } from '../api/types';

interface FoodEntry {
  keys: string[]; // lowercase substrings to match
  dish: string;
  calories: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
}

// Per-unit nutrition for common foods. Quantities multiply these.
const FOODS: FoodEntry[] = [
  { keys: ['idli'], dish: 'Idli', calories: 58, protein_g: 2, carbs_g: 12, fat_g: 0 },
  { keys: ['dosa', 'masala dosa'], dish: 'Dosa', calories: 168, protein_g: 4, carbs_g: 25, fat_g: 6 },
  { keys: ['roti', 'chapati', 'phulka'], dish: 'Roti', calories: 110, protein_g: 3, carbs_g: 20, fat_g: 2 },
  { keys: ['paratha'], dish: 'Paratha', calories: 210, protein_g: 5, carbs_g: 28, fat_g: 9 },
  { keys: ['rice', 'chawal'], dish: 'Rice', calories: 205, protein_g: 4, carbs_g: 45, fat_g: 1 },
  { keys: ['dal', 'daal', 'sambar', 'sambhar'], dish: 'Dal', calories: 150, protein_g: 8, carbs_g: 22, fat_g: 4 },
  { keys: ['paneer'], dish: 'Paneer', calories: 265, protein_g: 14, carbs_g: 8, fat_g: 20 },
  { keys: ['egg', 'anda'], dish: 'Egg', calories: 78, protein_g: 6, carbs_g: 1, fat_g: 5 },
  { keys: ['banana', 'kela'], dish: 'Banana', calories: 105, protein_g: 1, carbs_g: 27, fat_g: 0 },
  { keys: ['apple'], dish: 'Apple', calories: 95, protein_g: 0, carbs_g: 25, fat_g: 0 },
  { keys: ['curd', 'dahi', 'yogurt', 'yoghurt'], dish: 'Curd', calories: 98, protein_g: 5, carbs_g: 7, fat_g: 5 },
  { keys: ['poha'], dish: 'Poha', calories: 250, protein_g: 5, carbs_g: 45, fat_g: 6 },
  { keys: ['upma'], dish: 'Upma', calories: 230, protein_g: 5, carbs_g: 40, fat_g: 6 },
  { keys: ['biryani'], dish: 'Biryani', calories: 490, protein_g: 20, carbs_g: 62, fat_g: 18 },
  { keys: ['chicken'], dish: 'Chicken', calories: 220, protein_g: 27, carbs_g: 0, fat_g: 12 },
  { keys: ['chai', 'tea', 'coffee'], dish: 'Chai', calories: 60, protein_g: 2, carbs_g: 8, fat_g: 2 },
];

const NUM_WORDS: Record<string, number> = {
  a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6,
  couple: 2, half: 0.5,
};

// Returns a MealInput if the text reads like "I ate <food>" (or just "<qty>
// <food>"), else null.
export function parseAteMessage(text: string): MealInput | null {
  const lower = text.toLowerCase();
  // Look for a food keyword anywhere in the message.
  for (const food of FOODS) {
    for (const key of food.keys) {
      const idx = lower.indexOf(key);
      if (idx === -1) continue;
      const qty = quantityBefore(lower, idx);
      const mult = Math.max(0.5, qty);
      return {
        dish: qty > 1 ? `${food.dish} ×${qty}` : food.dish,
        calories: Math.round(food.calories * mult),
        protein_g: Math.round(food.protein_g * mult),
        carbs_g: Math.round(food.carbs_g * mult),
        fat_g: Math.round(food.fat_g * mult),
        source: 'suggestion',
      };
    }
  }
  return null;
}

// Scan the few words before the food keyword for a quantity (digit or word).
function quantityBefore(lower: string, idx: number): number {
  const before = lower.slice(Math.max(0, idx - 24), idx).trim();
  const words = before.split(/\s+/);
  for (let i = words.length - 1; i >= 0; i--) {
    const w = words[i].replace(/[^a-z0-9.]/g, '');
    if (!w) continue;
    const n = parseFloat(w);
    if (Number.isFinite(n) && n > 0) return n;
    if (w in NUM_WORDS) return NUM_WORDS[w];
    // Stop scanning at the first non-number word so "ate two idli" works but we
    // don't grab a stray number far away.
    break;
  }
  return 1;
}
