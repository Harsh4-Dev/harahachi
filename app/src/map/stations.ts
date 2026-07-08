// Station layout on the map. Coordinates are in the SVG's 1000x1000 user space.
// Stations sit inland on the continent; the road threads through all six.

export type StationId = 'honjin' | 'chaya' | 'koyomi' | 'dojo' | 'ichiba' | 'tegata';

export interface Station {
  id: StationId;
  kanji: string;
  name: string; // romaji
  english: string;
  x: number;
  y: number;
}

// Phones crop the 1000x1000 map to a central column (~x 270-730 at 9:19.5),
// so every station must live inside that band; terrain may bleed past it.
export const STATIONS: Station[] = [
  { id: 'honjin', kanji: '本', name: 'Honjin', english: 'Home', x: 350, y: 820 },
  { id: 'chaya', kanji: '茶', name: 'Chaya', english: 'Meals', x: 330, y: 640 },
  { id: 'koyomi', kanji: '暦', name: 'Koyomi', english: 'Calendar', x: 520, y: 520 },
  { id: 'dojo', kanji: '道', name: 'Dōjō', english: 'Fitness', x: 660, y: 380 },
  { id: 'ichiba', kanji: '市', name: 'Ichiba', english: 'Store', x: 450, y: 250 },
  { id: 'tegata', kanji: '手', name: 'Tegata', english: 'Profile', x: 620, y: 120 },
];

export function stationById(id: StationId): Station {
  const s = STATIONS.find((x) => x.id === id);
  if (!s) throw new Error('unknown station ' + id);
  return s;
}

// The main trail through the stations: starts at the small SW harbor, winds
// around terrain like a game-map route. Hand-tuned cubics so getPointAtLength
// gives a pleasant traveler walk.
export const ROAD_D = [
  'M 290 880',
  'C 300 860, 320 845, 350 820', // -> Honjin (up from the harbor)
  'C 390 780, 350 700, 330 640', // -> Chaya (through the sakura province)
  'C 310 580, 440 560, 520 520', // -> Koyomi (crosses the river ford)
  'C 590 490, 620 440, 660 380', // -> Dojo (into the matcha province)
  'C 700 320, 520 320, 450 250', // -> Ichiba (around the ridge line)
  'C 410 210, 540 160, 620 120', // -> Tegata (northern highlands)
].join(' ');

// Approximate fraction (0..1) of the total road length at each station, used to
// place the traveler and to know where "home" sits. Tuned to the path above.
export const STATION_T: Record<StationId, number> = {
  honjin: 0.07,
  chaya: 0.23,
  koyomi: 0.42,
  dojo: 0.59,
  ichiba: 0.81,
  tegata: 0.98,
};
