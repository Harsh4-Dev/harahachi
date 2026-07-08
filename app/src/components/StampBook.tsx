// Stamp book 御朱印帳 (DESIGN.md §6.7): a goshuin grid, one ornate red day stamp
// per on-target day, with gold-ringed milestone stamps (7/30 days). Data from
// GET /streaks.stamps. Days are grouped newest-first; the seal glyph varies by
// kind (印 on-target, 七 / 卅 milestones, 初 first log).

import type { Stamp, StampKind } from '../api/types';

interface Props {
  stamps: Stamp[];
  current: number;
  best: number;
}

const GLYPH: Record<StampKind, string> = {
  on_target: '印',
  milestone_7: '七',
  milestone_30: '卅',
  first_log: '初',
};

const KIND_LABEL: Record<StampKind, string> = {
  on_target: 'On target',
  milestone_7: '7-day milestone',
  milestone_30: '30-day milestone',
  first_log: 'First log',
};

function dayNum(date: string): string {
  return date.slice(-2).replace(/^0/, '');
}

export function StampBook({ stamps, current, best }: Props) {
  const sorted = [...stamps].sort((a, b) => b.date.localeCompare(a.date));

  return (
    <div>
      <div className="row between" style={{ marginBottom: 12 }}>
        <div>
          <div className="display" style={{ fontSize: 22 }}>
            {current}日
          </div>
          <div className="small muted">current streak</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div className="display" style={{ fontSize: 22 }}>
            {best}日
          </div>
          <div className="small muted">best streak</div>
        </div>
      </div>

      {sorted.length === 0 ? (
        <div className="empty">No stamps yet — land a day at or under target to earn your first goshuin.</div>
      ) : (
        <div className="stamp-grid">
          {sorted.map((s) => {
            const milestone = s.kind === 'milestone_7' || s.kind === 'milestone_30';
            return (
              <div
                key={s.date}
                className={`stamp ${milestone ? 'stamp-milestone' : ''} stamp-${s.kind}`}
                title={`${s.date} · ${KIND_LABEL[s.kind]}`}
              >
                <span className="stamp-glyph">{GLYPH[s.kind]}</span>
                <span className="stamp-day tnum">{dayNum(s.date)}</span>
              </div>
            );
          })}
        </div>
      )}

      <div className="stamp-legend small muted">
        <span>
          <span className="stamp-swatch stamp-on" />on target
        </span>
        <span>
          <span className="stamp-swatch stamp-gold" />milestone (7 · 30)
        </span>
      </div>
    </div>
  );
}
