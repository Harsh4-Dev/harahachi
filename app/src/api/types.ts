// Types mirroring the Sekisho API contract (see API.md).

export type Sex = 'm' | 'f' | 'x';
export type Activity = 'sedentary' | 'light' | 'moderate' | 'active';
export type Goal = 'cut' | 'maintain' | 'bulk';
export type Portion = 'small' | 'regular' | 'large';
// v6: barcode | quicklog | chat let the ops console split meals by entry path.
export type MealSource = 'photo' | 'manual' | 'suggestion' | 'barcode' | 'quicklog' | 'chat';
export type DayStatus = 'under' | 'on' | 'over' | 'empty';

export interface Macros {
  calories: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
}

// v3 extended nutrients (fiber/sugar/sodium) — always optional so the big three
// never depend on them. Rendered as tertiary "more" chips.
export interface ExtraNutrients {
  fiber_g?: number;
  sugar_g?: number;
  sodium_mg?: number;
}

export interface Profile {
  name: string;
  age: number;
  sex: Sex;
  height_cm: number;
  weight_kg: number;
  activity: Activity;
  goal: Goal;
  // Body composition (API.md v2). When body_fat_pct is set, targets switch from
  // Mifflin-St Jeor to Katch-McArdle. null/undefined = not provided.
  body_fat_pct?: number | null;
  body_fat_goal_pct?: number | null;
  // Body composition (API.md v3). Goal weight + weekly pace drive the calorie
  // deficit/surplus (7700 kcal ≈ 1 kg) instead of the flat ±20/10%.
  weight_goal_kg?: number | null;
  pace_kg_per_week?: number | null;
}

export interface ProfileWithTargets extends Profile {
  targets: Macros;
  // v3: projected date to reach weight_goal_kg at pace_kg_per_week (ISO date or
  // null when no goal/pace set).
  eta_date?: string | null;
}

// ---- Accounts (API.md v2) ----

export interface User {
  id: string;
  username: string;
  // Checkpoint keeper (admin) flag. GET /auth/me sets this true for the keeper
  // account; the app reveals the admin "Checkpoint keeper" card only then. The
  // server enforces every /keeper/* endpoint regardless (403 {code:"not_keeper"}).
  is_admin?: boolean;
}

export interface RegisterInput {
  username: string;
  pin: string;
  profile: Profile;
}

export interface AuthResponse {
  user: User;
  profile?: ProfileWithTargets;
}

// ---- Body-photo estimate (POST /profile/body-photo) ----

export interface BodyPhotoEstimate {
  body_fat_pct: number;
  range: [number, number];
  confidence: number;
  notes: string;
}

// ---- Assistant chat (POST /chat v2) ----

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

// The server may embed ONE executed action; it strips it from the reply and
// returns it here (already executed server-side).
export interface ChatAction {
  action: 'log_meal' | 'log_workout' | 'set_goal';
  // v3: when true, the server (or demo) SUGGESTED this action for a "what should
  // I eat?" intent but did NOT execute it. The app renders a confirm chip that
  // logs only on tap. Absent/false → already executed server-side ("I ate X").
  proposed?: boolean;
  dish?: string;
  calories?: number;
  protein_g?: number;
  carbs_g?: number;
  fat_g?: number;
  kind?: string;
  minutes?: number;
  calories_burned?: number;
  goal?: Goal;
  // The real server nests the executed record under these keys (API.md v2);
  // the flat fields above are what the demo client emits.
  meal?: { dish?: string; calories?: number; protein_g?: number; carbs_g?: number; fat_g?: number };
  workout?: { kind?: string; minutes?: number; calories_burned?: number };
}

export interface ChatResponse {
  reply: string;
  action: ChatAction | null;
}

// ---- Push (API.md v2) ----

export interface VapidKeyResponse {
  key: string;
}

// One component of a plate. API.md requires per-item macros for complex/Indian
// meals (dal + rice + 2 roti + curd …); protein/carbs/fat are optional so older
// or partial payloads still typecheck.
export interface AnalyzeItem {
  name: string;
  calories: number;
  protein_g?: number;
  carbs_g?: number;
  fat_g?: number;
  fiber_g?: number;
  sugar_g?: number;
  sodium_mg?: number;
  // Approximate normalized center of this component in the image (0..1 in each
  // axis), so the app can pin the label next to its part of the dish. Optional:
  // the vision model omits it when it can't ground the component reliably.
  point?: { x: number; y: number };
}

export interface AnalyzeResult {
  dish: string;
  calories: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
  confidence: number;
  question: string | null;
  items: AnalyzeItem[];
  // v3 extended nutrients (optional).
  fiber_g?: number;
  sugar_g?: number;
  sodium_mg?: number;
}

