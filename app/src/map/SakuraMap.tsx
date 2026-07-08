// The centerpiece: full-viewport inline SVG styled as an aged game-world map —
// an organic continent with a jagged coastline, mountain ridges, groves,
// rivers, tinted provinces, a branching dashed trail network between six
// hanko-seal stations, burnt parchment edges, and a traveler marker that walks
// the road via getPointAtLength. Tapping a seal zooms deep into its region.

import { memo, useEffect, useMemo, useRef } from 'react';
import { ROAD_D, STATIONS, type Station, type StationId } from './stations';

// Pre-baked 64×64 grayscale+alpha noise tile (base64 PNG). Replaces a
// full-screen SVG feTurbulence paper-grain filter, which is a heavy per-frame
// GPU cost in the Android WebView — the tiled PNG gives the identical aged-paper
// speckle for ~free. Deterministically generated (faint sumi flecks, α ≤ 14/255).
const PAPER_GRAIN =
  'data:image/png;base64,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';

interface Props {
  active: StationId | null; // null == full map view
  travelerT: number; // 0..1 position of traveler along the road (home progress)
  onSelect: (id: StationId) => void;
  reducedMotion: boolean;
}

// Deterministic pseudo-random in [-1, 1).
function noise(seed: number): number {
  const v = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
  return (v - Math.floor(v)) * 2 - 1;
}

// Jagged closed path through anchor points: each segment is subdivided and
// jittered perpendicular to its direction, giving a hand-drawn coastline.
function jagged(points: Array<[number, number]>, seed: number, amp: number): string {
  const out: string[] = [];
  const n = points.length;
  for (let i = 0; i < n; i++) {
    const [x0, y0] = points[i];
    const [x1, y1] = points[(i + 1) % n];
    const dx = x1 - x0;
    const dy = y1 - y0;
    const len = Math.hypot(dx, dy) || 1;
    const px = -dy / len;
    const py = dx / len;
    if (i === 0) out.push(`M ${x0.toFixed(1)} ${y0.toFixed(1)}`);
    const subs = 4;
    for (let s = 1; s <= subs; s++) {
      const t = s / subs;
      const wob = s === subs ? 0 : noise(seed + i * 13.7 + s * 3.1) * amp;
      const x = x0 + dx * t + px * wob;
      const y = y0 + dy * t + py * wob;
      out.push(`L ${x.toFixed(1)} ${y.toFixed(1)}`);
    }
  }
  return out.join(' ') + ' Z';
}

// Irregular closed blob (province tints, islets, burnt-border hole).
function blob(cx: number, cy: number, r: number, seed: number): string {
  const pts = 14;
  let d = '';
  for (let i = 0; i <= pts; i++) {
    const a = (i / pts) * Math.PI * 2;
    const wobble = 1 + 0.13 * Math.sin(a * 3 + seed) + 0.09 * Math.cos(a * 5 + seed * 2);
    const x = cx + Math.cos(a) * r * wobble;
    const y = cy + Math.sin(a) * r * wobble * 0.9;
    d += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
  }
  return d + 'Z';
}

// The continent's coastline anchors (clockwise), shared by fill + hatching.
const COAST: Array<[number, number]> = [
  [300, 80], [500, 45], [650, 100], [790, 50], [900, 130], [860, 250],
  [940, 360], [850, 450], [910, 560], [830, 660], [870, 780], [720, 850],
  [560, 800], [430, 880], [260, 900], [140, 820], [90, 690], [150, 590],
  [70, 470], [130, 350], [80, 230], [180, 140],
];
const COAST_D = jagged(COAST, 5, 16);

function Blossom({ x, y, s, deep }: { x: number; y: number; s: number; deep?: boolean }) {
  const petals = [0, 72, 144, 216, 288];
  return (
    <g transform={`translate(${x} ${y}) scale(${s})`}>
      {petals.map((deg) => (
        <ellipse
          key={deg}
          cx={0}
          cy={-7}
          rx={4}
          ry={7}
          fill={deep ? 'var(--sakura-deep)' : 'var(--sakura)'}
          opacity={0.9}
          transform={`rotate(${deg})`}
        />
      ))}
      <circle r={2.4} fill="var(--kin)" />
    </g>
  );
}

// A chain of mountain carets, old-map style.
function Ridge({ x, y, n, rot = 0 }: { x: number; y: number; n: number; rot?: number }) {
  return (
    <g transform={`translate(${x} ${y}) rotate(${rot})`} stroke="var(--sumi)" fill="none" opacity={0.5}>
      {Array.from({ length: n }, (_, i) => {
        const ox = i * 26 + noise(i + x) * 5;
        const oy = (i % 2) * 9 + noise(i * 2 + y) * 4;
        const s = 1 + noise(i * 3 + x + y) * 0.25;
        return (
          <path
            key={i}
            d={`M ${ox - 9 * s} ${oy + 7 * s} L ${ox} ${oy - 9 * s} L ${ox + 9 * s} ${oy + 7 * s} M ${ox} ${oy - 9 * s} L ${ox + 3 * s} ${oy - 1 * s}`}
            strokeWidth={1.6}
          />
        );
      })}
    </g>
  );
}

