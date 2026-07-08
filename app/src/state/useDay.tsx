// ONE shared day-data store (today's meals/totals + profile targets). Every
// surface that shows today's kcal — top ribbon, Honjin journey bar + macro
// paths, Chaya list, Dōjō burned view, Annai context refresh, streak/insights —
// subscribes to this single provider, so they can never disagree.
//
// Consistency guarantees (user-reported "counts inconsistent / zero on reopen"):
//  • ONE fetch, shared by all subscribers (was: each useDay() call fetched
//    independently and drifted).
//  • localStorage cache is an INSTANT placeholder only; every mount + every
//    resume (visibilitychange/focus/online) revalidates against server truth
//    within one fetch. Cache is written ONLY from a real server response, so a
//    cached zero-state can never overwrite fetched data.
//  • A latest-fetch-wins sequence guard: a slow older response can't clobber a
//    newer one (kills the "cached zero overwrites fetched data" race).
//  • Local date keys everywhere (todayStr / dayCacheKey are local, not UTC).
//
// Water (user-reported "+/− flaky"): optimistic local update + a coalescing
// mutation queue (rapid taps batch into one POST) + server-truth reconcile, so
// taps never no-op and never race.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useApp } from './AppContext';
import { todayStr } from '../lib/format';
import { readDayCache, writeDayCache } from '../api/storage';
import type { DayTotals, Macros, Meal, ProfileWithTargets } from '../api/types';

// The day totals we hand to stations always have burned present; water/nutrient
// extras stay optional (only present when the server/demo reports them).
type Totals = DayTotals & { burned: number };

export interface DayData {
  loading: boolean;
  meals: Meal[];
  totals: Totals;
  targets: Macros;
  profile: ProfileWithTargets | null;
  reload: () => void;
  // Optimistic, queued water mutation (+250 / −250 …). Never no-ops, never races.
  adjustWater: (deltaMl: number) => void;
}

const ZERO: Totals = { calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0, burned: 0 };
const DEFAULT_TARGETS: Macros = { calories: 2000, protein_g: 120, carbs_g: 220, fat_g: 60 };

interface CachedDay {
  meals: Meal[];
  totals: Totals;
  targets: Macros;
}

const DayContext = createContext<DayData | null>(null);

export function DayProvider({ children }: { children: ReactNode }) {
  const { client, user, toast } = useApp();
  const uid = user?.id ?? 'anon';

  const [loading, setLoading] = useState(true);
  const [meals, setMeals] = useState<Meal[]>([]);
  const [totals, setTotals] = useState<Totals>(ZERO);
  const [targets, setTargets] = useState<Macros>(DEFAULT_TARGETS);
  const [profile, setProfile] = useState<ProfileWithTargets | null>(null);

  // Latest-fetch-wins guard + water mutation queue.
  const seq = useRef(0);
  const waterQueue = useRef(0);
  const waterRunning = useRef(false);

  const fetchDay = useCallback(async () => {
    const date = todayStr();
    const mySeq = ++seq.current;
    try {
      const [m, p] = await Promise.all([client.getMeals(date), client.getProfile()]);
      if (mySeq !== seq.current) return; // a newer fetch superseded us — drop this one
      const t: Totals = { ...ZERO, ...m.totals, burned: m.totals.burned ?? 0 };
      const tg = p.targets ?? DEFAULT_TARGETS;
      setMeals(m.meals);
      setTotals(t);
      setProfile(p);
      setTargets(tg);
      setLoading(false);
      // Cache ONLY real server truth, so a placeholder can never be persisted
      // back over fetched data.
      writeDayCache<CachedDay>(uid, date, { meals: m.meals, totals: t, targets: tg });
    } catch {
      if (mySeq !== seq.current) return;
      // Offline / unpaired: keep whatever we have (cache or prior fetch); just
      // stop the spinner. Never wipe to zeros here.
      setLoading(false);
    }
  }, [client, uid]);

  // On identity change (account switch / boot): hydrate from cache instantly as
  // a placeholder, then always revalidate.
  useEffect(() => {
    const date = todayStr();
    const cached = readDayCache<CachedDay>(uid, date);
    if (cached) {
      setMeals(cached.meals);
      setTotals({ ...ZERO, ...cached.totals });
      setTargets(cached.targets ?? DEFAULT_TARGETS);
      setLoading(false);
    } else {
      setMeals([]);
      setTotals(ZERO);
      setTargets(DEFAULT_TARGETS);
      setProfile(null);
      setLoading(true);
    }
    void fetchDay();
  }, [uid, fetchDay]);

  const reload = useCallback(() => {
    void fetchDay();
  }, [fetchDay]);

  // Refetch server truth whenever the app comes back to the foreground so a
  // reopen never lingers on a stale cache.
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === 'visible') void fetchDay();
    };
    const onFocus = () => void fetchDay();
    document.addEventListener('visibilitychange', onVis);
    window.addEventListener('focus', onFocus);
    window.addEventListener('online', onFocus);
    return () => {
      document.removeEventListener('visibilitychange', onVis);
      window.removeEventListener('focus', onFocus);
      window.removeEventListener('online', onFocus);
    };
  }, [fetchDay]);

  // Drain the water queue: coalesce all outstanding deltas into ONE POST, apply
  // the server's absolute total, then loop for anything queued meanwhile. On
  // failure, reconcile from server truth (fetchDay) so the row is never wrong.
  const drainWater = useCallback(async () => {
    if (waterRunning.current) return;
    waterRunning.current = true;
    try {
      while (waterQueue.current !== 0) {
        const batch = waterQueue.current;
        waterQueue.current = 0;
        try {
          const res = await client.logWater(batch);
          setTotals((t) => ({ ...t, water_ml: res.water_ml, water_target_ml: res.water_target_ml }));
        } catch {
          toast('Could not save water.', 'warn');
          void fetchDay(); // reconcile optimistic state back to server truth
          return;
        }
      }
    } finally {
      waterRunning.current = false;
    }
  }, [client, toast, fetchDay]);

  const adjustWater = useCallback(
    (deltaMl: number) => {
      // Optimistic: reflect instantly so a tap never appears to no-op.
      setTotals((t) => ({ ...t, water_ml: Math.max(0, (t.water_ml ?? 0) + deltaMl) }));
      waterQueue.current += deltaMl;
      void drainWater();
    },
    [drainWater],
  );

  const value: DayData = {
    loading,
    meals,
    totals,
    targets,
    profile,
    reload,
    adjustWater,
  };

  return <DayContext.Provider value={value}>{children}</DayContext.Provider>;
}

export function useDay(): DayData {
  const v = useContext(DayContext);
  if (!v) throw new Error('useDay must be used within DayProvider');
  return v;
}
