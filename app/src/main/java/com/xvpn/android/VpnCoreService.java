package com.xvpn.android;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.drawable.Icon;
import android.net.ConnectivityManager;
import android.net.IpPrefix;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;
import android.net.VpnService;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.ParcelFileDescriptor;
import android.os.Process;
import android.os.SystemClock;
import android.net.TrafficStats;
import android.system.OsConstants;
import android.util.Base64;
import android.util.Log;

import io.nekohasekai.libbox.CommandServer;
import io.nekohasekai.libbox.CommandServerHandler;
import io.nekohasekai.libbox.ConnectionOwner;
import io.nekohasekai.libbox.InterfaceUpdateListener;
import io.nekohasekai.libbox.Libbox;
import io.nekohasekai.libbox.LocalDNSTransport;
import io.nekohasekai.libbox.NetworkInterfaceIterator;
import io.nekohasekai.libbox.Notification;
import io.nekohasekai.libbox.OverrideOptions;
import io.nekohasekai.libbox.PlatformInterface;
import io.nekohasekai.libbox.RoutePrefix;
import io.nekohasekai.libbox.RoutePrefixIterator;
import io.nekohasekai.libbox.SetupOptions;
import io.nekohasekai.libbox.StringIterator;
import io.nekohasekai.libbox.SystemProxyStatus;
import io.nekohasekai.libbox.TunOptions;
import io.nekohasekai.libbox.WIFIState;

import org.json.JSONObject;

import java.io.File;
import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URL;
import java.security.KeyStore;
import java.security.cert.Certificate;
import java.util.ArrayList;
import java.util.Enumeration;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Real sing-box 1.13.19 data plane for XVPN Android 1.0.1.
 *
 * Connection state is published only after libbox has accepted the config and
 * established Android's TUN file descriptor.  No optimistic/fake connected
 * state is used.
 */
public final class VpnCoreService extends VpnService implements PlatformInterface, CommandServerHandler {
    private static final String TAG = "XVPN-Core";
    private static final String ACTION_START = "com.xvpn.android.action.START_CORE";
    private static final String ACTION_STOP = "com.xvpn.android.action.STOP_CORE";
    private static final String ACTION_RECONFIGURE = "com.xvpn.android.action.RECONFIGURE_CORE";
    private static final String EXTRA_CONFIG = "config";
    private static final String EXTRA_NODE_ID = "node_id";
    private static final String EXTRA_NODE_NAME = "node_name";
    private static final String EXTRA_ROUTE_LABEL = "route_label";
    // Keep the established channel identity so upgrades preserve the user's
    // notification preference instead of creating a duplicate channel.
    private static final String NOTIFICATION_CHANNEL = "xvpn_vpn_service";
    private static final int NOTIFICATION_ID = 51;
    private static final Object SETUP_LOCK = new Object();
    private static volatile boolean libboxReady;
    private static volatile boolean live;
    private static volatile VpnCoreService activeInstance;

    private final ExecutorService coreExecutor = Executors.newSingleThreadExecutor();
    private final ExecutorService reportIo = Executors.newSingleThreadExecutor();
    private final AtomicBoolean stopRequested = new AtomicBoolean(false);
    private final AtomicBoolean switchInProgress = new AtomicBoolean(false);
    private CommandServer commandServer;
    private ParcelFileDescriptor tunDescriptor;
    private ConnectivityManager connectivity;
    private ConnectivityManager.NetworkCallback networkCallback;
    private volatile Network underlyingNetwork;
    private volatile InterfaceUpdateListener interfaceListener;
    private volatile int nodeId;
    private volatile String nodeName = "";
    private volatile String routeLabel = "智能分流";
    private volatile String sessionId = "";
    private volatile String activeConfig = "";

    private ScheduledExecutorService metricsExecutor;
    private ScheduledExecutorService reportExecutor;
    private long baselineTx;
    private long baselineRx;
    private long previousTx;
    private long previousRx;
    private long previousMetricAt;
    private volatile long uploadTotal;
    private volatile long downloadTotal;
    private volatile long lastNotificationAt;

    public static boolean isLive() { return live; }

    static InetAddress[] resolveProbeHost(String host) throws Exception {
        VpnCoreService service = activeInstance;
        if (service == null || !CoreState.read(service).isActive()) {
            return InetAddress.getAllByName(host);
        }
        Network physical = service.physicalNetwork();
        if (physical == null) throw new IOException("未找到可用的 Wi-Fi 或移动网络");
        return physical.getAllByName(host);
    }

    static void prepareProbeSocket(Socket socket) throws Exception {
        prepareProbeSocket(socket, true);
    }

    /**
     * Makes an entry-probe socket bypass XVPN itself.  A few OEM network
     * stacks reject a socket that is both protected and explicitly bound to
     * their underlying Network, so callers may retry with only protect().
     * protect() is still mandatory: a probe must never recurse into its own
     * VPN tunnel.
     */
    static void prepareProbeSocket(Socket socket, boolean bindUnderlyingNetwork) throws Exception {
        VpnCoreService service = activeInstance;
        if (service == null || !CoreState.read(service).isActive()) return;
        Network physical = service.physicalNetwork();
        if (physical == null) throw new IOException("未找到可用的物理网络");
        if (!service.protect(socket)) throw new IOException("无法让测速连接绕过当前 VPN");
        if (bindUnderlyingNetwork) physical.bindSocket(socket);
    }

    static TunnelHealth checkTunnelHealthNow() {
        VpnCoreService service = activeInstance;
        if (service == null || CoreState.read(service).state != CoreState.RUNNING) {
            return TunnelHealth.failure("VPN 尚未连接");
        }
        return service.probeTunnelOnce();
    }

