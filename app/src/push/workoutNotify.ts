// Ongoing workout notification for the APK (Capacitor @capacitor/local-
// notifications). While a workout timer runs, the Android shell shows a
// persistent notification with the elapsed time so the user can see it with the
// app closed; we refresh the body periodically (the plugin has no chronometer,
// so we re-post the same id with an updated "Elapsed mm:ss").
//
// Web fallback = NONE (documented): browsers get no ongoing notification — the
// in-app pill + persisted timer state cover the web/PWA case. Every call is
// guarded by isNativeShell() and wrapped in try/catch so nothing throws on web.

import { LocalNotifications } from '@capacitor/local-notifications';
import { isNativeShell } from '../api/storage';
import { formatElapsed, type WorkoutType } from '../lib/workouts';

const NOTIF_ID = 8080; // stable id so updates replace, not stack

let permissionAsked = false;

async function ensurePermission(): Promise<boolean> {
  try {
    const status = await LocalNotifications.checkPermissions();
    if (status.display === 'granted') return true;
    if (permissionAsked) return false;
    permissionAsked = true;
    const req = await LocalNotifications.requestPermissions();
    return req.display === 'granted';
  } catch {
    return false;
  }
}

// Show or update the ongoing notification. Safe to call repeatedly (same id).
export async function showWorkoutNotification(type: WorkoutType, elapsedSec: number): Promise<void> {
  if (!isNativeShell()) return; // web: no-op (documented fallback)
  try {
    if (!(await ensurePermission())) return;
    await LocalNotifications.schedule({
      notifications: [
        {
          id: NOTIF_ID,
          title: `Harahachi · ${type} 道場`,
          body: `Workout in progress — ${formatElapsed(elapsedSec)} elapsed`,
          ongoing: true, // persistent, not swipe-dismissible (Android)
          autoCancel: false,
          smallIcon: 'ic_stat_icon_config_sample',
          schedule: undefined,
        },
      ],
    });
  } catch {
    /* best-effort — a missing channel/permission must not break the timer */
  }
}

export async function clearWorkoutNotification(): Promise<void> {
  if (!isNativeShell()) return;
  try {
    await LocalNotifications.cancel({ notifications: [{ id: NOTIF_ID }] });
  } catch {
    /* best-effort */
  }
}