// A cluster of trees (forest blobs), old-map style.
function Grove({ x, y, seed, n = 7 }: { x: number; y: number; seed: number; n?: number }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      {Array.from({ length: n }, (_, i) => {
        const ox = noise(seed + i * 7.3) * 34;
        const oy = noise(seed + i * 11.9) * 22;
        const r = 5.5 + noise(seed + i * 3.7) * 2;
        return (
          <g key={i}>
            <circle cx={ox} cy={oy} r={r} fill="var(--matcha)" opacity={0.45} />
            <circle cx={ox} cy={oy} r={r} fill="none" stroke="var(--matcha)" strokeWidth={1.2} opacity={0.7} />
            <line x1={ox} y1={oy + r} x2={ox} y2={oy + r + 3} stroke="var(--sumi)" strokeWidth={1} opacity={0.5} />
          </g>
        );
      })}
    </g>
  );
}

// Ukiyo-e style mountain: shaded left face, ink ridgeline, optional snow cap.
function Mountain({ x, y, s = 1, snow = false }: { x: number; y: number; s?: number; snow?: boolean }) {
  return (
    <g transform={`translate(${x} ${y}) scale(${s})`}>
      {/* washi-toned peak (original palette), shaded left face + ink ridgeline */}
      <path d="M -46 26 L -8 -44 Q -4 -50 0 -44 L 38 26 Q 0 34 -46 26 Z" fill="var(--washi-deep)" stroke="var(--sumi)" strokeWidth={1.4} strokeOpacity={0.55} />
      <path d="M -8 -44 L -46 26 Q -22 30 -4 28 L -6 -40 Z" fill="var(--sumi)" opacity={0.14} />
      <path d="M -6 -38 L -12 -20 M -5 -28 L -14 -4 M -4 -16 L -16 8" stroke="var(--sumi)" strokeWidth={1} fill="none" opacity={0.3} />
      {snow && <path d="M -14 -32 L -8 -44 Q -4 -50 0 -44 L 6 -33 Q 0 -27 -6 -31 Q -10 -27 -14 -32 Z" fill="var(--washi)" stroke="var(--sumi)" strokeWidth={0.8} strokeOpacity={0.4} />}
    </g>
  );
}

// A chain of overlapping peaks.
function Range({ x, y, n, s = 1, seed = 0 }: { x: number; y: number; n: number; s?: number; seed?: number }) {
  return (
    <g>
      {Array.from({ length: n }, (_, i) => (
        <Mountain
          key={i}
          x={x + i * 52 * s + noise(seed + i * 5.1) * 14}
          y={y + noise(seed + i * 9.7) * 12 + (i % 2) * 10 * s}
          s={s * (0.75 + 0.35 * Math.abs(noise(seed + i * 3.3)))}
          snow={Math.abs(noise(seed + i * 7.7)) > 0.55}
        />
      ))}
    </g>
  );
}

// Stylized pine tree (layered canopy over an ink trunk).
function Pine({ x, y, s = 1, autumn = false }: { x: number; y: number; s?: number; autumn?: boolean }) {
  // Dense dark canopies per the reference: clumped round crowns in deep forest
  // green with a lighter top highlight, over a short ink trunk.
  const c = autumn ? 'var(--kin)' : '#4E6647';
  const hi = autumn ? '#DDB84A' : '#6B855C';
  return (
    <g transform={`translate(${x} ${y}) scale(${s})`}>
      <line x1={0} y1={0} x2={0} y2={-6} stroke="#3B3A33" strokeWidth={1.8} opacity={0.8} />
      <circle cx={-5} cy={-8} r={5.2} fill={c} />
      <circle cx={5} cy={-8} r={5} fill={c} />
      <circle cx={0} cy={-13} r={5.8} fill={c} />
      <circle cx={-1.5} cy={-14.5} r={3} fill={hi} opacity={0.8} />
      <path d="M -9.5 -6.5 Q 0 -2.5 9.5 -6.5" fill="none" stroke="#33452F" strokeWidth={0.9} opacity={0.55} />
    </g>
  );
}

// A dense pine forest cluster.
function Forest({ x, y, n, seed, spread = 46 }: { x: number; y: number; n: number; seed: number; spread?: number }) {
  return (
    <g>
      {Array.from({ length: n }, (_, i) => (
        <Pine
          key={i}
          x={x + noise(seed + i * 7.3) * spread}
          y={y + noise(seed + i * 12.9) * spread * 0.55 + (i % 3) * 7}
          s={0.8 + 0.45 * Math.abs(noise(seed + i * 3.7))}
          autumn={Math.abs(noise(seed + i * 17.3)) > 0.82}
        />
      ))}
    </g>
  );
}

