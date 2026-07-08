// Koyomi's Weight tab (DESIGN.md §6.7): a big stepper entry that POSTs today's
// weight, and the sumi-line trend chart below (WeightChart). Loads ~3 weeks of
// history via GET /weight and the profile's weight_goal_kg for the goal marker.

import { useEffect, useState } from 'react';
import { useApp } from '../state/AppContext';
import { WeightChart } from './WeightChart';
import { todayStr } from '../lib/format';
import type { WeightEntry, WeightTrendPoint } from '../api/types';

function dateDaysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

export function WeightTab() {
  const { client, toast } = useApp();
  const [entries, setEntries] = useState<WeightEntry[]>([]);
  const [trend, setTrend] = useState<WeightTrendPoint[]>([]);
  const [goalKg, setGoalKg] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [weight, setWeight] = useState<number>(70);
  const [saving, setSaving] = useState(false);

  const reload = () => {
    setLoading(true);
    Promise.all([client.getWeight(dateDaysAgo(30), todayStr()), client.getProfile()])
      .then(([w, p]) => {
        setEntries(w.entries);
        setTrend(w.trend);
        setGoalKg(p.weight_goal_kg ?? null);
        // Seed the stepper from the most recent weigh-in (or profile weight).
        const last = w.entries.length ? w.entries[w.entries.length - 1].weight_kg : p.weight_kg;
        setWeight(Math.round(last * 10) / 10);
      })
      .catch(() => {
        setEntries([]);
        setTrend([]);
      })
      .finally(() => setLoading(false));
  };

  useEffect(reload, [client]);

  const save = async () => {
    setSaving(true);
    try {
      // POST /weight returns just the stored entry; re-fetch the window to
      // refresh the trend line + goal marker.
      await client.logWeight(weight, todayStr());
      const w = await client.getWeight(dateDaysAgo(30), todayStr());
      setEntries(w.entries);
      setTrend(w.trend);
      toast(`Logged ${weight} kg for today.`, 'ok');
    } catch {
      toast('Could not save your weight.', 'warn');
    } finally {
      setSaving(false);
    }
  };

  const step = (delta: number) => setWeight((w) => Math.min(300, Math.max(20, Math.round((w + delta) * 10) / 10)));

  return (
    <div className="card glass">
      <div className="card-title">Today&apos;s weigh-in</div>
      <div className="weight-stepper">
        <button className="stepper-btn" onClick={() => step(-0.1)} aria-label="Decrease weight">
          −
        </button>
        <div className="weight-val">
          <span className="display tnum">{weight.toFixed(1)}</span>
          <span className="small muted">kg</span>
        </div>
        <button className="stepper-btn" onClick={() => step(0.1)} aria-label="Increase weight">
          +
        </button>
      </div>
      <div className="row" style={{ gap: 8, justifyContent: 'center', marginTop: 6 }}>
        <button className="pill" style={{ flex: '0 0 auto', padding: '4px 12px' }} onClick={() => step(-0.5)}>
          −0.5
        </button>
        <button className="pill" style={{ flex: '0 0 auto', padding: '4px 12px' }} onClick={() => step(0.5)}>
          +0.5
        </button>
      </div>
      <button className="btn btn-primary" style={{ width: '100%', marginTop: 12 }} onClick={() => void save()} disabled={saving}>
        {saving ? 'Saving…' : 'Log weight'}
      </button>

      <div className="section-label" style={{ marginTop: 16 }}>
        Trend · 推移
      </div>
      {loading ? <div className="spinner" /> : <WeightChart entries={entries} trend={trend} goalKg={goalKg} />}
      {!loading && entries.length > 0 && (
        <div className="small muted" style={{ marginTop: 6, textAlign: 'center' }}>
          <span style={{ color: 'var(--sakura-deep)' }}>●</span> daily &nbsp;
          <span style={{ color: 'var(--matcha)' }}>┅</span> 7-day trend
          {goalKg != null && (
            <>
              {' '}
              &nbsp;<span style={{ color: 'var(--shu)' }}>┄</span> goal {goalKg}kg
            </>
          )}
        </div>
      )}
    </div>
  );
}
