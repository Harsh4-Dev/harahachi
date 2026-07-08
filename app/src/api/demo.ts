// Demo mode: a full in-memory stub implementing HarahachiClient with realistic
// fake data, so the app is fully demoable standalone (VITE_DEMO=1 or the
// "try demo" button on the pairing screen). Persists to localStorage under a
// separate namespace so a demo session survives reloads.

import type {
  AnalyzeResult,
  AuthResponse,
  BackendRole,
  BarcodeResult,
  BodyPhotoEstimate,
  CalendarResponse,
  ChatMessage,
  ChatResponse,
  HarahachiClient,
  HealthResponse,
  KeeperBackend,
  KeeperBackendsResponse,
  KeeperOverview,
  KeeperPairResponse,
  KeeperUserDetail,
  Meal,
  MealInput,
  MealsResponse,
  Portion,
  Profile,
  ProfileWithTargets,
  RecentMeal,
  RecentMealsResponse,
  RegisterInput,
  StreaksResponse,
  Stamp,
  SuggestionsResponse,
  InsightsResponse,
  User,
  WeightResponse,
  WeightEntry,
  Workout,
  WorkoutsResponse,
} from './types';
import { computeTargets, computeEtaDate } from '../lib/targets';
import { parseAteMessage } from '../lib/foodParser';

// The demo's fake account. Real accounts live server-side; demo mode gets one
// canned traveler so the account UI (username, logout, switch) is exercisable.
// The demo account is a keeper (admin) so the Checkpoint keeper card is
// demoable standalone.
const DEMO_USER: User = { id: 'demo-user', username: 'traveler', is_admin: true };

const DEMO_KEY = 'hh.demoState';

interface DemoState {
  profile: Profile;
  meals: Meal[];
  workouts: (Workout & { date: string })[];
  // v3: water logged per day (ml) keyed by YYYY-MM-DD.
  water: Record<string, number>;
  // v3: weight history (~3 weeks seeded).
  weights: WeightEntry[];
  // v3: dish signatures the user has favorited (matched by lower-cased dish).
  favorites: string[];
}

// Deterministic pseudo-random in [-1, 1) for seeding plausible history.
function noise(seed: number): number {
  const v = Math.sin(seed * 91.7 + 47.3) * 43758.5453;
  return (v - Math.floor(v)) * 2 - 1;
}

// All demo dates use the LOCAL calendar day so they line up with the app's
// format.todayStr() (also local). Using toISOString() here would key data under
// the UTC day, which drifts a day off for users east/west of UTC and hides
// "today"'s seeded meals/water/streak near the date boundary.
function pad2(n: number): string {
  return String(n).padStart(2, '0');
}
function localDate(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

function today(): string {
  return localDate(new Date());
}

// A local-time ISO-ish stamp whose slice(0,10) equals the LOCAL day (no Z), so
// filtering meals by eaten_at.slice(0,10) === todayStr() works at any timezone.
function isoNow(hourOffset = 0): string {
  const d = new Date();
  d.setHours(d.getHours() - hourOffset);
  return `${localDate(d)}T${pad2(d.getHours())}:${pad2(d.getMinutes())}:00.000`;
}

// YYYY-MM-DD n days before today (local).
function dateDaysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return localDate(d);
}

// Water target: 35 ml/kg, min 2000 (API.md v3).
function waterTarget(p: Profile): number {
  return Math.max(2000, Math.round(p.weight_kg * 35));
}

function seedState(): DemoState {
  const t = today();
  // ~3 weeks of weight, drifting gently down (a cut) with day-to-day water noise.
  const weights: WeightEntry[] = [];
  for (let i = 20; i >= 0; i--) {
    const base = 71.4 - (20 - i) * 0.06; // ~1.2 kg over 3 weeks
    const jitter = noise(i * 5 + 1) * 0.35;
    weights.push({ date: dateDaysAgo(i), weight_kg: Math.round((base + jitter) * 10) / 10 });
  }
  // Seed water for the last few days (today gets partial so the row is tappable).
  const water: Record<string, number> = {
    [dateDaysAgo(3)]: 2500,
    [dateDaysAgo(2)]: 2750,
    [dateDaysAgo(1)]: 2250,
    [t]: 1000,
  };
  return {
    profile: {
      name: 'Traveler',
      age: 24,
      sex: 'm',
      height_cm: 175,
      weight_kg: 70,
      activity: 'moderate',
      goal: 'cut',
      body_fat_pct: 20,
      body_fat_goal_pct: 15,
      weight_goal_kg: 65,
      pace_kg_per_week: 0.5,
    },
    water,
    weights,
    favorites: ['paneer wrap + curd'],
    meals: [
      {
        id: 'm1',
        dish: 'Masala oats with almonds',
        calories: 320,
        protein_g: 12,
        carbs_g: 48,
        fat_g: 9,
        eaten_at: `${t}T08:15:00.000Z`,
        source: 'photo',
      },
      {
        id: 'm2',
        dish: 'Paneer wrap + curd',
        calories: 610,
        protein_g: 28,
        carbs_g: 52,
        fat_g: 30,
        eaten_at: `${t}T13:05:00.000Z`,
        source: 'photo',
        fiber_g: 6,
        sugar_g: 9,
        sodium_mg: 720,
        favorite: true,
      },
      {
        id: 'm3',
        dish: 'Filter coffee + 2 marie biscuits',
        calories: 140,
        protein_g: 3,
        carbs_g: 22,
        fat_g: 5,
        eaten_at: `${t}T16:40:00.000Z`,
        source: 'manual',
      },
    ],
    workouts: [
      { date: t, kind: 'Walk', minutes: 35, calories_burned: 160, note: 'Evening loop' },
    ],
  };
}

