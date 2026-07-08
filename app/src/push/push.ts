// Push subscription helpers (API.md v2). Fetch the VAPID public key, subscribe
// via the service worker's PushManager, POST /push/subscribe with tz_offset_min.
// Graceful everywhere: returns a typed result so the toggle can explain WHY it
// couldn't enable (no SW, denied, unsupported) and fall back to in-app banners.

import type { HarahachiClient } from '../api/types';

export type PushResult =
  | { ok: true }
  | { ok: false; reason: 'unsupported' | 'denied' | 'no-sw' | 'error'; message: string };

// Convert a base64url VAPID key to the Uint8Array applicationServerKey wants.
function urlBase64ToUint8Array(base64String: string): Uint8Array<ArrayBuffer> {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const buf = new ArrayBuffer(raw.length);
  const out = new Uint8Array(buf);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

export function pushSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  );
}

export async function enablePush(client: HarahachiClient): Promise<PushResult> {
  if (!pushSupported()) {
    return { ok: false, reason: 'unsupported', message: 'Push isn’t supported here — you’ll get in-app reminders instead.' };
  }
  let permission = Notification.permission;
  if (permission === 'default') permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    return { ok: false, reason: 'denied', message: 'Notifications are blocked — you’ll get in-app reminders instead.' };
  }
  const reg = await navigator.serviceWorker.getRegistration();
  if (!reg) {
    return { ok: false, reason: 'no-sw', message: 'The app isn’t installed as a PWA yet — reminders show in-app for now.' };
  }
  try {
    const key = await client.vapidPublicKey();
    const existing = await reg.pushManager.getSubscription();
    const sub =
      existing ??
      (await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(key),
      }));
    const tzOffsetMin = -new Date().getTimezoneOffset(); // minutes east of UTC
    await client.subscribePush(sub.toJSON(), tzOffsetMin);
    return { ok: true };
  } catch (e) {
    return { ok: false, reason: 'error', message: (e as Error)?.message || 'Could not enable push.' };
  }
}

export async function disablePush(client: HarahachiClient): Promise<void> {
  try {
    if (pushSupported()) {
      const reg = await navigator.serviceWorker.getRegistration();
      const sub = await reg?.pushManager.getSubscription();
      await sub?.unsubscribe();
    }
  } catch {
    /* best-effort */
  }
  try {
    await client.unsubscribePush();
  } catch {
    /* best-effort */
  }
}
