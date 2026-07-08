// Sakura-petal loader — the app's signature loading state. Five sakura petals
// orbit + flutter around a kin (gold) center. Transform/opacity only, so it
// composites cleanly on the Android WebView. With prefers-reduced-motion the
// animation is frozen into a static open blossom (handled in components.css).
//
// Two shapes:
//   <PetalLoader />                    inline, ~34px (default)
//   <PetalLoader block caption="…" />  centered column w/ larger petals + caption

import type { CSSProperties } from 'react';

interface Props {
  size?: number; // petal-cluster diameter in px
  caption?: string; // optional muted line under the petals
  block?: boolean; // center in a padded column (overlay/list loading)
  className?: string;
}

const PETALS = [0, 1, 2, 3, 4];

export function PetalLoader({ size, caption, block = false, className = '' }: Props) {
  const px = size ?? (block ? 48 : 34);
  const petals = (
    <span
      className="petal-loader"
      style={{ '--pl-size': `${px}px` } as CSSProperties}
      role="status"
      aria-label={caption ?? 'Loading'}
    >
      <span className="pl-orbit">
        {PETALS.map((i) => (
          <span
            key={i}
            className="pl-petal"
            style={{ '--pl-a': `${i * 72}deg`, animationDelay: `${i * 0.16}s` } as CSSProperties}
          />
        ))}
      </span>
      <span className="pl-center" aria-hidden />
    </span>
  );

  if (!block) return className ? <span className={className}>{petals}</span> : petals;

  return (
    <div className={`petal-loading ${className}`}>
      {petals}
      {caption && <span className="pl-caption small muted">{caption}</span>}
    </div>
  );
}
