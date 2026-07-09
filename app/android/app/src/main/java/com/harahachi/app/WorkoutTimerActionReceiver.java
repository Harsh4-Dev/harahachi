package com.harahachi.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

import androidx.core.app.NotificationManagerCompat;

/**
 * Receives Pause / Stop taps from the ongoing workout notification. It persists
 * the action (so a killed app applies it on next launch) and delivers it to a
 * live webview via WorkoutTimerPlugin.deliverAction(). Both actions freeze the
 * timer, so the ongoing notification is cancelled here.
 */
public class WorkoutTimerActionReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent.getAction();
        String mapped = null;
        if (WorkoutTimerPlugin.ACTION_PAUSE.equals(action)) {
            mapped = "pause";
        } else if (WorkoutTimerPlugin.ACTION_STOP.equals(action)) {
            mapped = "stop";
        }
        if (mapped == null) {
            return;
        }

        // Persist for a dead app to pick up on next launch.
        context.getSharedPreferences(WorkoutTimerPlugin.PREFS, Context.MODE_PRIVATE)
                .edit().putString(WorkoutTimerPlugin.PREF_PENDING, mapped).apply();

        // Deliver to a live webview (no-op if the app process is gone).
        WorkoutTimerPlugin.deliverAction(mapped);

        // Freeze → remove the ongoing notification.
        NotificationManagerCompat.from(context).cancel(WorkoutTimerPlugin.NOTIF_ID);
    }
}