// Small temple/pagoda with indigo tiered roofs and upturned eaves.
function Pagoda({ x, y, s = 1, tiers = 2 }: { x: number; y: number; s?: number; tiers?: number }) {
  return (
    <g transform={`translate(${x} ${y}) scale(${s})`}>
      {/* stone plinth + ground shadow */}
      <ellipse cx={0} cy={1.5} rx={12} ry={2.4} fill="var(--sumi)" opacity={0.12} />
      <rect x={-10} y={-1.5} width={20} height={2.6} rx={0.8} fill="var(--washi-deep)" stroke="var(--sumi)" strokeWidth={0.7} strokeOpacity={0.5} />
      {/* main hall wall with door + beam lines */}
      <rect x={-8} y={-8} width={16} height={7} fill="var(--washi)" stroke="var(--sumi)" strokeWidth={1} strokeOpacity={0.65} />
      <rect x={-1.8} y={-6} width={3.6} height={5} fill="var(--sumi)" opacity={0.55} />
      <path d="M -8 -5.8 H 8 M -5 -8 V -1.5 M 5 -8 V -1.5" stroke="var(--sumi)" strokeWidth={0.6} opacity={0.35} />
      {Array.from({ length: tiers }, (_, i) => (
        <g key={i} transform={`translate(0 ${-8 - i * 9})`}>
          {i > 0 && (
            <>
              <rect x={-6 + i} y={-4} width={12 - i * 2} height={5} fill="var(--washi)" stroke="var(--sumi)" strokeWidth={0.8} strokeOpacity={0.6} />
              <rect x={-1.2} y={-3} width={2.4} height={3} fill="var(--sumi)" opacity={0.4} />
            </>
          )}
          {/* vermillion roof with sweeping upturned eaves + eave shadow */}
          <path
            d={`M ${-15 + i * 2} 0 Q ${-17 + i * 2} -1.5 ${-13 + i * 2} -4 Q 0 -10.5 ${13 - i * 2} -4 Q ${17 - i * 2} -1.5 ${15 - i * 2} 0 Q 0 -4.5 ${-15 + i * 2} 0 Z`}
            fill="var(--shu)" stroke="var(--sumi)" strokeWidth={0.9} strokeOpacity={0.55}
          />
          <path d={`M ${-13 + i * 2} -3.4 Q 0 -9.5 ${13 - i * 2} -3.4`} fill="none" stroke="var(--ai)" strokeWidth={1.6} opacity={0.75} />
          <path d={`M ${-14 + i * 2} -0.4 Q 0 -4.6 ${14 - i * 2} -0.4`} fill="none" stroke="var(--sumi)" strokeWidth={0.7} opacity={0.3} />
        </g>
      ))}
      {/* ridge finial (sōrin) */}
      <line x1={0} y1={-8 - (tiers - 1) * 9 - 9} x2={0} y2={-8 - (tiers - 1) * 9 - 14} stroke="var(--kin)" strokeWidth={1.6} />
      <circle cx={0} cy={-8 - (tiers - 1) * 9 - 15} r={1.4} fill="var(--kin)" />
    </g>
  );
}

// Vermillion torii gate.
function Torii({ x, y, s = 1 }: { x: number; y: number; s?: number }) {
  return (
    <g transform={`translate(${x} ${y}) scale(${s})`} stroke="var(--shu)" strokeWidth={2.2} strokeLinecap="round">
      <line x1={-7} y1={0} x2={-7} y2={-14} />
      <line x1={7} y1={0} x2={7} y2={-14} />
      <path d="M -11 -14 Q 0 -17 11 -14" fill="none" />
      <line x1={-8.5} y1={-10} x2={8.5} y2={-10} strokeWidth={1.6} />
    </g>
  );
}

// Little ink boat with a washi sail, for the open water.
function Boat({ x, y, s = 1 }: { x: number; y: number; s?: number }) {
  return (
    <g transform={`translate(${x} ${y}) scale(${s})`} opacity={0.8}>
      <path d="M -12 0 Q 0 6 12 0 L 8 -2 L -9 -2 Z" fill="var(--sumi)" opacity={0.65} />
      <line x1={1} y1={-2} x2={1} y2={-14} stroke="var(--sumi)" strokeWidth={1.2} />
      <path d="M 1 -14 Q 8 -9 7 -3 L 1 -3 Z" fill="var(--washi)" stroke="var(--sumi)" strokeWidth={0.8} strokeOpacity={0.6} />
    </g>
  );
}

