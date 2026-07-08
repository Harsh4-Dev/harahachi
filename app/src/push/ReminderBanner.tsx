// In-app reminder banner (API.md v2 / DESIGN.md 6.6). When push is unavailable
// (APK webview, permission denied) the app shows the same reminders as a glass
// banner sliding from the top ribbon while the app is open:
//   • Meal slots — 18:00 (what's left) and 22:00 (day recap), with a one-tap
//     "log it" for a suggested regular meal.
//   • Water slots — 11:00 / 15:00 / 19:00 (mirroring Sekisho's water reminder
//     slots), each with a one-tap "+250 ml", shown only when still below target.
//
// Each slot fires at most once per slot per calendar day (tracked in
// localStorage) and fires whenever now >= the slot time (not on the exact
// minute), so a reopen mid-afternoon still catches an earlier slot. Dismiss,
// log, or drink all close it.

import { useCallback, useEffect, useState } from 'react';
import { useApp } from '../state/AppContext';
import { isPushEnabled } from '../api/storage';
import { todayStr } from '../lib/format';
import type { MealInput, Suggestion } from '../api/types';

// Meal slots keep their bare-hour ids for back-compat with any stored state;
// water slots are prefixed. The union is what we persist as "fired".
type Slot = '1800' | '2200' | 'w1100' | 'w1500' | 'w1900';

interface WaterSlotDef {
  slot: Slot;
  hour: number;
}
// Sekisho's water reminder slots (late morning, mid-afternoon, evening).
const WATER_SLOTS: WaterSlotDef[] = [
  { slot: 'w1100', hour: 11 },
  { slot: 'w1500', hour: 15 },
  { slot: 'w1900', hour: 19 },
];

interface Reminder {
  slot: Slot;
  kind: 'meal' | 'water';
  text: string;
  suggestion?: Suggestion;
}

const FIRED_KEY = 'hh.reminderFired'; // JSON { date, slots: string[] }

function firedSlots(): { date: string; slots: Slot[] } {
  try {
    const raw = localStorage.getItem(FIRED_KEY);
    const v = raw ? (JSON.parse(raw) as { date: string; slots: Slot[] }) : null;
    if (v && v.date === todayStr()) return v;
  } catch {
    /* reset below */
  }
  return { date: todayStr(), slots: [] };
}

function markFired(slot: Slot): void {
  const cur = firedSlots();
  if (!cur.slots.includes(slot)) cur.slots.push(slot);
  localStorage.setItem(FIRED_KEY, JSON.stringify(cur));
}

export function ReminderBanner({ onLogged, onDrink }: { onLogged: () => void; onDrink: (ml: number) => void }) {
  const { client, logMeal, toast } = useApp();
  const [reminder, setReminder] = useState<Reminder | null>(null);

  const buildMealReminder = useCallback(
    async (slot: Slot) => {
      try {
        const [{ totals }, sug, profile] = await Promise.all([
          client.getMeals(todayStr()),
          client.getSuggestions(todayStr()),
          client.getProfile(),
        ]);
        const remaining = Math.max(0, profile.targets.calories - totals.calories);
        const proteinLeft = Math.max(0, profile.targets.protein_g - totals.protein_g);

        if (slot === '1800') {
          if (remaining <= 60) return; // goal effectively met → skip
          const pick = sug.suggestions[0];
          setReminder({
            slot,
            kind: 'meal',
            text: `${remaining} kcal and ${proteinLeft}g protein to go${pick ? ` — ${pick.dish} would land you on target` : ''}.`,
            suggestion: pick,
          });
        } else {
          if (totals.calories === 0) {
            setReminder({ slot, kind: 'meal', text: "You haven't logged anything today — don't forget to log your meals." });
          } else {
            const over = totals.calories - profile.targets.calories;
            const line =
              over > 120
                ? `A bit over today (${totals.calories}/${profile.targets.calories}). Tomorrow, hara-hachi-bu — eat to eighty percent full.`
                : over < -300
                  ? `Under target today (${totals.calories}/${profile.targets.calories}). A gentle day on the road.`
                  : `Right on target today (${totals.calories}/${profile.targets.calories}). Well walked.`;
            setReminder({ slot, kind: 'meal', text: line });
          }
        }
        markFired(slot);
      } catch {
        /* offline → skip the reminder rather than showing a broken one */
      }
    },
    [client],
  );

  const buildWaterReminder = useCallback(
    async (slot: Slot) => {
      try {
        const { totals } = await client.getMeals(todayStr());
        const target = totals.water_target_ml ?? 2000;
        const have = totals.water_ml ?? 0;
        // Collapse any backlog: mark EVERY already-past water slot fired (not just
        // this one), so opening the app late shows a single water nudge instead of
        // firing 11:00/15:00/19:00 one after another.
        const nowHour = new Date().getHours();
        WATER_SLOTS.forEach((w) => {
          if (nowHour >= w.hour) markFired(w.slot);
        });
        if (have >= target) return; // already hydrated → no nudge
        const left = Math.max(0, target - have);
        setReminder({
          slot,
          kind: 'water',
          text: `Water check — ${have} / ${target} ml so far. ${left} ml to go. Have a cup?`,
        });
      } catch {
        /* offline → skip */
      }
    },
    [client],
  );

  useEffect(() => {
    // Only run the in-app fallback when push is NOT the delivery path (APK
    // webview / push denied → banners are the mechanism).
    if (isPushEnabled()) return;
    const check = () => {
      if (reminder) return;
      const now = new Date();
      const hh = now.getHours();
      const fired = firedSlots().slots;
      // Meal slots first (fire when now >= the slot hour).
      if (hh >= 18 && hh < 22 && !fired.includes('1800')) {
        void buildMealReminder('1800');
        return;
      }
      if (hh >= 22 && !fired.includes('2200')) {
        void buildMealReminder('2200');
        return;
      }
      // Water slots: the latest un-fired slot whose hour has passed.
      const due = [...WATER_SLOTS].reverse().find((w) => hh >= w.hour && !fired.includes(w.slot));
      if (due) void buildWaterReminder(due.slot);
    };
    check();
    const t = window.setInterval(check, 60_000);
    return () => window.clearInterval(t);
  }, [reminder, buildMealReminder, buildWaterReminder]);

  if (!reminder) return null;

  const logIt = async () => {
    const s = reminder.suggestion;
    if (!s) {
      setReminder(null);
      return;
    }
    const meal: MealInput = {
      dish: s.dish,
      calories: s.calories,
      protein_g: s.protein_g,
      carbs_g: s.carbs_g,
      fat_g: s.fat_g,
      source: 'suggestion',
    };
    try {
      await logMeal(meal);
      toast(`Logged ${s.dish}.`, 'ok');
      onLogged();
    } finally {
      setReminder(null);
    }
  };

  const drink = () => {
    onDrink(250);
    toast('+250 ml — 水. Keep it up.', 'ok');
    setReminder(null);
  };

  return (
    <div className="reminder-banner glass-ink" role="status">
      <span className="reminder-hanko">{reminder.kind === 'water' ? '水' : '腹'}</span>
      <span className="reminder-text small">{reminder.text}</span>
      {reminder.kind === 'water' ? (
        <button className="reminder-log" onClick={drink}>
          +250 ml
        </button>
      ) : (
        reminder.suggestion && (
          <button className="reminder-log" onClick={() => void logIt()}>
            log it
          </button>
        )
      )}
      <button className="reminder-x" aria-label="Dismiss reminder" onClick={() => setReminder(null)}>
        ✕
      </button>
    </div>
  );
}
