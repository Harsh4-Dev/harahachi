// Body-fat diagram picker (DESIGN.md 6.6): two rows — "your body now" and
// "your goal" — each a horizontal scroll of 7 glass tiles with sumi-line
// silhouettes at ~10/15/20/25/30/35/40% body fat. Selected tile gets the --shu
// ring. Used in onboarding (a step between body stats and activity/goal) and in
// Tegata (editable). A small "or estimate from a photo" button switches to the
// camera path (handled by the parent).

import { BODY_FAT_LEVELS, BodySilhouette, bodyFatLabel } from './BodySilhouette';
import type { Sex } from '../api/types';

interface Props {
  sex: Sex;
  now: number | null;
  goal: number | null;
  onNow: (pct: number) => void;
  onGoal: (pct: number) => void;
  onPhoto?: () => void;
}

function Row({
  title,
  sex,
  value,
  onPick,
}: {
  title: string;
  sex: Sex;
  value: number | null;
  onPick: (pct: number) => void;
}) {
  return (
    <div className="bodyrow">
      <div className="bodyrow-title small">{title}</div>
      <div className="bodyrow-scroll">
        {BODY_FAT_LEVELS.map((pct) => (
          <button
            key={pct}
            type="button"
            className={`body-tile glass ${value === pct ? 'selected' : ''}`}
            onClick={() => onPick(pct)}
            aria-pressed={value === pct}
            aria-label={bodyFatLabel(pct)}
          >
            <BodySilhouette pct={pct} sex={sex} size={54} />
            <span className="body-tile-label small">{bodyFatLabel(pct)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function BodyPicker({ sex, now, goal, onNow, onGoal, onPhoto }: Props) {
  return (
    <div className="body-picker">
      <Row title="Your body now" sex={sex} value={now} onPick={onNow} />
      <Row title="Your goal" sex={sex} value={goal} onPick={onGoal} />
      {onPhoto && (
        <button type="button" className="link-btn" onClick={onPhoto} style={{ display: 'block', margin: '4px auto 0' }}>
          or estimate from a photo →
        </button>
      )}
    </div>
  );
}