function load(): DemoState {
  try {
    const raw = localStorage.getItem(DEMO_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<DemoState>;
      // Backfill v3 fields for demo sessions saved before v3 (water/weights/
      // favorites) so nothing crashes on a stale localStorage blob.
      if (!parsed.water || !parsed.weights || !parsed.favorites) {
        const fresh = seedState();
        const merged: DemoState = {
          profile: { ...fresh.profile, ...(parsed.profile ?? {}) },
          meals: parsed.meals ?? fresh.meals,
          workouts: parsed.workouts ?? fresh.workouts,
          water: parsed.water ?? fresh.water,
          weights: parsed.weights ?? fresh.weights,
          favorites: parsed.favorites ?? fresh.favorites,
        };
        save(merged);
        return merged;
      }
      return parsed as DemoState;
    }
  } catch {
    /* fall through to seed */
  }
  const s = seedState();
  save(s);
  return s;
}

function save(s: DemoState): void {
  localStorage.setItem(DEMO_KEY, JSON.stringify(s));
}

// Deterministic fake analyzer keyed off blob size + hint, so the same photo +
// hint returns a stable answer (mirrors the server's hash-keyed mock).
const DISHES: AnalyzeResult[] = [
  {
    // A full North-Indian thali — the multi-item case API.md calls out.
    dish: 'North Indian thali (dal, rice, roti, sabzi, curd)',
    calories: 780,
    protein_g: 26,
    carbs_g: 108,
    fat_g: 24,
    fiber_g: 14,
    sugar_g: 11,
    sodium_mg: 1240,
    confidence: 0.72,
    question: 'Was there ghee brushed on the rotis?',
    items: [
      { name: 'Dal tadka', calories: 180, protein_g: 9, carbs_g: 24, fat_g: 6, point: { x: 0.28, y: 0.34 } },
      { name: 'Steamed rice', calories: 210, protein_g: 4, carbs_g: 46, fat_g: 1, point: { x: 0.7, y: 0.32 } },
      { name: 'Roti ×2', calories: 220, protein_g: 7, carbs_g: 40, fat_g: 4, point: { x: 0.5, y: 0.62 } },
      { name: 'Aloo sabzi', calories: 130, protein_g: 3, carbs_g: 16, fat_g: 7, point: { x: 0.76, y: 0.6 } },
      { name: 'Curd', calories: 90, protein_g: 5, carbs_g: 7, fat_g: 5, point: { x: 0.24, y: 0.64 } },
      { name: 'Pickle + papad', calories: 50, protein_g: 1, carbs_g: 6, fat_g: 3, point: { x: 0.5, y: 0.2 } },
    ],
  },
  {
    dish: 'Paneer butter masala with 2 rotis',
    calories: 640,
    protein_g: 24,
    carbs_g: 58,
    fat_g: 32,
    confidence: 0.78,
    question: 'Was the paneer portion closer to 100g or 200g?',
    items: [
      { name: 'Paneer butter masala', calories: 420, protein_g: 17, carbs_g: 18, fat_g: 28, point: { x: 0.38, y: 0.42 } },
      { name: 'Roti ×2', calories: 220, protein_g: 7, carbs_g: 40, fat_g: 4, point: { x: 0.68, y: 0.6 } },
    ],
  },
  {
    dish: 'Chicken biryani (single plate)',
    calories: 720,
    protein_g: 34,
    carbs_g: 82,
    fat_g: 26,
    confidence: 0.88,
    question: null,
    items: [
      { name: 'Chicken biryani', calories: 640, protein_g: 32, carbs_g: 74, fat_g: 22, point: { x: 0.46, y: 0.46 } },
      { name: 'Raita', calories: 80, protein_g: 2, carbs_g: 8, fat_g: 4, point: { x: 0.76, y: 0.68 } },
    ],
  },
  {
    dish: 'Masala dosa with sambar & chutney',
    calories: 480,
    protein_g: 11,
    carbs_g: 66,
    fat_g: 18,
    confidence: 0.82,
    question: 'One dosa or two?',
    items: [
      { name: 'Masala dosa', calories: 360, protein_g: 8, carbs_g: 48, fat_g: 15, point: { x: 0.5, y: 0.44 } },
      { name: 'Sambar + chutney', calories: 120, protein_g: 3, carbs_g: 18, fat_g: 3, point: { x: 0.24, y: 0.66 } },
    ],
  },
  {
    dish: 'Greek yogurt bowl with berries & granola',
    calories: 310,
    protein_g: 20,
    carbs_g: 38,
    fat_g: 8,
    confidence: 0.9,
    question: null,
    items: [
      { name: 'Greek yogurt', calories: 150, protein_g: 15, carbs_g: 12, fat_g: 4 },
      { name: 'Berries + granola', calories: 160, protein_g: 5, carbs_g: 26, fat_g: 4 },
    ],
  },
];

// Deterministic mock packaged-food products for barcode demo mode.
const DEMO_PRODUCTS: Record<string, BarcodeResult> = {
  '8901058000290': {
    found: true,
    code: '8901058000290',
    product: {
      name: 'Maggi Masala Noodles',
      brand: 'Nestlé',
      serving: '70 g',
      per_serving: { calories: 313, protein_g: 7, carbs_g: 42, fat_g: 13, fiber_g: 2, sugar_g: 3, sodium_mg: 760 },
      per_100g: { calories: 447, protein_g: 10, carbs_g: 60, fat_g: 18, fiber_g: 3, sugar_g: 4, sodium_mg: 1086 },
    },
  },
  '8901491101837': {
    found: true,
    code: '8901491101837',
    product: {
      name: 'Lay’s Classic Salted',
      brand: 'PepsiCo',
      serving: '52 g',
      per_serving: { calories: 279, protein_g: 3, carbs_g: 31, fat_g: 16 },
      per_100g: { calories: 536, protein_g: 6, carbs_g: 60, fat_g: 31 },
    },
  },
  '8901719110018': {
    found: true,
    code: '8901719110018',
    product: {
      name: 'Amul Taaza Toned Milk',
      brand: 'Amul',
      serving: '200 ml',
      per_serving: { calories: 116, protein_g: 6, carbs_g: 10, fat_g: 6 },
      per_100g: { calories: 58, protein_g: 3, carbs_g: 5, fat_g: 3 },
    },
  },
};
const DEMO_PRODUCT_CODES = Object.keys(DEMO_PRODUCTS);