    static void start(Context context, String config, int nodeId, String nodeName, String routeLabel) {
        Intent intent = new Intent(context, VpnCoreService.class)
                .setAction(ACTION_START)
                .putExtra(EXTRA_CONFIG, config)
                .putExtra(EXTRA_NODE_ID, nodeId)
                .putExtra(EXTRA_NODE_NAME, nodeName == null ? "" : nodeName)
                .putExtra(EXTRA_ROUTE_LABEL, routeLabel == null ? "" : routeLabel);
        context.startForegroundService(intent);
    }

    static void reconfigure(Context context, String config, int nodeId, String nodeName, String routeLabel) {
        Intent intent = new Intent(context, VpnCoreService.class)
                .setAction(ACTION_RECONFIGURE)
                .putExtra(EXTRA_CONFIG, config)
                .putExtra(EXTRA_NODE_ID, nodeId)
                .putExtra(EXTRA_NODE_NAME, nodeName == null ? "" : nodeName)
                .putExtra(EXTRA_ROUTE_LABEL, routeLabel == null ? "" : routeLabel);
        context.startService(intent);
    }

    static void stop(Context context) {
        if (!live) {
            CoreState.Snapshot state = CoreState.read(context);
            CoreState.publishLifecycle(context, CoreState.STOPPED, state.nodeId, state.nodeName, "");
            return;
        }
        Intent intent = new Intent(context, VpnCoreService.class).setAction(ACTION_STOP);
        context.startService(intent);
    }

    @Override public void onCreate() {
        super.onCreate();
        live = true;
        activeInstance = this;
        connectivity = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        createNotificationChannels();
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? "" : intent.getAction();
        if (ACTION_STOP.equals(action)) {
            requestStop();
            return Service.START_NOT_STICKY;
        }
        if (ACTION_RECONFIGURE.equals(action) && intent != null) {
            String config = intent.getStringExtra(EXTRA_CONFIG);
            int nextNodeId = intent.getIntExtra(EXTRA_NODE_ID, 0);
            String nextNodeName = value(intent.getStringExtra(EXTRA_NODE_NAME));
            String nextRouteLabel = value(intent.getStringExtra(EXTRA_ROUTE_LABEL));
            CoreState.Snapshot state = CoreState.read(this);
            if (commandServer == null || state.state != CoreState.RUNNING) return Service.START_NOT_STICKY;
            if (config == null || config.trim().isEmpty() || nextNodeId <= 0) return Service.START_NOT_STICKY;
            if (!switchInProgress.compareAndSet(false, true)) return Service.START_NOT_STICKY;
            CoreState.publishLifecycle(this, CoreState.SWITCHING, nextNodeId, nextNodeName, "");
            updateForegroundNotification("XVPN 正在切换", "正在应用 "
                    + (nextRouteLabel.isEmpty() ? routeLabel : nextRouteLabel) + " · "
                    + (nextNodeName.isEmpty() ? "当前节点" : nextNodeName), false);
            coreExecutor.execute(() -> reconfigureCore(config, nextNodeId, nextNodeName, nextRouteLabel));
            return Service.START_NOT_STICKY;
        }
        if (!ACTION_START.equals(action) || intent == null) return Service.START_NOT_STICKY;
        if (commandServer != null || CoreState.read(this).isActive()) return Service.START_NOT_STICKY;

        String config = intent.getStringExtra(EXTRA_CONFIG);
        nodeId = intent.getIntExtra(EXTRA_NODE_ID, 0);
        nodeName = value(intent.getStringExtra(EXTRA_NODE_NAME));
        routeLabel = value(intent.getStringExtra(EXTRA_ROUTE_LABEL));
        if (routeLabel.isEmpty()) routeLabel = "智能分流";
        if (config == null || config.trim().isEmpty() || nodeId <= 0) {
            failStart("节点配置为空，请刷新节点后重试");
            return Service.START_NOT_STICKY;
        }

        stopRequested.set(false);
        sessionId = UUID.randomUUID().toString();
        CoreState.publishLifecycle(this, CoreState.STARTING, nodeId, nodeName, "");
        startForeground(NOTIFICATION_ID, serviceNotification(
                "XVPN 正在连接", displayNodeName() + " · " + routeLabel, true, false));
        coreExecutor.execute(() -> startCore(config));
        return Service.START_NOT_STICKY;
    }

    @Override public IBinder onBind(Intent intent) {
        IBinder system = super.onBind(intent);
        return system;
    }

    @Override public void onRevoke() {
        requestStop();
    }

    @Override public void onDestroy() {
        live = false;
        if (activeInstance == this) activeInstance = null;
        stopRequested.set(true);
        closeNetworkMonitor();
        shutdownSchedulers(false);
        cleanupCoreObjects();
        CoreState.Snapshot state = CoreState.read(this);
        if (state.isActive()) {
            CoreState.publishLifecycle(this, CoreState.STOPPED,
                    state.nodeId > 0 ? state.nodeId : nodeId,
                    state.nodeName.isEmpty() ? nodeName : state.nodeName, "");
        }
        coreExecutor.shutdownNow();
        // Let the last immutable traffic snapshot reach the panel.  The task
        // no longer touches service state, so a graceful drain is safe here.
        reportIo.shutdown();
        super.onDestroy();
    }

    private void startCore(String config) {
        try {
            ensureLibboxSetup();
            Libbox.checkConfig(config);
            if (stopRequested.get()) return;

            commandServer = new CommandServer(this, this);
            commandServer.start();
            OverrideOptions overrides = new OverrideOptions();
            overrides.setAutoRedirect(false);
            commandServer.startOrReloadService(config, overrides);
            if (stopRequested.get()) {
                stopCoreInternal();
                return;
            }

            TunnelHealth health = waitForTunnelHealth();
            if (!health.healthy) {
                throw new IllegalStateException("隧道已建立，但联网检测失败：" + health.error);
            }

            activeConfig = config;
            CoreState.publishLifecycle(this, CoreState.RUNNING, nodeId, nodeName, "");
            updateForegroundNotification("XVPN 已连接", connectedNotificationText(0L, 0L), true);
            startMetricsAndReporting();
        } catch (Throwable error) {
            logCoreFailure("Core start failed", error);
            cleanupCoreObjects();
            failStart(friendlyCoreError(error));
        }
    }

