package com.harahachi.app;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;

import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * Custom Capacitor plugin: an ongoing workout notification whose elapsed time is
 * ticked by ANDROID (setUsesChronometer + setWhen), not by JS. This fixes the
 * "static time" bug — a JS setInterval cannot run while the app is backgrounded
 * or killed, so the previous @capacitor/local-notifications approach froze the
 * time at the last foreground moment.
 *
 * Pause / Stop action buttons broadcast to WorkoutTimerActionReceiver, which
 * (a) delivers the action to a live webview via {@link #deliverAction(String)},
 * and (b) persists it so a killed app can apply pause/stop on next launch
 * (see {@link #consumePendingAction(PluginCall)}).
 */
@CapacitorPlugin(name = "WorkoutTimer")
public class WorkoutTimerPlugin extends Plugin {
    public static final String CHANNEL_ID = "workout_timer";
    public static final int NOTIF_ID = 8080;
    public static final String ACTION_PAUSE = "com.harahachi.app.WORKOUT_PAUSE";
    public static final String ACTION_STOP = "com.harahachi.app.WORKOUT_STOP";
    public static final String PREFS = "hh_workout_timer";
    public static final String PREF_PENDING = "pending_action";

    private static WorkoutTimerPlugin instance;

    @Override
    public void load() {
        instance = this;
        createChannel();
    }

    /** Emit an action event to the JS layer if a plugin instance is alive. */
    public static void deliverAction(String action) {
        if (instance != null) {
            JSObject data = new JSObject();
            data.put("action", action);
            instance.notifyListeners("action", data);
        }
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationManager nm =
                    (NotificationManager) getContext().getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null && nm.getNotificationChannel(CHANNEL_ID) == null) {
                NotificationChannel ch = new NotificationChannel(
                        CHANNEL_ID, "Workout timer", NotificationManager.IMPORTANCE_LOW);
                ch.setDescription("Ongoing workout elapsed time");
                ch.setShowBadge(false);
                nm.createNotificationChannel(ch);
            }
        }
    }

    private PendingIntent actionIntent(String action) {
        Intent i = new Intent(getContext(), WorkoutTimerActionReceiver.class);
        i.setAction(action);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            flags |= PendingIntent.FLAG_IMMUTABLE;
        }
        return PendingIntent.getBroadcast(getContext(), action.hashCode(), i, flags);
    }

    @PluginMethod
    public void start(PluginCall call) {
        long startEpoch = call.getLong("startEpoch", System.currentTimeMillis());
        String title = call.getString("title", "Workout");
        String text = call.getString("text", "Workout in progress");
        createChannel();

        // Tapping the notification body reopens the app.
        PendingIntent contentPi = null;
        Intent launch = getContext().getPackageManager()
                .getLaunchIntentForPackage(getContext().getPackageName());
        if (launch != null) {
            int flags = PendingIntent.FLAG_UPDATE_CURRENT;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                flags |= PendingIntent.FLAG_IMMUTABLE;
            }
            contentPi = PendingIntent.getActivity(getContext(), 0, launch, flags);
        }

        NotificationCompat.Builder b = new NotificationCompat.Builder(getContext(), CHANNEL_ID)
                .setSmallIcon(getContext().getApplicationInfo().icon)
                .setContentTitle(title)
                .setContentText(text)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setUsesChronometer(true)   // Android ticks the elapsed time itself
                .setWhen(startEpoch)        // ...counting from the true start epoch
                .setShowWhen(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .addAction(0, "Pause", actionIntent(ACTION_PAUSE))
                .addAction(0, "Stop", actionIntent(ACTION_STOP));
        if (contentPi != null) {
            b.setContentIntent(contentPi);
        }

        // A fresh run clears any stale action persisted from a prior session.
        getContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit().remove(PREF_PENDING).apply();

        try {
            NotificationManagerCompat.from(getContext()).notify(NOTIF_ID, b.build());
        } catch (SecurityException ignored) {
            // POST_NOTIFICATIONS not granted — non-fatal, the in-app timer still runs.
        }
        call.resolve();
    }

    @PluginMethod
    public void stop(PluginCall call) {
        NotificationManagerCompat.from(getContext()).cancel(NOTIF_ID);
        call.resolve();
    }

    @PluginMethod
    public void consumePendingAction(PluginCall call) {
        SharedPreferences prefs = getContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String pending = prefs.getString(PREF_PENDING, null);
        if (pending != null) {
            prefs.edit().remove(PREF_PENDING).apply();
        }
        JSObject ret = new JSObject();
        ret.put("action", pending); // null when there is none
        call.resolve(ret);
    }
}
