// Streak chip (DESIGN.md §6.7): the current on-target streak as a small hanko
// count, e.g. "7日 streak". A vermillion seal glyph carries the number; the best
// streak sits underneath as a quiet caption. Zero streak still renders (a gentle
// "start today" nudge) so the chip is never a layout hole.

interface Props {
  current: number;
  best: number;
}

export function StreakChip({ current, best }: Props) {
  return (
    <div className="streak-chip" title={best > 0 ? `Best streak: ${best} days` : undefined}>
      <span className="streak-seal">{current > 0 ? `${current}日` : '始'}</span>
      <span className="streak-text">
        {current > 0 ? 'streak' : 'start today'}
        {best > 0 && current > 0 && <span className="streak-best"> · best {best}</span>}
      </span>
    </div>
  );
}
