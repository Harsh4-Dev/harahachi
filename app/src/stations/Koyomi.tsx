// Koyomi 暦: the weekly insights scroll card at the top, then three glass-pill
// tabs — Calendar heat-map (GET /calendar), Weight (stepper entry + sumi-line
// trend chart), and the Stamp book 御朱印帳 (goshuin grid from GET /streaks).

import { useEffect, useState } from 'react';
import { useApp } from '../state/AppContext';
import { fmtInt, monthStr, todayStr, fmtTime } from '../lib/format';
import { InsightsCard } from '../components/InsightsCard';
import { WeightTab } from '../components/WeightTab';
import { StampBook } from '../components/StampBook';
import type { CalendarDay, InsightsResponse, Meal, StreaksResponse } from '../api/types';

const DOW = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];

type Tab = 'calendar' | 'weight' | 'stamps';

function statusClass(s: CalendarDay['status']): string {
  return s === 'on' ? 'cal-on' : s === 'under' ? 'cal-under' : s === 'over' ? 'cal-over' : 'cal-empty';
}

export function Koyomi() {
  const { client } = useApp();
  const [tab, setTab] = useState<Tab>('calendar');
  const [month, setMonth] = useState(monthStr());
  const [days, setDays] = useState<CalendarDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [dayMeals, setDayMeals] = useState<Meal[]>([]);

  // Insights (top scroll card) + streaks (stamp book) load once.
  const [insights, setInsights] = useState<InsightsResponse | null>(null);
  const [insightsLoading, setInsightsLoading] = useState(true);
  const [streaks, setStreaks] = useState<StreaksResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    setInsightsLoading(true);
    client
      .getInsights(todayStr())
      .then((r) => !cancelled && setInsights(r))
      .catch(() => !cancelled && setInsights(null))
      .finally(() => !cancelled && setInsightsLoading(false));
    client
      .getStreaks()
      .then((r) => !cancelled && setStreaks(r))
      .catch(() => !cancelled && setStreaks(null));
    return () => {
      cancelled = true;
    };
  }, [client]);

  useEffect(() => {
    if (tab !== 'calendar') return;
    let cancelled = false;
    setLoading(true);
    client
      .getCalendar(month)
      .then((r) => !cancelled && setDays(r.days))
      .catch(() => !cancelled && setDays([]))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [client, month, tab]);

  const openDay = async (date: string) => {
    setSelected(date);
    try {
      const r = await client.getMeals(date);
      setDayMeals(r.meals);
    } catch {
      setDayMeals([]);
    }
  };

  const shift = (delta: number) => {
    const [y, m] = month.split('-').map(Number);
    const d = new Date(y, m - 1 + delta, 1);
    setMonth(monthStr(d));
    setSelected(null);
  };

  // Pad leading blanks to the correct weekday.
  const firstDow = days.length ? new Date(days[0].date + 'T00:00:00').getDay() : 0;
  const today = todayStr();

  return (
    <div>
      {/* Weekly insights scroll card at the very top. */}
      <InsightsCard insights={insights} loading={insightsLoading} />

      {/* Tab pills */}
      <div className="pill-group koyomi-tabs">
        <button className={`pill ${tab === 'calendar' ? 'active' : ''}`} onClick={() => setTab('calendar')}>
          暦 Calendar
        </button>
        <button className={`pill ${tab === 'weight' ? 'active' : ''}`} onClick={() => setTab('weight')}>
          量 Weight
        </button>
        <button className={`pill ${tab === 'stamps' ? 'active' : ''}`} onClick={() => setTab('stamps')}>
          印 Stamps
        </button>
      </div>

      {tab === 'weight' && <WeightTab />}

      {tab === 'stamps' && (
        <div className="card glass">
          <div className="card-title">御朱印帳 · Stamp book</div>
          <StampBook stamps={streaks?.stamps ?? []} current={streaks?.current ?? 0} best={streaks?.best ?? 0} />
        </div>
      )}

      {tab === 'calendar' && (
      <>
      <div className="card glass">
        <div className="row between" style={{ marginBottom: 10 }}>
          <button className="icon-btn" onClick={() => shift(-1)} aria-label="Previous month">
            ‹
          </button>
          <div className="display" style={{ fontSize: 18 }}>
            {month}
          </div>
          <button className="icon-btn" onClick={() => shift(1)} aria-label="Next month">
            ›
          </button>
        </div>

        {loading ? (
          <div className="spinner" />
        ) : (
          <>
            <div className="cal-grid" style={{ marginBottom: 6 }}>
              {DOW.map((d, i) => (
                <div className="cal-dow" key={i}>
                  {d}
                </div>
              ))}
            </div>
            <div className="cal-grid">
              {Array.from({ length: firstDow }).map((_, i) => (
                <div key={`pad-${i}`} />
              ))}
              {days.map((d) => {
                const day = parseInt(d.date.slice(-2), 10);
                return (
                  <button
                    key={d.date}
                    className={`cal-cell ${statusClass(d.status)} ${d.date === today ? 'today' : ''}`}
                    onClick={() => openDay(d.date)}
                    title={d.status === 'empty' ? 'No data' : `${fmtInt(d.calories)} / ${fmtInt(d.target)} kcal`}
                  >
                    {day}
                  </button>
                );
              })}
            </div>
            <div className="cal-legend">
              <span>
                <span className="swatch cal-under" />under
              </span>
              <span>
                <span className="swatch cal-on" />on target
              </span>
              <span>
                <span className="swatch cal-over" />over
              </span>
              <span>
                <span className="swatch cal-empty" />no data
              </span>
            </div>
          </>
        )}
      </div>

      {selected && (
        <div className="card glass">
          <div className="card-title">{selected}</div>
          {dayMeals.length === 0 ? (
            <div className="empty">No meals logged this day.</div>
          ) : (
            dayMeals.map((m) => (
              <div className="meal-row" key={m.id}>
                <div className="meal-main">
                  <div className="meal-dish">{m.dish}</div>
                  <div className="meal-sub">{fmtTime(m.eaten_at)}</div>
                </div>
                <span className="meal-kcal tnum">{fmtInt(m.calories)}</span>
              </div>
            ))
          )}
        </div>
      )}
      </>
      )}
    </div>
  );
}