    private void reconfigureCore(String config, int nextNodeId, String nextNodeName, String nextRouteLabel) {
        final int previousNodeId = nodeId;
        final String previousNodeName = nodeName;
        final String previousRouteLabel = routeLabel;
        final String previousConfig = activeConfig;
        boolean schedulersStopped = false;
        boolean reloadAttempted = false;
        try {
            ensureLibboxSetup();
            // Validate before touching the live tunnel. Invalid Panel data can
            // therefore never tear down an otherwise healthy connection.
            Libbox.checkConfig(config);
            updateMetricsSnapshot();
            ReportSnapshot previous = currentReportSnapshot();
            shutdownSchedulers(false);
            schedulersStopped = true;
            if (previous != null) reportIo.execute(() -> reportTrafficSafe(previous));
            if (stopRequested.get()) {
                stopCoreInternal();
                return;
            }

            nodeId = nextNodeId;
            nodeName = value(nextNodeName);
            if (!value(nextRouteLabel).isEmpty()) routeLabel = value(nextRouteLabel);
            sessionId = UUID.randomUUID().toString();
            CommandServer server = commandServer;
            if (server == null) throw new IllegalStateException("VPN 内核已停止");
            OverrideOptions overrides = new OverrideOptions();
            overrides.setAutoRedirect(false);
            reloadAttempted = true;
            server.startOrReloadService(config, overrides);
            if (stopRequested.get()) {
                stopCoreInternal();
                return;
            }

            TunnelHealth health = waitForTunnelHealth();
            if (!health.healthy) {
                throw new IllegalStateException("新配置联网检测失败：" + health.error);
            }

            activeConfig = config;
            CoreState.publishLifecycle(this, CoreState.RUNNING, nodeId, nodeName, "");
            updateForegroundNotification("XVPN 已连接", connectedNotificationText(0L, 0L), true);
            startMetricsAndReporting();
        } catch (Throwable error) {
            logCoreFailure("Core reconfigure failed", error);
            String message = friendlyCoreError(error);
            CommandServer server = commandServer;
            if (!stopRequested.get() && server != null && (!reloadAttempted || !previousConfig.isEmpty())) {
                try {
                    if (reloadAttempted) {
                        OverrideOptions rollback = new OverrideOptions();
                        rollback.setAutoRedirect(false);
                        server.startOrReloadService(previousConfig, rollback);
                        TunnelHealth restored = waitForTunnelHealth();
                        if (!restored.healthy) {
                            throw new IllegalStateException("原连接恢复后仍无法联网：" + restored.error);
                        }
                    }
                    nodeId = previousNodeId;
                    nodeName = previousNodeName;
                    routeLabel = previousRouteLabel;
                    activeConfig = previousConfig;
                    if (schedulersStopped) {
                        sessionId = UUID.randomUUID().toString();
                        startMetricsAndReporting();
                    }
                    CoreState.publishLifecycle(this, CoreState.RUNNING, nodeId, nodeName, "");
                    updateForegroundNotification("XVPN 已连接", connectedNotificationText(0L, 0L), true);
                    CoreState.notifySwitchFailed(this, "切换失败，已保留原连接 · " + message,
                            previousNodeId, previousRouteLabel);
                    return;
                } catch (Throwable rollbackError) {
                    logCoreFailure("Core rollback failed", rollbackError);
                }
            }
            cleanupCoreObjects();
            failStart(message);
        } finally {
            switchInProgress.set(false);
        }
    }

    private void ensureLibboxSetup() {
        synchronized (SETUP_LOCK) {
            if (libboxReady) return;
            File base = getFilesDir();
            File working = getExternalFilesDir(null);
            if (working == null) working = new File(base, "core-work");
            File temp = new File(getCacheDir(), "libbox");
            base.mkdirs();
            working.mkdirs();
            temp.mkdirs();

            SetupOptions options = new SetupOptions();
            options.setBasePath(base.getAbsolutePath());
            options.setWorkingPath(working.getAbsolutePath());
            options.setTempPath(temp.getAbsolutePath());
            options.setFixAndroidStack(BuildConfig.DEBUG || Build.VERSION.SDK_INT >= 28);
            options.setLogMaxLines(1200L);
            options.setDebug(BuildConfig.DEBUG);
            Libbox.setup(options);
            libboxReady = true;
            Log.i(TAG, "libbox ready: " + Libbox.version());
        }
    }

    private void requestStop() {
        if (!stopRequested.compareAndSet(false, true)) return;
        CoreState.Snapshot state = CoreState.read(this);
        CoreState.publishLifecycle(this, CoreState.STOPPING,
                state.nodeId > 0 ? state.nodeId : nodeId,
                state.nodeName.isEmpty() ? nodeName : state.nodeName, "");
        updateForegroundNotification("XVPN 正在断开", displayNodeName() + " · 正在保存连接数据", false);
        coreExecutor.execute(this::stopCoreInternal);
    }