// GET /food/barcode/{code} (see API.md). OpenFoodFacts-normalized packaged food.
export interface BarcodeNutrition {
  calories: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
  // v3 extended nutrients (optional; OFF often carries these).
  fiber_g?: number;
  sugar_g?: number;
  sodium_mg?: number;
}

export interface BarcodeProduct {
  name: string;
  brand: string;
  serving: string;
  per_serving: BarcodeNutrition;
  per_100g?: BarcodeNutrition;
}

export interface BarcodeResult {
  found: boolean;
  code: string;
  product?: BarcodeProduct;
}

export interface Meal {
  id: string;
  dish: string;
  calories: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
  eaten_at: string; // iso8601
  source: MealSource;
  // v3 extended nutrients (optional).
  fiber_g?: number;
  sugar_g?: number;
  sodium_mg?: number;
  favorite?: boolean;
}

export interface MealInput {
  dish: string;
  calories: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
  eaten_at?: string;
  source: MealSource;
  // v3 extended nutrients (optional).
  fiber_g?: number;
  sugar_g?: number;
  sodium_mg?: number;
}

// Day totals carry burned (v2) plus water + water target and the extended
// nutrients (v3). Everything past the big three is optional.
export type DayTotals = Macros & {
  burned?: number;
  water_ml?: number;
  water_target_ml?: number;
} & ExtraNutrients;

export interface MealsResponse {
  meals: Meal[];
  totals: DayTotals;
}

// v3: GET /meals/recent — deduped quick-log list (favorites first, then most
// frequent recent dishes). The server dedups BY DISH, so entries carry no meal
// id (favorite toggling uses a real meal id from the day list). Each entry adds
// last_eaten_at + count. `id` is a client-only convenience the demo fills with a
// dish signature so demo favoriting persists; the real server omits it.
export interface RecentMeal {
  dish: string;
  calories: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
  favorite: boolean;
  fiber_g?: number;
  sugar_g?: number;
  sodium_mg?: number;
  last_eaten_at?: string;
  count?: number;
  id?: string;
}

// Server wire shape: `{ recent: [...] }` (NOT `meals`).
export interface RecentMealsResponse {
  recent: RecentMeal[];
}

// v3: POST /weight, GET /weight?from&to
export interface WeightEntry {
  date: string; // YYYY-MM-DD
  weight_kg: number;
}

export interface WeightTrendPoint {
  date: string;
  avg_kg: number;
}

export interface WeightResponse {
  entries: WeightEntry[];
  trend: WeightTrendPoint[];
}

// v3: GET /streaks
export type StampKind = 'on_target' | 'milestone_7' | 'milestone_30' | 'first_log';

export interface Stamp {
  date: string;
  kind: StampKind;
}

export interface StreaksResponse {
  current: number;
  best: number;
  stamps: Stamp[];
}

// v3: GET /insights?week=YYYY-MM-DD. Server keys the macro split as
// `macro_split_pct: {protein,carbs,fat}` and adds week_start/week_end/
// days_logged/target_calories.
export interface InsightsResponse {
  week_start?: string;
  week_end?: string;
  days_logged?: number;
  target_calories?: number;
  averages: Macros & { water_ml?: number };
  adherence_pct: number;
  best_day: { date: string; note?: string } | null;
  worst_day: { date: string; note?: string } | null;
  weight_delta_kg: number | null;
  macro_split_pct: { protein: number; carbs: number; fat: number };
  summary: string;
}

export interface Suggestion {
  dish: string;
  calories: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
  reason: string;
}

export interface SuggestionsResponse {
  remaining: Macros;
  suggestions: Suggestion[];
}

export interface Workout {
  id?: string;
  kind: string;
  minutes: number;
  calories_burned: number;
  note?: string;
}

export interface WorkoutsResponse {
  workouts: Workout[];
}

export interface CalendarDay {
  date: string;
  calories: number;
  target: number;
  burned: number;
  status: DayStatus;
}

export interface CalendarResponse {
  days: CalendarDay[];
}

export interface HealthResponse {
  status: string;
  vllm: 'connected' | 'mock';
  model: string | null;
}

export interface PairClaimResponse {
  device_id: string;
  device_key: string;
}

// ---- Checkpoint keeper (関 admin mode) ----
//
// The keeper surface lives entirely inside the app behind is_admin. All paths
// are under /api/v1/keeper/* and every one is Bearer device-key authed; the
// server rejects a non-keeper device with 403 {code:"not_keeper"}.

// POST /keeper/pair → a fresh pairing invite for another traveler's phone.
export interface KeeperPairResponse {
  token: string;
  url: string;
  // Base64-encoded PNG (no data: prefix) of the pairing QR.
  qr_png_base64: string;
  expires_in: number; // seconds until the token expires
}

// One traveler in the overview list.
export interface KeeperUserRow {
  id: string;
  username: string;
  today_kcal: number;
  target_kcal: number;
  streak: number;
  last_activity: string | null; // iso8601, or null if never
}