function delay<T>(value: T, ms = 320): Promise<T> {
  return new Promise((res) => setTimeout(() => res(value), ms));
}

// Profile + computed targets + v3 eta_date, mirroring GET /profile.
function withTargets(p: Profile): ProfileWithTargets {
  return { ...p, targets: computeTargets(p), eta_date: computeEtaDate(p) };
}

export function createDemoClient(): HarahachiClient {
  return {
    isDemo: true,

    async health(): Promise<HealthResponse> {
      return delay({ status: 'ok', vllm: 'mock', model: 'demo-vision-mock' }, 120);
    },

    async analyzeMeal(image: Blob, hint?: string, portion?: Portion): Promise<AnalyzeResult> {
      const seed = (image.size % 7) + (hint ? hint.length % 3 : 0);
      const base = DISHES[seed % DISHES.length];
      const scale = portion === 'small' ? 0.75 : portion === 'large' ? 1.35 : 1;
      const result: AnalyzeResult = {
        ...base,
        dish: hint ? `${base.dish} (${hint})` : base.dish,
        calories: Math.round(base.calories * scale),
        protein_g: Math.round(base.protein_g * scale),
        carbs_g: Math.round(base.carbs_g * scale),
        fat_g: Math.round(base.fat_g * scale),
      };
      return delay(result, 900);
    },

    async lookupBarcode(code: string): Promise<BarcodeResult> {
      const hit = DEMO_PRODUCTS[code];
      if (hit) return delay(hit, 500);
      // Unknown-in-table codes: map a couple deterministically to a known
      // product so a scan in demo almost always resolves; leave the rest as
      // found:false to exercise the manual-entry path.
      if (/^\d{8,14}$/.test(code)) {
        const idx = code.split('').reduce((a, c) => a + c.charCodeAt(0), 0);
        if (idx % 3 !== 0) {
          const pick = DEMO_PRODUCTS[DEMO_PRODUCT_CODES[idx % DEMO_PRODUCT_CODES.length]];
          return delay({ ...pick, code }, 500);
        }
      }
      return delay({ found: false, code }, 500);
    },

    async logMeal(meal: MealInput): Promise<Meal> {
      const s = load();
      const stored: Meal = {
        id: 'm_' + Math.random().toString(36).slice(2, 8),
        eaten_at: meal.eaten_at ?? isoNow(),
        ...meal,
      };
      s.meals.push(stored);
      save(s);
      return delay(stored, 200);
    },

    async getMeals(date: string): Promise<MealsResponse> {
      const s = load();
      const favSet = new Set(s.favorites);
      const meals = s.meals
        .filter((m) => m.eaten_at.slice(0, 10) === date)
        .map((m) => ({ ...m, favorite: m.favorite ?? favSet.has(m.dish.toLowerCase()) }));
      const totals = meals.reduce(
        (a, m) => ({
          calories: a.calories + m.calories,
          protein_g: a.protein_g + m.protein_g,
          carbs_g: a.carbs_g + m.carbs_g,
          fat_g: a.fat_g + m.fat_g,
          fiber_g: a.fiber_g + (m.fiber_g ?? 0),
          sugar_g: a.sugar_g + (m.sugar_g ?? 0),
          sodium_mg: a.sodium_mg + (m.sodium_mg ?? 0),
        }),
        { calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0, fiber_g: 0, sugar_g: 0, sodium_mg: 0 },
      );
      const burned = s.workouts
        .filter((w) => w.date === date)
        .reduce((a, w) => a + w.calories_burned, 0);
      const water_ml = s.water[date] ?? 0;
      return delay(
        {
          meals,
          totals: { ...totals, burned, water_ml, water_target_ml: waterTarget(s.profile) },
        },
        180,
      );
    },

    async deleteMeal(id: string): Promise<void> {
      const s = load();
      s.meals = s.meals.filter((m) => m.id !== id);
      save(s);
      await delay(null, 120);
    },

    async getSuggestions(date: string): Promise<SuggestionsResponse> {
      const s = load();
      const targets = computeTargets(s.profile);
      const { totals } = await this.getMeals(date);
      const remaining = {
        calories: Math.max(0, targets.calories - totals.calories),
        protein_g: Math.max(0, targets.protein_g - totals.protein_g),
        carbs_g: Math.max(0, targets.carbs_g - totals.carbs_g),
        fat_g: Math.max(0, targets.fat_g - totals.fat_g),
      };
      const suggestions = [
        {
          dish: 'Dal tadka + 2 roti',
          calories: 420,
          protein_g: 18,
          carbs_g: 60,
          fat_g: 10,
          reason: `fits your remaining ${remaining.calories} kcal and adds ${18}g protein`,
        },
        {
          dish: 'Grilled chicken salad',
          calories: 350,
          protein_g: 34,
          carbs_g: 14,
          fat_g: 16,
          reason: `high-protein close to your ${remaining.protein_g}g protein gap`,
        },
        {
          dish: 'Moong chaat bowl',
          calories: 260,
          protein_g: 14,
          carbs_g: 34,
          fat_g: 6,
          reason: 'a light option you logged twice last week',
        },
      ].filter((x) => x.calories <= Math.max(260, remaining.calories + 120));
      return delay({ remaining, suggestions }, 260);
    },

    async getProfile(): Promise<ProfileWithTargets> {
      const s = load();
      return delay(withTargets(s.profile), 140);
    },

    async putProfile(profile: Profile): Promise<ProfileWithTargets> {
      const s = load();
      s.profile = profile;
      save(s);
      return delay(withTargets(profile), 160);
    },

    async logWorkout(workout: Workout): Promise<Workout> {
      const s = load();
      const stored = { ...workout, date: today(), id: 'w_' + Math.random().toString(36).slice(2, 8) };
      s.workouts.push(stored);
      save(s);
      return delay(stored, 180);
    },

    async getWorkouts(date: string): Promise<WorkoutsResponse> {
      const s = load();
      return delay({ workouts: s.workouts.filter((w) => w.date === date) }, 140);
    },

    async getCalendar(month: string): Promise<CalendarResponse> {
      const s = load();
      const targets = computeTargets(s.profile);
      const [y, m] = month.split('-').map(Number);
      const daysInMonth = new Date(y, m, 0).getDate();
      const todayStr = today();
      const days = [];
      for (let d = 1; d <= daysInMonth; d++) {
        const date = `${month}-${String(d).padStart(2, '0')}`;
        const dayMeals = s.meals.filter((x) => x.eaten_at.slice(0, 10) === date);
        let calories = dayMeals.reduce((a, x) => a + x.calories, 0);
        const burned = s.workouts
          .filter((w) => w.date === date)
          .reduce((a, w) => a + w.calories_burned, 0);
        // Seed a plausible history for past days that have no logged data.
        if (calories === 0 && date < todayStr) {
          const pseudo = 1500 + ((d * 137) % 1100);
          calories = pseudo;
        }
        let status: 'under' | 'on' | 'over' | 'empty' = 'empty';
        if (calories > 0 || dayMeals.length > 0) {
          const net = calories - burned;
          const ratio = net / targets.calories;
          status = ratio > 1.08 ? 'over' : ratio < 0.85 ? 'under' : 'on';
        }
        if (date > todayStr) {
          calories = 0;
          status = 'empty';
        }
        days.push({ date, calories, target: targets.calories, burned, status });
      }
      return delay({ days }, 220);
    },

    // ---- v2: accounts (canned demo account) ----

    async register(input: RegisterInput): Promise<AuthResponse> {
      const s = load();
      s.profile = input.profile;
      save(s);
      const user: User = { id: 'demo-user', username: input.username || 'traveler', is_admin: true };
      return delay({ user, profile: withTargets(s.profile) }, 200);
    },

    async login(username: string): Promise<AuthResponse> {
      const s = load();
      const user: User = { id: 'demo-user', username: username || 'traveler', is_admin: true };
      return delay({ user, profile: withTargets(s.profile) }, 200);
    },

    async logout(): Promise<void> {
      await delay(null, 120);
    },

    async me(): Promise<AuthResponse> {
      const s = load();
      return delay({ user: DEMO_USER, profile: withTargets(s.profile) }, 120);
    },

    // ---- v2: body-photo estimate (deterministic mock) ----

    async estimateBodyPhoto(image: Blob): Promise<BodyPhotoEstimate> {
      // Deterministic off the blob size so the same photo returns a stable %.
      const pct = 14 + (image.size % 16); // 14..29
      const lo = Math.max(6, pct - 3);
      const hi = Math.min(45, pct + 3);
      return delay(
        {
          body_fat_pct: pct,
          range: [lo, hi],
          confidence: 0.66,
          notes: 'Estimate from a single photo is approximate; adjust if you have a caliper or DEXA number.',
        },
        1100,
      );
    },

    // ---- v2: assistant chat (canned coach + "I ate X" parser) ----

    async chat(messages: ChatMessage[]): Promise<ChatResponse> {
      const last = [...messages].reverse().find((m) => m.role === 'user');
      const text = last?.content ?? '';
      const lower = text.toLowerCase();

      // Try to log a meal from natural language ("I ate 2 idlis and sambar").
      const parsed = /\b(ate|had|eat|log|logged)\b/.test(lower) || /^\s*\d/.test(lower)
        ? parseAteMessage(text)
        : null;
      if (parsed) {
        const s = load();
        const stored: Meal = { id: 'm_' + Math.random().toString(36).slice(2, 8), eaten_at: isoNow(), ...parsed };
        s.meals.push(stored);
        save(s);
        const { totals } = await this.getMeals(today());
        const targets = computeTargets(s.profile);
        const remaining = Math.max(0, targets.calories - totals.calories);
        return delay(
          {
            reply: `Logged ${parsed.dish} (${parsed.calories} kcal). You have about ${remaining} kcal left on today's road.`,
            action: {
              action: 'log_meal',
              dish: parsed.dish,
              calories: parsed.calories,
              protein_g: parsed.protein_g,
              carbs_g: parsed.carbs_g,
              fat_g: parsed.fat_g,
            },
          },
          650,
        );
      }

      // Canned coach replies keyed off intent.
      let reply: string;
      if (/(what.?s left|left today|remaining|how much)/.test(lower)) {
        const s = load();
        const { totals } = await this.getMeals(today());
        const targets = computeTargets(s.profile);
        const remaining = Math.max(0, targets.calories - totals.calories);
        const p = Math.max(0, targets.protein_g - totals.protein_g);
        reply = `You've got about ${remaining} kcal and ${p}g protein left today. A bowl of dal with 2 rotis would fit nicely.`;
      } else if (/(what should i eat|dinner|tonight|suggest)/.test(lower)) {
        // A "what should I eat?" intent SUGGESTS a meal but does NOT log it —
        // emit a PROPOSED action so Annai shows a confirm chip that logs only on
        // tap (mirrors the server's action.proposed:true). Pick from the
        // day's suggestions so the macros are real.
        const sug = await this.getSuggestions(today());
        const pick = sug.suggestions[0];
        reply = `For tonight, grilled paneer or chicken with a big salad keeps protein high and calories in check${
          pick ? ` — ${pick.dish} (${pick.calories} kcal) would fit your road` : ''
        }. Want me to log it?`;
        return delay(
          {
            reply,
            action: pick
              ? {
                  action: 'log_meal',
                  proposed: true,
                  dish: pick.dish,
                  calories: pick.calories,
                  protein_g: pick.protein_g,
                  carbs_g: pick.carbs_g,
                  fat_g: pick.fat_g,
                }
              : null,
          },
          600,
        );
      } else if (/(how|work|use|app|help|station)/.test(lower)) {
        reply = `Tap a seal on the map to travel: Honjin 本陣 is home, Chaya 茶屋 is the camera, Dōjō 道場 logs workouts, Koyomi 暦 is your calendar, Tegata 手形 is your permit. Snap a dish at Chaya and stamp the red seal to log it. Just tell me "I ate ..." and I'll log it here.`;
      } else {
        reply = `I'm Annai, your road guide. Tell me what you ate ("I ate 2 idlis and sambar"), ask what's left today, or ask what to eat tonight.`;
      }
      return delay({ reply, action: null }, 550);
    },

    // ---- v2: push (no-op stubs in demo) ----

    async vapidPublicKey(): Promise<string> {
      // A syntactically valid base64url VAPID public key so subscribe() can run
      // in demo without a server (the browser still won't push, but the toggle
      // flow is exercisable end-to-end).
      return delay('BP_demo_key_not_used_for_real_delivery_padding_padding_padding0', 120);
    },

    async subscribePush(): Promise<void> {
      await delay(null, 150);
    },

    async unsubscribePush(): Promise<void> {
      await delay(null, 120);
    },

    // ---- v3: the feedback loop ----

    async logWater(ml: number): Promise<{ date: string; water_ml: number; water_target_ml: number }> {
      const s = load();
      const t = today();
      const next = Math.max(0, (s.water[t] ?? 0) + ml);
      s.water[t] = next;
      save(s);
      return delay({ date: t, water_ml: next, water_target_ml: waterTarget(s.profile) }, 140);
    },

    async logWeight(weightKg: number, date?: string): Promise<WeightEntry> {
      const s = load();
      const d = date ?? today();
      const existing = s.weights.find((w) => w.date === d);
      if (existing) existing.weight_kg = weightKg;
      else s.weights.push({ date: d, weight_kg: weightKg });
      s.weights.sort((a, b) => a.date.localeCompare(b.date));
      save(s);
      // POST /weight returns just the stored entry; the UI re-fetches getWeight().
      return delay({ date: d, weight_kg: weightKg }, 180);
    },

    async getWeight(from: string, to: string): Promise<WeightResponse> {
      const s = load();
      const entries = s.weights.filter((w) => w.date >= from && w.date <= to);
      return delay(buildWeightResponse(entries), 160);
    },

    async getStreaks(): Promise<StreaksResponse> {
      const s = load();
      return delay(buildStreaks(s), 200);
    },

    async getInsights(week: string): Promise<InsightsResponse> {
      const s = load();
      return delay(buildInsights(s, week), 260);
    },

    async getRecentMeals(): Promise<RecentMealsResponse> {
      const s = load();
      return delay({ recent: buildRecent(s) }, 200);
    },

    async favoriteMeal(id: string): Promise<void> {
      const s = load();
      const m = s.meals.find((x) => x.id === id);
      const dish = m?.dish.toLowerCase();
      // id may be a recent-list signature id ("r_<dish>"); fall back to that.
      const sig = dish ?? id.replace(/^r_/, '');
      if (!s.favorites.includes(sig)) s.favorites.push(sig);
      if (m) m.favorite = true;
      save(s);
      await delay(null, 120);
    },

    async unfavoriteMeal(id: string): Promise<void> {
      const s = load();
      const m = s.meals.find((x) => x.id === id);
      const sig = m?.dish.toLowerCase() ?? id.replace(/^r_/, '');
      s.favorites = s.favorites.filter((f) => f !== sig);
      s.meals.forEach((x) => {
        if (x.dish.toLowerCase() === sig) x.favorite = false;
      });
      save(s);
      await delay(null, 120);
    },

    async exportCsv(): Promise<Blob> {
      const s = load();
      return delay(new Blob([buildCsv(s)], { type: 'text/csv' }), 200);
    },

    // ---- Checkpoint keeper (admin) stubs ----

    async keeperPair(): Promise<KeeperPairResponse> {
      const token = 'DEMO-' + Math.random().toString(36).slice(2, 8).toUpperCase();
      return delay(
        {
          token,
          url: `${DEMO_PUBLIC_URL}/pair#${token}`,
          qr_png_base64: fakeQrPngBase64(token),
          expires_in: 300, // 5 minutes
        },
        260,
      );
    },

    async keeperOverview(): Promise<KeeperOverview> {
      const s = load();
      const self = withTargets(s.profile);
      const { totals } = await this.getMeals(today());
      const users = [
        {
          id: 'demo-user',
          username: 'traveler',
          today_kcal: Math.round(totals.calories),
          target_kcal: self.targets.calories,
          streak: buildStreaks(s).current,
          last_activity: isoNow(0),
        },
        {
          id: 'u_sora',
          username: 'sora',
          today_kcal: 1180,
          target_kcal: 1900,
          streak: 4,
          last_activity: isoNow(3),
        },
        {
          id: 'u_kenji',
          username: 'kenji',
          today_kcal: 2360,
          target_kcal: 2200,
          streak: 0,
          last_activity: isoNow(28),
        },
      ];
      const devices = keeperDevices();
      return delay(
        {
          users,
          devices,
          backend_mode: activeBackendLabel(),
          push_subs: 2,
          public_url: DEMO_PUBLIC_URL,
        },
        240,
      );
    },

    async keeperUser(id: string): Promise<KeeperUserDetail> {
      const s = load();
      if (id === 'demo-user') {
        return delay({ profile: withTargets(s.profile), recent_meals: [...s.meals].reverse().slice(0, 6) }, 220);
      }
      return delay(FAKE_USER_DETAIL[id] ?? FAKE_USER_DETAIL.u_sora, 220);
    },

    async keeperRevokeDevice(id: string): Promise<void> {
      const list = loadKeeper();
      list.revoked.push(id);
      saveKeeper(list);
      await delay(null, 160);
    },

    async keeperBackends(): Promise<KeeperBackendsResponse> {
      return delay({ backends: keeperBackends() }, 200);
    },

    async keeperSetBackend(id: string, role: BackendRole): Promise<void> {
      const list = loadKeeper();
      if (role === 'vision') list.activeVision = id;
      else list.activeText = id;
      saveKeeper(list);
      await delay(null, 200);
    },
  };
}

