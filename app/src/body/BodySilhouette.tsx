// Abstract sumi-line body silhouettes for the body-fat picker (DESIGN.md 6.6).
// Inline SVG line art — NOT photos. A single parametric drawing whose torso /
// waist / limb width grows with the body-fat level, with light sex-appropriate
// cues (shoulders vs hips). Tasteful and abstract; drawn in --sumi strokes.

import type { Sex } from '../api/types';

// The seven levels the picker offers.
export const BODY_FAT_LEVELS = [10, 15, 20, 25, 30, 35, 40] as const;
export type BodyFatLevel = (typeof BODY_FAT_LEVELS)[number];

export function bodyFatLabel(pct: number): string {
  if (pct <= 11) return `~${pct}% · very lean`;
  if (pct <= 16) return `~${pct}% · lean`;
  if (pct <= 21) return `~${pct}% · fit`;
  if (pct <= 26) return `~${pct}% · average`;
  if (pct <= 31) return `~${pct}% · soft`;
  if (pct <= 36) return `~${pct}% · higher`;
  return `~${pct}% · high`;
}

interface Props {
  pct: number;
  sex: Sex;
  size?: number;
}

// Map body-fat % (10..40) to a 0..1 "fullness" factor driving widths.
function fullness(pct: number): number {
  return Math.min(1, Math.max(0, (pct - 10) / 30));
}

export function BodySilhouette({ pct, sex, size = 88 }: Props) {
  const f = fullness(pct);

  // Base half-widths (at the silhouette's centre x=50) in a 100×160 viewBox.
  // Shoulders wider for male, hips wider for female; both grow with fullness.
  const shoulder = (sex === 'f' ? 15 : 19) + f * 8;
  const chest = (sex === 'f' ? 13 : 15) + f * 9;
  const waist = (sex === 'f' ? 9 : 10) + f * 13; // waist thickens most with fat
  const hip = (sex === 'f' ? 16 : 13) + f * 11;
  const thigh = (sex === 'f' ? 8 : 8) + f * 5;
  const neck = 4 + f * 1.5;

  const cx = 50;
  // Vertical anchors.
  const yHead = 20;
  const yNeck = 32;
  const yShoulder = 40;
  const yChest = 58;
  const yWaist = 82;
  const yHip = 98;
  const yKnee = 128;
  const yFoot = 152;

  // Build a smooth closed body outline (right side down, left side up) using the
  // half-widths above. Quadratic curves keep it organic.
  const P = (x: number, y: number) => `${x.toFixed(1)},${y.toFixed(1)}`;
  const outline = [
    // neck → right shoulder
    `M ${P(cx + neck, yNeck)}`,
    `Q ${P(cx + shoulder, yShoulder - 4)} ${P(cx + shoulder, yShoulder)}`,
    // shoulder → chest
    `Q ${P(cx + chest + 2, yChest - 10)} ${P(cx + chest, yChest)}`,
    // chest → waist
    `Q ${P(cx + waist + 1, yWaist - 12)} ${P(cx + waist, yWaist)}`,
    // waist → hip
    `Q ${P(cx + hip + 1, yHip - 8)} ${P(cx + hip, yHip)}`,
    // hip → right knee (leg)
    `Q ${P(cx + thigh + 4, yKnee - 20)} ${P(cx + thigh - 1, yKnee)}`,
    // knee → foot
    `L ${P(cx + thigh - 3, yFoot)}`,
    `L ${P(cx + 1.5, yFoot)}`,
    // inner right leg up to crotch
    `L ${P(cx + 2, yHip + 4)}`,
    // crotch across to inner left leg
    `L ${P(cx - 2, yHip + 4)}`,
    `L ${P(cx - 1.5, yFoot)}`,
    // left foot → left knee up
    `L ${P(cx - thigh + 3, yFoot)}`,
    `L ${P(cx - thigh + 1, yKnee)}`,
    // left knee → hip
    `Q ${P(cx - thigh - 4, yKnee - 20)} ${P(cx - hip, yHip)}`,
    // hip → waist
    `Q ${P(cx - waist - 1, yHip - 8)} ${P(cx - waist, yWaist)}`,
    // waist → chest
    `Q ${P(cx - waist - 1, yWaist - 12)} ${P(cx - chest, yChest)}`,
    // chest → shoulder
    `Q ${P(cx - chest - 2, yChest - 10)} ${P(cx - shoulder, yShoulder)}`,
    // shoulder → neck
    `Q ${P(cx - shoulder, yShoulder - 4)} ${P(cx - neck, yNeck)}`,
    'Z',
  ].join(' ');

  // Arms hang beside the torso; they thicken slightly with fullness.
  const armW = 3.5 + f * 2.5;
  const armTopX = cx + shoulder - 1;
  const armBotX = cx + waist + armW + 2 + f * 3;
  const armRight = `M ${P(armTopX, yShoulder + 2)} Q ${P(armBotX + 3, yChest)} ${P(armBotX, yWaist + 2)}`;
  const armLeft = `M ${P(cx - shoulder + 1, yShoulder + 2)} Q ${P(cx - (armBotX - cx) - 3, yChest)} ${P(cx - (armBotX - cx), yWaist + 2)}`;

  return (
    <svg
      viewBox="0 0 100 160"
      width={size}
      height={size * 1.6}
      aria-hidden
      className="body-silhouette"
    >
      {/* Head */}
      <circle cx={cx} cy={yHead} r={9 + f * 1.5} fill="none" stroke="var(--sumi)" strokeWidth={2} />
      {/* Neck */}
      <line x1={cx} y1={yNeck - 4} x2={cx} y2={yNeck} stroke="var(--sumi)" strokeWidth={2} />
      {/* Body outline (subtle washi fill so it reads as a figure) */}
      <path d={outline} fill="rgba(43,42,51,0.05)" stroke="var(--sumi)" strokeWidth={2} strokeLinejoin="round" />
      {/* Arms */}
      <path d={armRight} fill="none" stroke="var(--sumi)" strokeWidth={armW} strokeLinecap="round" opacity={0.9} />
      <path d={armLeft} fill="none" stroke="var(--sumi)" strokeWidth={armW} strokeLinecap="round" opacity={0.9} />
    </svg>
  );
}
