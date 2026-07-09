// Dōjō 道場: a MET-based workout timer. Pick a type (each carries a MET value),
// Start → a live count-up with calories burned CALCULATED from
// MET × profile weight × elapsed hours (no manual kcal entry; minutes derived).
// Stop → a summary card → confirm logs POST /workouts. Burned kcal visibly
// extends today's road (shown on the journey bar via the burned total).
//
// While running, the timer docks here; on any other screen a floating pill
// (see App.tsx) shows the same elapsed time and jumps back to Dōjō on tap.

import { useCallback, useEffect, useState } from 'react';
import { JourneyBar } from '../components/JourneyBar';
import { useApp } from '../state/AppContext';
import { useDay } from '../state/useDay';
import { useWorkoutTimer, useElapsed } from '../state/useWorkoutTimer';
import { WORKOUT_METS, elapsedMinutes, formatElapsed, type WorkoutType } from '../lib/workouts';
import { fmtInt } from '../lib/format';

export function Dojo() {
  const { client, showError, toast } = useApp();
  const day = useDay();
  const timer = useWorkoutTimer();
  const { elapsedSec, calories } = useElapsed();
  const [picked, setPicked] = useState<WorkoutType | null>(null);
  const [busy, setBusy] = useState(false);
  const [workouts, setWorkouts] = useState<{ kind: string; minutes: number; calories_burned: number }[]>([]);

  const load = useCallback(async () => {
    try {
      const r = await client.getWorkouts(new Date().toISOString().slice(0, 10));
      setWorkouts(r.workouts);
    } catch {
      /* ignore */
    }
  }, [client]);

  useEffect(() => {
    void load();
  }, [load]);

  const confirmLog = async () => {
    if (timer.type == null) return;
    setBusy(true);
    const kind = timer.type;
    const minutes = elapsedMinutes(elapsedSec);
    const calories_burned = calories;
    try {
      await client.logWorkout({ kind, minutes, calories_burned });
      toast(`+${fmtInt(calories_burned)} kcal — the road stretches further.`, 'ok');
      timer.reset();
      setPicked(null);
      day.reload();
      await load();
    } catch {
      showError({ message: 'Could not log your workout.', retry: () => void confirmLog() });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="card glass">
        <div className="card-title">Today&apos;s road, with burn credited</div>
        <JourneyBar consumed={day.totals.calories} target={day.targets.calories} burned={day.totals.burned} />
      </div>

      {/* Timer surface: picker (idle) → live dock (running) → summary (stopped) */}
      {timer.phase === 'idle' && (
        <div className="card glass">
          <div className="card-title">Start a workout</div>
          <div className="small muted" style={{ marginBottom: 10 }}>
            Pick your training — calories are counted live from its intensity and your weight.
          </div>
          <div className="dojo-type-grid">
            {WORKOUT_METS.map((w) => (
              <button
                key={w.type}
                className={`dojo-type ${picked === w.type ? 'active' : ''}`}
                onClick={() => setPicked(w.type)}
              >
                <span className="dojo-type-kanji">{w.kanji}</span>
                <span className="dojo-type-name">{w.type}</span>
                <span className="dojo-type-met small muted">{w.met} MET</span>
              </button>
            ))}
          </div>
          <button
            className="btn btn-matcha"
            style={{ width: '100%', marginTop: 12 }}
            disabled={picked == null}
            onClick={() => picked && timer.start(picked)}
          >
            {picked ? `Start ${picked}` : 'Pick a type to start'}
          </button>
        </div>
      )}

      {timer.phase === 'running' && (
        <div className="card glass dojo-dock">
          <div className="dojo-dock-type">
            <span className="dojo-live-dot" aria-hidden /> {timer.type} · in progress
          </div>
          <div className="dojo-dock-time tnum">{formatElapsed(elapsedSec)}</div>
          <div className="dojo-dock-kcal tnum">
            {fmtInt(calories)} <span className="small muted">kcal burned</span>
          </div>
          <button className="btn btn-danger" style={{ width: '100%', marginTop: 12 }} onClick={timer.stop}>
            Stop
          </button>
        </div>
      )}

      {timer.phase === 'stopped' && (
        <div className="card glass dojo-summary">
          <div className="card-title">Workout complete</div>
          <div className="dojo-summary-grid">
            <div>
              <div className="k">Type</div>
              <div className="v">{timer.type}</div>
            </div>
            <div>
              <div className="k">Time</div>
              <div className="v tnum">{formatElapsed(elapsedSec)}</div>
            </div>
            <div>
              <div className="k">Minutes</div>
              <div className="v tnum">{elapsedMinutes(elapsedSec)}</div>
            </div>
            <div>
              <div className="k">Burned</div>
              <div className="v tnum">{fmtInt(calories)} kcal</div>
            </div>
          </div>
          <div className="row between" style={{ marginTop: 14 }}>
            <button className="btn btn-ghost" onClick={() => { timer.reset(); setPicked(null); }} disabled={busy}>
              Discard
            </button>
            <button className="btn btn-primary" onClick={() => void confirmLog()} disabled={busy}>
              {busy ? 'Logging…' : 'Confirm & log'}
            </button>
          </div>
        </div>
      )}

      {workouts.length > 0 && (
        <>
          <div className="section-label">Today&apos;s workouts</div>
          {workouts.map((w, i) => (
            <div className="meal-row" key={i}>
              <div className="meal-main">
                <div className="meal-dish">{w.kind}</div>
                <div className="meal-sub">{w.minutes} min</div>
              </div>
              <span className="meal-kcal tnum" style={{ color: 'var(--matcha)' }}>
                −{fmtInt(w.calories_burned)}
              </span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