// ---- Keeper demo fixtures ----

const DEMO_PUBLIC_URL = 'https://sekisho.demo.local';

// Persisted keeper state (which backend is active per role, revoked devices) so
// switches + revokes survive a reload in the demo, like the rest of demo state.
const KEEPER_KEY = 'hh.demoKeeper';
interface KeeperDemoState {
  activeVision: string;
  activeText: string;
  revoked: string[];
}
function loadKeeper(): KeeperDemoState {
  try {
    const raw = localStorage.getItem(KEEPER_KEY);
    if (raw) return JSON.parse(raw) as KeeperDemoState;
  } catch {
    /* fall through */
  }
  const fresh: KeeperDemoState = { activeVision: 'b_vllm_vision', activeText: 'b_vllm_text', revoked: [] };
  saveKeeper(fresh);
  return fresh;
}
function saveKeeper(s: KeeperDemoState): void {
  localStorage.setItem(KEEPER_KEY, JSON.stringify(s));
}

function activeBackendLabel(): string {
  const s = loadKeeper();
  const v = ALL_BACKENDS.find((b) => b.id === s.activeVision);
  return v ? v.kind : 'vllm';
}

function keeperBackends(): KeeperBackend[] {
  const s = loadKeeper();
  return ALL_BACKENDS.map((b) => ({
    ...b,
    active: b.role === 'vision' ? b.id === s.activeVision : b.id === s.activeText,
  }));
}