// A single tiny hip-roofed house (minka): sumi line outline, washi fill, an
// indigo hip roof. Decorative — never interactive.
function House({ x, y, s = 1 }: { x: number; y: number; s?: number }) {
  return (
    <g transform={`translate(${x} ${y}) scale(${s})`}>
      {/* body */}
      <rect x={-6} y={-4} width={12} height={7} fill="var(--washi)" stroke="var(--sumi)" strokeWidth={0.9} strokeOpacity={0.6} />
      {/* hip roof (trapezoid with a slight overhang) */}
      <path
        d="M -9 -4 L -4 -10 L 4 -10 L 9 -4 Z"
        fill="var(--sumi)"
        opacity={0.35}
        stroke="var(--sumi)"
        strokeWidth={0.9}
        strokeOpacity={0.55}
      />
      {/* ridge line */}
      <line x1={-4} y1={-10} x2={4} y2={-10} stroke="var(--sumi)" strokeWidth={0.8} strokeOpacity={0.5} />
    </g>
  );
}

// A village cluster: 3-6 tiny hip-roofed houses scattered around a point, along
// the road between stations. Decorative (pointerEvents none via the parent map).
function Village({ x, y, seed, n = 4, s = 1 }: { x: number; y: number; seed: number; n?: number; s?: number }) {
  const count = Math.max(3, Math.min(6, n));
  return (
    <g>
      {Array.from({ length: count }, (_, i) => {
        const ox = noise(seed + i * 6.1) * 26 * s;
        const oy = noise(seed + i * 10.7) * 16 * s + (i % 2) * 5 * s;
        const hs = s * (0.85 + 0.3 * Math.abs(noise(seed + i * 3.3)));
        return <House key={i} x={x + ox} y={y + oy} s={hs} />;
      })}
    </g>
  );
}

// A hand-drawn province/domain border: an irregular dashed --sumi boundary
// (1px, dash 3 4, opacity .3) enclosing a region. Built from a jittered blob so
// each border feels drawn by hand like an Edo-map domain line.
function ProvinceBorder({ cx, cy, r, seed }: { cx: number; cy: number; r: number; seed: number }) {
  return (
    <path
      d={blob(cx, cy, r, seed)}
      fill="none"
      stroke="var(--sumi)"
      strokeWidth={1}
      strokeDasharray="3 4"
      opacity={0.3}
    />
  );
}

// Kasumi mist band (ukiyo-e cloud), drifts over terrain edges.
function Mist({ x, y, w, s = 1 }: { x: number; y: number; w: number; s?: number }) {
  return (
    <g transform={`translate(${x} ${y}) scale(${s})`}>
      <path
        d={`M 0 0 Q 8 -10 24 -9 Q 34 -16 50 -13 Q 66 -18 ${w * 0.62} -10 Q ${w * 0.8} -13 ${w * 0.92} -6 Q ${w} -2 ${w * 0.9} 2 L 14 4 Q 2 4 0 0 Z`}
        fill="var(--washi)" opacity={0.85} stroke="var(--sumi)" strokeWidth={0.9} strokeOpacity={0.18}
      />
    </g>
  );
}

// Red-bordered legend cartouche in the sea corner, like old chart title boxes.
function Cartouche() {
  return (
    <g transform="translate(795 900)">
      <rect x={0} y={0} width={160} height={84} fill="var(--washi)" stroke="var(--shu)" strokeWidth={3} opacity={0.95} />
      <rect x={5} y={5} width={150} height={74} fill="none" stroke="var(--shu)" strokeWidth={1} opacity={0.8} />
      <text x={26} y={26} fontFamily="var(--font-display)" fontSize={19} fill="var(--sumi)" style={{ writingMode: 'vertical-rl' } as React.CSSProperties}>腹八分</text>
      <text x={62} y={22} fontFamily="var(--font-display)" fontSize={10} fill="var(--sumi)" opacity={0.75} style={{ writingMode: 'vertical-rl' } as React.CSSProperties}>旅路の図</text>
      <text x={88} y={22} fontFamily="var(--font-display)" fontSize={10} fill="var(--sumi)" opacity={0.6} style={{ writingMode: 'vertical-rl' } as React.CSSProperties}>八分目に</text>
      <circle cx={128} cy={58} r={13} fill="var(--shu)" opacity={0.9} />
      <text x={128} y={62} textAnchor="middle" fontFamily="var(--font-display)" fontSize={11} fill="var(--washi)">印</text>
    </g>
  );
}

