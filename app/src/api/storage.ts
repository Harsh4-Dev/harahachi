// Central localStorage helpers: pairing credentials, offline meal queue,
// glass transparency, profile cache, demo flag, active user (accounts v2).

import type { MealInput, Profile, User } from './types';

const K = {
  baseUrl: 'hh.baseUrl',
  deviceKey: 'hh.deviceKey',
  deviceId: 'hh.deviceId',
  demo: 'hh.demo',
  glassAlpha: 'hh.glassAlpha',
  queue: 'hh.queue',
  onboarded: 'hh.onboarded',
  draftProfile: 'hh.draftProfile',
  activeUser: 'hh.activeUser', // JSON {id, username} — the logged-in account
  pushEnabled: 'hh.pushEnabled', // '1' when the user opted into push
} as const;

export interface Pairing {
  baseUrl: string;
  deviceKey: string;
  deviceId: string;
}

// True when the app is running inside the Capacitor WebView (the APK). There
// the page origin is capacitor://localhost or https://localhost, so same-origin
// API calls are impossible — a real server base URL (from pairing) must be used.
export function isNativeShell(): boolean {
  if (typeof window === 'undefined') return false;
  const w = window as unknown as { Capacitor?: { isNativePlatform?: () => boolean } };
  if (w.Capacitor?.isNativePlatform?.()) return true;
  const o = window.location.origin;
  return o.startsWith('capacitor://') || o === 'https://localhost' || o === 'http://localhost';
}

export function getPairing(): Pairing | null {
  const baseUrl = localStorage.getItem(K.baseUrl);
  const deviceKey = localStorage.getItem(K.deviceKey);
  const deviceId = localStorage.getItem(K.deviceId);
  if (baseUrl && deviceKey && deviceId) return { baseUrl, deviceKey, deviceId };
  return null;
}

export function setPairing(p: Pairing): void {
  localStorage.setItem(K.baseUrl, p.baseUrl);
  localStorage.setItem(K.deviceKey, p.deviceKey);
  localStorage.setItem(K.deviceId, p.deviceId);
}

export function clearPairing(): void {
  localStorage.removeItem(K.baseUrl);
  localStorage.removeItem(K.deviceKey);
  localStorage.removeItem(K.deviceId);
}

export function isDemoEnabled(): boolean {
  return localStorage.getItem(K.demo) === '1';
}

export function setDemoEnabled(on: boolean): void {
  if (on) localStorage.setItem(K.demo, '1');
  else localStorage.removeItem(K.demo);
}

export function isOnboarded(): boolean {
  return localStorage.getItem(K.onboarded) === '1';
}

export function setOnboarded(on: boolean): void {
  if (on) localStorage.setItem(K.onboarded, '1');
  else localStorage.removeItem(K.onboarded);
}

// The profile gathered during onboarding, held until a checkpoint is paired
// (or demo starts), then PUT to the server.
export function getDraftProfile(): Profile | null {
  try {
    const raw = localStorage.getItem(K.draftProfile);
    return raw ? (JSON.parse(raw) as Profile) : null;
  } catch {
    return null;
  }
}

export function setDraftProfile(p: Profile): void {
  localStorage.setItem(K.draftProfile, JSON.stringify(p));
}

export function clearDraftProfile(): void {
  localStorage.removeItem(K.draftProfile);
}

// ---- Glass transparency (DESIGN.md hard requirement) ----

export const DEFAULT_GLASS_ALPHA = 0.55;

export function getGlassAlpha(): number {
  const raw = localStorage.getItem(K.glassAlpha);
  const v = raw == null ? DEFAULT_GLASS_ALPHA : parseFloat(raw);
  return Number.isFinite(v) ? clamp(v, 0.15, 0.9) : DEFAULT_GLASS_ALPHA;
}

export function setGlassAlpha(v: number): void {
  const clamped = clamp(v, 0.15, 0.9);
  localStorage.setItem(K.glassAlpha, String(clamped));
  document.documentElement.style.setProperty('--glass-alpha', String(clamped));
}

export function applyGlassAlpha(): void {
  document.documentElement.style.setProperty('--glass-alpha', String(getGlassAlpha()));
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

// ---- Offline meal queue ----

export interface QueuedMeal {
  tempId: string;
  meal: MealInput;
}

export function getQueue(): QueuedMeal[] {
  try {
    const raw = localStorage.getItem(K.queue);
    return raw ? (JSON.parse(raw) as QueuedMeal[]) : [];
  } catch {
    return [];
  }
}

export function setQueue(q: QueuedMeal[]): void {
  localStorage.setItem(K.queue, JSON.stringify(q));
}

export function enqueueMeal(meal: MealInput): QueuedMeal {
  const q = getQueue();
  const item: QueuedMeal = { tempId: 'q_' + Math.random().toString(36).slice(2), meal };
  q.push(item);
  setQueue(q);
  return item;
}

// ---- Active user account (accounts v2) ----

export function getActiveUser(): User | null {
  try {
    const raw = localStorage.getItem(K.activeUser);
    return raw ? (JSON.parse(raw) as User) : null;
  } catch {
    return null;
  }
}

export function setActiveUser(u: User): void {
  localStorage.setItem(K.activeUser, JSON.stringify(u));
}

export function clearActiveUser(): void {
  localStorage.removeItem(K.activeUser);
}

// ---- Push opt-in flag ----

export function isPushEnabled(): boolean {
  return localStorage.getItem(K.pushEnabled) === '1';
}

export function setPushEnabled(on: boolean): void {
  if (on) localStorage.setItem(K.pushEnabled, '1');
  else localStorage.removeItem(K.pushEnabled);
}

// ---- Volatile per-session caches ----
//
// Day-data caches are namespaced by user id under the hh.day.<uid>.* prefix so a
// previous session's numbers can never leak into a NEW account. On logout /
// account switch we drop every volatile cache (day data, drafts, queued items
// belong to the outgoing user) so the incoming account starts clean.

const DAY_CACHE_PREFIX = 'hh.day.';

export function dayCacheKey(userId: string, date: string): string {
  return `${DAY_CACHE_PREFIX}${userId}.${date}`;
}

export function readDayCache<T>(userId: string, date: string): T | null {
  try {
    const raw = localStorage.getItem(dayCacheKey(userId, date));
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

export function writeDayCache<T>(userId: string, date: string, value: T): void {
  try {
    localStorage.setItem(dayCacheKey(userId, date), JSON.stringify(value));
  } catch {
    /* quota — non-fatal, cache is best-effort */
  }
}

// Wipe all volatile caches (day data, the offline queue, the onboarding draft).
// Pairing credentials + glass alpha are intentionally preserved: the device
// stays paired, only the account context changes.
export function clearVolatileCaches(): void {
  const kill: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (!key) continue;
    if (key.startsWith(DAY_CACHE_PREFIX)) kill.push(key);
  }
  kill.forEach((k) => localStorage.removeItem(k));
  localStorage.removeItem(K.queue);
  localStorage.removeItem(K.draftProfile);
  // Demo state is per-session sample data — also volatile.
  localStorage.removeItem('hh.demoState');
  localStorage.removeItem('hh.demoKeeper');
}