const ALL_BACKENDS: KeeperBackend[] = [
  { id: 'b_vllm_vision', name: 'vLLM · Qwen2-VL-7B', kind: 'vllm', role: 'vision', active: true },
  { id: 'b_nvidia_vision', name: 'NVIDIA NIM · llama-3.2-vision', kind: 'nvidia', role: 'vision', active: false },
  { id: 'b_vllm_text', name: 'vLLM · Qwen2.5-7B', kind: 'vllm', role: 'text', active: true },
  { id: 'b_lmstudio_text', name: 'LM Studio · local', kind: 'lmstudio', role: 'text', active: false },
  { id: 'b_openai_text', name: 'OpenAI · gpt-4o-mini', kind: 'openai', role: 'text', active: false },
];

function keeperDevices() {
  const s = loadKeeper();
  const all = [
    { id: 'd_pixel', name: 'Pixel 8 (traveler)', last_seen: isoNow(0), active_user: 'traveler' },
    { id: 'd_ipad', name: 'iPad · kitchen', last_seen: isoNow(9), active_user: 'sora' },
  ];
  return all.filter((d) => !s.revoked.includes(d.id));
}

const FAKE_USER_DETAIL: Record<string, KeeperUserDetail> = {
  u_sora: {
    profile: withTargets({
      name: 'Sora',
      age: 29,
      sex: 'f',
      height_cm: 164,
      weight_kg: 58,
      activity: 'light',
      goal: 'maintain',
    }),
    recent_meals: [
      { id: 'sm1', dish: 'Miso salmon + rice', calories: 540, protein_g: 34, carbs_g: 52, fat_g: 18, eaten_at: isoNow(2), source: 'photo' },
      { id: 'sm2', dish: 'Matcha latte', calories: 120, protein_g: 5, carbs_g: 16, fat_g: 4, eaten_at: isoNow(5), source: 'manual' },
      { id: 'sm3', dish: 'Edamame', calories: 130, protein_g: 11, carbs_g: 10, fat_g: 5, eaten_at: isoNow(7), source: 'manual' },
    ],
  },
  u_kenji: {
    profile: withTargets({
      name: 'Kenji',
      age: 41,
      sex: 'm',
      height_cm: 178,
      weight_kg: 84,
      activity: 'sedentary',
      goal: 'cut',
    }),
    recent_meals: [
      { id: 'km1', dish: 'Katsu curry', calories: 980, protein_g: 38, carbs_g: 110, fat_g: 40, eaten_at: isoNow(1), source: 'photo' },
      { id: 'km2', dish: 'Beer (2)', calories: 300, protein_g: 0, carbs_g: 24, fat_g: 0, eaten_at: isoNow(2), source: 'manual' },
      { id: 'km3', dish: 'Ramen', calories: 620, protein_g: 26, carbs_g: 74, fat_g: 22, eaten_at: isoNow(26), source: 'photo' },
    ],
  },
};

