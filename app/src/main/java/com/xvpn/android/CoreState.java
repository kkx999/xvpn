package com.xvpn.android;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;

/** Process-wide VPN state with a small persistent lifecycle snapshot. */
final class CoreState {
    static final String ACTION_CHANGED = "com.xvpn.android.action.CORE_STATE_CHANGED";
    static final String ACTION_AUTH_INVALID = "com.xvpn.android.action.CORE_AUTH_INVALID";
    static final String ACTION_NODE_INVALID = "com.xvpn.android.action.CORE_NODE_INVALID";
    static final String ACTION_SWITCH_FAILED = "com.xvpn.android.action.CORE_SWITCH_FAILED";

    static final int STOPPED = 0;
    static final int STARTING = 1;
    static final int RUNNING = 2;
    static final int STOPPING = 3;
    static final int ERROR = 4;
    static final int SWITCHING = 5;

    private static final String PREFS = "xvpn_core_state_v1";
    private static final Object LOCK = new Object();
    private static boolean loaded;
    private static Snapshot current = new Snapshot(STOPPED, 0, "", "", 0L, 0L, 0L, 0L, 0L);

    private CoreState() {}

    static Snapshot read(Context context) {
        synchronized (LOCK) {
            ensureLoaded(context.getApplicationContext());
            return current.copy();
        }
    }

    static void publishLifecycle(Context context, int state, int nodeId, String nodeName, String error) {
        Context app = context.getApplicationContext();
        synchronized (LOCK) {
            ensureLoaded(app);
            long startedAt = (state == RUNNING || state == SWITCHING)
                    ? (current.startedAt > 0L ? current.startedAt : System.currentTimeMillis())
                    : 0L;
            long totalUp = (state == STOPPED || state == ERROR) ? 0L : current.uploadTotal;
            long totalDown = (state == STOPPED || state == ERROR) ? 0L : current.downloadTotal;
            current = new Snapshot(state, nodeId, safe(nodeName), safe(error), startedAt,
                    totalUp, totalDown, 0L, 0L);
            SharedPreferences.Editor edit = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                    .putInt("state", state)
                    .putInt("node_id", nodeId)
                    .putString("node_name", current.nodeName)
                    .putString("error", current.error)
                    .putLong("started_at", startedAt);
            edit.apply();
        }
        broadcast(app, ACTION_CHANGED);
    }

    static void publishMetrics(Context context, long uploadTotal, long downloadTotal, long uploadRate, long downloadRate) {
        Context app = context.getApplicationContext();
        synchronized (LOCK) {
            ensureLoaded(app);
            if (current.state != RUNNING) return;
            current = new Snapshot(current.state, current.nodeId, current.nodeName, current.error,
                    current.startedAt, Math.max(0L, uploadTotal), Math.max(0L, downloadTotal),
                    Math.max(0L, uploadRate), Math.max(0L, downloadRate));
        }
        broadcast(app, ACTION_CHANGED);
    }

    static void notifyAuthInvalid(Context context, String code) {
        Intent intent = new Intent(ACTION_AUTH_INVALID).setPackage(context.getPackageName());
        intent.putExtra("code", safe(code));
        context.sendBroadcast(intent);
    }

    static void notifyNodeInvalid(Context context) {
        context.sendBroadcast(new Intent(ACTION_NODE_INVALID).setPackage(context.getPackageName()));
    }

    static void notifySwitchFailed(Context context, String message, int restoredNodeId, String restoredRouteLabel) {
        Intent intent = new Intent(ACTION_SWITCH_FAILED).setPackage(context.getPackageName());
        intent.putExtra("message", safe(message));
        intent.putExtra("node_id", restoredNodeId);
        intent.putExtra("route_label", safe(restoredRouteLabel));
        context.sendBroadcast(intent);
    }

    static void reconcileAfterProcessStart(Context context) {
        synchronized (LOCK) {
            ensureLoaded(context.getApplicationContext());
            if (current.isActive() && !VpnCoreService.isLive()) {
                current = new Snapshot(STOPPED, current.nodeId, current.nodeName, "", 0L, 0L, 0L, 0L, 0L);
                context.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                        .putInt("state", STOPPED).putString("error", "").putLong("started_at", 0L).apply();
            }
        }
    }

    private static void ensureLoaded(Context context) {
        if (loaded) return;
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        current = new Snapshot(
                prefs.getInt("state", STOPPED),
                prefs.getInt("node_id", 0),
                prefs.getString("node_name", ""),
                prefs.getString("error", ""),
                prefs.getLong("started_at", 0L),
                0L, 0L, 0L, 0L);
        loaded = true;
    }

    private static void broadcast(Context context, String action) {
        context.sendBroadcast(new Intent(action).setPackage(context.getPackageName()));
    }

    private static String safe(String value) { return value == null ? "" : value; }

    static final class Snapshot {
        final int state;
        final int nodeId;
        final String nodeName;
        final String error;
        final long startedAt;
        final long uploadTotal;
        final long downloadTotal;
        final long uploadRate;
        final long downloadRate;

        Snapshot(int state, int nodeId, String nodeName, String error, long startedAt,
                 long uploadTotal, long downloadTotal, long uploadRate, long downloadRate) {
            this.state = state;
            this.nodeId = nodeId;
            this.nodeName = safe(nodeName);
            this.error = safe(error);
            this.startedAt = startedAt;
            this.uploadTotal = uploadTotal;
            this.downloadTotal = downloadTotal;
            this.uploadRate = uploadRate;
            this.downloadRate = downloadRate;
        }

        boolean isActive() {
            return state == STARTING || state == RUNNING || state == SWITCHING || state == STOPPING;
        }

        boolean isBusy() { return state == STARTING || state == SWITCHING || state == STOPPING; }

        Snapshot copy() {
            return new Snapshot(state, nodeId, nodeName, error, startedAt,
                    uploadTotal, downloadTotal, uploadRate, downloadRate);
        }
    }
}