function Seal({ st, active, onSelect }: { st: Station; active: boolean; onSelect: (id: StationId) => void }) {
  return (
    <g
      transform={`translate(${st.x} ${st.y})`}
      onClick={() => onSelect(st.id)}
      style={{ cursor: 'pointer' }}
      role="button"
      aria-label={`${st.name} ${st.english}`}
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') onSelect(st.id);
      }}
    >
      {/* generous invisible tap target */}
      <circle r={40} fill="transparent" />
      {/* ring-pulse fired when this seal becomes the active one */}
      {active && <circle className="seal-pulse" r={26} />}
      <circle r={26} fill={active ? 'var(--shu)' : 'var(--washi)'} stroke="var(--shu)" strokeWidth={4} />
      <text
        y={1}
        textAnchor="middle"
        dominantBaseline="central"
        fontFamily="var(--font-display)"
        fontSize={26}
        fontWeight={700}
        fill={active ? 'var(--washi)' : 'var(--shu)'}
      >
        {st.kanji}
      </text>
      <text
        y={44}
        textAnchor="middle"
        fontFamily="var(--font-display)"
        fontSize={15}
        fill="var(--sumi)"
      >
        {st.name}
      </text>
    </g>
  );
}

// Static blossom cluster layout — module-scope so its identity never changes
// across re-renders (no per-render array allocation).
const SAKURA_CLUSTERS = [
  { x: 170, y: 700, s: 1.0 }, { x: 300, y: 810, s: 0.8, deep: true },
  { x: 330, y: 500, s: 0.9 }, { x: 470, y: 560, s: 0.8, deep: true },
  { x: 640, y: 520, s: 0.9 }, { x: 520, y: 300, s: 0.8 },
  { x: 700, y: 250, s: 0.9, deep: true }, { x: 860, y: 200, s: 0.8 },
];

// Screen-space drifting petals. Capped at 2 (was 3) — each is an infinite
// compositor animation, so fewer live layers = smoother panning on Android.
const PETALS = [
  { x: 220, d: 0, dur: 24 },
  { x: 640, d: 10, dur: 27 },
];