    private void stopCoreInternal() {
        updateMetricsSnapshot();
        shutdownSchedulers(true);
        cleanupCoreObjects();
        closeNetworkMonitor();
        CoreState.publishLifecycle(this, CoreState.STOPPED, nodeId, nodeName, "");
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    private void cleanupCoreObjects() {
        CommandServer server = commandServer;
        commandServer = null;
        if (server != null) {
            try { server.closeService(); } catch (Throwable ignored) {}
        }
        ParcelFileDescriptor descriptor = tunDescriptor;
        tunDescriptor = null;
        if (descriptor != null) {
            try { descriptor.close(); } catch (Exception ignored) {}
        }
        if (server != null) {
            try { server.close(); } catch (Throwable ignored) {}
        }
    }

    private void failStart(String message) {
        CoreState.publishLifecycle(this, CoreState.ERROR, nodeId, nodeName, value(message));
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    /**
     * End-to-end data-plane check. These Java connections are deliberately not
     * protected, so Android sends them into XVPN's TUN and they exercise DNS,
     * routing, the selected protocol and the remote egress together.
     */
    private TunnelHealth waitForTunnelHealth() {
        TunnelHealth last = TunnelHealth.failure("网络暂无响应");
        for (int attempt = 0; attempt < 2 && !stopRequested.get(); attempt++) {
            last = probeTunnelOnce();
            if (last.healthy) return last;
            if (attempt == 0) {
                try { Thread.sleep(450L); }
                catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    return TunnelHealth.failure("联网检测已取消");
                }
            }
        }
        return last;
    }

    private TunnelHealth probeTunnelOnce() {
        String[] targets = {
                "https://www.gstatic.com/generate_204",
                "https://github.com/favicon.ico"
        };
        String lastError = "无法访问检测网站";
        for (String target : targets) {
            HttpURLConnection connection = null;
            long started = System.nanoTime();
            try {
                connection = (HttpURLConnection) new URL(target).openConnection();
                connection.setConnectTimeout(2800);
                connection.setReadTimeout(2800);
                connection.setInstanceFollowRedirects(false);
                connection.setUseCaches(false);
                connection.setRequestMethod("GET");
                connection.setRequestProperty("Connection", "close");
                connection.setRequestProperty("User-Agent", "XVPN-Android/" + BuildConfig.VERSION_NAME);
                connection.setRequestProperty("X-XVPN-Health", "1");
                int status = connection.getResponseCode();
                if (status >= 200 && status < 400) {
                    long latency = Math.max(1L, (System.nanoTime() - started) / 1_000_000L);
                    return TunnelHealth.success(new URL(target).getHost(), latency);
                }
                lastError = new URL(target).getHost() + " 返回 HTTP " + status;
            } catch (Exception error) {
                String name = error.getClass().getSimpleName();
                if (name.contains("UnknownHost")) lastError = "DNS 解析失败";
                else if (name.contains("SocketTimeout")) lastError = "代理出口响应超时";
                else if (name.contains("SSL")) lastError = "代理出口 TLS 握手失败";
                else if (name.contains("Connect")) lastError = "代理出口连接失败";
                else lastError = value(error.getMessage()).isEmpty() ? "联网检测失败" : value(error.getMessage());
            } finally {
                if (connection != null) connection.disconnect();
            }
        }
        return TunnelHealth.failure(lastError);
    }

    // ----- Android TUN / libbox platform bridge -----

    @Override public boolean usePlatformAutoDetectInterfaceControl() { return true; }

    @Override public void autoDetectInterfaceControl(int fd) {
        if (!protect(fd)) Log.w(TAG, "Unable to protect core socket " + fd);
    }

    @Override public int openTun(TunOptions options) {
        if (VpnService.prepare(this) != null) throw new IllegalStateException("缺少 VPN 授权");

        Builder builder = new Builder().setSession("XVPN · " + displayNodeName()).setMtu(options.getMTU());
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) builder.setMetered(false);
        Network physical = physicalNetwork();
        if (physical != null) builder.setUnderlyingNetworks(new Network[]{physical});

        addAddresses(builder, options.getInet4Address());
        addAddresses(builder, options.getInet6Address());

        if (options.getAutoRoute()) {
            builder.addDnsServer(options.getDNSServerAddress().getValue());
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                int v4 = addRoutes(builder, options.getInet4RouteAddress(), false);
                if (v4 == 0 && options.getInet4Address().hasNext()) builder.addRoute("0.0.0.0", 0);
                int v6 = addRoutes(builder, options.getInet6RouteAddress(), false);
                if (v6 == 0 && options.getInet6Address().hasNext()) builder.addRoute("::", 0);
                addRoutes(builder, options.getInet4RouteExcludeAddress(), true);
                addRoutes(builder, options.getInet6RouteExcludeAddress(), true);
            } else {
                addLegacyRoutes(builder, options.getInet4RouteRange());
                addLegacyRoutes(builder, options.getInet6RouteRange());
            }
            addPackages(builder, options.getIncludePackage(), true);
            addPackages(builder, options.getExcludePackage(), false);
        }