// A deterministic pseudo-QR PNG (base64, no data: prefix) drawn on a canvas —
// not a scannable code, just a plausible-looking placeholder for the demo. Real
// pairing QRs come from the server as an actual encoded PNG.
function fakeQrPngBase64(token: string): string {
  const modules = 25;
  const scale = 8;
  const px = modules * scale;
  const canvas = document.createElement('canvas');
  canvas.width = px;
  canvas.height = px;
  const ctx = canvas.getContext('2d');
  if (!ctx) return '';
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, px, px);
  // Deterministic PRNG seeded from the token.
  let h = 0x811c9dc5;
  for (const c of token) h = (Math.imul(h ^ c.charCodeAt(0), 0x01000193) >>> 0);
  const rand = () => {
    h = (Math.imul(h, 1103515245) + 12345) >>> 0;
    return h / 0xffffffff;
  };
  ctx.fillStyle = '#111111';
  for (let y = 0; y < modules; y++) {
    for (let x = 0; x < modules; x++) {
      if (rand() > 0.5) ctx.fillRect(x * scale, y * scale, scale, scale);
    }
  }
  // Three finder patterns in the corners so it reads as a QR at a glance.
  const finder = (ox: number, oy: number) => {
    ctx.fillStyle = '#111111';
    ctx.fillRect(ox, oy, 7 * scale, 7 * scale);
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(ox + scale, oy + scale, 5 * scale, 5 * scale);
    ctx.fillStyle = '#111111';
    ctx.fillRect(ox + 2 * scale, oy + 2 * scale, 3 * scale, 3 * scale);
  };
  finder(0, 0);
  finder((modules - 7) * scale, 0);
  finder(0, (modules - 7) * scale);
  return canvas.toDataURL('image/png').split(',')[1] ?? '';
}

