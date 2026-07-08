// Weekly insights scroll card (DESIGN.md §6.7): a scroll-styled card at the top
// of Koyomi — averages (kcal/macros/water), adherence %, best/worst day seals,
// weight delta, macro split %, and Annai's short summary. Data from GET
// /insights?week=.

import { fmtInt } from '../lib/format';
import type { InsightsResponse } from '../api/types';

interface Props {
  insights: InsightsResponse | null;
  loading: boolean;
}

function dayShort(date?: string): string {
  if (!date) return '—';
  try {
    return new Date(date + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'short' });
  } catch {
    return date.slice(-2);
  }
}

export function InsightsCard({ insights, loading }: Props) {
  if (loading) {
    return (
      <div className="card glass insights-scroll">
        <div className="spinner" />
      </div>
    );
  }
  if (!insights) {
    return (
      <div className="card glass insights-scroll">
        <div className="card-title">This week</div>
        <div className="small muted">No insights yet — log a few days to see your week take shape.</div>
      </div>
    );
  }

  const a = insights.averages;
  const split = insights.macro_split_pct;
  const delta = insights.weight_delta_kg;

  return (
    <div className="card glass insights-scroll">
      <div className="row between" style={{ marginBottom: 8 }}>
        <div className="card-title" style={{ margin: 0 }}>
          This week · 今週
        </div>
        <div className="insights-adherence">
          <span className="ia-num tnum">{insights.adherence_pct}%</span>
          <span className="small muted">on target</span>
        </div>
      </div>

      <div className="insights-averages">
        <div>
          <div className="ia-k">avg kcal</div>
          <div className="ia-v tnum">{fmtInt(a.calories)}</div>
        </div>
        <div>
          <div className="ia-k">protein</div>
          <div className="ia-v tnum">{a.protein_g}g</div>
        </div>
        <div>
          <div className="ia-k">water</div>
          <div className="ia-v tnum">{a.water_ml != null ? `${(a.water_ml / 1000).toFixed(1)}L` : '—'}</div>
        </div>
        <div>
          <div className="ia-k">weight Δ</div>
          <div className="ia-v tnum" style={{ color: delta != null && delta < 0 ? 'var(--matcha)' : delta != null && delta > 0 ? 'var(--shu)' : undefined }}>
            {delta == null ? '—' : `${delta > 0 ? '+' : ''}${delta}kg`}
          </div>
        </div>
      </div>

      {/* Macro split bar */}
      <div className="insights-split" aria-label="Macro split">
        <span className="seg seg-p" style={{ width: `${split.protein}%` }} title={`Protein ${split.protein}%`} />
        <span className="seg seg-c" style={{ width: `${split.carbs}%` }} title={`Carbs ${split.carbs}%`} />
        <span className="seg seg-f" style={{ width: `${split.fat}%` }} title={`Fat ${split.fat}%`} />
      </div>
      <div className="insights-split-legend small muted">
        <span>P {split.protein}%</span>
        <span>C {split.carbs}%</span>
        <span>F {split.fat}%</span>
      </div>

      {/* Best / worst day seals */}
      <div className="insights-days">
        <div className="iday iday-best">
          <span className="iday-seal">佳</span>
          <div>
            <div className="iday-label">Best day</div>
            <div className="iday-note small muted">
              {dayShort(insights.best_day?.date)}
              {insights.best_day?.note ? ` · ${insights.best_day.note}` : ''}
            </div>
          </div>
        </div>
        <div className="iday iday-worst">
          <span className="iday-seal">難</span>
          <div>
            <div className="iday-label">Toughest</div>
            <div className="iday-note small muted">
              {dayShort(insights.worst_day?.date)}
              {insights.worst_day?.note ? ` · ${insights.worst_day.note}` : ''}
            </div>
          </div>
        </div>
      </div>

      {/* Annai's summary */}
      <div className="insights-summary glass-ink">
        <span className="annai-avatar">案</span>
        <span>{insights.summary}</span>
      </div>
    </div>
  );
}
