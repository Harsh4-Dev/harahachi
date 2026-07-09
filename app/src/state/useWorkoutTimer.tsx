// Global Dōjō workout-timer state. Lives above the whole map so:
//   • the live count-up survives navigating between stations,
//   • a floating pill can show on every screen except Dōjō (where it docks),
//   • the elapsed time + type persist to localStorage (start epoch + type), so
//     reopening the app restores a running (or just-stopped) timer,
//   • the APK shows an ongoing chronometer notification (Android ticks it).
//
// Calories are CALCULATED (MET × weight × hours) — never entered by hand.
//
// PERF (real-device pass): the per-second tick used to live in this PROVIDER,
// so every 1s it re-rendered the whole map subtree (MapShellInner is a child of
// the provider). The tick now lives in `useElapsed()`, a leaf hook the pill and
// the Dōjō dock call — only those small nodes re-render each second, never the
// map. The provider re-renders only on start/stop/reset (rare).

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';
import { useDay } from './useDay';
import { caloriesBurned, type WorkoutType } from '../lib/workouts';
import { startWorkoutNotification, stopWorkoutNotification } from '../push/workoutNotify';
import { WorkoutTimer as WorkoutTimerNative } from '../lib/workoutTimerPlugin';
import { isNativeShell } from '../api/storage';

export type TimerPhase = 'idle' | 'running' | 'stopped';

interface Persisted {
  type: WorkoutType;
  startEpoch: number; // ms
  stoppedAt?: number; // ms — present once stopped (summary awaiting confirm)
}

const STORE_KEY = 'hh.workoutTimer';

function readPersisted(): Persisted | null {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    return raw ? (JSON.parse(raw) as Persisted) : null;
  } catch {
    return null;
  }
}
function writePersisted(p: Persisted | null): void {
  try {
    if (p) localStorage.setItem(STORE_KEY, JSON.stringify(p));
    else localStorage.removeItem(STORE_KEY);
  } catch {
    /* non-fatal */
  }
}

// Static timer facts. Live elapsed/calories are derived via useElapsed() so a
// running timer never re-renders this provider (and thus the map) each second.
export interface WorkoutTimer {
  phase: TimerPhase;
  type: WorkoutType | null;
  startEpoch: number | null;
  stoppedAt: number | null;
  weightKg: number;
  start: (type: WorkoutType) => void;
  stop: () => void; // freeze → summary
  reset: () => void; // discard/after-confirm → idle
}

const Ctx = createContext<WorkoutTimer | null>(null);

export function WorkoutTimerProvider({ children }: { children: ReactNode }) {
  const day = useDay();
  const weightKg = day.profile?.weight_kg ?? 70;

  // Lazy initializers: read persisted timer state exactly once, on mount, so a
  // reopen restores a running (or just-stopped) timer.
  const [type, setType] = useState<WorkoutType | null>(() => readPersisted()?.type ?? null);
  const [startEpoch, setStartEpoch] = useState<number | null>(() => readPersisted()?.startEpoch ?? null);
  const [stoppedAt, setStoppedAt] = useState<number | null>(() => readPersisted()?.stoppedAt ?? null);

  const phase: TimerPhase = startEpoch == null ? 'idle' : stoppedAt != null ? 'stopped' : 'running';

  // Re-assert the ongoing chronometer notification on mount if a run was already
  // in progress when the app was reopened (Android keeps ticking `when`).
  useEffect(() => {
    if (phase === 'running' && type != null && startEpoch != null) {
      void startWorkoutNotification(type, startEpoch);
    }
    // Only on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const start = useCallback((t: WorkoutType) => {
    const epoch = Date.now();
    setType(t);
    setStartEpoch(epoch);
    setStoppedAt(null);
    writePersisted({ type: t, startEpoch: epoch });
    void startWorkoutNotification(t, epoch);
  }, []);

  const stop = useCallback(() => {
    setStartEpoch((s) => {
      if (s == null) return s;
      const at = Date.now();
      setStoppedAt(at);
      writePersisted({ type: type!, startEpoch: s, stoppedAt: at });
      return s;
    });
    void stopWorkoutNotification();
  }, [type]);

  // Notification Pause/Stop → freeze the timer to its summary. Deliver both the
  // live event (app foregrounded) and any action persisted while the app was
  // dead (consumed once on mount). The 2-phase Dōjō model has no distinct pause,
  // so Pause and Stop both freeze → summary.
  useEffect(() => {
    if (!isNativeShell()) return;
    let handle: { remove: () => void } | null = null;
    void WorkoutTimerNative.consumePendingAction()
      .then((r) => {
        if (r?.action === 'stop' || r?.action === 'pause') stop();
      })
      .catch(() => {});
    void WorkoutTimerNative.addListener('action', (data) => {
      if (data.action === 'stop' || data.action === 'pause') stop();
    })
      .then((h) => {
        handle = h;
      })
      .catch(() => {});
    return () => handle?.remove();
  }, [stop]);

  const reset = useCallback(() => {
    setType(null);
    setStartEpoch(null);
    setStoppedAt(null);
    writePersisted(null);
    void stopWorkoutNotification();
  }, []);

  const value: WorkoutTimer = {
    phase,
    type,
    startEpoch,
    stoppedAt,
    weightKg,
    start,
    stop,
    reset,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWorkoutTimer(): WorkoutTimer {
  const v = useContext(Ctx);
  if (!v) throw new Error('useWorkoutTimer must be used within WorkoutTimerProvider');
  return v;
}

// Live elapsed seconds + calories. Ticks locally (1s) ONLY while running, so the
// component calling this is the only thing that re-renders each second — the map
// tree above stays still. Frozen at the stop time once stopped.
export function useElapsed(): { elapsedSec: number; calories: number } {
  const { phase, type, startEpoch, stoppedAt, weightKg } = useWorkoutTimer();
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    if (phase !== 'running') return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [phase]);

  const elapsedSec =
    startEpoch == null ? 0 : Math.max(0, Math.floor(((stoppedAt ?? now) - startEpoch) / 1000));
  const calories = type ? caloriesBurned(type, weightKg, elapsedSec) : 0;
  return { elapsedSec, calories };
}