// Build a 7-day moving-average trend over sorted weight entries.
function buildWeightResponse(entries: WeightEntry[]): WeightResponse {
  const sorted = [...entries].sort((a, b) => a.date.localeCompare(b.date));
  const trend = sorted.map((_, i) => {
    const window = sorted.slice(Math.max(0, i - 6), i + 1);
    const avg = window.reduce((a, w) => a + w.weight_kg, 0) / window.length;
    return { date: sorted[i].date, avg_kg: Math.round(avg * 100) / 100 };
  });
  return { entries: sorted, trend };
}

// Streaks + a stamp book: an on-target stamp for each day the deficit landed,
// with 7/30-day milestones and a first-log stamp on the earliest day.
function buildStreaks(s: DemoState): StreaksResponse {
  const targets = computeTargets(s.profile);
  const stamps: Stamp[] = [];
  // Consider the last 35 days; a day earns a stamp when calories ≤ target and
  // ≥ 50% of it. Seed a plausible dense-but-broken history so a streak + a
  // milestone both show.
  const earned: string[] = [];
  for (let i = 34; i >= 0; i--) {
    const date = dateDaysAgo(i);
    const dayMeals = s.meals.filter((m) => m.eaten_at.slice(0, 10) === date);
    let cals = dayMeals.reduce((a, m) => a + m.calories, 0);
    if (cals === 0 && i > 0) {
      // Pseudo history: most days on target, a few over (breaks the streak).
      const n = Math.abs(noise(i * 3.1));
      cals = n > 0.86 ? Math.round(targets.calories * 1.15) : Math.round(targets.calories * (0.6 + n * 0.35));
    }
    const onTarget = cals <= targets.calories && cals >= targets.calories * 0.5;
    if (onTarget) {
      earned.push(date);
      stamps.push({ date, kind: 'on_target' });
    }
  }
  // First-log stamp on the earliest earned day.
  if (earned.length) stamps[0] = { date: earned[0], kind: 'first_log' };
  // Milestone stamps: mark the 7th and 30th earned day.
  if (earned.length >= 7) {
    const idx = stamps.findIndex((st) => st.date === earned[6]);
    if (idx >= 0) stamps[idx] = { date: earned[6], kind: 'milestone_7' };
  }
  if (earned.length >= 30) {
    const idx = stamps.findIndex((st) => st.date === earned[29]);
    if (idx >= 0) stamps[idx] = { date: earned[29], kind: 'milestone_30' };
  }
  // Current streak: trailing run of earned days up to today.
  let current = 0;
  for (let i = 0; ; i++) {
    if (earned.includes(dateDaysAgo(i))) current++;
    else break;
    if (i > 40) break;
  }
  // Best streak across the earned set.
  let best = 0;
  let run = 0;
  const earnedSet = new Set(earned);
  for (let i = 34; i >= 0; i--) {
    if (earnedSet.has(dateDaysAgo(i))) {
      run++;
      best = Math.max(best, run);
    } else run = 0;
  }
  return { current, best: Math.max(best, current), stamps };
}

// Weekly insights: averages, adherence %, best/worst day, weight delta, macro
// split, and an Annai-voiced rule-based summary.
function buildInsights(s: DemoState, week: string): InsightsResponse {
  const targets = computeTargets(s.profile);
  // The 7 days of the ISO-ish week containing `week` (Mon..Sun by simple math).
  const anchor = new Date(week + 'T00:00:00');
  const dow = (anchor.getDay() + 6) % 7; // 0 = Monday
  const monday = new Date(anchor);
  monday.setDate(anchor.getDate() - dow);
  const days: string[] = [];
  for (let i = 0; i < 7; i++) {
    const d = new Date(monday);
    d.setDate(monday.getDate() + i);
    days.push(d.toISOString().slice(0, 10));
  }
  let cal = 0;
  let p = 0;
  let c = 0;
  let f = 0;
  let water = 0;
  let counted = 0;
  let adhered = 0;
  let best: { date: string; note?: string } | null = null;
  let worst: { date: string; note?: string } | null = null;
  let bestScore = Infinity;
  let worstScore = -Infinity;
  for (const date of days) {
    const dayMeals = s.meals.filter((m) => m.eaten_at.slice(0, 10) === date);
    let dcal = dayMeals.reduce((a, m) => a + m.calories, 0);
    if (dcal === 0) {
      const n = Math.abs(noise(days.indexOf(date) * 4.7 + 2));
      dcal = Math.round(targets.calories * (0.7 + n * 0.5));
    }
    const dp = dayMeals.reduce((a, m) => a + m.protein_g, 0) || Math.round(targets.protein_g * 0.8);
    const dc = dayMeals.reduce((a, m) => a + m.carbs_g, 0) || Math.round(targets.carbs_g * 0.85);
    const df = dayMeals.reduce((a, m) => a + m.fat_g, 0) || Math.round(targets.fat_g * 0.9);
    cal += dcal;
    p += dp;
    c += dc;
    f += df;
    water += s.water[date] ?? 2200;
    counted++;
    const over = dcal - targets.calories;
    if (dcal <= targets.calories && dcal >= targets.calories * 0.5) adhered++;
    if (Math.abs(over) < bestScore) {
      bestScore = Math.abs(over);
      best = { date, note: `${Math.round(dcal)} kcal · on target` };
    }
    if (over > worstScore) {
      worstScore = over;
      worst = { date, note: over > 0 ? `${Math.round(dcal)} kcal · +${Math.round(over)} over` : `${Math.round(dcal)} kcal` };
    }
  }
  const n = counted || 1;
  const avgP = Math.round(p / n);
  const avgC = Math.round(c / n);
  const avgF = Math.round(f / n);
  const macroCals = avgP * 4 + avgC * 4 + avgF * 9 || 1;
  const adherence = Math.round((adhered / n) * 100);
  // Weight delta across the week from history.
  const inWeek = s.weights.filter((w) => days.includes(w.date));
  const weightDelta =
    inWeek.length >= 2 ? Math.round((inWeek[inWeek.length - 1].weight_kg - inWeek[0].weight_kg) * 10) / 10 : null;
  const summary =
    `You held to your road ${adherence}% of the week — averaging ${Math.round(cal / n)} kcal a day. ` +
    (weightDelta != null && weightDelta < 0
      ? `The scale eased ${Math.abs(weightDelta)} kg; steady steps. `
      : weightDelta != null && weightDelta > 0
        ? `The scale nudged up ${weightDelta} kg — one heavier day, no alarm. `
        : `Weight held steady. `) +
    `Protein stayed strong at ${avgP}g. Keep walking; 八分目 — eighty percent full.`;
  return {
    week_start: days[0],
    week_end: days[days.length - 1],
    days_logged: counted,
    target_calories: targets.calories,
    averages: { calories: Math.round(cal / n), protein_g: avgP, carbs_g: avgC, fat_g: avgF, water_ml: Math.round(water / n) },
    adherence_pct: adherence,
    best_day: best,
    worst_day: worst,
    weight_delta_kg: weightDelta,
    macro_split_pct: {
      protein: Math.round(((avgP * 4) / macroCals) * 100),
      carbs: Math.round(((avgC * 4) / macroCals) * 100),
      fat: Math.round(((avgF * 9) / macroCals) * 100),
    },
    summary,
  };
}