// One paired device.
export interface KeeperDevice {
  id: string;
  name: string;
  last_seen: string | null; // iso8601
  active_user: string | null; // username of the account currently active on it
}

// GET /keeper/overview.
export interface KeeperOverview {
  users: KeeperUserRow[];
  devices: KeeperDevice[];
  backend_mode: string; // active backend label, e.g. "vllm" | "mock"
  push_subs: number; // total push subscriptions across users
  // The public URL is shown ONLY here — the keeper context is the sole
  // exception to the app's no-server-URLs rule.
  public_url: string;
}

// GET /keeper/users/{id} — drill-down for one traveler.
export interface KeeperUserDetail {
  profile: ProfileWithTargets;
  recent_meals: Meal[];
}

export type BackendRole = 'vision' | 'text';

// One entry from GET /keeper/backends.
export interface KeeperBackend {
  id: string;
  name: string;
  kind: string; // vllm | nvidia | lmstudio | openai
  role: BackendRole; // which slot (vision or text) this backend serves
  active: boolean; // the currently-active backend for its role
}

export interface KeeperBackendsResponse {
  backends: KeeperBackend[];
}

// The typed surface the whole app talks to. Both the real HTTP client and the
// demo stub implement this identical interface.
export interface HarahachiClient {
  readonly isDemo: boolean;
  health(): Promise<HealthResponse>;
  analyzeMeal(image: Blob, hint?: string, portion?: Portion): Promise<AnalyzeResult>;
  lookupBarcode(code: string): Promise<BarcodeResult>;
  logMeal(meal: MealInput): Promise<Meal>;
  getMeals(date: string): Promise<MealsResponse>;
  deleteMeal(id: string): Promise<void>;
  getSuggestions(date: string): Promise<SuggestionsResponse>;
  getProfile(): Promise<ProfileWithTargets>;
  putProfile(profile: Profile): Promise<ProfileWithTargets>;
  logWorkout(workout: Workout): Promise<Workout>;
  getWorkouts(date: string): Promise<WorkoutsResponse>;
  getCalendar(month: string): Promise<CalendarResponse>;
  // ---- v2 ----
  // Accounts. register/login set the device's active user server-side.
  register(input: RegisterInput): Promise<AuthResponse>;
  login(username: string, pin: string): Promise<AuthResponse>;
  logout(): Promise<void>;
  me(): Promise<AuthResponse>;
  // Body-composition photo estimate (analyzed + discarded server-side).
  estimateBodyPhoto(image: Blob): Promise<BodyPhotoEstimate>;
  // Assistant chat. The whole message history is sent; server executes actions.
  chat(messages: ChatMessage[]): Promise<ChatResponse>;
  // Push.
  vapidPublicKey(): Promise<string>;
  subscribePush(subscription: PushSubscriptionJSON, tzOffsetMin: number): Promise<void>;
  unsubscribePush(): Promise<void>;
  // ---- v3: the feedback loop ----
  // Water. POST /water {ml} (negative allowed to undo); returns the new day
  // total ({date, water_ml, water_target_ml}).
  logWater(ml: number): Promise<{ date: string; water_ml: number; water_target_ml: number }>;
  // Weight. POST /weight returns the single stored entry ({date, weight_kg});
  // the caller re-fetches getWeight for the refreshed history + 7-day trend.
  logWeight(weightKg: number, date?: string): Promise<WeightEntry>;
  getWeight(from: string, to: string): Promise<WeightResponse>;
  // Streaks + stamp book.
  getStreaks(): Promise<StreaksResponse>;
  // Weekly insights (any day in the week).
  getInsights(week: string): Promise<InsightsResponse>;
  // Quick-log: recent/favorite dishes + favorite toggle.
  getRecentMeals(): Promise<RecentMealsResponse>;
  favoriteMeal(id: string): Promise<void>;
  unfavoriteMeal(id: string): Promise<void>;
  // CSV export. Returns a Blob (fetched with the auth header) so the app can
  // trigger a download.
  exportCsv(): Promise<Blob>;
  // ---- Checkpoint keeper (admin) ----
  // Mint a pairing invite for another traveler's phone.
  keeperPair(): Promise<KeeperPairResponse>;
  // Fleet overview: travelers, devices, backend mode, push subs, public URL.
  keeperOverview(): Promise<KeeperOverview>;
  // Drill into one traveler (profile + targets + recent meals).
  keeperUser(id: string): Promise<KeeperUserDetail>;
  // Revoke a paired device (DELETE /keeper/devices/{id}).
  keeperRevokeDevice(id: string): Promise<void>;
  // Backend registry + switch the active vision/text backend.
  keeperBackends(): Promise<KeeperBackendsResponse>;
  keeperSetBackend(id: string, role: BackendRole): Promise<void>;
}