        ParcelFileDescriptor descriptor = builder.establish();
        if (descriptor == null) throw new IllegalStateException("系统未能建立 VPN 接口");
        ParcelFileDescriptor old = tunDescriptor;
        tunDescriptor = descriptor;
        if (old != null) try { old.close(); } catch (Exception ignored) {}
        return descriptor.getFd();
    }

    private void addAddresses(Builder builder, RoutePrefixIterator iterator) {
        if (iterator == null) return;
        while (iterator.hasNext()) {
            RoutePrefix prefix = iterator.next();
            builder.addAddress(prefix.address(), prefix.prefix());
        }
    }

    // Calls are guarded by SDK_INT >= 33 in openTun; keep the project AndroidX-free.
    @android.annotation.SuppressLint("UseRequiresApi")
    @android.annotation.TargetApi(Build.VERSION_CODES.TIRAMISU)
    private int addRoutes(Builder builder, RoutePrefixIterator iterator, boolean exclude) {
        int count = 0;
        if (iterator == null) return count;
        while (iterator.hasNext()) {
            RoutePrefix route = iterator.next();
            IpPrefix prefix;
            try {
                prefix = new IpPrefix(InetAddress.getByName(route.address()), route.prefix());
            } catch (Exception invalidRoute) {
                throw new IllegalArgumentException("内核返回了无效路由：" + route.string(), invalidRoute);
            }
            if (exclude) builder.excludeRoute(prefix); else builder.addRoute(prefix);
            count++;
        }
        return count;
    }

    private void addLegacyRoutes(Builder builder, RoutePrefixIterator iterator) {
        if (iterator == null) return;
        while (iterator.hasNext()) {
            RoutePrefix route = iterator.next();
            builder.addRoute(route.address(), route.prefix());
        }
    }

    private void addPackages(Builder builder, StringIterator iterator, boolean allowed) {
        if (iterator == null) return;
        while (iterator.hasNext()) {
            String packageName = iterator.next();
            if (packageName == null || packageName.isEmpty()) continue;
            try {
                if (allowed) builder.addAllowedApplication(packageName);
                else builder.addDisallowedApplication(packageName);
            } catch (PackageManager.NameNotFoundException error) {
                Log.w(TAG, "Package rule skipped: " + packageName);
            }
        }
    }

    @Override public boolean useProcFS() { return Build.VERSION.SDK_INT < Build.VERSION_CODES.Q; }

    @Override public ConnectionOwner findConnectionOwner(int protocol, String sourceAddress, int sourcePort,
                                                          String destinationAddress, int destinationPort) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) throw new IllegalStateException("连接归属查询不可用");
        int uid = connectivity.getConnectionOwnerUid(protocol,
                new InetSocketAddress(sourceAddress, sourcePort),
                new InetSocketAddress(destinationAddress, destinationPort));
        if (uid == Process.INVALID_UID) throw new IllegalStateException("未找到连接所属应用");
        String[] packages = getPackageManager().getPackagesForUid(uid);
        ConnectionOwner owner = new ConnectionOwner();
        owner.setUserId(uid);
        owner.setUserName(packages != null && packages.length > 0 ? packages[0] : "");
        owner.setProcessPath("");
        owner.setAndroidPackageNames(new StringArray(packages));
        return owner;
    }

    @Override public void startDefaultInterfaceMonitor(InterfaceUpdateListener listener) {
        interfaceListener = listener;
        if (networkCallback == null) {
            networkCallback = new ConnectivityManager.NetworkCallback() {
                @Override public void onAvailable(Network network) { underlyingNetwork = network; updateDefaultInterface(); }
                @Override public void onLost(Network network) {
                    if (network.equals(underlyingNetwork)) underlyingNetwork = null;
                    updateDefaultInterface();
                }
                @Override public void onLinkPropertiesChanged(Network network, LinkProperties properties) { updateDefaultInterface(); }
                @Override public void onCapabilitiesChanged(Network network, NetworkCapabilities capabilities) { updateDefaultInterface(); }
            };
            NetworkRequest request = new NetworkRequest.Builder()
                    .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                    .addCapability(NetworkCapabilities.NET_CAPABILITY_NOT_RESTRICTED)
                    .addCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
                    .build();
            Handler main = new Handler(Looper.getMainLooper());
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                connectivity.registerBestMatchingNetworkCallback(request, networkCallback, main);
            } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                connectivity.requestNetwork(request, networkCallback, main);
            } else {
                connectivity.registerDefaultNetworkCallback(networkCallback, main);
            }
        }
        updateDefaultInterface();
    }

    @Override public void closeDefaultInterfaceMonitor(InterfaceUpdateListener listener) {
        interfaceListener = null;
        closeNetworkMonitor();
    }

    private void closeNetworkMonitor() {
        ConnectivityManager.NetworkCallback callback = networkCallback;
        networkCallback = null;
        underlyingNetwork = null;
        if (callback != null && connectivity != null) {
            try { connectivity.unregisterNetworkCallback(callback); } catch (Exception ignored) {}
        }
    }

    private void updateDefaultInterface() {
        InterfaceUpdateListener listener = interfaceListener;
        if (listener == null) return;
        try {
            Network network = physicalNetwork();
            LinkProperties link = network == null ? null : connectivity.getLinkProperties(network);
            if (link == null || link.getInterfaceName() == null) {
                listener.updateDefaultInterface("", -1, false, false);
                return;
            }
            java.net.NetworkInterface netIf = java.net.NetworkInterface.getByName(link.getInterfaceName());
            NetworkCapabilities caps = connectivity.getNetworkCapabilities(network);
            boolean metered = caps == null || !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED);
            listener.updateDefaultInterface(link.getInterfaceName(), netIf == null ? -1 : netIf.getIndex(), metered, false);
        } catch (Exception error) {
            Log.w(TAG, "Default interface update failed", error);
            listener.updateDefaultInterface("", -1, false, false);
        }
    }

    @Override public NetworkInterfaceIterator getInterfaces() {
        List<io.nekohasekai.libbox.NetworkInterface> out = new ArrayList<>();
        for (Network network : connectivity.getAllNetworks()) {
            LinkProperties link = connectivity.getLinkProperties(network);
            NetworkCapabilities caps = connectivity.getNetworkCapabilities(network);
            if (link == null || caps == null || link.getInterfaceName() == null) continue;
            // Never feed XVPN's own TUN back into sing-box auto detection.
            if (!caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
                    || caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) continue;
            try {
                java.net.NetworkInterface source = java.net.NetworkInterface.getByName(link.getInterfaceName());
                if (source == null) continue;
                io.nekohasekai.libbox.NetworkInterface item = new io.nekohasekai.libbox.NetworkInterface();
                item.setName(source.getName());
                item.setIndex(source.getIndex());
                item.setMTU(source.getMTU());
                item.setFlags(interfaceFlags(source, caps));
                item.setType(interfaceType(caps));
                item.setMetered(!caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED));

                List<String> addresses = new ArrayList<>();
                for (java.net.InterfaceAddress address : source.getInterfaceAddresses()) {
                    String host = address.getAddress().getHostAddress();
                    if (host == null) continue;
                    int zone = host.indexOf('%');
                    if (zone >= 0) host = host.substring(0, zone);
                    addresses.add(host + "/" + address.getNetworkPrefixLength());
                }
                List<String> dns = new ArrayList<>();
                for (java.net.InetAddress server : link.getDnsServers()) {
                    if (server.getHostAddress() != null) dns.add(server.getHostAddress());
                }
                item.setAddresses(new StringArray(addresses));
                item.setDNSServer(new StringArray(dns));
                out.add(item);
            } catch (Exception error) {
                Log.w(TAG, "Interface skipped: " + link.getInterfaceName(), error);
            }
        }
        return new InterfaceArray(out);
    }

    private Network physicalNetwork() {
        Network preferred = underlyingNetwork;
        NetworkCapabilities preferredCaps = preferred == null ? null : connectivity.getNetworkCapabilities(preferred);
        if (preferredCaps != null && preferredCaps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
                && !preferredCaps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) return preferred;
        Network fallback = null;
        for (Network network : connectivity.getAllNetworks()) {
            NetworkCapabilities caps = connectivity.getNetworkCapabilities(network);
            if (caps == null || !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                    || !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
                    || caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) continue;
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
                    || caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)
                    || caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) return network;
            if (fallback == null) fallback = network;
        }
        return fallback;
    }

    private int interfaceFlags(java.net.NetworkInterface source, NetworkCapabilities caps) {
        int flags = 0;
        try { if (source.isUp()) flags |= OsConstants.IFF_UP | OsConstants.IFF_RUNNING; } catch (Exception ignored) {}
        try { if (source.isLoopback()) flags |= OsConstants.IFF_LOOPBACK; } catch (Exception ignored) {}
        try { if (source.isPointToPoint()) flags |= OsConstants.IFF_POINTOPOINT; } catch (Exception ignored) {}
        try { if (source.supportsMulticast()) flags |= OsConstants.IFF_MULTICAST; } catch (Exception ignored) {}
        return flags;
    }

    private int interfaceType(NetworkCapabilities caps) {
        if (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) return Libbox.InterfaceTypeWIFI;
        if (caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) return Libbox.InterfaceTypeCellular;
        if (caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) return Libbox.InterfaceTypeEthernet;
        return Libbox.InterfaceTypeOther;
    }

    @Override public boolean underNetworkExtension() { return false; }
    @Override public boolean includeAllNetworks() { return false; }
    @Override public WIFIState readWIFIState() { return null; }
    @Override public LocalDNSTransport localDNSTransport() { return null; }
    @Override public void clearDNSCache() {}

    @Override public StringIterator systemCertificates() {
        List<String> certificates = new ArrayList<>();
        try {
            KeyStore store = KeyStore.getInstance("AndroidCAStore");
            store.load(null);
            Enumeration<String> aliases = store.aliases();
            while (aliases.hasMoreElements()) {
                Certificate certificate = store.getCertificate(aliases.nextElement());
                if (certificate == null) continue;
                certificates.add("-----BEGIN CERTIFICATE-----\n"
                        + Base64.encodeToString(certificate.getEncoded(), Base64.NO_WRAP)
                        + "\n-----END CERTIFICATE-----");
            }
        } catch (Exception error) {
            Log.w(TAG, "Unable to read Android CA store", error);
        }
        return new StringArray(certificates);
    }

    @Override public void sendNotification(Notification notification) {
        if (notification != null && notification.getBody() != null) {
            Log.i(TAG, "Core notification: " + notification.getBody());
        }
    }

    // ----- libbox command callbacks -----

    @Override public void serviceStop() { requestStop(); }
    @Override public void serviceReload() { Log.i(TAG, "Core requested reload; keeping current validated profile"); }

    @Override public SystemProxyStatus getSystemProxyStatus() {
        SystemProxyStatus status = new SystemProxyStatus();
        status.setAvailable(false);
        status.setEnabled(false);
        return status;
    }

    @Override public void setSystemProxyEnabled(boolean enabled) {}
    @Override public void writeDebugMessage(String message) { if (BuildConfig.DEBUG) Log.d(TAG, value(message)); }

    // ----- Metrics and Panel cumulative reporting -----

    private void startMetricsAndReporting() {
        long tx = safeTraffic(TrafficStats.getUidTxBytes(Process.myUid()));
        long rx = safeTraffic(TrafficStats.getUidRxBytes(Process.myUid()));
        baselineTx = previousTx = tx;
        baselineRx = previousRx = rx;
        previousMetricAt = SystemClock.elapsedRealtime();
        uploadTotal = downloadTotal = 0L;

        metricsExecutor = Executors.newSingleThreadScheduledExecutor();
        metricsExecutor.scheduleWithFixedDelay(this::updateMetricsSnapshot, 1L, 1L, TimeUnit.SECONDS);

        SharedPreferences prefs = getSharedPreferences("xvpn_preferences_v1", MODE_PRIVATE);
        if (!prefs.getBoolean("traffic_reporting", false)) return;
        long interval = prefs.getLong("traffic_report_interval_seconds", 300L);
        interval = Math.max(60L, Math.min(3600L, interval));
        reportExecutor = Executors.newSingleThreadScheduledExecutor();
        reportExecutor.scheduleWithFixedDelay(() -> {
            ReportSnapshot report = currentReportSnapshot();
            if (report != null) reportIo.execute(() -> reportTrafficSafe(report));
        }, 0L, interval, TimeUnit.SECONDS);
    }

    private void updateMetricsSnapshot() {
        int state = CoreState.read(this).state;
        if (state != CoreState.RUNNING && state != CoreState.SWITCHING
                && state != CoreState.STOPPING && !stopRequested.get()) return;
        long now = SystemClock.elapsedRealtime();
        long tx = safeTraffic(TrafficStats.getUidTxBytes(Process.myUid()));
        long rx = safeTraffic(TrafficStats.getUidRxBytes(Process.myUid()));
        long elapsed = Math.max(1L, now - previousMetricAt);
        long upRate = Math.max(0L, tx - previousTx) * 1000L / elapsed;
        long downRate = Math.max(0L, rx - previousRx) * 1000L / elapsed;
        uploadTotal = Math.max(0L, tx - baselineTx);
        downloadTotal = Math.max(0L, rx - baselineRx);
        previousTx = tx;
        previousRx = rx;
        previousMetricAt = now;
        CoreState.publishMetrics(this, uploadTotal, downloadTotal, upRate, downRate);
        if (state == CoreState.RUNNING && now - lastNotificationAt >= 2000L) {
            lastNotificationAt = now;
            updateForegroundNotification("XVPN 已连接", connectedNotificationText(upRate, downRate), true);
        }
    }

    private ReportSnapshot currentReportSnapshot() {
        SharedPreferences prefs = getSharedPreferences("xvpn_preferences_v1", MODE_PRIVATE);
        if (!prefs.getBoolean("traffic_reporting", false) || nodeId <= 0 || sessionId.isEmpty()) return null;
        return new ReportSnapshot(nodeId, sessionId, uploadTotal, downloadTotal);
    }

    private void reportTrafficSafe(ReportSnapshot report) {
        if (report == null) return;
        try {
            SharedPreferences prefs = getSharedPreferences("xvpn_preferences_v1", MODE_PRIVATE);
            String panel = ApiClient.normalizePanelBase(prefs.getString("base_url", ""));
            String auth = SecureTokenStore.load(prefs);
            if (panel.isEmpty() || auth == null || auth.isEmpty()) return;

            String deviceId = persistentDeviceId();
            JSONObject body = new JSONObject()
                    .put("device_id", deviceId)
                    .put("session_id", report.sessionId)
                    .put("node_id", report.nodeId)
                    .put("upload_total_bytes", Math.max(0L, report.uploadTotal))
                    .put("download_total_bytes", Math.max(0L, report.downloadTotal))
                    .put("app_version", BuildConfig.VERSION_NAME);
            ApiClient.request(panel, "/traffic/report", "POST", auth, body);
        } catch (ApiClient.ApiException error) {
            if (error.isAuthFailure()) {
                CoreState.notifyAuthInvalid(this, error.code);
                requestStop();
            } else if ("INVALID_NODE_ID".equals(error.code)) {
                // A final report from the previous hot-switched node must not tear down the new node.
                if (report.sessionId.equals(sessionId)) {
                    CoreState.notifyNodeInvalid(this);
                    requestStop();
                }
            } else if (!"TRAFFIC_USER_REQUIRED".equals(error.code)) {
                Log.w(TAG, "Traffic report rejected: " + error.code);
            }
        } catch (Exception error) {
            Log.w(TAG, "Traffic report deferred", error);
        }
    }

    private String persistentDeviceId() {
        SharedPreferences identity = getSharedPreferences("xvpn_device_identity_v1", MODE_PRIVATE);
        String id = identity.getString("device_id", "");
        if (id == null || id.length() < 8) {
            id = UUID.randomUUID().toString();
            identity.edit().putString("device_id", id).apply();
        }
        return id;
    }

    private void shutdownSchedulers(boolean finalReport) {
        ReportSnapshot finalSnapshot = finalReport ? currentReportSnapshot() : null;
        ScheduledExecutorService metrics = metricsExecutor;
        metricsExecutor = null;
        if (metrics != null) metrics.shutdownNow();

        ScheduledExecutorService reports = reportExecutor;
        reportExecutor = null;
        if (reports != null) reports.shutdownNow();
        if (finalSnapshot != null) reportIo.execute(() -> reportTrafficSafe(finalSnapshot));
    }

    private long safeTraffic(long value) { return value < 0L ? 0L : value; }

    // ----- Foreground notification -----

    private void createNotificationChannels() {
        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        NotificationChannel channel = new NotificationChannel(
                NOTIFICATION_CHANNEL, "XVPN 连接状态", NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("显示当前节点、分流模式、实时速率与安全断开操作");
        channel.setShowBadge(false);
        channel.setSound(null, null);
        channel.enableVibration(false);
        channel.enableLights(false);
        channel.setLockscreenVisibility(android.app.Notification.VISIBILITY_PRIVATE);
        manager.createNotificationChannel(channel);
    }

    private android.app.Notification serviceNotification(String title, String content,
                                                         boolean ongoing, boolean chronometer) {
        PendingIntent open = PendingIntent.getActivity(this, 0,
                new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent stop = PendingIntent.getService(this, 1,
                new Intent(this, VpnCoreService.class).setAction(ACTION_STOP),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        android.app.Notification.Builder builder = new android.app.Notification.Builder(this, NOTIFICATION_CHANNEL);
        CoreState.Snapshot snapshot = CoreState.read(this);
        String connectionLine = displayNodeName() + " · " + routeLabel;
        android.app.Notification.Style style;
        if (chronometer) {
            style = new android.app.Notification.InboxStyle()
                    .setBigContentTitle(title)
                    .addLine(connectionLine)
                    .addLine("↑ " + compactRate(snapshot.uploadRate) + "    ↓ " + compactRate(snapshot.downloadRate))
                    .addLine("网络健康 · DNS 与代理出口已验证")
                    .setSummaryText("PRIVATE NETWORK");
        } else {
            style = new android.app.Notification.BigTextStyle()
                    .setBigContentTitle(title)
                    .bigText(content)
                    .setSummaryText("PRIVATE NETWORK");
        }
        android.app.Notification publicVersion = new android.app.Notification.Builder(this, NOTIFICATION_CHANNEL)
                .setSmallIcon(R.drawable.ic_vpn_status)
                .setContentTitle(chronometer ? "XVPN 已连接" : title)
                .setContentText(chronometer ? "VPN 保护正在运行" : "VPN 服务状态")
                .setOngoing(ongoing)
                .setOnlyAlertOnce(true)
                .setColor(chronometer ? 0xFF22B78B : 0xFF6487FF)
                .setShowWhen(false)
                .setVisibility(android.app.Notification.VISIBILITY_PUBLIC)
                .setCategory(android.app.Notification.CATEGORY_SERVICE)
                .build();
        builder.setSmallIcon(R.drawable.ic_vpn_status)
                .setContentTitle(title)
                .setContentText(chronometer ? connectionLine : content)
                .setSubText("PRIVATE NETWORK")
                .setStyle(style)
                .setContentIntent(open)
                .setOngoing(ongoing)
                .setAutoCancel(false)
                .setOnlyAlertOnce(true)
                .setColor(chronometer ? 0xFF22B78B : 0xFF6487FF)
                .setColorized(false)
                .setVisibility(android.app.Notification.VISIBILITY_PRIVATE)
                .setPublicVersion(publicVersion)
                .setCategory(android.app.Notification.CATEGORY_SERVICE)
                .addAction(new android.app.Notification.Action.Builder(
                        Icon.createWithResource(this, R.drawable.ic_vpn_status), "安全断开", stop).build());
        if (chronometer) {
            long startedAt = CoreState.read(this).startedAt;
            builder.setWhen(startedAt > 0L ? startedAt : System.currentTimeMillis())
                    .setUsesChronometer(true)
                    .setShowWhen(true);
        } else {
            builder.setShowWhen(false).setUsesChronometer(false);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            builder.setForegroundServiceBehavior(android.app.Notification.FOREGROUND_SERVICE_IMMEDIATE);
        }
        return builder.build();
    }

    private void updateForegroundNotification(String title, String text, boolean chronometer) {
        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        manager.notify(NOTIFICATION_ID, serviceNotification(title, text, true, chronometer));
    }

    private String displayNodeName() { return nodeName.isEmpty() ? "XVPN 节点" : nodeName; }

    private String connectedNotificationText(long uploadRate, long downloadRate) {
        return displayNodeName() + " · " + routeLabel + "   ↑ " + compactRate(uploadRate)
                + "   ↓ " + compactRate(downloadRate);
    }

    private String compactRate(long bytesPerSecond) {
        double value = Math.max(0L, bytesPerSecond);
        String[] units = {"B/s", "KB/s", "MB/s", "GB/s"};
        int unit = 0;
        while (value >= 1024d && unit < units.length - 1) {
            value /= 1024d;
            unit++;
        }
        if (unit == 0) return ((long) value) + " " + units[unit];
        return String.format(Locale.US, value >= 100d ? "%.0f %s" : "%.1f %s", value, units[unit]);
    }

    private String friendlyCoreError(Throwable error) {
        String message = error == null ? "" : value(error.getMessage());
        String lower = message.toLowerCase(Locale.ROOT);
        if (lower.contains("permission") || lower.contains("prepared") || lower.contains("revoked")) {
            return "VPN 授权已失效，请重新连接";
        }
        if (lower.contains("detour to an empty direct outbound")) {
            return "DNS 直连配置与当前内核不兼容，请更新客户端";
        }
        if (lower.contains("reality") && lower.contains("public")) return "节点 REALITY 公钥配置无效";
        if (lower.contains("unknown outbound") || lower.contains("unsupported")) return "当前节点协议暂不受此版本支持";
        if (message.isEmpty()) return "内核启动失败，请检查节点配置";
        message = message.replaceAll("(?i)(vless|trojan|vmess|ss|shadowsocks|hysteria2|hy2|tuic|anytls)://[^\\s]+", "$1://••••")
                .replaceAll("(?i)(\"(?:password|uuid|private_key|token)\"\\s*:\\s*\")[^\"]*(\")", "$1••••$2")
                .replaceAll("(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "••••");
        return message.length() > 220 ? message.substring(0, 220) + "…" : message;
    }

    private void logCoreFailure(String stage, Throwable error) {
        if (BuildConfig.DEBUG) Log.e(TAG, stage, error);
        else Log.e(TAG, stage + ": " + friendlyCoreError(error));
    }

    private static String value(String text) { return text == null ? "" : text.trim(); }

    static final class TunnelHealth {
        final boolean healthy;
        final String endpoint;
        final long latencyMs;
        final String error;

        private TunnelHealth(boolean healthy, String endpoint, long latencyMs, String error) {
            this.healthy = healthy;
            this.endpoint = value(endpoint);
            this.latencyMs = Math.max(0L, latencyMs);
            this.error = value(error);
        }

        static TunnelHealth success(String endpoint, long latencyMs) {
            return new TunnelHealth(true, endpoint, latencyMs, "");
        }

        static TunnelHealth failure(String error) {
            return new TunnelHealth(false, "", 0L, error);
        }
    }

    private static final class ReportSnapshot {
        final int nodeId;
        final String sessionId;
        final long uploadTotal;
        final long downloadTotal;

        ReportSnapshot(int nodeId, String sessionId, long uploadTotal, long downloadTotal) {
            this.nodeId = nodeId;
            this.sessionId = sessionId;
            this.uploadTotal = uploadTotal;
            this.downloadTotal = downloadTotal;
        }
    }

    private static final class StringArray implements StringIterator {
        private final List<String> values;
        private int index;

        StringArray(String[] source) {
            values = new ArrayList<>();
            if (source != null) for (String item : source) if (item != null) values.add(item);
        }

        StringArray(List<String> source) {
            values = source == null ? new ArrayList<>() : new ArrayList<>(source);
        }

        @Override public boolean hasNext() { return index < values.size(); }
        @Override public int len() { return Math.max(0, values.size() - index); }
        @Override public String next() { return hasNext() ? values.get(index++) : ""; }
    }

    private static final class InterfaceArray implements NetworkInterfaceIterator {
        private final List<io.nekohasekai.libbox.NetworkInterface> values;
        private int index;

        InterfaceArray(List<io.nekohasekai.libbox.NetworkInterface> values) { this.values = values; }
        @Override public boolean hasNext() { return index < values.size(); }
        @Override public io.nekohasekai.libbox.NetworkInterface next() {
            return hasNext() ? values.get(index++) : null;
        }
    }
}
