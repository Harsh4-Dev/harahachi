// Global Dōjō workout-timer state. Lives above the whole map so:
//   • the live count-up survives navigating between stations,
//   • a floating pill can show on every screen except Dōjō (where it docks),
//   • the elapsed time + type persist to localStorage (start epoch + type), so
//     reopening the app restores a running (or just-stopped) timer,
//   • the APK shows an ongoing notification with elapsed time (web = no-op).
//
// Calories are CALCULATED (MET × weight × hours) — never entered by hand.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useDay } from './useDay';
import { caloriesBurned, type WorkoutType } from '../lib/workouts';
import { showWorkoutNotification, clearWorkoutNotification } from '../push/workoutNotify';

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

export interface WorkoutTimer {
  phase: TimerPhase;
  type: WorkoutType | null;
  elapsedSec: number; // live while running, frozen once stopped
  weightKg: number;
  calories: number; // live/frozen calories burned, computed from MET × weight × hours
  start: (type: WorkoutType) => void;
  stop: () => void; // freeze → summary
  reset: () => void; // discard/after-confirm → idle
}

const Ctx = createContext<WorkoutTimer | null>(null);

export function WorkoutTimerProvider({ children }: { children: ReactNode }) {
  const day = useDay();
  const weightKg = day.profile?.weight_kg ?? 70;

  // Lazy initializers: read persisted timer state exactly once, on mount, so a
  // reopen restores a running (or just-stopped) timer without re-reading storage
  // on every 1s tick.
  const [type, setType] = useState<WorkoutType | null>(() => readPersisted()?.type ?? null);
  const [startEpoch, setStartEpoch] = useState<number | null>(() => readPersisted()?.startEpoch ?? null);
  const [stoppedAt, setStoppedAt] = useState<number | null>(() => readPersisted()?.stoppedAt ?? null);
  const [now, setNow] = useState<number>(() => Date.now());

  const phase: TimerPhase = startEpoch == null ? 'idle' : stoppedAt != null ? 'stopped' : 'running';

  // 1s tick while running so the pill + docked read-out count up live.
  useEffect(() => {
    if (phase !== 'running') return;
    setNow(Date.now());
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [phase]);

  const elapsedSec =
    startEpoch == null ? 0 : Math.max(0, Math.floor(((stoppedAt ?? now) - startEpoch) / 1000));
  const calories = type ? caloriesBurned(type, weightKg, elapsedSec) : 0;

  // Refresh the ongoing APK notification roughly every 30s while running (and
  // once on (re)start), so its elapsed text stays current without spamming.
  const lastNotify = useRef(0);
  useEffect(() => {
    if (phase !== 'running' || type == null) return;
    if (elapsedSec - lastNotify.current >= 30 || lastNotify.current === 0) {
      lastNotify.current = elapsedSec;
      void showWorkoutNotification(type, elapsedSec);
    }
  }, [phase, type, elapsedSec]);

  const start = useCallback((t: WorkoutType) => {
    const epoch = Date.now();
    setType(t);
    setStartEpoch(epoch);
    setStoppedAt(null);
    setNow(epoch);
    lastNotify.current = 0;
    writePersisted({ type: t, startEpoch: epoch });
    void showWorkoutNotification(t, 0);
  }, []);

  const stop = useCallback(() => {
    setStartEpoch((s) => {
      if (s == null) return s;
      const at = Date.now();
      setStoppedAt(at);
      setNow(at);
      writePersisted({ type: type!, startEpoch: s, stoppedAt: at });
      return s;
    });
    void clearWorkoutNotification();
  }, [type]);

  const reset = useCallback(() => {
    setType(null);
    setStartEpoch(null);
    setStoppedAt(null);
    lastNotify.current = 0;
    writePersisted(null);
    void clearWorkoutNotification();
  }, []);

  const value: WorkoutTimer = {
    phase,
    type,
    elapsedSec,
    weightKg,
    calories,
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
