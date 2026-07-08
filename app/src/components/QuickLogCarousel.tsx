// "From your road" quick-log carousel (DESIGN.md §6.7): a horizontal scroll of
// glass cards for recent/favorite dishes (GET /meals/recent). Each card shows
// dish + kcal + a ♥ favorite toggle (POST/DELETE /meals/{id}/favorite) and a
// one-tap log. Favorites sort first (server-provided order is respected).

import { useEffect, useState } from 'react';
import { useApp } from '../state/AppContext';
import { fmtInt } from '../lib/format';
import type { MealInput, RecentMeal } from '../api/types';

interface Props {
  onLog: (meal: MealInput) => Promise<void> | void;
}

export function QuickLogCarousel({ onLog }: Props) {
  const { client, toast } = useApp();
  const [items, setItems] = useState<RecentMeal[]>([]);
  const [loading, setLoading] = useState(true);
  const [logging, setLogging] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    client
      .getRecentMeals()
      .then((r) => !cancelled && setItems(r.recent))
      .catch(() => !cancelled && setItems([]))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [client]);

  const toggleFav = async (m: RecentMeal) => {
    // Entries dedup by dish, so identity is the dish name.
    setItems((prev) => prev.map((x) => (x.dish === m.dish ? { ...x, favorite: !x.favorite } : x)));
    // The server favorite endpoint is meal-id based; the deduped recent list only
    // carries an id in demo (a dish signature). When no id is available (real
    // server), the optimistic flip is a preview until the next recent fetch.
    if (!m.id) return;
    try {
      if (m.favorite) await client.unfavoriteMeal(m.id);
      else await client.favoriteMeal(m.id);
    } catch {
      // Revert on failure.
      setItems((prev) => prev.map((x) => (x.dish === m.dish ? { ...x, favorite: m.favorite } : x)));
      toast('Could not update favorite.', 'warn');
    }
  };

  const log = async (m: RecentMeal) => {
    setLogging(m.dish);
    try {
      await onLog({
        dish: m.dish,
        calories: m.calories,
        protein_g: m.protein_g,
        carbs_g: m.carbs_g,
        fat_g: m.fat_g,
        source: 'suggestion',
        ...(m.fiber_g != null ? { fiber_g: m.fiber_g } : {}),
        ...(m.sugar_g != null ? { sugar_g: m.sugar_g } : {}),
        ...(m.sodium_mg != null ? { sodium_mg: m.sodium_mg } : {}),
      });
    } finally {
      setLogging(null);
    }
  };

  if (loading) return <div className="spinner" />;
  if (items.length === 0) {
    return <div className="empty">Log a few dishes and they&apos;ll appear here for one-tap re-logging.</div>;
  }

  return (
    <div className="quicklog-scroll" role="list" aria-label="Recent and favorite dishes">
      {items.map((m) => (
        <div className="glass quicklog-card" role="listitem" key={m.dish}>
          <button
            className={`quicklog-fav ${m.favorite ? 'on' : ''}`}
            onClick={() => void toggleFav(m)}
            aria-label={m.favorite ? `Unfavorite ${m.dish}` : `Favorite ${m.dish}`}
            aria-pressed={m.favorite}
          >
            {m.favorite ? '♥' : '♡'}
          </button>
          <div className="quicklog-dish">{m.dish}</div>
          <div className="quicklog-kcal tnum">{fmtInt(m.calories)} kcal</div>
          <div className="quicklog-macros small muted tnum">
            P{m.protein_g} C{m.carbs_g} F{m.fat_g}
          </div>
          <button
            className="btn btn-matcha quicklog-log"
            onClick={() => void log(m)}
            disabled={logging === m.dish}
          >
            {logging === m.dish ? '…' : 'log'}
          </button>
        </div>
      ))}
    </div>
  );
}
