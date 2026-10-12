// The real HTTP client for Sekisho. All app→server access goes through this
// module. Bearer device-key auth, JSON in/out, multipart for analyze.
//
// v2 additions:
//  • Client-side timeouts on EVERY call via AbortController (analyze/body-photo
//    65s, everything else 12s) — no request may hang forever (user requirement).
//  • Accounts (register/login/logout/me), body-photo estimate, chat, push.
//  • A global onNoUser hook: any 401 {code:"no_user"} routes the app to login.
//
// v4 (Vercel + NVIDIA NIM): a global device-revoked hook (401 invalid_key →
// "pair this phone again"), and non-JSON error bodies (platform 413/504 pages)
// no longer crash the parser. Photo callers downscale via lib/imagePrep first.

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
  RecentMealsResponse,
  RegisterInput,
  StreaksResponse,
  SuggestionsResponse,
  InsightsResponse,
  VapidKeyResponse,
  WeightEntry,
  WeightResponse,
  Workout,
  WorkoutsResponse,
} from './types';
import type { Pairing } from './storage';

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

// Timeouts (ms). Analyze + body-photo call a vision model → generous window;
// everything else is snappy.
export const TIMEOUT_LONG = 65_000;
export const TIMEOUT_SHORT = 12_000;

// Global hook: fired when any authenticated call returns 401 {code:"no_user"}.
// AppContext registers a handler that routes to the login screen.
let noUserHandler: (() => void) | null = null;
export function setNoUserHandler(fn: (() => void) | null): void {
  noUserHandler = fn;
}

// 401 codes meaning the device key itself is unknown / revoked (NOT a bad PIN
// and NOT "no active user", which routes to login instead). v4: devices must
// re-pair to the new Vercel deployment, so this is an expected state.
export const DEVICE_REVOKED_CODES = new Set([
  'invalid_key',
  'missing_bearer',
  'unknown_device',
  'device_revoked',
  'revoked_device',
  'invalid_device',
]);

// Global hook: fired when any authenticated call returns 401 with a
// DEVICE_REVOKED_CODES code. AppContext routes to the "pair again" screen.
let deviceRevokedHandler: (() => void) | null = null;
export function setDeviceRevokedHandler(fn: (() => void) | null): void {
  deviceRevokedHandler = fn;
}

// Build the ApiError for a failed response and fire the global hooks. Shared
// by the real client and the DEV-only fault injection in the demo client so
// both exercise the same routing.
export function apiErrorFrom(status: number, body: unknown, statusText = ''): ApiError {
  const err = ((body as { error?: { code?: string; message?: string } } | null)?.error ?? {});
  const code = err.code ?? (status === 413 ? 'image_too_large' : 'http_error');
  if (status === 401 && code === 'no_user') noUserHandler?.();
  else if (status === 401 && DEVICE_REVOKED_CODES.has(code)) deviceRevokedHandler?.();
  return new ApiError(code, err.message ?? statusText, status);
}

// Parse a response body that SHOULD be JSON but may not be (a platform-level
// 413/504 from Vercel is HTML/plain text). Never throws.
function safeJson(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function apiRoot(baseUrl: string): string {
  return baseUrl.replace(/\/+$/, '') + '/api/v1';
}

// A fetch wrapper that always attaches an AbortController timeout and maps
// failures to ApiError. status 0 = network/offline/timeout (retryable).
async function timedFetch(url: string, init: RequestInit, timeoutMs: number): Promise<Response> {
  const ctrl = new AbortController();
  const t = window.setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: ctrl.signal });
  } catch (e) {
    const aborted = (e as Error)?.name === 'AbortError';
    throw new ApiError(
      aborted ? 'timeout' : 'network',
      aborted ? 'The request timed out.' : (e as Error).message || 'Network error',
      0,
    );
  } finally {
    window.clearTimeout(t);
  }
}

