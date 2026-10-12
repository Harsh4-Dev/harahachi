// The shell: full-viewport sakura map as navigation, a thin glass top ribbon,
// station panels that slide up over the lower ~70%, and a toast stack. Routes
// between onboarding, pairing, and the ready (map) experience by phase.

import { useEffect, useMemo, useState } from 'react';
import { SakuraMap } from './map/SakuraMap';
import { STATIONS, STATION_T, stationById, type Station, type StationId } from './map/stations';
import { useApp } from './state/AppContext';
import { DayProvider, useDay } from './state/useDay';
import { WorkoutTimerProvider, useWorkoutTimer, useElapsed } from './state/useWorkoutTimer';
import { useSwipeDown } from './lib/useSwipeDown';
import { fmtInt } from './lib/format';
import { formatElapsed } from './lib/workouts';
import { Honjin } from './stations/Honjin';
import { Chaya } from './stations/Chaya';
import { Koyomi } from './stations/Koyomi';
import { Dojo } from './stations/Dojo';
import { Ichiba } from './stations/Ichiba';
import { Tegata } from './stations/Tegata';
import { Onboarding } from './onboarding/Onboarding';
import { Pairing } from './onboarding/Pairing';
import { Account } from './onboarding/Account';
import { RePair } from './onboarding/RePair';
import { AnnaiButton } from './chat/Annai';
import { ReminderBanner } from './push/ReminderBanner';

export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  );
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const on = () => setReduced(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return reduced;
}

function StationBody({ id }: { id: StationId }) {
  switch (id) {
    case 'honjin':
      return <Honjin />;
    case 'chaya':
      return <Chaya />;
    case 'koyomi':
      return <Koyomi />;
    case 'dojo':
      return <Dojo />;
    case 'ichiba':
      return <Ichiba />;
    case 'tegata':
      return <Tegata />;
  }
}

function Toasts() {
  const { toasts, dismissToast } = useApp();
  return (
    <div className="toasts">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`glass-ink toast toast-${t.kind}`}
          onClick={() => dismissToast(t.id)}
          role="status"
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}