// Recent quick-log list: favorites first, then most-frequent recent dishes,
// deduped by dish name, ≤20.
function buildRecent(s: DemoState): RecentMeal[] {
  const favSet = new Set(s.favorites);
  const byDish = new Map<string, { meal: Meal; count: number }>();
  // Newest first so the representative row keeps the latest macros.
  const ordered = [...s.meals].sort((a, b) => b.eaten_at.localeCompare(a.eaten_at));
  for (const m of ordered) {
    const key = m.dish.toLowerCase();
    const cur = byDish.get(key);
    if (cur) cur.count++;
    else byDish.set(key, { meal: m, count: 1 });
  }
  // A couple of canned "road" dishes so a fresh demo still has a carousel.
  const canned: Meal[] = [
    { id: 'seed-idli', dish: 'Idli x3 + sambar', calories: 280, protein_g: 8, carbs_g: 54, fat_g: 3, eaten_at: isoNow(48), source: 'manual', fiber_g: 5, sugar_g: 4, sodium_mg: 480 },
    { id: 'seed-egg', dish: 'Egg bhurji + 2 toast', calories: 360, protein_g: 20, carbs_g: 28, fat_g: 18, eaten_at: isoNow(72), source: 'manual' },
    { id: 'seed-poha', dish: 'Poha with peanuts', calories: 300, protein_g: 7, carbs_g: 50, fat_g: 8, eaten_at: isoNow(96), source: 'manual', fiber_g: 4 },
  ];
  for (const m of canned) {
    const key = m.dish.toLowerCase();
    if (!byDish.has(key)) byDish.set(key, { meal: m, count: 1 });
  }
  const list = [...byDish.values()].map(({ meal, count }) => ({
    // `id` is a demo-only convenience (dish signature) so demo favoriting can
    // persist; the real server dedups by dish and omits it.
    id: 'r_' + meal.dish.toLowerCase(),
    dish: meal.dish,
    calories: meal.calories,
    protein_g: meal.protein_g,
    carbs_g: meal.carbs_g,
    fat_g: meal.fat_g,
    favorite: (meal.favorite ?? false) || favSet.has(meal.dish.toLowerCase()),
    fiber_g: meal.fiber_g,
    sugar_g: meal.sugar_g,
    sodium_mg: meal.sodium_mg,
    last_eaten_at: meal.eaten_at,
    count,
  }));
  // Favorites first, then by frequency.
  list.sort((a, b) => (Number(b.favorite) - Number(a.favorite)) || b.count - a.count);
  return list.slice(0, 20);
}

// CSV of everything: meals, workouts, weights, water.
function buildCsv(s: DemoState): string {
  const rows: string[] = [];
  const esc = (v: string | number) => {
    const str = String(v);
    return /[",\n]/.test(str) ? `"${str.replace(/"/g, '""')}"` : str;
  };
  rows.push('type,date,dish_or_kind,calories,protein_g,carbs_g,fat_g,extra');
  for (const m of s.meals) {
    rows.push(['meal', m.eaten_at, m.dish, m.calories, m.protein_g, m.carbs_g, m.fat_g, m.source].map(esc).join(','));
  }
  for (const w of s.workouts) {
    rows.push(['workout', w.date, w.kind, w.calories_burned, '', '', '', `${w.minutes} min`].map(esc).join(','));
  }
  for (const w of s.weights) {
    rows.push(['weight', w.date, '', '', '', '', '', `${w.weight_kg} kg`].map(esc).join(','));
  }
  for (const [date, ml] of Object.entries(s.water)) {
    rows.push(['water', date, '', '', '', '', '', `${ml} ml`].map(esc).join(','));
  }
  return rows.join('\n') + '\n';
}

export function resetDemo(): void {
  localStorage.removeItem(DEMO_KEY);
  localStorage.removeItem(KEEPER_KEY);
}
