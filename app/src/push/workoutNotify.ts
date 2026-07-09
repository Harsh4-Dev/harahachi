// Ongoing workout notification for the APK.
//
// PRIMARY (fixes the "static time" bug): the custom WorkoutTimer Capacitor
// plugin posts an ongoing notification with a native CHRONOMETER (setWhen +
// setUsesChronometer), so Android ticks the elapsed time itself — no JS timer,
// works while the app is backgrounded/killed. It also carries Pause + Stop
// action buttons.
//
// FALLBACK (plugin not compiled in yet, e.g. before the Android Studio build):
// @capacitor/local-notifications with a start-time line ("since 6:42 pm"). This
// does NOT tick — it is the honest plugin-API-only approximation, re-posted on
// app resume. Documented so we never silently ship the broken ticking.
//
// WEB/PWA: no-op (the in-app pill + persisted timer state cover it) — unchanged.

import { LocalNotifications } from '@capacitor/local-notifications';
import { isNativeShell } from '../api/storage';
import { WorkoutTimer } from '../lib/workoutTimerPlugin';
import type { WorkoutType } from '../lib/workouts';

const NOTIF_ID = 8080; // stable id so updates replace, not stack

let pluginOk = true; // flips false once we learn the custom plugin isn't present
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

function startTimeText(startEpoch: number): string {
  try {
    return new Date(startEpoch).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  } catch {
    return '';
  }
}

// Start (or re-assert) the ongoing notification. `startEpoch` (ms) is the wall
// clock the timer began at, so the chronometer counts from the true start even
// after a background/kill.
export async function startWorkoutNotification(type: WorkoutType, startEpoch: number): Promise<void> {
  if (!isNativeShell()) return; // web: no-op (documented)

  if (pluginOk) {
    try {
      await WorkoutTimer.start({
        startEpoch,
        title: `Harahachi · ${type} 道場`,
        text: 'Workout in progress',
      });
      return;
    } catch {
      // Custom plugin unavailable → fall through to the local-notifications path.
      pluginOk = false;
    }
  }

  // Fallback: static "since <start time>" (no live tick — approximation).
  try {
    if (!(await ensurePermission())) return;
    await LocalNotifications.schedule({
      notifications: [
        {
          id: NOTIF_ID,
          title: `Harahachi · ${type} 道場`,
          body: `Workout in progress — since ${startTimeText(startEpoch)}`,
          ongoing: true,
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

export async function stopWorkoutNotification(): Promise<void> {
  if (!isNativeShell()) return;
  if (pluginOk) {
    try {
      await WorkoutTimer.stop();
      return;
    } catch {
      pluginOk = false;
    }
  }
  try {
    await LocalNotifications.cancel({ notifications: [{ id: NOTIF_ID }] });
  } catch {
    /* best-effort */
  }
}
