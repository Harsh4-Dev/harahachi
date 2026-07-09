// JS bridge to the custom Android plugin (android/.../WorkoutTimerPlugin.java).
//
// The plugin posts an ONGOING notification with setUsesChronometer(true) +
// setWhen(startEpoch), so ANDROID itself ticks the elapsed time even while the
// JS/webview is frozen in the background — the fix for the "static time" bug.
// It also renders Pause / Stop action buttons whose taps broadcast back to the
// app (delivered as a 'action' plugin event when alive, and persisted so a
// killed app can apply the state on next launch).
//
// If the plugin is not present (web/PWA, or an APK built before the plugin was
// compiled in), registerPlugin still returns a proxy; every call rejects and the
// caller (push/workoutNotify.ts) falls back to @capacitor/local-notifications.

import { registerPlugin, type PluginListenerHandle } from '@capacitor/core';

export interface WorkoutTimerPlugin {
  // Post/replace the ongoing chronometer notification.
  start(opts: { startEpoch: number; title: string; text: string }): Promise<void>;
  // Cancel the ongoing notification.
  stop(): Promise<void>;
  // Read + clear any action delivered while the app was dead (pause/stop tapped
  // in the notification with no live webview). Returns { action: null } if none.
  consumePendingAction(): Promise<{ action: 'pause' | 'stop' | null }>;
  addListener(
    eventName: 'action',
    listener: (data: { action: 'pause' | 'stop' }) => void,
  ): Promise<PluginListenerHandle>;
}

export const WorkoutTimer = registerPlugin<WorkoutTimerPlugin>('WorkoutTimer');