export function createHttpClient(pairing: Pairing): HarahachiClient {
  const root = apiRoot(pairing.baseUrl);
  const authHeaders = { Authorization: `Bearer ${pairing.deviceKey}` };

  async function req<T>(path: string, init: RequestInit = {}, timeoutMs = TIMEOUT_SHORT): Promise<T> {
    const res = await timedFetch(root + path, {
      ...init,
      headers: { ...authHeaders, ...(init.headers ?? {}) },
    }, timeoutMs);
    if (res.status === 204) return undefined as T;
    const text = await res.text();
    const body = safeJson(text);
    // Global handling: no_user → login; unknown/revoked device → pair again.
    if (!res.ok) throw apiErrorFrom(res.status, body, res.statusText);
    if (body === null && text) throw new ApiError('bad_response', 'Unexpected server response.', res.status);
    return body as T;
  }

  return {
    isDemo: false,

    health(): Promise<HealthResponse> {
      return req<HealthResponse>('/health');
    },

    analyzeMeal(image: Blob, hint?: string, portion?: Portion): Promise<AnalyzeResult> {
      const fd = new FormData();
      fd.append('image', image, 'meal.jpg');
      if (hint) fd.append('hint', hint);
      if (portion) fd.append('portion', portion);
      return req<AnalyzeResult>('/meals/analyze', { method: 'POST', body: fd }, TIMEOUT_LONG);
    },

    lookupBarcode(code: string): Promise<BarcodeResult> {
      return req<BarcodeResult>(`/food/barcode/${encodeURIComponent(code)}`);
    },

    logMeal(meal: MealInput): Promise<Meal> {
      return req<Meal>('/meals', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(meal),
      });
    },

    getMeals(date: string): Promise<MealsResponse> {
      return req<MealsResponse>(`/meals?date=${encodeURIComponent(date)}`);
    },

    deleteMeal(id: string): Promise<void> {
      return req<void>(`/meals/${encodeURIComponent(id)}`, { method: 'DELETE' });
    },

    getSuggestions(date: string): Promise<SuggestionsResponse> {
      return req<SuggestionsResponse>(`/suggestions?date=${encodeURIComponent(date)}`);
    },

    getProfile(): Promise<ProfileWithTargets> {
      return req<ProfileWithTargets>('/profile');
    },

    putProfile(profile: Profile): Promise<ProfileWithTargets> {
      return req<ProfileWithTargets>('/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(profile),
      });
    },

    logWorkout(workout: Workout): Promise<Workout> {
      return req<Workout>('/workouts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(workout),
      });
    },

    getWorkouts(date: string): Promise<WorkoutsResponse> {
      return req<WorkoutsResponse>(`/workouts?date=${encodeURIComponent(date)}`);
    },

    getCalendar(month: string): Promise<CalendarResponse> {
      return req<CalendarResponse>(`/calendar?month=${encodeURIComponent(month)}`);
    },

    // ---- v2: accounts ----

    register(input: RegisterInput): Promise<AuthResponse> {
      return req<AuthResponse>('/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      });
    },

    login(username: string, pin: string): Promise<AuthResponse> {
      return req<AuthResponse>('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, pin }),
      });
    },

    logout(): Promise<void> {
      return req<void>('/auth/logout', { method: 'POST' });
    },

    me(): Promise<AuthResponse> {
      return req<AuthResponse>('/auth/me');
    },

    // ---- v2: body-photo estimate (analyzed + discarded server-side) ----

    estimateBodyPhoto(image: Blob): Promise<BodyPhotoEstimate> {
      const fd = new FormData();
      fd.append('image', image, 'body.jpg');
      return req<BodyPhotoEstimate>('/profile/body-photo', { method: 'POST', body: fd }, TIMEOUT_LONG);
    },

    // ---- v2: assistant chat ----

    chat(messages: ChatMessage[]): Promise<ChatResponse> {
      return req<ChatResponse>('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages }),
      });
    },

    // ---- v2: push ----

    async vapidPublicKey(): Promise<string> {
      // GET /push/vapid-public-key (no auth per API.md, but sending the header is
      // harmless). Server returns { key }.
      const res = await timedFetch(root + '/push/vapid-public-key', {}, TIMEOUT_SHORT);
      const text = await res.text();
      const body = text ? (JSON.parse(text) as VapidKeyResponse) : null;
      if (!res.ok || !body?.key) throw new ApiError('push_key', 'No VAPID key.', res.status);
      return body.key;
    },

    subscribePush(subscription: PushSubscriptionJSON, tzOffsetMin: number): Promise<void> {
      return req<void>('/push/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subscription, tz_offset_min: tzOffsetMin }),
      });
    },

    unsubscribePush(): Promise<void> {
      return req<void>('/push/subscribe', { method: 'DELETE' });
    },

    // ---- v3: the feedback loop ----

    logWater(ml: number): Promise<{ date: string; water_ml: number; water_target_ml: number }> {
      return req<{ date: string; water_ml: number; water_target_ml: number }>('/water', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ml }),
      });
    },

    logWeight(weightKg: number, date?: string): Promise<WeightEntry> {
      // POST /weight returns just the stored entry {date, weight_kg}; the UI
      // re-fetches getWeight() to refresh the trend line.
      return req<WeightEntry>('/weight', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(date ? { weight_kg: weightKg, date } : { weight_kg: weightKg }),
      });
    },

    getWeight(from: string, to: string): Promise<WeightResponse> {
      return req<WeightResponse>(
        `/weight?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`,
      );
    },

    getStreaks(): Promise<StreaksResponse> {
      return req<StreaksResponse>('/streaks');
    },

    getInsights(week: string): Promise<InsightsResponse> {
      return req<InsightsResponse>(`/insights?week=${encodeURIComponent(week)}`);
    },

    getRecentMeals(): Promise<RecentMealsResponse> {
      return req<RecentMealsResponse>('/meals/recent');
    },

    favoriteMeal(id: string): Promise<void> {
      return req<void>(`/meals/${encodeURIComponent(id)}/favorite`, { method: 'POST' });
    },

    unfavoriteMeal(id: string): Promise<void> {
      return req<void>(`/meals/${encodeURIComponent(id)}/favorite`, { method: 'DELETE' });
    },

    async exportCsv(): Promise<Blob> {
      // Device-authed CSV. Fetch with the bearer header and return a Blob so the
      // caller can trigger a download (fetch+blob per DESIGN.md).
      const res = await timedFetch(root + '/export.csv', { headers: { ...authHeaders } }, TIMEOUT_SHORT);
      if (!res.ok) throw new ApiError('export_failed', 'Could not export your records.', res.status);
      return res.blob();
    },

    // ---- Checkpoint keeper (admin) ----
    // Every call is Bearer device-key authed (via req). A non-keeper device is
    // rejected with 403 {code:"not_keeper"} — surfaced to the caller as ApiError.

    keeperPair(): Promise<KeeperPairResponse> {
      return req<KeeperPairResponse>('/keeper/pair', { method: 'POST' });
    },

    keeperOverview(): Promise<KeeperOverview> {
      return req<KeeperOverview>('/keeper/overview');
    },

    keeperUser(id: string): Promise<KeeperUserDetail> {
      return req<KeeperUserDetail>(`/keeper/users/${encodeURIComponent(id)}`);
    },

    keeperRevokeDevice(id: string): Promise<void> {
      return req<void>(`/keeper/devices/${encodeURIComponent(id)}`, { method: 'DELETE' });
    },

    keeperBackends(): Promise<KeeperBackendsResponse> {
      return req<KeeperBackendsResponse>('/keeper/backends');
    },

    keeperSetBackend(id: string, role: BackendRole): Promise<void> {
      return req<void>('/keeper/backends/active', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, role }),
      });
    },
  };
}

// Unauthenticated pairing claim (with a client-side timeout too).
export async function claimPairing(
  baseUrl: string,
  token: string,
  deviceName: string,
): Promise<{ device_id: string; device_key: string }> {
  const res = await timedFetch(
    apiRoot(baseUrl) + '/pair/claim',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, device_name: deviceName }),
    },
    TIMEOUT_SHORT,
  );
  const text = await res.text();
  const body = safeJson(text) as { device_id: string; device_key: string; error?: { code?: string; message?: string } } | null;
  if (!res.ok || !body?.device_key) {
    const err = body?.error ?? {};
    throw new ApiError(
      err.code ?? 'pair_failed',
      err.message ?? (res.ok ? 'That address did not answer like a checkpoint.' : res.statusText),
      res.ok ? 502 : res.status,
    );
  }
  return body;
}
