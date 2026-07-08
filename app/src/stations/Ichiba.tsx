// Ichiba 市場: glass merch placeholder cards + "healthy nearby" deep links out.

const MERCH = [
  { emoji: '🍶', name: 'Harahachi bottle' },
  { emoji: '👕', name: 'Traveler tee' },
  { emoji: '🍱', name: 'Bento box' },
];

const MAPS_LINK =
  'https://www.google.com/maps/search/healthy+restaurants+near+me';

const LINKS = [
  { emoji: '🥗', label: 'Healthy salads on Swiggy', href: 'https://www.swiggy.com/search?query=healthy%20salad' },
  { emoji: '🍜', label: 'Healthy options on Zomato', href: 'https://www.zomato.com/search?q=healthy' },
  { emoji: '📍', label: 'Healthy restaurants nearby', href: MAPS_LINK },
];

export function Ichiba() {
  return (
    <div>
      <div className="card-title" style={{ marginBottom: 10 }}>
        Merch — coming soon
      </div>
      <div className="merch-grid">
        {MERCH.map((m) => (
          <div className="merch-card glass" key={m.name}>
            <div className="merch-emoji">{m.emoji}</div>
            <div className="small" style={{ marginTop: 6 }}>
              {m.name}
            </div>
            <div className="coming-soon">coming soon</div>
          </div>
        ))}
      </div>

      <div className="section-label">Healthy nearby</div>
      {LINKS.map((l) => (
        <a className="deep-link glass" key={l.href} href={l.href} target="_blank" rel="noopener noreferrer">
          <span className="dl-emoji">{l.emoji}</span>
          <span style={{ flex: 1 }}>{l.label}</span>
          <span aria-hidden>↗</span>
        </a>
      ))}
    </div>
  );
}
