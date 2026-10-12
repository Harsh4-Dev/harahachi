// Friendly classification of API failures for the shared error popup
// (API.md v4: Vercel + NVIDIA NIM). One place decides what the user sees so
// no screen leaks internals ("mock", backend names, raw server messages).
//
//   status 0            → network / timeout (retry)
//   413                  → photo too large (retry — callers re-shrink harder)
//   429 + daily-cap code → today's AI allowance is spent (manual entry only)
//   429 otherwise        → per-minute rate limit (retry in a moment)
//   503                  → AI provider unavailable (retry + manual entry)
//   401 invalid device   → this phone must be paired again (handled globally
//                          by AppContext; screens should just stop)

import { ApiError, DEVICE_REVOKED_CODES } from './http';

export type ApiFailureKind =
  | 'network'
  | 'too_large'
  | 'daily_cap'
  | 'rate_limited'
  | 'unavailable'
  | 'device_revoked'
  | 'no_user'
  | 'other';

// 429 codes that mean the per-user DAILY AI cap is reached (vs. the
// per-minute limiter). The server's exact code is matched loosely so a
// naming tweak on the server side still lands on the right copy.
const DAILY_CAP_CODES = new Set([
  'daily_cap',
  'daily_cap_reached',
  'daily_limit',
  'daily_limit_reached',
  'daily_quota',
  'ai_daily_cap',
  'llm_daily_cap',
  'quota_exceeded',
]);
export function isDailyCapCode(code: string): boolean {
  const c = code.toLowerCase();
  return DAILY_CAP_CODES.has(c) || /daily|quota/.test(c);
}

export function classifyApiError(e: unknown): ApiFailureKind {
  if (!(e instanceof ApiError)) return 'other';
  if (e.status === 0) return 'network';
  if (e.status === 413) return 'too_large';
  if (e.status === 429) return isDailyCapCode(e.code) ? 'daily_cap' : 'rate_limited';
  if (e.status === 503 || e.status === 502 || e.status === 504) return 'unavailable';
  if (e.status === 401 && DEVICE_REVOKED_CODES.has(e.code)) return 'device_revoked';
  if (e.status === 401 && e.code === 'no_user') return 'no_user';
  return 'other';
}

// User-facing copy for AI-backed features (dish photo, body photo, chat).
export const AI_COPY = {
  network: 'Sekisho is unreachable — check your connection and try again.',
  too_large: 'That photo is too large — try again.',
  daily_cap: 'You’ve reached today’s AI limit — log manually or try tomorrow.',
  rate_limited: 'Too many requests at once — wait a moment and try again.',
  unavailable: 'The food reader is resting right now — try again, or enter it manually.',
} as const;