function SakuraMapImpl({ active, travelerT, onSelect, reducedMotion }: Props) {
  const roadRef = useRef<SVGPathElement | null>(null);
  const travelerRef = useRef<SVGGElement | null>(null);

  // Move the traveler along the road whenever progress changes.
  useEffect(() => {
    const road = roadRef.current;
    const marker = travelerRef.current;
    if (!road || !marker) return;
    const len = road.getTotalLength();
    const p = road.getPointAtLength(len * Math.min(1, Math.max(0, travelerT)));
    marker.setAttribute('transform', `translate(${p.x} ${p.y})`);
  }, [travelerT]);

  // Deep region zoom for the active station (700ms via CSS), like moving to a
  // zone on a game world map. The region centers in the map area left visible
  // above the glass panel.
  const groupTransform = useMemo(() => {
    if (!active) return 'translate(0 0) scale(1)';
    const st = STATIONS.find((s) => s.id === active)!;
    const scale = 2.4;
    // Land the seal in the map strip left visible above the glass panel
    // (panel covers the lower ~72%, so target y ≈ 140 in user space).
    const tx = 500 - st.x * scale;
    const ty = 140 - st.y * scale;
    return `translate(${tx} ${ty}) scale(${scale})`;
  }, [active]);

  return (
    <svg
      className="map-svg"
      viewBox="0 0 1000 1000"
      preserveAspectRatio="xMidYMid slice"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden={active ? true : undefined}
    >
      <defs>
        <pattern id="paper-grain" width={64} height={64} patternUnits="userSpaceOnUse">
          <image href={PAPER_GRAIN} width={64} height={64} />
        </pattern>
        <radialGradient id="vignette" cx="50%" cy="42%" r="75%">
          <stop offset="55%" stopColor="var(--washi)" stopOpacity="0" />
          <stop offset="100%" stopColor="#6f5f3e" stopOpacity="0.34" />
        </radialGradient>
        <pattern id="seigaiha" x="0" y="0" width="60" height="30" patternUnits="userSpaceOnUse">
          {[0, 30, 60].map((ox) =>
            [8, 14, 20, 26].map((r) => (
              <path
                key={`${ox}-${r}`}
                d={`M ${ox - 30} 30 a ${r} ${r} 0 0 1 ${r * 2} 0`}
                fill="none"
                stroke="#B8CFDA"
                strokeWidth={1}
                opacity={0.55}
              />
            )),
          )}
        </pattern>
      </defs>

      {/* The sea: deep indigo open water stepping up through lighter
          turquoise bands toward every shore (ukiyo-e chart style). Bands are
          wide-to-narrow strokes on the coast path, lightest nearest land,
          painted before the landmass so the land fill crops their inner half. */}
      <rect x={0} y={0} width={1000} height={1000} fill="#35526B" />
      <rect x={0} y={0} width={1000} height={1000} fill="url(#seigaiha)" opacity={0.18} />

      {/* Everything that pans/zooms lives in this group */}
      <g
        className={reducedMotion ? 'map-group no-anim' : 'map-group'}
        transform={groupTransform}
      >
        {/* Coastal glow: turquoise bands stepping from deep sea to shore,
            lightest nearest land; the land fill crops their inner half. */}
        <g fill="none" strokeLinejoin="round" pointerEvents="none">
          <path d={COAST_D} stroke="#4A6E86" strokeWidth={64} opacity={0.9} />
          <path d={COAST_D} stroke="#5F8AA0" strokeWidth={42} opacity={0.95} />
          <path d={COAST_D} stroke="#7FA9BA" strokeWidth={24} />
          <path d={COAST_D} stroke="#A9CAD4" strokeWidth={10} />
          {[blob(105, 150, 34, 2), blob(925, 855, 42, 9)].map((d, i) => (
            <g key={i}>
              <path d={d} stroke="#5F8AA0" strokeWidth={26} opacity={0.95} />
              <path d={d} stroke="#7FA9BA" strokeWidth={14} />
              <path d={d} stroke="#A9CAD4" strokeWidth={6} />
            </g>
          ))}
        </g>

        {/* Sea décor per the reference: vermillion star motifs + white
            blossom sprigs floating on open water */}
        <g pointerEvents="none">
          {[
            { x: 320, y: 945, s: 1.0 }, { x: 600, y: 950, s: 0.8 },
            { x: 60, y: 430, s: 0.9 }, { x: 945, y: 620, s: 0.85 },
            { x: 240, y: 55, s: 0.7 },
          ].map((p, i) => (
            <g key={i} transform={`translate(${p.x} ${p.y}) scale(${p.s})`} stroke="var(--shu)" strokeWidth={1.5} strokeLinecap="round" opacity={0.85}>
              <path d="M 0 -8 V 8 M -8 0 H 8 M -5.5 -5.5 L 5.5 5.5 M -5.5 5.5 L 5.5 -5.5" />
              <circle r={2} fill="var(--shu)" stroke="none" />
              {[0, 45, 90, 135].map((deg) => (
                <circle key={deg} cx={0} cy={-11} r={0.9} fill="var(--shu)" stroke="none" transform={`rotate(${deg + 22})`} />
              ))}
            </g>
          ))}
          {[
            { x: 150, y: 950 }, { x: 880, y: 930 }, { x: 55, y: 240 },
          ].map((p, i) => (
            <g key={i} transform={`translate(${p.x} ${p.y})`} opacity={0.9}>
              <path d="M 0 8 Q 3 0 8 -6" fill="none" stroke="#FDFBF4" strokeWidth={1.2} />
              {[[0, 0], [5, -4], [9, -8], [3, -8]].map(([bx, by], j) => (
                <circle key={j} cx={bx} cy={by} r={2.6} fill="#FDFBF4" />
              ))}
              <circle cx={5} cy={-5} r={1} fill="var(--sakura-deep)" opacity={0.7} />
            </g>
          ))}
        </g>

        {/* The continent */}
        <path d={COAST_D} fill="var(--washi)" stroke="var(--sumi)" strokeWidth={2.2} strokeOpacity={0.55} />

        {/* Offshore islets */}
        <path d={blob(105, 150, 34, 2)} fill="var(--washi)" stroke="var(--sumi)" strokeWidth={1.5} strokeOpacity={0.5} />
        <path d={blob(925, 855, 42, 9)} fill="var(--washi)" stroke="var(--sumi)" strokeWidth={1.5} strokeOpacity={0.5} />

        {/* Province tints — the central forest meadow reads as a real
            moss-green heart of the continent (per the reference); the sakura
            province stays a soft blush */}
        <path d={blob(330, 780, 150, 4)} fill="var(--sakura)" opacity={0.14} />
        <path d={blob(680, 400, 130, 7)} fill="#8CA66B" opacity={0.34} />
        <path d={blob(420, 560, 170, 12)} fill="#8CA66B" opacity={0.28} />
        <path d={blob(430, 555, 172, 12)} fill="none" stroke="#6B855C" strokeWidth={1.2} opacity={0.35} />

        {/* Province & domain borders (DESIGN.md 6.7): hand-drawn dashed --sumi
            boundaries enclosing the two tinted provinces plus a few implied
            regions, like domain borders on Edo maps. Decorative — the parent
            group's pointerEvents rule keeps taps reaching the seals. */}
        <g pointerEvents="none">
          <ProvinceBorder cx={330} cy={780} r={158} seed={4} />
          <ProvinceBorder cx={680} cy={400} r={138} seed={7} />
          <ProvinceBorder cx={470} cy={230} r={120} seed={31} />
          <ProvinceBorder cx={230} cy={470} r={104} seed={44} />
          <ProvinceBorder cx={720} cy={640} r={112} seed={52} />
        </g>

        {/* Inland elevation patches with faint contour outlines */}
        {[
          { x: 560, y: 330, r: 120, s: 11 },
          { x: 330, y: 430, r: 90, s: 13 },
          { x: 700, y: 600, r: 100, s: 17 },
        ].map((e, i) => (
          <g key={i}>
            <path d={blob(e.x, e.y, e.r, e.s)} fill="var(--washi-deep)" opacity={0.5} />
            <path d={blob(e.x, e.y, e.r * 0.62, e.s + 1)} fill="none" stroke="var(--sumi)" strokeWidth={1} strokeOpacity={0.16} />
            <path d={blob(e.x, e.y, e.r, e.s)} fill="none" stroke="var(--sumi)" strokeWidth={1} strokeOpacity={0.12} />
          </g>
        ))}

        {/* Mountain ranges (ukiyo-e layered peaks) */}
        <Range x={300} y={110} n={5} s={1.15} seed={2} />
        <Range x={330} y={165} n={3} s={0.8} seed={12} />
        <Range x={720} y={200} n={3} s={1.0} seed={5} />
        <Range x={770} y={255} n={2} s={0.7} seed={15} />
        <Range x={140} y={360} n={3} s={0.95} seed={8} />
        <Range x={165} y={430} n={2} s={0.65} seed={18} />
        <Range x={545} y={320} n={3} s={0.9} seed={22} />
        <Range x={800} y={470} n={2} s={0.85} seed={25} />
        <Range x={790} y={545} n={2} s={0.6} seed={35} />
        <Range x={175} y={760} n={2} s={0.55} seed={41} />
        <Ridge x={430} y={430} n={3} rot={-8} />
        <Ridge x={620} y={560} n={3} rot={-14} />

        {/* No rivers — per the reference, the land reads as trail + forest +
            meadow only. Decorative white cloud puffs drift over terrain edges,
            and the sea carries vermillion star motifs + white blossom sprigs. */}
        <g pointerEvents="none">
          {[
            { x: 470, y: 395, s: 1.0 }, { x: 615, y: 300, s: 0.8 },
            { x: 350, y: 545, s: 0.9 }, { x: 545, y: 705, s: 0.85 },
            { x: 685, y: 505, s: 0.75 },
          ].map((c, i) => (
            <g key={i} transform={`translate(${c.x} ${c.y}) scale(${c.s})`} opacity={0.92}>
              <ellipse cx={-9} cy={2} rx={10} ry={5.5} fill="#FDFBF4" />
              <ellipse cx={2} cy={-2} rx={12} ry={7} fill="#FDFBF4" />
              <ellipse cx={13} cy={2.5} rx={9} ry={5} fill="#FDFBF4" />
              <path d="M -19 4 Q 2 10 22 4" fill="none" stroke="var(--sumi)" strokeWidth={0.8} opacity={0.18} />
            </g>
          ))}
        </g>

        {/* Dense pine forests (with the odd autumn tree) */}
        <Forest x={290} y={710} n={12} seed={3} spread={60} />
        <Forest x={445} y={615} n={9} seed={7} />
        <Forest x={730} y={470} n={13} seed={8} spread={58} />
        <Forest x={415} y={345} n={8} seed={11} />
        <Forest x={700} y={95} n={9} seed={16} spread={52} />
        <Forest x={215} y={555} n={6} seed={14} spread={38} />
        <Forest x={555} y={655} n={7} seed={19} spread={42} />
        <Forest x={340} y={240} n={5} seed={23} spread={34} />
        <Grove x={870} y={640} seed={27} n={4} />

        {/* Settlements: temples, teahouses, gates by each station */}
        <Pagoda x={393} y={838} s={1.1} tiers={2} />
        <Pagoda x={318} y={852} s={0.85} tiers={1} />
        <Pagoda x={293} y={608} s={0.95} tiers={1} />
        <Pagoda x={558} y={548} s={0.9} tiers={1} />
        <Torii x={487} y={556} s={0.9} />
        <Pagoda x={702} y={352} s={1.15} tiers={3} />
        <Pagoda x={419} y={287} s={0.85} tiers={1} />
        <Pagoda x={484} y={292} s={0.85} tiers={1} />
        <Pagoda x={585} y={92} s={1.0} tiers={2} />
        <Torii x={657} y={152} s={1.1} />
        <Torii x={398} y={372} s={0.8} />

        {/* Village clusters along the road between stations (hip-roofed minka).
            Placed between seal pairs, inside the phone-visible band. */}
        <g pointerEvents="none">
          <Village x={368} y={735} seed={61} n={5} s={1.0} />
          <Village x={432} y={575} seed={67} n={4} s={0.95} />
          <Village x={600} y={445} seed={73} n={5} s={0.9} />
          <Village x={540} y={300} seed={79} n={4} s={0.85} />
        </g>

        {/* Open water: boats and mist bands */}
        <Boat x={62} y={545} s={1.0} />
        <Boat x={882} y={725} s={0.85} />
        <Boat x={505} y={942} s={0.9} />
        <Boat x={150} y={95} s={0.7} />
        <Mist x={200} y={195} w={170} />
        <Mist x={700} y={630} w={150} s={0.9} />
        <Mist x={90} y={640} w={120} s={0.8} />
        <Cartouche />

        {/* Trail network: spurs first (under), then the main road */}
        <path
          d="M 520 520 C 460 460, 430 430, 400 380"
          fill="none" stroke="var(--sumi)" strokeWidth={2} strokeDasharray="6 5" opacity={0.4}
        />
        <path
          d="M 450 250 C 400 220, 370 210, 330 190"
          fill="none" stroke="var(--sumi)" strokeWidth={2} strokeDasharray="6 5" opacity={0.4}
        />
        <path
          d="M 330 640 C 300 690, 290 720, 300 760"
          fill="none" stroke="var(--sumi)" strokeWidth={2} strokeDasharray="6 5" opacity={0.4}
        />
        <path
          ref={roadRef}
          d={ROAD_D}
          fill="none"
          stroke="var(--sumi)"
          strokeWidth={2.5}
          strokeDasharray="8 4"
          strokeLinecap="round"
          opacity={0.75}
        />

        {/* Sakura clusters near the trails */}
        {SAKURA_CLUSTERS.map((c, i) => (
          <Blossom key={i} x={c.x} y={c.y} s={c.s} deep={c.deep} />
        ))}

        {/* Station seals */}
        {STATIONS.map((st) => (
          <Seal key={st.id} st={st} active={active === st.id} onSelect={onSelect} />
        ))}

        {/* Traveler marker (straw hat) — decorative, must not steal seal taps */}
        <g ref={travelerRef} className="traveler" pointerEvents="none">
          <circle r={13} fill="var(--washi)" stroke="var(--sumi)" strokeWidth={2} />
          {/* straw-hat triangle */}
          <path d="M -11 2 L 11 2 L 0 -13 Z" fill="var(--kin)" stroke="var(--sumi)" strokeWidth={1.5} />
          <line x1={-13} y1={2} x2={13} y2={2} stroke="var(--sumi)" strokeWidth={1.5} />
        </g>

        {/* Compass rose (SE, over the sea) */}
        <g transform="translate(660 930)" opacity={0.85} pointerEvents="none">
          <circle r={46} fill="none" stroke="var(--sumi)" strokeWidth={1.5} opacity={0.5} />
          {[0, 45, 90, 135, 180, 225, 270, 315].map((deg) => (
            <line
              key={deg}
              x1={0}
              y1={0}
              x2={0}
              y2={deg % 90 === 0 ? -46 : -30}
              stroke="var(--sumi)"
              strokeWidth={deg % 90 === 0 ? 2 : 1}
              opacity={deg % 90 === 0 ? 0.75 : 0.4}
              transform={`rotate(${deg})`}
            />
          ))}
          <path d="M 0 -46 L 7 0 L 0 46 L -7 0 Z" fill="var(--shu)" opacity={0.85} />
          <Blossom x={0} y={0} s={0.8} deep />
          <text y={-52} textAnchor="middle" fontFamily="var(--font-display)" fontSize={14} fill="var(--sumi)">
            北
          </text>
        </g>
      </g>

      {/* Drifting petals (screen-space, above map, below panels). Paused while a
          station panel is open — the panel covers ~72% and the petals would just
          burn compositor cycles behind it. */}
      {!reducedMotion && (
        <g className={active ? 'petals paused' : 'petals'} pointerEvents="none">
          {PETALS.map((p, i) => (
            <g key={i} className="petal" style={{ animationDelay: `${p.d}s`, animationDuration: `${p.dur}s` }}>
              <g transform={`translate(${p.x} 0)`}>
                <ellipse rx={7} ry={11} fill="var(--sakura)" opacity={0.85} />
              </g>
            </g>
          ))}
        </g>
      )}

      {/* Burnt parchment border: dark ring with an irregular inner edge.
          These overlays sit above the seals, so they MUST be click-transparent
          or real (hit-tested) taps never reach the stations. */}
      <path
        d={`M 0 0 H 1000 V 1000 H 0 Z ${blob(500, 500, 610, 3)}`}
        fill="#5d4e33"
        opacity={0.3}
        fillRule="evenodd"
        pointerEvents="none"
      />

      {/* Paper aging: grain (pre-baked noise tile) + vignette overlays */}
      <rect x={0} y={0} width={1000} height={1000} fill="url(#paper-grain)" opacity={0.5} pointerEvents="none" />
      <rect x={0} y={0} width={1000} height={1000} fill="url(#vignette)" pointerEvents="none" />
    </svg>
  );
}

// The map is expensive to reconcile (hundreds of decorative SVG nodes) yet its
// props are stable across day-data polls (active/onSelect identity holds, the
// traveler moves via an imperative ref, not a re-render). Memoize so a parent
// re-render — e.g. a toast or a day reload — never re-renders the whole map.
export const SakuraMap = memo(SakuraMapImpl);