// Shared error surface (user requirement): one glass popup with a clear message,
// a RETRY, and an optional secondary path ("enter manually", "re-scan"…). Any
// call that fails routes here instead of leaving a stuck screen/spinner.
function ErrorPopup() {
  const { error, clearError } = useApp();
  if (!error) return null;
  return (
    <div className="error-scrim" role="alertdialog" aria-label="Error" onClick={clearError}>
      <div className="glass error-popup" onClick={(e) => e.stopPropagation()}>
        <div className="error-seal">!</div>
        <div className="error-message">{error.message}</div>
        <div className="row between" style={{ marginTop: 14 }}>
          {error.secondaryLabel ? (
            <button
              className="btn btn-ghost"
              onClick={() => {
                error.onSecondary?.();
                clearError();
              }}
            >
              {error.secondaryLabel}
            </button>
          ) : (
            <button className="btn btn-ghost" onClick={clearError}>
              Dismiss
            </button>
          )}
          {error.retry && (
            <button
              className="btn btn-primary"
              onClick={() => {
                const r = error.retry!;
                clearError();
                r();
              }}
            >
              Retry
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// The station bottom-sheet. Its own component so the swipe-to-close hook binds
// on mount (and rebinds per station via the parent key). Swipe the grip/header
// down — or the body when scrolled to the top — past ~80px to close; the ✕ in
// the ribbon still works too.
function StationPanel({
  st,
  onClose,
  onNavigate,
}: {
  st: Station;
  onClose: () => void;
  onNavigate: (dir: 1 | -1) => void;
}) {
  const sheetRef = useSwipeDown<HTMLElement>(onClose, onNavigate);
  return (
    <section ref={sheetRef} className="panel glass" aria-label={`${st.name} station`}>
      <div className="panel-grip" data-swipe-handle />
      <div className="panel-head" data-swipe-handle>
        <h2 className="display">
          {st.name} <span className="muted small">{st.english}</span>
        </h2>
        <span className="kanji-big">{st.kanji}</span>
      </div>
      <div className="panel-body scroll" data-swipe-scroll>
        <StationBody id={st.id} />
      </div>
    </section>
  );
}

function MapShell() {
  return (
    <DayProvider>
      <WorkoutTimerProvider>
        <MapShellInner />
      </WorkoutTimerProvider>
    </DayProvider>
  );
}

// Floating glass timer pill: shown on every screen EXCEPT Dōjō while a workout
// timer is active; tap to jump to Dōjō (where the timer docks instead).
function TimerPill({ active, onJump }: { active: StationId | null; onJump: () => void }) {
  const timer = useWorkoutTimer();
  const { elapsedSec } = useElapsed();
  if (timer.phase === 'idle' || active === 'dojo' || timer.type == null) return null;
  return (
    <button className="timer-pill glass-ink" onClick={onJump} aria-label={`${timer.type} workout, ${formatElapsed(elapsedSec)} elapsed — open Dōjō`}>
      <span className="timer-pill-dot" aria-hidden />
      <span className="timer-pill-type">{timer.type}</span>
      <span className="timer-pill-time tnum">{formatElapsed(elapsedSec)}</span>
    </button>
  );
}

function MapShellInner() {
  const reduced = usePrefersReducedMotion();
  const day = useDay();
  const [active, setActive] = useState<StationId | null>(null);

  // Traveler position: today's calorie progress mapped between Honjin and the
  // road ahead. On the full map it also reflects progress.
  const travelerT = useMemo(() => {
    const frac = day.targets.calories > 0 ? day.totals.calories / day.targets.calories : 0;
    const clamped = Math.min(1.15, Math.max(0, frac));
    // Home sits at STATION_T.honjin; progress walks from there toward tegata.
    const start = STATION_T.honjin;
    return start + clamped * (0.98 - start);
  }, [day.totals.calories, day.targets.calories]);

  const frac = `${fmtInt(day.totals.calories)} / ${fmtInt(day.targets.calories)}`;
  const st = active ? stationById(active) : null;

  // Horizontal swipe on an open panel's header steps to the adjacent station in
  // road order (no wrap). The map pan animates to the new region and the panel
  // content crossfades via its per-station key remount.
  const navAdjacent = (dir: 1 | -1) => {
    setActive((cur) => {
      if (!cur) return cur;
      const i = STATIONS.findIndex((s) => s.id === cur);
      const next = i + dir;
      if (next < 0 || next >= STATIONS.length) return cur; // no wrap
      return STATIONS[next].id;
    });
  };

  return (
    <div className="shell">
      <SakuraMap active={active} travelerT={travelerT} onSelect={setActive} reducedMotion={reduced} />

      {/* Top glass ribbon */}
      <div className="ribbon glass">
        <div className="station-name">
          <span className="kanji">{st ? st.kanji : '腹八分'}</span>
          <span className="romaji">{st ? `${st.name} · ${st.english}` : 'Harahachi'}</span>
        </div>
        <div className="kcal tnum">
          <span className="frac">{frac}</span> kcal
        </div>
        {st ? (
          <button className="back" aria-label="Back to map" onClick={() => setActive(null)}>
            ✕
          </button>
        ) : (
          <span style={{ width: 42 }} />
        )}
      </div>

      {!active && <div className="map-hint glass">Tap a seal to travel there</div>}

      {st && <StationPanel key={st.id} st={st} onClose={() => setActive(null)} onNavigate={navAdjacent} />}

      {/* Annai chat — floating above the map and inside panels; a logged action
          refreshes today's data so the journey bar advances live. */}
      <AnnaiButton onDataChanged={day.reload} />

      {/* Floating workout timer pill — everywhere except Dōjō; taps jump there. */}
      <TimerPill active={active} onJump={() => setActive('dojo')} />

      {/* In-app reminder fallback (meal 18:00/22:00 + water 11/15/19 when push
          is unavailable — the mechanism in the APK webview). */}
      <ReminderBanner onLogged={day.reload} onDrink={(ml) => day.adjustWater(ml)} />

      <Toasts />
    </div>
  );
}

export function App() {
  const { phase } = useApp();
  if (phase === 'onboarding') return <><Onboarding /><Toasts /><ErrorPopup /></>;
  if (phase === 'pairing') return <><Pairing /><Toasts /><ErrorPopup /></>;
  if (phase === 'login') return <><Account /><Toasts /><ErrorPopup /></>;
  if (phase === 'repair') return <><RePair /><Toasts /><ErrorPopup /></>;
  return <><MapShell /><ErrorPopup /></>;
}
