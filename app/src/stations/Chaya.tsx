// Chaya 茶屋 (meals): the full-screen camera capture (DESIGN.md §6.5) with Dish
// and Barcode modes, plus a manual-entry form and the day's meal history with
// delete. Tapping "Open camera" takes over the whole screen; the file input
// lives inside Capture as the gallery/fallback path.

import { useState } from 'react';
import { createPortal } from 'react-dom';
import { Capture } from '../capture/Capture';
import { QuickLogCarousel } from '../components/QuickLogCarousel';
import { usePrefersReducedMotion } from '../App';
import { useApp } from '../state/AppContext';
import { useDay } from '../state/useDay';
import { fmtInt, fmtTime } from '../lib/format';
import type { MealInput } from '../api/types';

export function Chaya() {
  const { client, logMeal, toast } = useApp();
  const day = useDay();
  const reduced = usePrefersReducedMotion();

  const [capturing, setCapturing] = useState(false);
  const [manual, setManual] = useState(false);
  const [busy, setBusy] = useState(false);

  const doLog = async (meal: MealInput) => {
    setBusy(true);
    try {
      await logMeal(meal);
      toast('Meal logged — the traveler moves on.', 'ok');
      day.reload();
      setManual(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="card glass capture-cta">
        <div className="capture-icon">📷</div>
        <div className="muted small" style={{ textAlign: 'center' }}>
          Snap your dish or scan a barcode — Sekisho reads the calories.
        </div>
        <button className="btn btn-primary" style={{ width: '100%' }} onClick={() => setCapturing(true)}>
          Open camera
        </button>
        <button className="btn btn-ghost" style={{ width: '100%' }} onClick={() => setManual((m) => !m)}>
          {manual ? 'Hide manual entry' : 'Enter manually instead'}
        </button>
      </div>

      {/* "From your road" — quick-log carousel above manual entry. One tap logs
          and advances the journey (doLog reloads the day). */}
      <div className="section-label">From your road · 旅路より</div>
      <QuickLogCarousel onLog={doLog} />

      {manual && <ManualEntry onLog={doLog} onCancel={() => setManual(false)} busy={busy} />}

      <div className="section-label">Today&apos;s meals</div>
      {day.meals.length === 0 ? (
        <div className="empty">Nothing logged yet today.</div>
      ) : (
        day.meals.map((m) => (
          <div className="meal-row" key={m.id}>
            <div className="meal-main">
              <div className="meal-dish">{m.dish}</div>
              <div className="meal-sub">
                {fmtTime(m.eaten_at)} · {m.source}
              </div>
            </div>
            <span className="meal-kcal tnum">{fmtInt(m.calories)}</span>
            <button
              className="icon-btn"
              aria-label="Delete meal"
              onClick={async () => {
                try {
                  await client.deleteMeal(m.id);
                  day.reload();
                } catch {
                  toast('Could not delete.', 'warn');
                }
              }}
            >
              ✕
            </button>
          </div>
        ))
      )}

      {capturing &&
        createPortal(
          <Capture
            reducedMotion={reduced}
            onClose={() => setCapturing(false)}
            onLogged={() => {
              day.reload();
              setCapturing(false);
            }}
          />,
          document.body,
        )}
    </div>
  );
}

function ManualEntry({
  onLog,
  onCancel,
  busy,
}: {
  onLog: (m: MealInput) => void;
  onCancel: () => void;
  busy?: boolean;
}) {
  const [dish, setDish] = useState('');
  const [cal, setCal] = useState('');
  const [p, setP] = useState('');
  const [c, setC] = useState('');
  const [f, setF] = useState('');

  const submit = () => {
    if (!dish.trim() || !cal) return;
    onLog({
      dish: dish.trim(),
      calories: parseInt(cal, 10) || 0,
      protein_g: parseInt(p, 10) || 0,
      carbs_g: parseInt(c, 10) || 0,
      fat_g: parseInt(f, 10) || 0,
      source: 'manual',
    });
  };

  return (
    <div className="card glass">
      <div className="card-title">Manual entry</div>
      <div className="field">
        <label>Dish</label>
        <input className="input" value={dish} onChange={(e) => setDish(e.target.value)} placeholder="Dal + rice" />
      </div>
      <div className="field">
        <label>Calories</label>
        <input className="input" inputMode="numeric" value={cal} onChange={(e) => setCal(e.target.value)} />
      </div>
      <div className="row" style={{ gap: 10 }}>
        <div className="field" style={{ flex: 1 }}>
          <label>Protein g</label>
          <input className="input" inputMode="numeric" value={p} onChange={(e) => setP(e.target.value)} />
        </div>
        <div className="field" style={{ flex: 1 }}>
          <label>Carbs g</label>
          <input className="input" inputMode="numeric" value={c} onChange={(e) => setC(e.target.value)} />
        </div>
        <div className="field" style={{ flex: 1 }}>
          <label>Fat g</label>
          <input className="input" inputMode="numeric" value={f} onChange={(e) => setF(e.target.value)} />
        </div>
      </div>
      <div className="row between">
        <button className="btn btn-ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button className="btn btn-primary" onClick={submit} disabled={busy || !dish.trim() || !cal}>
          Log meal
        </button>
      </div>
    </div>
  );
}
