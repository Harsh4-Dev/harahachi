// Honjin 本陣 (home): journey bar, a water row + streak chip (v3), three macro
// paths, today's meals, and "From your past travels" suggestions with one-tap
// log.

import { useEffect, useState } from 'react';
import { JourneyBar } from '../components/JourneyBar';
import { MacroPath } from '../components/MacroPath';
import { WaterRow } from '../components/WaterRow';
import { StreakChip } from '../components/StreakChip';
import { NutrientChips } from '../components/NutrientChips';
import { useApp } from '../state/AppContext';
import { useDay } from '../state/useDay';
import { fmtInt, fmtTime, todayStr } from '../lib/format';
import type { StreaksResponse, Suggestion } from '../api/types';

export function Honjin() {
  const { client, logMeal, toast } = useApp();
  const day = useDay();
  const { adjustWater } = day;
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [loadingSug, setLoadingSug] = useState(true);
  const [streaks, setStreaks] = useState<StreaksResponse | null>(null);
  const [showExtras, setShowExtras] = useState(false);

  useEffect(() => {
    let cancelled = false;
    client
      .getStreaks()
      .then((r) => !cancelled && setStreaks(r))
      .catch(() => !cancelled && setStreaks(null));
    return () => {
      cancelled = true;
    };
  }, [client, day.totals.calories]);

  useEffect(() => {
    let cancelled = false;
    setLoadingSug(true);
    client
      .getSuggestions(todayStr())
      .then((r) => !cancelled && setSuggestions(r.suggestions))
      .catch(() => !cancelled && setSuggestions([]))
      .finally(() => !cancelled && setLoadingSug(false));
    return () => {
      cancelled = true;
    };
  }, [client, day.totals.calories]);

  const netCals = day.totals.calories; // burned shown separately on the bar

  const logSuggestion = async (s: Suggestion) => {
    await logMeal({
      dish: s.dish,
      calories: s.calories,
      protein_g: s.protein_g,
      carbs_g: s.carbs_g,
      fat_g: s.fat_g,
      source: 'suggestion',
    });
    toast(`Logged ${s.dish}.`, 'ok');
    day.reload();
  };

  const deleteMeal = async (id: string) => {
    try {
      await client.deleteMeal(id);
      day.reload();
    } catch {
      toast('Could not delete meal.', 'warn');
    }
  };

  return (
    <div>
      <div className="card glass">
        <div className="row between" style={{ marginBottom: 10 }}>
          <div className="card-title" style={{ margin: 0 }}>
            Today&apos;s road
          </div>
          <StreakChip current={streaks?.current ?? 0} best={streaks?.best ?? 0} />
        </div>
        <JourneyBar consumed={netCals} target={day.targets.calories} burned={day.totals.burned} />
        <WaterRow
          ml={day.totals.water_ml ?? 0}
          targetMl={day.totals.water_target_ml ?? 2000}
          onAdd={() => adjustWater(250)}
          onUndo={() => adjustWater(-250)}
        />
      </div>

      <div className="card glass">
        <div className="card-title">Macro paths</div>
        <MacroPath label="protein" kanji="蛋" color="var(--matcha)" value={day.totals.protein_g} target={day.targets.protein_g} />
        <MacroPath label="carbs" kanji="炭" color="var(--ai)" value={day.totals.carbs_g} target={day.targets.carbs_g} />
        <MacroPath label="fat" kanji="脂" color="var(--kin)" value={day.totals.fat_g} target={day.targets.fat_g} />
        {(day.totals.fiber_g != null || day.totals.sugar_g != null || day.totals.sodium_mg != null) &&
          ((day.totals.fiber_g ?? 0) + (day.totals.sugar_g ?? 0) + (day.totals.sodium_mg ?? 0) > 0) && (
            <div style={{ marginTop: 8 }}>
              <button className="chip chip-more" onClick={() => setShowExtras((v) => !v)} aria-expanded={showExtras}>
                {showExtras ? 'less' : 'more'}
              </button>
              {showExtras && (
                <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
                  <NutrientChips fiber={day.totals.fiber_g} sugar={day.totals.sugar_g} sodium={day.totals.sodium_mg} />
                </div>
              )}
            </div>
          )}
      </div>

      <div className="section-label">Today&apos;s meals</div>
      {day.loading ? (
        <div className="spinner" />
      ) : day.meals.length === 0 ? (
        <div className="empty">No meals yet — visit Chaya 茶屋 to snap your first dish.</div>
      ) : (
        day.meals.map((m) => (
          <div className="meal-row" key={m.id}>
            <div className="meal-main">
              <div className="meal-dish">{m.dish}</div>
              <div className="meal-sub">
                {fmtTime(m.eaten_at)} · P{m.protein_g} C{m.carbs_g} F{m.fat_g}
              </div>
            </div>
            <span className="meal-kcal tnum">{fmtInt(m.calories)}</span>
            <button className="icon-btn" aria-label="Delete meal" onClick={() => deleteMeal(m.id)}>
              ✕
            </button>
          </div>
        ))
      )}

      <div className="section-label">From your past travels</div>
      {loadingSug ? (
        <div className="spinner" />
      ) : suggestions.length === 0 ? (
        <div className="empty">No suggestions right now — you&apos;re on track.</div>
      ) : (
        suggestions.map((s, i) => (
          <div className="suggestion" key={i}>
            <div className="s-dish">{s.dish}</div>
            <div className="s-reason">{s.reason}</div>
            <div className="row between">
              <div className="row" style={{ gap: 6 }}>
                <span className="chip chip-protein">P {s.protein_g}g</span>
                <span className="chip chip-carbs">C {s.carbs_g}g</span>
                <span className="chip chip-fat">F {s.fat_g}g</span>
              </div>
              <button className="btn btn-matcha" onClick={() => logSuggestion(s)}>
                {fmtInt(s.calories)} · log
              </button>
            </div>
          </div>
        ))
      )}
    </div>
  );
}
