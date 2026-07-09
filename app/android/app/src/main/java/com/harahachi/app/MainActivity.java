package com.harahachi.app;

import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        // Register the custom workout-timer plugin before the bridge starts so
        // the JS registerPlugin('WorkoutTimer') proxy resolves to it.
        registerPlugin(WorkoutTimerPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
