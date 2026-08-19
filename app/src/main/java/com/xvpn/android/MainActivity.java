package com.xvpn.android;

import android.Manifest;
import android.animation.Animator;
import android.animation.ValueAnimator;
import android.app.Dialog;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.graphics.drawable.LayerDrawable;
import android.os.Build;
import android.net.Uri;
import android.net.VpnService;
import android.os.Bundle;
import android.os.SystemClock;
import android.text.InputType;
import android.view.Gravity;
import android.view.HapticFeedbackConstants;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.view.animation.OvershootInterpolator;
import android.view.animation.PathInterpolator;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.Space;
import android.widget.TextView;
import android.util.SparseArray;

import org.json.JSONObject;

import java.net.HttpURLConnection;
import java.net.ConnectException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.net.UnknownHostException;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.CompletionService;
import java.util.concurrent.ExecutorCompletionService;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

public final class MainActivity extends android.app.Activity {
    private static final String PREFS = "xvpn_preferences_v1";
    private static final String DEFAULT_BASE_URL = ApiClient.DEFAULT_PANEL_BASE;
    private static final String DEFAULT_LATENCY_URL = "https://www.gstatic.com/generate_204";
    private static final int TAB_HOME = 0;
    private static final int TAB_MINE = 1;
    private static final int REQUEST_VPN_PERMISSION = 509;
    private static final int REQUEST_NOTIFICATION_PERMISSION = 510;
    private static final long UPDATE_CHECK_INTERVAL_MS = 12L * 60L * 60L * 1000L;

    private SharedPreferences prefs;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final ExecutorService probeExecutor = Executors.newFixedThreadPool(4);
    private Palette p;
    private String baseUrl;
    private String token;
    private String username = "";
    private JSONObject bootstrap;
    private NodeCatalog catalog = new NodeCatalog();
    private NodeCatalog.Node selectedNode;
    private RouteMode routeMode = RouteMode.SMART;
    private int currentTab = TAB_HOME;
    private int screenGeneration = 0;
    private volatile boolean nodeScanRunning = false;
    private volatile boolean refreshing = false;
    private FrameLayout currentRoot;
    private Dialog activeNodePicker;
    private volatile boolean updateCheckRunning = false;
    private View activeNotice;
    private RefreshActionView activeRefreshControl;
    private String pendingCoreConfig;
    private int pendingCoreNodeId;
    private String pendingCoreNodeName;
    private String pendingCoreRouteLabel;
    private boolean coreReceiverRegistered;
    private final BroadcastReceiver coreReceiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            if (CoreState.ACTION_AUTH_INVALID.equals(intent.getAction())) {
                String code = intent.getStringExtra("code");
                forceLogout(code == null ? "UNAUTHORIZED" : code, null);
                return;
            }
            if (CoreState.ACTION_NODE_INVALID.equals(intent.getAction())) {
                toast("当前节点已失效，正在刷新节点列表");
                if (!refreshing) {
                    refreshing = true;
                    if (activeRefreshControl != null) activeRefreshControl.startRefreshMotion();
                    bootstrapSession(false);
                }
                return;
            }
            if(CoreState.ACTION_SWITCH_FAILED.equals(intent.getAction())){
                String message=intent.getStringExtra("message");
                int restoredNodeId=intent.getIntExtra("node_id",0);
                NodeCatalog.Node restoredNode=catalog.find(restoredNodeId);
                if(restoredNode!=null){selectedNode=restoredNode;prefs.edit().putInt("selected_node_id",restoredNode.id).apply();}
                String restoredLabel=intent.getStringExtra("route_label");
                RouteMode restoredMode=RouteMode.GLOBAL.label.equals(restoredLabel)?RouteMode.GLOBAL:RouteMode.SMART;
                boolean modeChanged=routeMode!=restoredMode;
                routeMode=restoredMode;prefs.edit().putString("route_mode",routeMode.key).apply();
                toast(message==null||message.isEmpty()?"切换失败，已保留原连接":message);
                if(modeChanged&&currentTab==TAB_HOME)showShell(TAB_HOME);else refreshHomeBoundViews();
                return;
            }
            refreshCoreBoundViews();
        }
    };

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        baseUrl = ApiClient.migratePanelBase(prefs.getString("base_url", DEFAULT_BASE_URL));
        if (!ApiClient.isValidPanelBase(baseUrl)) baseUrl = DEFAULT_BASE_URL;
        // VpnCoreService runs independently and reads this normalized endpoint
        // when it reports the current session's cumulative traffic.
        prefs.edit().putString("base_url", baseUrl).apply();
        token = SecureTokenStore.load(prefs);
        routeMode = RouteMode.fromKey(prefs.getString("route_mode", "smart"));
        if(savedInstanceState!=null){
            pendingCoreConfig=savedInstanceState.getString("pending_core_config");
            pendingCoreNodeId=savedInstanceState.getInt("pending_core_node_id",0);
            pendingCoreNodeName=savedInstanceState.getString("pending_core_node_name");
            pendingCoreRouteLabel=savedInstanceState.getString("pending_core_route_label");
        }
        CoreState.reconcileAfterProcessStart(this);
        refreshPalette();
        applySystemBars();
        showLaunchTransition();
    }

    @Override protected void onSaveInstanceState(Bundle outState){
        super.onSaveInstanceState(outState);
        if(pendingCoreConfig!=null)outState.putString("pending_core_config",pendingCoreConfig);
        outState.putInt("pending_core_node_id",pendingCoreNodeId);
        if(pendingCoreNodeName!=null)outState.putString("pending_core_node_name",pendingCoreNodeName);
        if(pendingCoreRouteLabel!=null)outState.putString("pending_core_route_label",pendingCoreRouteLabel);
    }

    private void showLaunchTransition() {
        final long started = SystemClock.uptimeMillis();
        final AuroraView root = new AuroraView(this, p.dark);
        root.setBackgroundColor(p.bg);
        currentRoot = root;

        LinearLayout center = column();
        center.setGravity(Gravity.CENTER);
        center.setPadding(dp(28), statusBarInset() + dp(28), dp(28), dp(36));
        root.addView(center, matchMatch());

        BrandMarkView launchLogo = brandMark();
        LinearLayout.LayoutParams launchLogoLp = new LinearLayout.LayoutParams(dp(92), dp(92));
        launchLogoLp.gravity = Gravity.CENTER_HORIZONTAL;
        center.addView(launchLogo, launchLogoLp);
        gap(center, 14);

        TextView brand = text("XVPN", 36, p.ink, true);
        brand.setLetterSpacing(.14f);
        brand.setGravity(Gravity.CENTER);
        center.addView(brand, wrapWrap());
        gap(center, 6);

        TextView micro = text("PRIVATE NETWORK", 10, p.muted, true);
        micro.setLetterSpacing(.22f);
        micro.setGravity(Gravity.CENTER);
        center.addView(micro, wrapWrap());
        gap(center, 30);

        FrameLayout progressTrack = new FrameLayout(this);
        progressTrack.setBackground(roundRect(p.dark ? 0xFF1B2230 : 0xFFE9EDF7, 4, 0, 0));
        LinearLayout.LayoutParams progressLp = new LinearLayout.LayoutParams(dp(132), dp(5));
        progressLp.gravity = Gravity.CENTER_HORIZONTAL;
        center.addView(progressTrack, progressLp);

        View pulse = new View(this);
        pulse.setBackground(horizontalGradient(p.accent, p.purple, 4));
        FrameLayout.LayoutParams pulseLp = new FrameLayout.LayoutParams(dp(42), dp(5));
        pulseLp.gravity = Gravity.START;
        progressTrack.addView(pulse, pulseLp);
        gap(center, 18);

        TextView loading = text("正在加载安全配置…", 12, p.subtle, true);
        loading.setGravity(Gravity.CENTER);
        center.addView(loading, wrapWrap());

        launchLogo.setAlpha(0f);
        launchLogo.setScaleX(.86f);
        launchLogo.setScaleY(.86f);
        brand.setAlpha(0f);
        brand.setScaleX(.92f);
        brand.setScaleY(.92f);
        micro.setAlpha(0f);
        micro.setTranslationY(dp(7));
        progressTrack.setAlpha(0f);
        progressTrack.setScaleX(.7f);
        loading.setAlpha(0f);
        loading.setTranslationY(dp(6));

        setContentView(root);
        launchLogo.animate().alpha(1f).scaleX(1f).scaleY(1f).setDuration(470).setInterpolator(new android.view.animation.DecelerateInterpolator()).start();
        brand.animate().alpha(1f).scaleX(1f).scaleY(1f).setStartDelay(80).setDuration(410).start();
        micro.animate().alpha(1f).translationY(0f).setStartDelay(110).setDuration(390).start();
        progressTrack.animate().alpha(1f).scaleX(1f).setStartDelay(220).setDuration(460).start();
        loading.animate().alpha(1f).translationY(0f).setStartDelay(320).setDuration(340).start();
        progressTrack.postDelayed(() -> animateLaunchBar(progressTrack, pulse), 360);

        if (token.isEmpty()) {
            root.postDelayed(() -> finishLaunch(root, started, () -> showLogin(null)), 1060);
            return;
        }

        root.postDelayed(() -> loading.setText("正在同步可用节点…"), 620);
        final int gen = ++screenGeneration;
        io.execute(() -> {
            try {
                JSONObject boot = fetchBootstrapCompat(token, null);
                runOnUiThread(() -> {
                    if (gen != screenGeneration || isFinishing()) return;
                    acceptBootstrap(boot);
                    loading.setText("安全配置已就绪");
                    finishLaunch(root, started, () -> {
                        showShell(TAB_HOME);
                        scheduleInitialBestNodeScan();
                    });
                });
            } catch (Exception e) {
                runOnUiThread(() -> {
                    if (gen != screenGeneration || isFinishing()) return;
                    String message = (e instanceof ApiClient.ApiException && ((ApiClient.ApiException)e).isAuthFailure())
                            ? reauthMessage(((ApiClient.ApiException)e).code)
                            : "同步失败：" + apiMessage(e);
                    if (e instanceof ApiClient.ApiException && ((ApiClient.ApiException)e).isAuthFailure()) clearSessionLocal();
                    loading.setText("正在进入登录页…");
                    finishLaunch(root, started, () -> showLogin(message));
                });
            }
        });
    }

    private void animateLaunchBar(FrameLayout track, View bar) {
        if (track.getWidth() <= 0 || bar.getWidth() <= 0 || !bar.isAttachedToWindow()) return;
        float distance = Math.max(0, track.getWidth() - bar.getWidth());
        bar.setTranslationX(0f);
        bar.animate().translationX(distance).alpha(.72f).setDuration(620).withEndAction(() ->
                bar.animate().translationX(0f).alpha(1f).setDuration(620).withEndAction(() -> {
                    if (bar.isAttachedToWindow()) animateLaunchBar(track, bar);
                }).start()).start();
    }

    private void finishLaunch(View root, long started, Runnable next) {
        long elapsed = SystemClock.uptimeMillis() - started;
        long wait = Math.max(0, 1380 - elapsed);
        root.postDelayed(() -> {
            if (isFinishing()) return;
            root.animate().alpha(0f).setDuration(210).withEndAction(next).start();
        }, wait);
    }

    @Override protected void onDestroy() {
        probeExecutor.shutdownNow();
        io.shutdownNow();
        super.onDestroy();
    }

    // The flags overload exists from API 26 (our minSdk); the API 33 flag value is inlined safely.
    @android.annotation.SuppressLint("InlinedApi")
    @Override protected void onStart() {
        super.onStart();
        if (coreReceiverRegistered) return;
        IntentFilter filter = new IntentFilter();
        filter.addAction(CoreState.ACTION_CHANGED);
        filter.addAction(CoreState.ACTION_AUTH_INVALID);
        filter.addAction(CoreState.ACTION_NODE_INVALID);
        filter.addAction(CoreState.ACTION_SWITCH_FAILED);
        registerReceiver(coreReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        coreReceiverRegistered = true;
        refreshCoreBoundViews();
        TextView notificationStatus=currentRoot==null?null:currentRoot.findViewWithTag("mine_notification_status");
        if(notificationStatus!=null)notificationStatus.setText(notificationStatusLabel());
    }

    @Override protected void onStop() {
        if (coreReceiverRegistered) {
            try { unregisterReceiver(coreReceiver); } catch (Exception ignored) {}
            coreReceiverRegistered = false;
        }
        super.onStop();
    }

    private void refreshPalette() {
        String theme = prefs.getString("theme", "system");
        boolean systemDark = (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
        boolean dark = "dark".equals(theme) || ("system".equals(theme) && systemDark);
        p = new Palette(dark);
    }

    private void applySystemBars() {
        getWindow().setStatusBarColor(p.bg);
        getWindow().setNavigationBarColor(p.bg);
        getWindow().setBackgroundDrawable(new ColorDrawable(p.bg));
        int flags = getWindow().getDecorView().getSystemUiVisibility();
        if (!p.dark) flags |= View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR; else flags &= ~View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
        getWindow().getDecorView().setSystemUiVisibility(flags);
    }

    private void showLogin(String banner) {
        screenGeneration++;
        currentTab = TAB_HOME;
        refreshPalette();
        applySystemBars();

        AuroraView root = new AuroraView(this, p.dark);
        root.setBackgroundColor(p.bg);
        currentRoot = root;
        ScrollView scroll = polishedScrollView(true);
        scroll.setFillViewport(true);
        scroll.setClipToPadding(false);
        root.addView(scroll, matchMatch());

        LinearLayout page = column();
        page.setPadding(dp(18), statusBarInset() + dp(20), dp(18), dp(32));
        scroll.addView(page, matchWrap());

        Space flexTop = new Space(this);
        page.addView(flexTop, new LinearLayout.LayoutParams(1, 0, .46f));

        LinearLayout brandRow = row();
        brandRow.setGravity(Gravity.CENTER_VERTICAL);
        BrandMarkView loginLogo = brandMark();
        brandRow.addView(loginLogo, new LinearLayout.LayoutParams(dp(44), dp(44)));
        TextView brand = text("XVPN", 20, p.ink, true);
        brand.setLetterSpacing(.10f);
        LinearLayout.LayoutParams brandLp = wrapWrap(); brandLp.leftMargin = dp(10);
        brandRow.addView(brand, brandLp);
        TextView micro = pill("PRIVATE ACCESS", p.accentSoft, p.accent, 10);
        LinearLayout.LayoutParams ml = wrapWrap(); ml.leftMargin = dp(10);
        brandRow.addView(micro, ml);
        View.OnLongClickListener advanced = v -> { openAdvancedSettings(v); return true; };
        loginLogo.setOnLongClickListener(advanced);
        brand.setOnLongClickListener(advanced);
        brandRow.setOnLongClickListener(advanced);
        page.addView(brandRow, matchWrap());
        gap(page, 32);

        TextView eyebrow = text("WELCOME BACK", 11, p.muted, true);
        eyebrow.setLetterSpacing(.16f);
        page.addView(eyebrow, matchWrap());
        gap(page, 9);
        TextView title = text("回到你的私人网络", 30, p.ink, true);
        title.setLineSpacing(0, 1.05f);
        page.addView(title, matchWrap());
        gap(page, 9);
        TextView sub = text("安全登录后，XVPN 会自动同步当前可用节点。", 14, p.muted, false);
        sub.setLineSpacing(dp(2), 1f);
        page.addView(sub, matchWrap());
        gap(page, 22);

        GlowCardLayout card = new GlowCardLayout(this, p.dark, 48f);
        card.setOrientation(LinearLayout.VERTICAL);
        // Keep the login card generous, but do not push it beyond its parent.
        // Negative margins clipped the continuous corners on real devices. Extra top/side
        // breathing room makes the white surface feel broader without touching register.
        card.setContentPadding(dp(19), dp(25), dp(19), dp(18));
        page.addView(card, matchWrap());

        if (banner != null && !banner.isEmpty()) {
            card.addView(sessionNotice(banner), matchWrap());
            gap(card, 11);
        }

        EditText user = input("用户名", false);
        user.setText(prefs.getString("last_username", ""));
        card.addView(user, matchWrap());
        gap(card, 11);
        EditText pass = input("密码", true);
        pass.setImeOptions(EditorInfo.IME_ACTION_DONE);
        card.addView(passwordField(pass), new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(55)));
        gap(card, 14);

        Button login = primaryButton("登录");
        pressMotion(login,.975f);
        LinearLayout.LayoutParams loginLp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(47));
        loginLp.leftMargin = dp(10); loginLp.rightMargin = dp(10);
        card.addView(login, loginLp);
        gap(card, 6);
        TextView register = text("没有账户？使用邀请码注册", 13, p.accent, true);
        register.setGravity(Gravity.CENTER);
        register.setPadding(dp(6), dp(10), dp(6), dp(7));
        register.setOnClickListener(v -> showRegister(user.getText().toString().trim()));
        card.addView(register, matchWrap());

        ProgressBar busy = new ProgressBar(this);
        busy.setVisibility(View.GONE);
        LinearLayout.LayoutParams busyLp = new LinearLayout.LayoutParams(dp(26), dp(26));
        busyLp.gravity = Gravity.CENTER_HORIZONTAL; busyLp.topMargin = dp(10);
        card.addView(busy, busyLp);

        login.setOnClickListener(v -> performLogin(user, pass, login, busy));
        pass.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_DONE) { performLogin(user, pass, login, busy); return true; }
            return false;
        });

        gap(page, 17);
        LinearLayout secure = row(); secure.setGravity(Gravity.CENTER);
        TextView dot = text("●", 9, p.success, true);
        secure.addView(dot, wrapWrap());
        TextView secureText = text("  HTTPS 安全连接", 11, p.subtle, false);
        secure.addView(secureText, wrapWrap());
        page.addView(secure, matchWrap());

        Space flexBottom = new Space(this);
        page.addView(flexBottom, new LinearLayout.LayoutParams(1, 0, .54f));
        setContentView(root);
        reveal(page, 0, 10);
    }

    private void showRegister(String suggestedUser) {
        screenGeneration++;
        refreshPalette();
        applySystemBars();
        AuroraView root = new AuroraView(this, p.dark);
        root.setBackgroundColor(p.bg);
        currentRoot = root;
        ScrollView scroll = polishedScrollView(true);
        scroll.setFillViewport(true);
        scroll.setClipToPadding(false);
        root.addView(scroll, matchMatch());

        LinearLayout page = column();
        page.setPadding(dp(18), statusBarInset() + dp(58), dp(18), dp(40));
        scroll.addView(page, matchWrap());

        gap(page, 38);
        TextView back = text("‹  返回登录", 12, p.accent, true);
        back.setGravity(Gravity.CENTER);
        back.setPadding(dp(15), dp(10), dp(15), dp(10));
        back.setBackground(ripple(authBackBg(), p.dark ? 0x24FFFFFF : 0x105D82FF));
        pressMotion(back, .95f);
        back.setOnClickListener(v -> {
            v.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);
            v.animate().translationX(-dp(2)).alpha(.78f).setDuration(90).withEndAction(() -> showLogin(null)).start();
        });
        page.addView(back, wrapWrap());
        gap(page, 46);

        TextView eyebrow = text("INVITE ONLY", 11, p.muted, true);
        eyebrow.setLetterSpacing(.16f);
        page.addView(eyebrow, matchWrap());
        gap(page, 9);
        page.addView(text("创建 XVPN 账户", 30, p.ink, true), matchWrap());
        gap(page, 9);
        page.addView(text("一个有效账户即可同步全部启用节点。", 14, p.muted, false), matchWrap());
        gap(page, 27);

        GlowCardLayout card = new GlowCardLayout(this, p.dark, 27f);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setContentPadding(dp(12), dp(18), dp(12), dp(18));
        page.addView(card, matchWrap());
        EditText invite = input("邀请码", false); card.addView(invite, matchWrap()); gap(card, 10);
        EditText user = input("用户名（3-32 位）", false); user.setText(suggestedUser); card.addView(user, matchWrap()); gap(card, 10);
        EditText pass = input("密码（至少 8 位）", true); card.addView(passwordField(pass), new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(55))); gap(card, 10);
        EditText confirm = input("确认密码", true); confirm.setImeOptions(EditorInfo.IME_ACTION_DONE); card.addView(passwordField(confirm), new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(55))); gap(card, 15);
        Button create = primaryButton("创建账户"); pressMotion(create,.975f); card.addView(create, matchWrap());
        ProgressBar busy = new ProgressBar(this); busy.setVisibility(View.GONE); LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(dp(26), dp(26)); blp.gravity=Gravity.CENTER_HORIZONTAL; blp.topMargin=dp(10); card.addView(busy, blp);
        create.setOnClickListener(v -> performRegister(invite, user, pass, confirm, create, busy));
        confirm.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_DONE) { performRegister(invite,user,pass,confirm,create,busy); return true; }
            return false;
        });
        gap(page, 34);
        setContentView(root);
        reveal(page, 0, 12);
    }

    private void performLogin(EditText user, EditText pass, Button button, ProgressBar busy) {
        String u = user.getText().toString().trim();
        String pw = pass.getText().toString();
        if (u.isEmpty() || pw.isEmpty()) { toast("请输入用户名和密码"); return; }
        if (!validateBaseForBuild()) return;
        setBusy(button, busy, true, "登录中…", "登录");
        int gen = screenGeneration;
        io.execute(() -> {
            try {
                JSONObject body = new JSONObject().put("username", u).put("password", pw);
                JSONObject out = ApiClient.request(baseUrl, "/login", "POST", null, body);
                if (!out.optBoolean("ok", true)) throw new ApiClient.ApiException(0, "LOGIN_FAILED", out.optString("message", "登录失败"), 0);
                String newToken = out.optString("token", "").trim();
                if (newToken.isEmpty()) throw new ApiClient.ApiException(0, "INVALID_RESPONSE", "服务器未返回登录令牌", 0);

                // Panel v1.2 separates administrator App tokens from normal-user tokens.
                // Verify the freshly issued token against bootstrap before persisting it, so a
                // failed/partially upgraded Panel cannot leave a bad token on the device.
                JSONObject boot = fetchBootstrapCompat(newToken, out.optJSONObject("user"));

                // SecureTokenStore.save() can throw because it uses Android Keystore. Keep it
                // inside this worker-thread try/catch instead of the runOnUiThread lambda, whose
                // Runnable contract cannot propagate checked exceptions.
                SecureTokenStore.save(prefs, newToken);
                prefs.edit().putString("last_username", u).apply();

                runOnUiThread(() -> {
                    if (gen != screenGeneration) return;
                    token = newToken;
                    username = out.optJSONObject("user") == null ? u : out.optJSONObject("user").optString("username", u);
                    acceptBootstrap(boot);
                    showShell(TAB_HOME);
                    scheduleInitialBestNodeScan();
                });
            } catch (Exception e) {
                runOnUiThread(() -> {
                    if (gen != screenGeneration) return;
                    setBusy(button, busy, false, "登录中…", "登录");
                    toast(apiMessage(e));
                });
            }
        });
    }

    private JSONObject fetchBootstrapCompat(String authToken, JSONObject loginUser) throws Exception {
        try {
            return ApiClient.request(baseUrl, "/app/bootstrap", "GET", authToken, null);
        } catch (ApiClient.ApiException e) {
            // /app/bootstrap is the v1.2 preferred path. Keep a narrow fallback for Panels
            // that are still completing an older API migration, without masking auth errors.
            if (e.status != 404 && e.status != 405) throw e;
        }

        JSONObject me = ApiClient.request(baseUrl, "/me", "GET", authToken, null);
        JSONObject nodes = ApiClient.request(baseUrl, "/nodes", "GET", authToken, null);
        JSONObject boot = new JSONObject();
        boot.put("ok", true);
        boot.put("api", "v1");
        boot.put("version", "compat");
        JSONObject meUser = me.optJSONObject("user");
        boot.put("user", meUser != null ? meUser : (loginUser != null ? loginUser : new JSONObject()));
        JSONObject nestedNodes = new JSONObject();
        nestedNodes.put("total", nodes.optInt("total", 0));
        nestedNodes.put("countries", nodes.optJSONArray("countries"));
        boot.put("nodes", nestedNodes);
        return boot;
    }

    private void performRegister(EditText invite, EditText user, EditText pass, EditText confirm, Button button, ProgressBar busy) {
        String code=invite.getText().toString().trim(), u=user.getText().toString().trim(), pw=pass.getText().toString(), pw2=confirm.getText().toString();
        if (code.isEmpty()) { toast("请输入邀请码"); return; }
        if (u.length() < 3 || u.length() > 32) { toast("用户名长度需为 3-32 位"); return; }
        if (pw.length() < 8) { toast("密码至少 8 位"); return; }
        if (!pw.equals(pw2)) { toast("两次输入的密码不一致"); return; }
        if (!validateBaseForBuild()) return;
        setBusy(button,busy,true,"注册中…","创建账户");
        int gen=screenGeneration;
        io.execute(() -> {
            try {
                JSONObject body=new JSONObject().put("invite_code",code).put("username",u).put("password",pw);
                ApiClient.request(baseUrl,"/register","POST",null,body);
                prefs.edit().putString("last_username",u).apply();
                runOnUiThread(() -> { if(gen==screenGeneration) showLogin("注册成功，请登录"); });
            } catch(Exception e) {
                runOnUiThread(() -> { if(gen!=screenGeneration)return; setBusy(button,busy,false,"注册中…","创建账户"); toast(apiMessage(e)); });
            }
        });
    }

    private void bootstrapSession(boolean coldStart) {
        final int gen = coldStart ? ++screenGeneration : screenGeneration;
        io.execute(() -> {
            try {
                JSONObject boot=fetchBootstrapCompat(token,null);
                runOnUiThread(() -> {
                    refreshing=false;
                    finishRefreshMotion();
                    if(gen!=screenGeneration)return;
                    acceptBootstrap(boot);
                    if(coldStart) showShell(currentTab);
                    else refreshHomeBoundViews();
                    scheduleInitialBestNodeScan();
                });
            } catch(Exception e) {
                runOnUiThread(() -> {
                    refreshing=false;
                    finishRefreshMotion();
                    if(gen!=screenGeneration)return;
                    if(e instanceof ApiClient.ApiException && ((ApiClient.ApiException)e).isAuthFailure()) {
                        forceLogout(((ApiClient.ApiException)e).code, e.getMessage());
                    } else if (coldStart) {
                        showLogin("同步失败：" + apiMessage(e));
                    } else {
                        toast(apiMessage(e));
                    }
                });
            }
        });
    }

    private void acceptBootstrap(JSONObject boot) {
        bootstrap=boot;
        JSONObject user=boot.optJSONObject("user");
        username=user==null?prefs.getString("last_username",""):user.optString("username","");
        catalog=NodeCatalog.fromBootstrap(boot);
        int selectedId=prefs.getInt("selected_node_id",0);
        selectedNode=catalog.find(selectedId);
        SharedPreferences.Editor edit=prefs.edit();
        if(selectedNode==null) {
            if(selectedId>0) edit.remove("manual_node_selected").remove("initial_best_node_scan_v1");
            selectedNode=catalog.firstNode();
        }
        if(selectedNode!=null) edit.putInt("selected_node_id",selectedNode.id); else edit.remove("selected_node_id");
        long reportInterval=Math.max(60L,Math.min(3600L,boot.optLong("traffic_report_interval_seconds",300L)));
        long updateInterval=Math.max(3600L,Math.min(7L*24L*3600L,boot.optLong("app_update_check_interval_seconds",43200L)));
        boolean trafficReporting=boot.optBoolean("traffic_reporting",false)
                && (user==null || !"admin".equalsIgnoreCase(user.optString("role","user")));
        edit.putLong("traffic_report_interval_seconds",reportInterval)
                .putLong("app_update_check_interval_seconds",updateInterval)
                .putBoolean("traffic_reporting",trafficReporting).apply();

        CoreState.Snapshot core=CoreState.read(this);
        if(core.isActive() && core.nodeId>0 && catalog.find(core.nodeId)==null) {
            VpnCoreService.stop(this);
            toast("当前连接节点已被停用，已安全断开");
        }
    }

private void showShell(int tab) {
        showShell(tab, false);
    }

    private void showShell(int tab, boolean themeReveal) {
        screenGeneration++;
        currentTab=tab;
        activeRefreshControl=null;
        FrameLayout root=new FrameLayout(this); root.setBackgroundColor(p.bg); currentRoot=root;
        AuroraView aurora=new AuroraView(this,p.dark); root.addView(aurora,matchMatch());
        LinearLayout outer=column(); outer.setPadding(dp(18),statusBarInset()+dp(14),dp(18),dp(30)); root.addView(outer,matchMatch());
        FrameLayout content=new FrameLayout(this); outer.addView(content,new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,0,1f));
        if(tab==TAB_HOME) content.addView(buildHome(),matchMatch()); else content.addView(buildMine(),matchMatch());
        gap(outer,16); View nav=bottomNav(); outer.addView(nav,matchWrap());
        setContentView(root);

        content.setAlpha(.94f);
        content.setTranslationX(dp(tab==TAB_HOME?-4:4));
        content.animate().alpha(1f).translationX(0f).setDuration(210).setInterpolator(new android.view.animation.DecelerateInterpolator()).start();

        if(themeReveal){
            // Kept for compatibility with callers; real theme crossfade is handled by a snapshot overlay.
        }
        if(tab==TAB_HOME) maybeCheckForUpdates();
    }

    private View buildHome() {
        ScrollView scroll=polishedScrollView(true);
        LinearLayout page=column(); page.setPadding(0,dp(8),0,dp(18)); scroll.addView(page,matchWrap());

        AmbientGlowFrameLayout headerShell=new AmbientGlowFrameLayout(this,p.dark,38f,.82f);
        headerShell.setPadding(dp(5),dp(5),dp(5),dp(5));
        LinearLayout header=row(); header.setGravity(Gravity.CENTER_VERTICAL); header.setPadding(dp(10),dp(8),dp(7),dp(8));
        BrandMarkView logo=brandMark(); header.addView(logo,new LinearLayout.LayoutParams(dp(50),dp(50)));
        LinearLayout brand=column(); LinearLayout.LayoutParams brandLp=new LinearLayout.LayoutParams(0,ViewGroup.LayoutParams.WRAP_CONTENT,1f); brandLp.leftMargin=dp(12);
        TextView brandName=text("XVPN",23,p.ink,true); brandName.setLetterSpacing(.06f); brand.addView(brandName,wrapWrap());
        TextView small=text("PRIVATE NETWORK",9,p.subtle,true); small.setLetterSpacing(.15f); brand.addView(small,wrapWrap());
        View.OnLongClickListener advanced=v->{openAdvancedSettings(v);return true;}; brand.setOnLongClickListener(advanced); brandName.setOnLongClickListener(advanced); logo.setOnLongClickListener(advanced);
        header.addView(brand,brandLp);

        RefreshActionView refresh=new RefreshActionView(this,p.dark,p.accent,p.accentSoft);
        activeRefreshControl=refresh;
        LinearLayout.LayoutParams rlp=new LinearLayout.LayoutParams(dp(42),dp(42)); header.addView(refresh,rlp); pressMotion(refresh,.94f);
        refresh.setOnClickListener(v->{
            if(refreshing)return;
            refreshing=true;
            v.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);
            refresh.startRefreshMotion();
            bootstrapSession(false);
        });
        headerShell.addView(header,matchMatch());
        LinearLayout.LayoutParams headerLp=new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,dp(78)); page.addView(headerShell,headerLp); reveal(headerShell,0,6);
        gap(page,27);

        FrameLayout orbHolder=new FrameLayout(this);
        ConnectOrbView orb=new ConnectOrbView(this,p.dark);
        CoreState.Snapshot core=CoreState.read(this);
        orb.setConnectionState(core.state);
        orb.setTag("home_connect_orb");
        FrameLayout.LayoutParams orbLp=new FrameLayout.LayoutParams(dp(306),dp(306),Gravity.CENTER);
        orbHolder.addView(orb,orbLp);
        page.addView(orbHolder,new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,dp(306))); reveal(orbHolder,45,8);
        orb.setOnClickListener(v->{v.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);handleConnect();});
        gap(page,0);
        TextView state=text(coreStateTitle(core),28,p.ink,true); state.setTag("home_core_state"); state.setGravity(Gravity.CENTER); page.addView(state,matchWrap());
        gap(page,4);
        TextView desc=text(coreStateDescription(core),14,p.muted,false); desc.setTag("home_core_desc"); desc.setGravity(Gravity.CENTER); page.addView(desc,matchWrap());
        gap(page,38);

        View routes=routeSegment(); page.addView(routes,matchWrap()); reveal(routes,80,6);
        gap(page,17);
        View node=nodeCard(); page.addView(node,matchWrap()); reveal(node,115,6);
        gap(page,13);

        LinearLayout speeds=row();
        speeds.addView(speedCard("↑","上传",formatRate(core.uploadRate),p.success,p.successSoft,"home_up_speed"),new LinearLayout.LayoutParams(0,dp(82),1f));
        gapH(speeds,12);
        speeds.addView(speedCard("↓","下载",formatRate(core.downloadRate),p.accent,p.accentSoft,"home_down_speed"),new LinearLayout.LayoutParams(0,dp(82),1f));
        page.addView(speeds,matchWrap()); reveal(speeds,145,6);
        gap(page,24);
        return scroll;
    }

    private View routeSegment() {
        FrameLayout track=new FrameLayout(this);
        track.setBackground(ambientTrackBg(32));
        track.setMinimumHeight(dp(64));

        View indicator=new View(this);
        indicator.setBackground(horizontalGradient(p.accent,p.purple,24));
        FrameLayout.LayoutParams ilp=new FrameLayout.LayoutParams(1,dp(48)); ilp.leftMargin=dp(6); ilp.topMargin=dp(8); track.addView(indicator,ilp);

        LinearLayout labels=row();
        TextView smart=text("智能分流",14,routeMode==RouteMode.SMART?Color.WHITE:p.ink,true); smart.setGravity(Gravity.CENTER);
        TextView global=text("全局代理",14,routeMode==RouteMode.GLOBAL?Color.WHITE:p.ink,true); global.setGravity(Gravity.CENTER);
        labels.addView(smart,new LinearLayout.LayoutParams(0,dp(48),1f)); labels.addView(global,new LinearLayout.LayoutParams(0,dp(48),1f));
        FrameLayout.LayoutParams llp=new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,dp(48)); llp.leftMargin=dp(6); llp.rightMargin=dp(6); llp.topMargin=dp(8); track.addView(labels,llp);
        track.post(()->{
            int segment=Math.max(1,(track.getWidth()-dp(12))/2);
            FrameLayout.LayoutParams lp=(FrameLayout.LayoutParams)indicator.getLayoutParams(); lp.width=segment; indicator.setLayoutParams(lp);
            indicator.setTranslationX(routeMode==RouteMode.GLOBAL?segment:0);
        });
        smart.setOnClickListener(v->animateRouteMode(RouteMode.SMART,0,track,indicator,smart,global));
        global.setOnClickListener(v->animateRouteMode(RouteMode.GLOBAL,1,track,indicator,smart,global));
        return track;
    }

private void setRouteMode(RouteMode mode) {
        if(routeMode==mode)return;
        routeMode=mode;
        prefs.edit().putString("route_mode",mode.key).apply();
    }

    private void animateRouteMode(RouteMode mode, int index, FrameLayout track, View indicator, TextView smart, TextView global) {
        CoreState.Snapshot core=CoreState.read(this);
        if(core.isBusy()){toast("当前配置正在切换，请稍候…");return;}
        if(routeMode==mode || Boolean.TRUE.equals(track.getTag())) return;
        String reloadConfig=null;
        NodeCatalog.Node activeNode=null;
        if(core.state==CoreState.RUNNING){
            activeNode=catalog.find(core.nodeId);
            if(activeNode==null){toast("当前连接节点已不在列表中，请先刷新节点");return;}
            try{reloadConfig=SingBoxConfigBuilder.build(this,activeNode,mode).toString();}
            catch(Exception e){toast(e.getMessage()==null?"分流配置暂不可用":e.getMessage());return;}
        }
        final String preparedConfig=reloadConfig;
        final NodeCatalog.Node connectedNode=activeNode;
        track.setTag(Boolean.TRUE);
        track.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);
        int segment=Math.max(1,(track.getWidth()-dp(12))/2);
        indicator.animate().translationX(segment*index).setDuration(300).setInterpolator(new PathInterpolator(.20f,0f,.10f,1f)).withEndAction(()->{
            setRouteMode(mode);
            smart.animate().alpha(.55f).setDuration(70).withEndAction(()->{smart.setTextColor(mode==RouteMode.SMART?Color.WHITE:p.ink);smart.animate().alpha(1f).setDuration(120).start();}).start();
            global.animate().alpha(.55f).setDuration(70).withEndAction(()->{global.setTextColor(mode==RouteMode.GLOBAL?Color.WHITE:p.ink);global.animate().alpha(1f).setDuration(120).start();}).start();
            track.setTag(null);
            if(preparedConfig!=null&&connectedNode!=null){
                VpnCoreService.reconfigure(this,preparedConfig,connectedNode.id,connectedNode.name,mode.label);
                toast("正在切换为"+mode.label+"…");
            }
        }).start();
    }

    private void refreshHomeBoundViews() {
        if(currentRoot==null || currentTab!=TAB_HOME) return;
        TextView flag=currentRoot.findViewWithTag("home_node_flag");
        TextView name=currentRoot.findViewWithTag("home_node_name");
        TextView meta=currentRoot.findViewWithTag("home_node_meta");
        TextView latency=currentRoot.findViewWithTag("home_node_latency");
        if(flag!=null) flag.setText(selectedNode==null?"🌐":selectedNode.flag);
        if(name!=null) name.setText(selectedNode==null?"暂无节点":selectedNode.name);
        if(meta!=null) meta.setText(selectedNode==null?"请在 Panel 启用节点":(selectedNode.country + (selectedNode.protocol.isEmpty()?"":" · "+selectedNode.protocol.toUpperCase(Locale.ROOT))));
        if(latency!=null && selectedNode!=null){
            long ms=savedLatency(selectedNode); boolean udpOnly=isUdpOnlyNode(selectedNode); latency.setText(udpOnly?"连接实测":ms>0?(ms+" ms"):"测速"); latency.setTextColor(udpOnly?p.warningText:ms>0?latencyColor(ms):p.accent);
            latency.setEnabled(true); latency.setAlpha(1f);
        } else if(latency!=null) {
            latency.setText("—"); latency.setTextColor(p.subtle); latency.setEnabled(false); latency.setAlpha(.62f);
        }
        refreshCoreBoundViews();
    }

    private void refreshCoreBoundViews() {
        if(currentRoot==null || currentTab!=TAB_HOME)return;
        CoreState.Snapshot core=CoreState.read(this);
        View orbView=currentRoot.findViewWithTag("home_connect_orb");
        if(orbView instanceof ConnectOrbView)((ConnectOrbView)orbView).setConnectionState(core.state);
        TextView state=currentRoot.findViewWithTag("home_core_state");
        TextView desc=currentRoot.findViewWithTag("home_core_desc");
        TextView up=currentRoot.findViewWithTag("home_up_speed");
        TextView down=currentRoot.findViewWithTag("home_down_speed");
        if(state!=null)state.setText(coreStateTitle(core));
        if(desc!=null)desc.setText(coreStateDescription(core));
        if(up!=null)up.setText(formatRate(core.uploadRate));
        if(down!=null)down.setText(formatRate(core.downloadRate));
    }

    private String coreStateTitle(CoreState.Snapshot core) {
        switch(core.state){
            case CoreState.STARTING:return "正在连接";
            case CoreState.SWITCHING:return "正在切换";
            case CoreState.RUNNING:return "已连接";
            case CoreState.STOPPING:return "正在断开";
            case CoreState.ERROR:return "连接失败";
            default:return "未连接";
        }
    }

    private String coreStateDescription(CoreState.Snapshot core) {
        if(core.state==CoreState.ERROR)return core.error.isEmpty()?"内核未能启动，请检查节点后重试":core.error;
        if(core.state==CoreState.STARTING)return "正在建立隧道并验证真实联网…";
        if(core.state==CoreState.SWITCHING)return (core.nodeName.isEmpty()?"新配置":core.nodeName)+" · 正在重载并验证联网…";
        if(core.state==CoreState.STOPPING)return "正在关闭隧道并保存本次流量…";
        if(core.state==CoreState.RUNNING)return (core.nodeName.isEmpty()?"安全隧道":core.nodeName)+" · "+routeMode.label+" · 网络正常";
        return selectedNode==null?"暂无可用节点":"点击按钮开启安全连接";
    }

    private void finishRefreshMotion() {
        RefreshActionView control=activeRefreshControl;
        if(control!=null && control.isAttachedToWindow())control.stopRefreshMotion();
    }

    private String formatRate(long bytesPerSecond) { return formatBytesCompact(bytesPerSecond)+"/s"; }

    private String formatBytesCompact(long value) {
        double amount=Math.max(0L,value);
        String[] units={"B","KB","MB","GB","TB"};
        int unit=0;
        while(amount>=1024d && unit<units.length-1){amount/=1024d;unit++;}
        if(unit==0)return ((long)amount)+" "+units[unit];
        String pattern=amount>=100d?"%.0f %s":amount>=10d?"%.1f %s":"%.2f %s";
        return String.format(Locale.US,pattern,amount,units[unit]);
    }

    private View nodeCard() {
        LinearLayout card=row(); card.setGravity(Gravity.CENTER_VERTICAL); card.setPadding(dp(16),dp(14),dp(12),dp(14)); card.setBackground(cardBg(22));
        TextView flag=text(selectedNode==null?"🌐":selectedNode.flag,24,p.ink,false); flag.setTag("home_node_flag"); flag.setGravity(Gravity.CENTER); flag.setBackground(roundRect(p.surfaceAlt,14,0,0)); card.addView(flag,new LinearLayout.LayoutParams(dp(46),dp(46)));
        LinearLayout labels=column(); LinearLayout.LayoutParams llp=new LinearLayout.LayoutParams(0,ViewGroup.LayoutParams.WRAP_CONTENT,1f); llp.leftMargin=dp(12);
        TextView nodeName=text(selectedNode==null?"暂无节点":selectedNode.name,15,p.ink,true); nodeName.setTag("home_node_name"); labels.addView(nodeName,matchWrap());
        String meta=selectedNode==null?"请在 Panel 启用节点":(selectedNode.country + (selectedNode.protocol.isEmpty()?"":" · "+selectedNode.protocol.toUpperCase(Locale.ROOT)));
        TextView nodeMeta=text(meta,11,p.muted,false); nodeMeta.setTag("home_node_meta"); labels.addView(nodeMeta,matchWrap()); card.addView(labels,llp);
        if(selectedNode!=null){
            long saved=savedLatency(selectedNode);
            boolean udpOnly=isUdpOnlyNode(selectedNode);
            TextView latency=text(udpOnly?"连接实测":saved>0?(saved+" ms"):"测速",11,udpOnly?p.warningText:saved>0?latencyColor(saved):p.accent,true); latency.setTag("home_node_latency");
            latency.setGravity(Gravity.CENTER); latency.setPadding(dp(11),dp(7),dp(11),dp(7)); latency.setBackground(ripple(roundRect(p.accentSoft,15,0,0),p.dark?0x22FFFFFF:0x0C5D82FF)); pressMotion(latency,.94f);
            latency.setOnClickListener(v->testSelectedNodeQuick(latency));
            card.addView(latency,wrapWrap()); gapH(card,7);
        }
        TextView arrow=text("›",27,p.subtle,false); arrow.setGravity(Gravity.CENTER); card.addView(arrow,new LinearLayout.LayoutParams(dp(24),dp(34)));
        pressMotion(card,.988f);
        card.setOnClickListener(v->{
            if(CoreState.read(this).isBusy()){toast("当前配置正在切换，请稍候…");return;}
            v.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);showNodePicker();
        }); return card;
    }

    private View speedCard(String icon,String label,String value,int accent,int soft,String valueTag) {
        LinearLayout card=row(); card.setGravity(Gravity.CENTER_VERTICAL); card.setPadding(dp(14),dp(12),dp(14),dp(12)); card.setBackground(floatingCardBg(19));
        TextView mark=text(icon,20,accent,true); mark.setGravity(Gravity.CENTER); mark.setBackground(roundRect(soft,13,0,0)); card.addView(mark,new LinearLayout.LayoutParams(dp(42),dp(42)));
        LinearLayout txt=column(); LinearLayout.LayoutParams tlp=new LinearLayout.LayoutParams(0,ViewGroup.LayoutParams.WRAP_CONTENT,1f); tlp.leftMargin=dp(10); txt.addView(text(label,11,p.muted,false),matchWrap()); TextView metric=text(value,15,accent,true); metric.setTag(valueTag); txt.addView(metric,matchWrap()); card.addView(txt,tlp); return card;
    }

    private View buildMine() {
        ScrollView scroll=polishedScrollView(true);
        LinearLayout page=column(); page.setPadding(0,dp(8),0,dp(26)); scroll.addView(page,matchWrap());

        TextView eyebrow=text("ACCOUNT",10,p.subtle,true); eyebrow.setLetterSpacing(.18f); page.addView(eyebrow,matchWrap());
        gap(page,7);
        page.addView(text("我的",30,p.ink,true),matchWrap());
        gap(page,5);
        page.addView(text("账户、流量与 XVPN 设置",13,p.muted,false),matchWrap());
        gap(page,20);

        LinearLayout profile=row(); profile.setGravity(Gravity.CENTER_VERTICAL); profile.setPadding(dp(18),dp(18),dp(18),dp(18)); profile.setBackground(profileCardBg(25));
        LinearLayout identity=column();
        identity.addView(text(username.isEmpty()?"XVPN 用户":username,22,Color.WHITE,true),matchWrap());
        gap(identity,6);
        identity.addView(text("●  账户正常 · PRIVATE NETWORK",11,0xFFEAF8F4,true),matchWrap());
        profile.addView(identity,new LinearLayout.LayoutParams(0,ViewGroup.LayoutParams.WRAP_CONTENT,1f));
        TextView member=pill("PRIVATE",0x24FFFFFF,Color.WHITE,10); profile.addView(member,wrapWrap());
        pressMotion(profile,.988f); profile.setOnClickListener(v->{v.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);showAccountSummary();});
        page.addView(profile,matchWrap()); reveal(profile,20,6);
        gap(page,14);

        JSONObject traffic=bootstrap==null?null:bootstrap.optJSONObject("traffic");
        String todayValue="—",todaySub="暂无统计",monthValue="—",monthSub="暂无统计",totalValue="—",totalSub="暂无统计";
        if(traffic!=null){
            long todayUp=traffic.optLong("today_upload",0L),todayDown=traffic.optLong("today_download",0L);
            long monthUp=traffic.optLong("month_upload",0L),monthDown=traffic.optLong("month_download",0L);
            long totalUp=traffic.optLong("total_upload",0L),totalDown=traffic.optLong("total_download",0L);
            todayValue=formatBytesCompact(todayUp+todayDown); todaySub="↑ "+formatBytesCompact(todayUp)+" · ↓ "+formatBytesCompact(todayDown);
            monthValue=formatBytesCompact(monthUp+monthDown); monthSub="↑ "+formatBytesCompact(monthUp)+" · ↓ "+formatBytesCompact(monthDown);
            totalValue=formatBytesCompact(totalUp+totalDown); totalSub="↑ "+formatBytesCompact(totalUp)+" · ↓ "+formatBytesCompact(totalDown);
        }
        LinearLayout stats=row();
        stats.addView(miniStat("今日",todayValue,todaySub),new LinearLayout.LayoutParams(0,dp(94),1f)); gapH(stats,10);
        stats.addView(miniStat("本月",monthValue,monthSub),new LinearLayout.LayoutParams(0,dp(94),1f)); gapH(stats,10);
        stats.addView(miniStat("累计",totalValue,totalSub),new LinearLayout.LayoutParams(0,dp(94),1f));
        page.addView(stats,matchWrap()); reveal(stats,50,5);
        gap(page,16);

        View theme=themeControl(); page.addView(theme,matchWrap()); reveal(theme,80,5);
        gap(page,16);

        TextView securityTitle=text("账户安全",11,p.subtle,true); securityTitle.setLetterSpacing(.08f); page.addView(securityTitle,matchWrap());
        gap(page,8);
        LinearLayout passwordCard=column(); passwordCard.setBackground(floatingCardBg(22));
        passwordCard.addView(settingRow("修改密码","更新后旧登录会失效",this::showChangePassword),matchWrap());
        page.addView(passwordCard,matchWrap());
        gap(page,16);

        TextView settingsTitle=text("连接与测试",11,p.subtle,true); settingsTitle.setLetterSpacing(.08f); page.addView(settingsTitle,matchWrap());
        gap(page,8);
        LinearLayout settings=column(); settings.setBackground(floatingCardBg(22));
        settings.addView(settingRow("延迟测试网站",latencyTargetLabel(),this::showLatencyTargetSettings),matchWrap());
        settings.addView(divider());
        settings.addView(settingRow("连接诊断",connectionDiagnosticLabel(),this::showConnectionDiagnostic),matchWrap());
        settings.addView(divider());
        LinearLayout notificationRow=(LinearLayout)settingRow("连接状态通知",notificationStatusLabel(),this::openNotificationSettings);
        View notificationLabels=notificationRow.getChildAt(0);
        if(notificationLabels instanceof LinearLayout&&((LinearLayout)notificationLabels).getChildCount()>1)((LinearLayout)notificationLabels).getChildAt(1).setTag("mine_notification_status");
        settings.addView(notificationRow,matchWrap());
        settings.addView(divider());
        View versionRow=settingRow("版本",BuildConfig.VERSION_NAME + " · 检查更新",()->checkForUpdates(true));
        versionRow.setOnLongClickListener(v->{openAdvancedSettings(v);return true;});
        settings.addView(versionRow,matchWrap());
        page.addView(settings,matchWrap());
        gap(page,18);

        TextView accountTitle=text("账户操作",11,p.subtle,true); accountTitle.setLetterSpacing(.08f); page.addView(accountTitle,matchWrap());
        gap(page,8);
        Button logout=logoutButton("退出登录"); pressMotion(logout,.975f);
        logout.setOnClickListener(v->confirmLogout());
        page.addView(logout,matchWrap());
        gap(page,8);
        TextView logoutHint=text("退出后会清除本机登录令牌",10,p.subtle,false); logoutHint.setGravity(Gravity.CENTER); page.addView(logoutHint,matchWrap());
        return scroll;
    }

    private String notificationStatusLabel(){
        android.app.NotificationManager manager=(android.app.NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        return manager!=null&&manager.areNotificationsEnabled()?"已开启 · 显示节点与实时速率":"未开启 · 点击前往系统设置";
    }

    private void openNotificationSettings(){
        try{
            Intent intent=new Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                    .putExtra(android.provider.Settings.EXTRA_APP_PACKAGE,getPackageName());
            startActivity(intent);
        }catch(Exception e){toast("无法打开通知设置");}
    }

    private View themeControl() {
        LinearLayout card=column(); card.setPadding(dp(16),dp(15),dp(16),dp(16)); card.setBackground(floatingCardBg(22));
        card.addView(text("外观",14,p.ink,true),matchWrap());
        gap(card,3);
        card.addView(text("跟随系统，或固定使用浅色 / 深色",11,p.muted,false),matchWrap());
        gap(card,13);

        String current=prefs.getString("theme","system");
        int currentIndex=themeIndex(current);
        FrameLayout track=new FrameLayout(this); track.setBackground(roundRect(p.surfaceAlt,18,p.dark?0xFF273148:0xFFE5EAF3,1));
        View indicator=new View(this); indicator.setBackground(horizontalGradient(p.accent,p.purple,15));
        FrameLayout.LayoutParams indicatorLp=new FrameLayout.LayoutParams(1,dp(44)); indicatorLp.gravity=Gravity.START|Gravity.CENTER_VERTICAL; indicatorLp.leftMargin=dp(4); track.addView(indicator,indicatorLp);

        LinearLayout labels=row(); labels.setPadding(dp(4),dp(4),dp(4),dp(4));
        labels.addView(themeChoice("跟随系统","system",current,track,indicator,0),new LinearLayout.LayoutParams(0,dp(44),1f));
        labels.addView(themeChoice("浅色","light",current,track,indicator,1),new LinearLayout.LayoutParams(0,dp(44),1f));
        labels.addView(themeChoice("深色","dark",current,track,indicator,2),new LinearLayout.LayoutParams(0,dp(44),1f));
        track.addView(labels,new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,dp(52)));
        card.addView(track,new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,dp(52)));
        track.post(()->{
            int segment=Math.max(1,(track.getWidth()-dp(8))/3);
            FrameLayout.LayoutParams lp=(FrameLayout.LayoutParams)indicator.getLayoutParams(); lp.width=segment; lp.height=dp(44); lp.leftMargin=dp(4); indicator.setLayoutParams(lp);
            indicator.setTranslationX(segment*currentIndex);
        });
        return card;
    }

    private TextView themeChoice(String label,String value,String current,FrameLayout track,View indicator,int index) {
        boolean selected=value.equals(current);
        TextView t=text(label,12,selected?Color.WHITE:p.muted,true); t.setGravity(Gravity.CENTER); pressMotion(t,.97f);
        t.setOnClickListener(v->{
            if(value.equals(prefs.getString("theme","system")))return;
            if(Boolean.TRUE.equals(track.getTag()))return;
            track.setTag(Boolean.TRUE);
            v.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);
            int segment=Math.max(1,(track.getWidth()-dp(8))/3);
            indicator.animate().translationX(segment*index).setDuration(300).setInterpolator(new PathInterpolator(.20f,0f,.10f,1f)).withEndAction(()->switchThemeWithFade(value)).start();
        });
        return t;
    }

    private int themeIndex(String value) { return "light".equals(value)?1:("dark".equals(value)?2:0); }

    private View miniStat(String title,String value,String sub) {
        LinearLayout c=column(); c.setPadding(dp(13),dp(12),dp(13),dp(12)); c.setBackground(floatingCardBg(18)); c.addView(text(title,11,p.muted,false),matchWrap()); gap(c,5); c.addView(text(value,15,p.ink,true),matchWrap()); c.addView(text(sub,9,p.subtle,false),matchWrap()); return c;
    }

    private View settingRow(String title,String value,Runnable action) {
        LinearLayout row=row(); row.setGravity(Gravity.CENTER_VERTICAL); row.setPadding(dp(16),dp(15),dp(16),dp(15)); LinearLayout labels=column(); labels.addView(text(title,14,p.ink,true),matchWrap()); labels.addView(text(value,11,p.muted,false),matchWrap()); row.addView(labels,new LinearLayout.LayoutParams(0,ViewGroup.LayoutParams.WRAP_CONTENT,1f)); if(action!=null){ row.addView(text("›",25,p.subtle,false),wrapWrap()); pressMotion(row,.988f); row.setOnClickListener(v->{v.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);action.run();}); } return row;
    }

    private View bottomNav() {
        AmbientGlowFrameLayout shell=new AmbientGlowFrameLayout(this,p.dark,38f,.82f);
        shell.setPadding(dp(6),dp(6),dp(6),dp(6));
        FrameLayout track=new FrameLayout(this);
        track.setBackgroundColor(Color.TRANSPARENT);

        View indicator=new View(this);
        indicator.setBackground(horizontalGradient(p.accent,p.purple,25));
        FrameLayout.LayoutParams ilp=new FrameLayout.LayoutParams(1,dp(50)); ilp.leftMargin=dp(7); ilp.topMargin=dp(8); track.addView(indicator,ilp);

        LinearLayout labels=row();
        TextView home=navItem("⌂\n首页",currentTab==TAB_HOME);
        TextView mine=navItem("○\n我的",currentTab==TAB_MINE);
        labels.addView(home,new LinearLayout.LayoutParams(0,dp(50),1f));
        labels.addView(mine,new LinearLayout.LayoutParams(0,dp(50),1f));
        FrameLayout.LayoutParams llp=new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,dp(50)); llp.leftMargin=dp(7); llp.rightMargin=dp(7); llp.topMargin=dp(8); track.addView(labels,llp);

        track.post(()->{
            int segment=Math.max(1,(track.getWidth()-dp(14))/2);
            FrameLayout.LayoutParams lp=(FrameLayout.LayoutParams)indicator.getLayoutParams(); lp.width=segment; indicator.setLayoutParams(lp);
            indicator.setTranslationX(currentTab==TAB_MINE?segment:0);
        });
        home.setOnClickListener(v->animateTabSwitch(TAB_HOME,0,track,indicator,home,mine));
        mine.setOnClickListener(v->animateTabSwitch(TAB_MINE,1,track,indicator,home,mine));
        shell.addView(track,matchMatch());
        shell.setMinimumHeight(dp(78));
        return shell;
    }

    private TextView navItem(String label,boolean selected) {
        TextView t=text(label,11,selected?Color.WHITE:p.muted,true); t.setGravity(Gravity.CENTER); t.setLineSpacing(0,.93f); return t;
    }

    private void animateTabSwitch(int targetTab,int index,FrameLayout track,View indicator,TextView home,TextView mine) {
        if(currentTab==targetTab || Boolean.TRUE.equals(track.getTag())) return;
        track.setTag(Boolean.TRUE);
        track.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);
        int segment=Math.max(1,(track.getWidth()-dp(14))/2);
        indicator.animate().translationX(segment*index).setDuration(285).setInterpolator(new PathInterpolator(.20f,0f,.10f,1f)).withEndAction(()->{
            home.setTextColor(targetTab==TAB_HOME?Color.WHITE:p.muted);
            mine.setTextColor(targetTab==TAB_MINE?Color.WHITE:p.muted);
            showShell(targetTab);
        }).start();
    }

    private void handleConnect() {
        CoreState.Snapshot core=CoreState.read(this);
        if(core.state==CoreState.RUNNING){
            VpnCoreService.stop(this);
            refreshCoreBoundViews();
            return;
        }
        if(core.state==CoreState.STARTING){toast("正在建立安全连接，请稍候…");return;}
        if(core.state==CoreState.SWITCHING){toast("正在应用新配置，请稍候…");return;}
        if(core.state==CoreState.STOPPING){toast("正在安全断开，请稍候…");return;}
        if(selectedNode==null){ toast("当前没有可用节点"); return; }
        try {
            pendingCoreConfig=SingBoxConfigBuilder.build(this,selectedNode,routeMode).toString();
            pendingCoreNodeId=selectedNode.id;
            pendingCoreNodeName=selectedNode.name;
            pendingCoreRouteLabel=routeMode.label;
            if(Build.VERSION.SDK_INT>=Build.VERSION_CODES.TIRAMISU
                    && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED
                    && !prefs.getBoolean("notification_permission_requested",false)){
                prefs.edit().putBoolean("notification_permission_requested",true).apply();
                requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},REQUEST_NOTIFICATION_PERMISSION);
                return;
            }
            android.app.NotificationManager notificationManager=(android.app.NotificationManager)getSystemService(NOTIFICATION_SERVICE);
            if(notificationManager!=null&&!notificationManager.areNotificationsEnabled())
                toast("连接状态通知未开启，可在“我的”中随时开启");
            continuePendingCoreAuthorization();
        } catch(Exception e) { toast(e.getMessage()==null?"节点配置暂不可用":e.getMessage()); }
    }

    private void continuePendingCoreAuthorization() {
        if(pendingCoreConfig==null || pendingCoreConfig.isEmpty())return;
        Intent permission=VpnService.prepare(this);
        if(permission!=null) startActivityForResult(permission,REQUEST_VPN_PERMISSION);
        else startPendingCore();
    }

    @Override public void onRequestPermissionsResult(int requestCode,String[] permissions,int[] grantResults) {
        super.onRequestPermissionsResult(requestCode,permissions,grantResults);
        if(requestCode!=REQUEST_NOTIFICATION_PERMISSION)return;
        boolean granted=grantResults.length>0&&grantResults[0]==PackageManager.PERMISSION_GRANTED;
        if(!granted)toast("通知未开启；VPN 仍可连接，可稍后在系统设置中开启");
        continuePendingCoreAuthorization();
    }

    @Override protected void onActivityResult(int requestCode,int resultCode,Intent data) {
        super.onActivityResult(requestCode,resultCode,data);
        if(requestCode!=REQUEST_VPN_PERMISSION)return;
        if(resultCode==RESULT_OK) startPendingCore();
        else {
            clearPendingCore();
            toast("未获得系统 VPN 授权，未建立连接");
        }
    }

    private void startPendingCore() {
        if(pendingCoreConfig==null || pendingCoreConfig.isEmpty() || pendingCoreNodeId<=0){toast("节点配置已失效，请重试");return;}
        String config=pendingCoreConfig; int id=pendingCoreNodeId; String name=pendingCoreNodeName; String label=pendingCoreRouteLabel;
        clearPendingCore();
        VpnCoreService.start(this,config,id,name,label);
        refreshCoreBoundViews();
    }

    private void clearPendingCore() {
        pendingCoreConfig=null; pendingCoreNodeId=0; pendingCoreNodeName=null; pendingCoreRouteLabel=null;
    }

    private void showNodePicker() {
        if(CoreState.read(this).isBusy()){toast("当前配置正在切换，请稍候…");return;}
        Dialog dialog=bottomDialog(); activeNodePicker=dialog;
        final boolean[] pickerChanged={false};
        dialog.setOnDismissListener(x->{
            if(activeNodePicker==dialog) activeNodePicker=null;
            if(pickerChanged[0] && currentTab==TAB_HOME) refreshHomeBoundViews();
        });
        LinearLayout sheet=sheet();
        LinearLayout titleRow=row(); titleRow.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout titleBox=column(); titleBox.addView(text("选择节点",21,p.ink,true),matchWrap()); gap(titleBox,4); titleBox.addView(text(CoreState.read(this).state==CoreState.RUNNING?"连接中也可直接无感切换节点":"点击国家标题可展开或收起节点",12,p.muted,false),matchWrap());
        titleRow.addView(titleBox,new LinearLayout.LayoutParams(0,ViewGroup.LayoutParams.WRAP_CONTENT,1f));
        boolean pickerConnected=CoreState.read(this).state==CoreState.RUNNING;
        TextView best=text(pickerConnected?"连接检测":"自动优选",12,p.accent,true); best.setGravity(Gravity.CENTER); best.setPadding(dp(11),dp(8),dp(11),dp(8)); best.setBackground(ripple(roundRect(p.accentSoft,15,0,0),p.dark?0x22FFFFFF:0x0C5D82FF)); pressMotion(best,.94f); titleRow.addView(best,wrapWrap());
        sheet.addView(titleRow,matchWrap()); gap(sheet,8);
        TextView scanStatus=text("",11,p.muted,true); scanStatus.setVisibility(View.GONE); scanStatus.setPadding(0,dp(3),0,dp(3)); sheet.addView(scanStatus,matchWrap()); gap(sheet,8);
        if(catalog.countries.isEmpty()) sheet.addView(text("暂无可用节点",14,p.muted,false),matchWrap());

        SparseArray<TextView> latencyBadges=new SparseArray<>();
        SparseArray<SelectionDotView> tails=new SparseArray<>();
        SparseArray<View> items=new SparseArray<>();

        for(NodeCatalog.Country c:catalog.countries){
            String key="node_country_collapsed_"+c.code;
            boolean selectedHere=countryContainsSelected(c);
            boolean collapsed=prefs.contains(key)?prefs.getBoolean(key,false):!selectedHere;
            if(selectedHere) collapsed=false;

            LinearLayout block=column();
            LinearLayout head=row(); head.setGravity(Gravity.CENTER_VERTICAL); head.setPadding(dp(12),dp(11),dp(10),dp(11)); head.setBackground(roundRect(p.surfaceAlt,16,p.dark?0xFF273148:0xFFE8ECF5,1));
            TextView country=text(c.flag+"  "+c.name,14,p.ink,true); head.addView(country,new LinearLayout.LayoutParams(0,ViewGroup.LayoutParams.WRAP_CONTENT,1f));
            TextView count=pill(String.valueOf(c.nodes.size()),p.surface,p.muted,11); head.addView(count,wrapWrap()); gapH(head,8);
            ChevronView arrow=new ChevronView(this,p.muted); arrow.setExpanded(!collapsed); head.addView(arrow,new LinearLayout.LayoutParams(dp(28),dp(28))); pressMotion(head,.988f);
            block.addView(head,matchWrap());

            LinearLayout body=column(); body.setPadding(0,dp(8),0,0);
            for(NodeCatalog.Node n:c.nodes){
                boolean chosen=selectedNode!=null&&selectedNode.id==n.id;
                LinearLayout item=row(); item.setGravity(Gravity.CENTER_VERTICAL); item.setPadding(dp(14),dp(13),dp(12),dp(13)); item.setBackground(roundRect(chosen?p.accentSoft:p.surfaceAlt,16,chosen?p.accent:0,chosen?1:0));
                LinearLayout info=column(); info.addView(text(n.name,14,p.ink,true),matchWrap());
                String nodeMeta=n.region==null||n.region.isEmpty()?n.protocol.toUpperCase(Locale.ROOT):(n.region+" · "+n.protocol.toUpperCase(Locale.ROOT)); info.addView(text(nodeMeta,10,p.muted,false),matchWrap()); item.addView(info,new LinearLayout.LayoutParams(0,ViewGroup.LayoutParams.WRAP_CONTENT,1f));
                long ms=savedLatency(n); boolean udpOnly=isUdpOnlyNode(n); TextView badge=text(udpOnly?"需连接":ms>0?(ms+" ms"):"—",10,udpOnly?p.warningText:ms>0?latencyColor(ms):p.subtle,true); badge.setGravity(Gravity.CENTER); badge.setPadding(dp(9),dp(6),dp(9),dp(6)); badge.setBackground(roundRect(p.surface,13,0,0)); item.addView(badge,wrapWrap()); latencyBadges.put(n.id,badge); gapH(item,7);
                SelectionDotView tail=new SelectionDotView(this,p.accent,p.subtle,chosen); item.addView(tail,new LinearLayout.LayoutParams(dp(30),dp(30))); tails.put(n.id,tail); items.put(n.id,item); pressMotion(item,.988f);
                item.setOnClickListener(v->{v.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);dialog.dismiss();v.postDelayed(()->showNodeDetail(n),motionEnabled()?185:0);}); body.addView(item,matchWrap()); gap(body,7);
            }
            body.setVisibility(collapsed?View.GONE:View.VISIBLE); block.addView(body,matchWrap());
            head.setOnClickListener(v->{
                boolean hide=body.getVisibility()==View.VISIBLE;
                if(hide){body.animate().alpha(0f).translationY(-dp(4)).setDuration(140).withEndAction(()->{body.setVisibility(View.GONE);body.setAlpha(1f);body.setTranslationY(0f);}).start();}
                else{body.setVisibility(View.VISIBLE);body.setAlpha(0f);body.setTranslationY(-dp(5));body.animate().alpha(1f).translationY(0f).setDuration(180).start();}
                arrow.animateExpanded(!hide); prefs.edit().putBoolean(key,hide).apply();
            });
            sheet.addView(block,matchWrap()); gap(sheet,10);
        }
        best.setOnClickListener(v->{v.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);runBestNodeScanInPicker(dialog,best,scanStatus,latencyBadges,tails,items,pickerChanged);});
        showBottomDialog(dialog,sheet);
    }

    private boolean countryContainsSelected(NodeCatalog.Country c) {
        if(selectedNode==null) return false;
        for(NodeCatalog.Node n:c.nodes) if(n.id==selectedNode.id) return true;
        return false;
    }

    private void openAdvancedSettings(View anchor) {
        anchor.performHapticFeedback(HapticFeedbackConstants.LONG_PRESS);
        anchor.animate().scaleX(.96f).scaleY(.96f).setDuration(90).withEndAction(() ->
                anchor.animate().scaleX(1f).scaleY(1f).setDuration(130).start()).start();
        anchor.postDelayed(this::showServerSettings, 170);
    }

    private void showNodeDetail(NodeCatalog.Node node) {
        Dialog dialog=bottomDialog();
        LinearLayout sheet=sheet();

        LinearLayout titleRow=row(); titleRow.setGravity(Gravity.CENTER_VERTICAL);
        TextView flag=text(node.flag,25,p.ink,false); flag.setGravity(Gravity.CENTER); flag.setBackground(roundRect(p.surfaceAlt,14,0,0)); titleRow.addView(flag,new LinearLayout.LayoutParams(dp(48),dp(48)));
        LinearLayout title=column(); LinearLayout.LayoutParams titleLp=new LinearLayout.LayoutParams(0,ViewGroup.LayoutParams.WRAP_CONTENT,1f); titleLp.leftMargin=dp(12);
        title.addView(text(node.name,21,p.ink,true),matchWrap());
        String detailMeta=node.country + (node.region==null||node.region.isEmpty()?"":" · "+node.region);
        title.addView(text(detailMeta,11,p.muted,false),matchWrap()); titleRow.addView(title,titleLp);
        sheet.addView(titleRow,matchWrap());
        gap(sheet,16);

        LinearLayout info=column(); info.setPadding(dp(15),dp(13),dp(15),dp(13)); info.setBackground(roundRect(p.surfaceAlt,18,0,0));
        info.addView(detailLine("协议",node.protocol.isEmpty()?"—":node.protocol.toUpperCase(Locale.ROOT)),matchWrap());
        info.addView(divider());
        info.addView(detailLine("节点延迟",isUdpOnlyNode(node)?"UDP 节点需连接后实测":"测试节点入口 TCP 握手"),matchWrap());
        sheet.addView(info,matchWrap());
        gap(sheet,14);

        TextView result=text("尚未测试",12,p.muted,true); result.setGravity(Gravity.CENTER); result.setPadding(dp(10),dp(10),dp(10),dp(10)); result.setBackground(roundRect(p.surfaceAlt,14,0,0)); sheet.addView(result,matchWrap());
        gap(sheet,10);

        LinearLayout actions=row();
        Button test=secondaryButton("测试延迟");
        boolean chosen=selectedNode!=null&&selectedNode.id==node.id;
        CoreState.Snapshot shownCore=CoreState.read(this);
        boolean connectedHere=shownCore.state==CoreState.RUNNING&&shownCore.nodeId==node.id;
        Button choose=primaryButton(connectedHere?"当前连接":shownCore.state==CoreState.RUNNING?"切换到此节点":chosen?"已选择 · 返回":"选择此节点");
        actions.addView(test,new LinearLayout.LayoutParams(0,dp(52),1f)); gapH(actions,10); actions.addView(choose,new LinearLayout.LayoutParams(0,dp(52),1f));
        sheet.addView(actions,matchWrap());

        test.setOnClickListener(v->{
            test.setEnabled(false); test.setText("测速中…"); result.setText("正在连接节点入口…"); result.setTextColor(p.muted);
            io.execute(()->{
                try{
                    long ms=measureNodeLatency(node);
                    prefs.edit().putLong("node_latency_"+node.id,ms).apply();
                    runOnUiThread(()->{test.setEnabled(true);test.setText("重新测试");result.setText(ms+" ms");result.setTextColor(latencyColor(ms));});
                }catch(Exception e){
                    runOnUiThread(()->{test.setEnabled(true);test.setText("重新测试");result.setText(e instanceof UdpProbeUnavailableException?"UDP 协议需连接后实测":probeFailureLabel(e));result.setTextColor(e instanceof UdpProbeUnavailableException?p.warningText:p.danger);});
                }
            });
        });
        choose.setOnClickListener(v->{
            v.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);
            if(!reconfigureConnectedNode(node))return;
            selectedNode=node;
            prefs.edit().putInt("selected_node_id",node.id).putBoolean("manual_node_selected",true).apply();
            dialog.dismiss(); refreshHomeBoundViews();
        });
        showBottomDialog(dialog,sheet);
    }

    private boolean reconfigureConnectedNode(NodeCatalog.Node node) {
        CoreState.Snapshot core=CoreState.read(this);
        if(core.isBusy()){toast("当前配置正在切换，请稍候…");return false;}
        if(core.state!=CoreState.RUNNING||core.nodeId==node.id)return true;
        try{
            String config=SingBoxConfigBuilder.build(this,node,routeMode).toString();
            VpnCoreService.reconfigure(this,config,node.id,node.name,routeMode.label);
            toast("正在切换至 "+node.name+"…");
            return true;
        }catch(Exception e){
            toast(e.getMessage()==null?"节点配置暂不可用":e.getMessage());
            return false;
        }
    }

    private long savedLatency(NodeCatalog.Node node) { return node==null?-1:prefs.getLong("node_latency_"+node.id,-1); }

    private boolean isUdpOnlyNode(NodeCatalog.Node node){
        String protocol=node==null||node.protocol==null?"":node.protocol.toLowerCase(Locale.ROOT);
        return "hysteria2".equals(protocol)||"hy2".equals(protocol)||"tuic".equals(protocol);
    }

    private void testSelectedNodeQuick(TextView badge) {
        NodeCatalog.Node node=selectedNode; if(node==null){toast("当前没有可用节点");return;}
        badge.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK); badge.setEnabled(false); badge.setText("测速中");
        io.execute(()->{
            try{
                long ms=measureNodeLatency(node); prefs.edit().putLong("node_latency_"+node.id,ms).apply();
                runOnUiThread(()->{badge.setEnabled(true);badge.setText(ms+" ms");badge.setTextColor(latencyColor(ms));});
            }catch(Exception e){runOnUiThread(()->{badge.setEnabled(true);badge.setText(probeFailureLabel(e));badge.setTextColor(e instanceof UdpProbeUnavailableException?p.warningText:p.danger);});}
        });
    }

    private void scheduleInitialBestNodeScan() {
        if(prefs.getBoolean("initial_best_node_scan_v1",false) || nodeScanRunning || catalog.countries.isEmpty())return;
        runBestNodeScan(false);
    }

    private void runBestNodeScan(boolean userInitiated) {
        if(nodeScanRunning){if(userInitiated)toast("节点测速正在进行中");return;}
        List<NodeCatalog.Node> nodes=new ArrayList<>(); for(NodeCatalog.Country c:catalog.countries)nodes.addAll(c.nodes);
        if(nodes.isEmpty()){if(userInitiated)toast("当前没有可测速节点");return;}
        nodeScanRunning=true; String scanToken=token;
        io.execute(()->{
            NodeCatalog.Node best=null; long bestMs=Long.MAX_VALUE; SharedPreferences.Editor latencyEditor=prefs.edit();
            List<NodeProbeResult> results=probeNodes(nodes,1600,null);
            for(NodeProbeResult result:results){
                if(!scanToken.equals(token)||token.isEmpty())break;
                if(result.error==null){latencyEditor.putLong("node_latency_"+result.node.id,result.latencyMs);if(result.latencyMs<bestMs){bestMs=result.latencyMs;best=result.node;}}
            }
            latencyEditor.apply(); final NodeCatalog.Node winner=best; final long winnerMs=bestMs;
            runOnUiThread(()->{
                nodeScanRunning=false; if(!scanToken.equals(token)||token.isEmpty())return;
                prefs.edit().putBoolean("initial_best_node_scan_v1",true).apply();
                if(winner!=null){
                    boolean canAuto=userInitiated||!prefs.getBoolean("manual_node_selected",false);
                    if(canAuto){
                        selectedNode=catalog.find(winner.id); if(selectedNode!=null)prefs.edit().putInt("selected_node_id",selectedNode.id).apply();
                        if((activeNodePicker==null||!activeNodePicker.isShowing()) && currentTab==TAB_HOME) refreshHomeBoundViews();
                        toast((userInitiated?"已选择":"已自动选择")+"低延迟节点 · "+winnerMs+" ms");
                    }
                }else if(userInitiated)toast("当前节点需连接后实测，未执行入口优选");
            });
        });
    }

    private View detailLine(String label,String value) {
        LinearLayout line=row(); line.setGravity(Gravity.CENTER_VERTICAL); line.setPadding(0,dp(10),0,dp(10));
        line.addView(text(label,12,p.muted,false),new LinearLayout.LayoutParams(0,ViewGroup.LayoutParams.WRAP_CONTENT,1f));
        TextView valueView=text(value,12,p.ink,true); valueView.setGravity(Gravity.END); line.addView(valueView,wrapWrap());
        return line;
    }

    private long measureNodeLatency(NodeCatalog.Node node) throws Exception { return measureNodeLatency(node,3000); }

    private long measureNodeLatency(NodeCatalog.Node node,int timeoutMs) throws Exception {
        CoreState.Snapshot core=CoreState.read(this);
        // Once this exact node is carrying the live VPN, the only meaningful
        // measurement is an end-to-end request through the TUN.  A bare TCP
        // connect to its server is merely an entry reachability hint and can
        // legitimately be blocked while the protocol itself is healthy.
        if(core.state==CoreState.RUNNING&&core.nodeId==node.id){
            VpnCoreService.TunnelHealth health=VpnCoreService.checkTunnelHealthNow();
            if(health.healthy)return health.latencyMs;
            throw new NodeProbeException("隧道异常",new IOException(health.error));
        }
        SingBoxConfigBuilder.Endpoint endpoint=SingBoxConfigBuilder.endpoint(node);
        if(!endpoint.tcpProbeSupported){
            throw new UdpProbeUnavailableException();
        }
        InetAddress[] resolved;
        try{resolved=VpnCoreService.resolveProbeHost(endpoint.host);}
        catch(UnknownHostException e){throw new NodeProbeException("DNS失败",e);}
        catch(Exception e){throw new NodeProbeException("物理网络不可用",e);}
        Exception last=null; boolean hasIpv4=false;
        for(InetAddress address:resolved){
            if(!(address instanceof java.net.Inet4Address))continue;
            hasIpv4=true;
            try{return measureEntrySocket(address,endpoint.port,timeoutMs,true);}
            catch(Exception primary){
                last=primary;
                // Some Android builds do not allow a protected socket to be
                // explicitly network-bound. Retry once with protect() only;
                // it still bypasses the active VPN and avoids a false red
                // result in the node picker.
                if(core.state==CoreState.RUNNING){
                    try{return measureEntrySocket(address,endpoint.port,timeoutMs,false);}
                    catch(Exception fallback){last=fallback;}
                }
            }
        }
        if(!hasIpv4)throw new NodeProbeException("仅 IPv6",null);
        if(last instanceof SocketTimeoutException)throw new NodeProbeException("入口超时",last);
        if(last instanceof ConnectException)throw new NodeProbeException("入口拒绝",last);
        throw new NodeProbeException("入口不可达",last);
    }

    private long measureEntrySocket(InetAddress address,int port,int timeoutMs,boolean bindUnderlyingNetwork) throws Exception {
        long start=System.nanoTime();
        try(Socket socket=new Socket()){
            VpnCoreService.prepareProbeSocket(socket,bindUnderlyingNetwork);
            socket.connect(new InetSocketAddress(address,port),timeoutMs);
            return Math.max(1,(System.nanoTime()-start)/1_000_000L);
        }
    }

    private String probeFailureLabel(Exception error){
        if(error instanceof UdpProbeUnavailableException)return "需连接";
        if(error instanceof NodeProbeException)return ((NodeProbeException)error).label;
        if(error instanceof UnknownHostException)return "DNS失败";
        if(error instanceof SocketTimeoutException)return "超时";
        return "失败";
    }

    private NodeProbeResult probeNode(NodeCatalog.Node node,int timeoutMs){
        try{return NodeProbeResult.success(node,measureNodeLatency(node,timeoutMs));}
        catch(Exception error){return NodeProbeResult.failure(node,error);}
    }

    private List<NodeProbeResult> probeNodes(List<NodeCatalog.Node> nodes,int timeoutMs,ProbeProgress progress){
        List<NodeProbeResult> results=new ArrayList<>();
        CompletionService<NodeProbeResult> completion=new ExecutorCompletionService<>(probeExecutor);
        List<Future<NodeProbeResult>> futures=new ArrayList<>();
        for(NodeCatalog.Node node:nodes)futures.add(completion.submit(()->probeNode(node,timeoutMs)));
        try{
            for(int completed=1;completed<=nodes.size();completed++){
                NodeProbeResult result=completion.take().get();results.add(result);
                if(progress!=null)progress.onResult(result,completed,nodes.size());
            }
        }catch(InterruptedException interrupted){Thread.currentThread().interrupt();}
        catch(Exception ignored){}
        finally{for(Future<NodeProbeResult> future:futures)if(!future.isDone())future.cancel(true);}
        return results;
    }

    private int latencyColor(long ms) {
        if(ms<100) return p.success;
        if(ms<250) return p.warningText;
        return p.danger;
    }

    private String latencyTargetLabel() {
        String url=prefs.getString("latency_test_url",DEFAULT_LATENCY_URL);
        try { return new URL(url).getHost(); } catch(Exception ignored) { return "Google"; }
    }

    private void showLatencyTargetSettings() {
        Dialog dialog=bottomDialog(); LinearLayout sheet=sheet();
        sheet.addView(text("延迟测试网站",21,p.ink,true),matchWrap()); gap(sheet,5);
        sheet.addView(text("用于网络连通性测试。节点延迟仍以节点入口 TCP 握手为准。",12,p.muted,false),matchWrap()); gap(sheet,14);
        EditText url=input("https://example.com",false); url.setText(prefs.getString("latency_test_url",DEFAULT_LATENCY_URL)); url.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI); sheet.addView(url,matchWrap());
        gap(sheet,10);
        TextView status=text("默认：Google generate_204",11,p.muted,false); sheet.addView(status,matchWrap()); gap(sheet,14);
        LinearLayout actions=row(); Button test=secondaryButton("测试网站"); Button save=primaryButton("保存"); actions.addView(test,new LinearLayout.LayoutParams(0,dp(52),1f)); gapH(actions,10); actions.addView(save,new LinearLayout.LayoutParams(0,dp(52),1f)); sheet.addView(actions,matchWrap()); gap(sheet,8);
        TextView reset=text("恢复默认网站",12,p.accent,true); reset.setGravity(Gravity.CENTER); reset.setPadding(dp(8),dp(10),dp(8),dp(8)); sheet.addView(reset,matchWrap());

        test.setOnClickListener(v->{
            String candidate=normalizeLatencyUrl(url.getText().toString()); if(candidate==null){toast("请输入有效的 HTTPS 网站");return;}
            test.setEnabled(false); test.setText("测试中…"); status.setText("正在请求…"); status.setTextColor(p.muted);
            io.execute(()->{
                try{long ms=measureHttpLatency(candidate);runOnUiThread(()->{test.setEnabled(true);test.setText("重新测试");status.setText("响应 "+ms+" ms");status.setTextColor(latencyColor(ms));});}
                catch(Exception e){runOnUiThread(()->{test.setEnabled(true);test.setText("重新测试");status.setText("连接失败");status.setTextColor(p.danger);});}
            });
        });
        save.setOnClickListener(v->{String candidate=normalizeLatencyUrl(url.getText().toString());if(candidate==null){toast("请输入有效的 HTTPS 网站");return;}prefs.edit().putString("latency_test_url",candidate).apply();dialog.dismiss();showShell(TAB_MINE);});
        reset.setOnClickListener(v->{url.setText(DEFAULT_LATENCY_URL);url.setSelection(url.length());});
        showBottomDialog(dialog,sheet);
    }

    private String connectionDiagnosticLabel(){
        CoreState.Snapshot core=CoreState.read(this);
        return core.state==CoreState.RUNNING?"已通过启动健康检查 · 点击复测":"连接后检测 DNS 与代理出口";
    }

    private void showConnectionDiagnostic(){
        Dialog dialog=bottomDialog();LinearLayout sheet=sheet();
        sheet.addView(text("连接诊断",21,p.ink,true),matchWrap());gap(sheet,5);
        sheet.addView(text("真实请求会经过当前 VPN，用于同时检查 DNS、分流、节点协议与代理出口。",12,p.muted,false),matchWrap());gap(sheet,14);
        LinearLayout profile=column();profile.setPadding(dp(15),dp(12),dp(15),dp(12));profile.setBackground(roundRect(p.surfaceAlt,17,0,0));
        CoreState.Snapshot core=CoreState.read(this);
        profile.addView(detailLine("当前节点",core.nodeName.isEmpty()?"—":core.nodeName),matchWrap());profile.addView(divider());
        profile.addView(detailLine("分流模式",routeMode.label),matchWrap());profile.addView(divider());
        profile.addView(detailLine("网络配置",SingBoxConfigBuilder.NETWORK_PROFILE),matchWrap());sheet.addView(profile,matchWrap());gap(sheet,12);
        TextView result=text(core.state==CoreState.RUNNING?"准备检测…":"请先连接 VPN",12,core.state==CoreState.RUNNING?p.muted:p.warningText,true);result.setGravity(Gravity.CENTER);result.setPadding(dp(12),dp(12),dp(12),dp(12));result.setBackground(roundRect(p.surfaceAlt,15,0,0));sheet.addView(result,matchWrap());gap(sheet,12);
        Button run=primaryButton("开始检测");run.setEnabled(core.state==CoreState.RUNNING);run.setAlpha(run.isEnabled()?1f:.58f);sheet.addView(run,matchWrap());
        run.setOnClickListener(v->{
            run.setEnabled(false);run.setAlpha(.78f);run.setText("检测中…");result.setText("正在通过隧道访问检测站点…");result.setTextColor(p.muted);
            io.execute(()->{VpnCoreService.TunnelHealth health=VpnCoreService.checkTunnelHealthNow();runOnUiThread(()->{
                run.setEnabled(true);run.setAlpha(1f);run.setText("重新检测");
                if(health.healthy){result.setText("网络正常 · "+health.endpoint+" · "+health.latencyMs+" ms");result.setTextColor(p.success);}
                else{result.setText("检测失败 · "+health.error);result.setTextColor(p.danger);}
            });});
        });
        showBottomDialog(dialog,sheet);
        if(core.state==CoreState.RUNNING)run.postDelayed(run::performClick,180L);
    }

    private String normalizeLatencyUrl(String raw) {
        String value=raw==null?"":raw.trim(); if(value.isEmpty()) return null;
        if(!value.contains("://")) value="https://"+value;
        try{
            URL u=new URL(value); String protocol=u.getProtocol();
            if(!"https".equalsIgnoreCase(protocol) && (!BuildConfig.DEBUG || !"http".equalsIgnoreCase(protocol))) return null;
            if(u.getHost()==null||u.getHost().isEmpty()) return null;
            return value;
        }catch(Exception e){return null;}
    }

    private long measureHttpLatency(String target) throws Exception {
        HttpURLConnection c=(HttpURLConnection)new URL(target).openConnection();
        c.setConnectTimeout(4000); c.setReadTimeout(4000); c.setInstanceFollowRedirects(true); c.setRequestMethod("GET");
        c.setRequestProperty("User-Agent","Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36");
        c.setRequestProperty("Accept","*/*");
        c.setRequestProperty("Accept-Language","zh-CN,zh;q=0.9,en;q=0.8");
        c.setRequestProperty("X-XVPN-Client","android/"+BuildConfig.VERSION_NAME);
        long start=System.nanoTime();
        try { c.getResponseCode(); return Math.max(1,(System.nanoTime()-start)/1_000_000L); }
        finally { c.disconnect(); }
    }

    private void showServerSettings() {
        Dialog dialog=bottomDialog(); LinearLayout sheet=sheet();
        TextView adv=text("ADVANCED",10,p.subtle,true); adv.setLetterSpacing(.16f); sheet.addView(adv,matchWrap()); gap(sheet,7);
        sheet.addView(text("连接设置",21,p.ink,true),matchWrap()); gap(sheet,5); sheet.addView(text("仅用于连接你的 XVPN Panel。修改地址后会退出当前账户。",12,p.muted,false),matchWrap()); gap(sheet,14);
        EditText url=input("https://panel.example.com",false); url.setText(baseUrl); url.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI); sheet.addView(url,matchWrap()); gap(sheet,10);
        TextView status=text("Release 构建强制使用 HTTPS",11,p.muted,false); sheet.addView(status,matchWrap()); gap(sheet,14);
        LinearLayout actions=row(); Button test=secondaryButton("测试连接"); Button save=primaryButton("保存"); actions.addView(test,new LinearLayout.LayoutParams(0,dp(52),1f)); gapH(actions,10); actions.addView(save,new LinearLayout.LayoutParams(0,dp(52),1f)); sheet.addView(actions,matchWrap()); gap(sheet,9);
        TextView reset=text("恢复默认地址",12,p.accent,true); reset.setGravity(Gravity.CENTER); reset.setPadding(dp(8),dp(10),dp(8),dp(8)); sheet.addView(reset,matchWrap());
        test.setOnClickListener(v->{ String candidate=ApiClient.normalizePanelBase(url.getText().toString()); if(!validServerCandidate(candidate)){return;} test.setEnabled(false); status.setText("正在测试…"); io.execute(()->{ try{ JSONObject h=ApiClient.request(candidate,"/health","GET",null,null); runOnUiThread(()->{test.setEnabled(true);status.setText("连接成功 · Panel "+h.optString("version",""));status.setTextColor(p.success);}); }catch(Exception e){runOnUiThread(()->{test.setEnabled(true);status.setText(apiMessage(e));status.setTextColor(p.warningText);});} }); });
        save.setOnClickListener(v->{ String candidate=ApiClient.normalizePanelBase(url.getText().toString()); if(!validServerCandidate(candidate))return; boolean changed=!candidate.equals(baseUrl); baseUrl=candidate; prefs.edit().putString("base_url",baseUrl).apply(); if(changed){ clearSessionLocal(); dialog.dismiss(); showLogin("服务器地址已更新，请重新登录"); } else { dialog.dismiss(); toast("地址已保存"); } });
        reset.setOnClickListener(v->{ url.setText(DEFAULT_BASE_URL); url.setSelection(url.length()); });
        showBottomDialog(dialog,sheet);
    }

    private boolean validServerCandidate(String candidate) {
        if(!ApiClient.isValidPanelBase(candidate)){toast("请输入完整 Panel 地址，例如 https://example.com");return false;}
        if(!BuildConfig.DEBUG&&!ApiClient.isHttps(candidate)){toast("正式版仅允许 HTTPS 地址");return false;}
        return true;
    }

    private boolean validateBaseForBuild() { return validServerCandidate(baseUrl); }

    private void showChangePassword() {
        Dialog dialog=bottomDialog(); LinearLayout sheet=sheet(); sheet.addView(text("修改密码",21,p.ink,true),matchWrap()); gap(sheet,5); sheet.addView(text("成功后 Panel 会撤销旧 Token，App 会自动替换为新 Token。",12,p.muted,false),matchWrap()); gap(sheet,14);
        EditText current=input("当前密码",true), next=input("新密码（至少 8 位）",true), confirm=input("确认新密码",true); sheet.addView(passwordField(current),new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,dp(55)));gap(sheet,10);sheet.addView(passwordField(next),new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,dp(55)));gap(sheet,10);sheet.addView(passwordField(confirm),new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,dp(55)));gap(sheet,14);
        Button save=primaryButton("更新密码"); sheet.addView(save,matchWrap()); save.setOnClickListener(v->{ String a=current.getText().toString(),b=next.getText().toString(),c=confirm.getText().toString(); if(a.isEmpty()){toast("请输入当前密码");return;} if(b.length()<8){toast("新密码至少 8 位");return;} if(a.equals(b)){toast("新密码不能与当前密码相同");return;} if(!b.equals(c)){toast("两次输入的新密码不一致");return;} save.setEnabled(false); save.setAlpha(.78f); save.setText("更新中…"); io.execute(()->{try{JSONObject body=new JSONObject().put("current_password",a).put("new_password",b);JSONObject out=ApiClient.request(baseUrl,"/change-password","POST",token,body);String newToken=out.optString("token","");if(newToken.isEmpty())throw new ApiClient.ApiException(0,"INVALID_RESPONSE","服务器未返回新 Token",0);SecureTokenStore.save(prefs,newToken);token=newToken;runOnUiThread(()->{dialog.dismiss();toast("密码修改成功");});}catch(Exception e){runOnUiThread(()->{save.setEnabled(true);save.setAlpha(1f);save.setText("更新密码");if(e instanceof ApiClient.ApiException&&((ApiClient.ApiException)e).isAuthFailure())forceLogout(((ApiClient.ApiException)e).code,e.getMessage());else toast(apiMessage(e));});}}); }); showBottomDialog(dialog,sheet);
    }

    private void confirmLogout() {
        Dialog dialog=bottomDialog();
        LinearLayout sheet=sheet();
        sheet.addView(text("退出登录",21,p.ink,true),matchWrap());
        gap(sheet,7);
        TextView body=text("退出后会清除本机登录令牌，下次使用需要重新登录。",12,p.muted,false);
        body.setLineSpacing(dp(3),1f);
        sheet.addView(body,matchWrap());
        gap(sheet,18);
        LinearLayout actions=row();
        Button cancel=secondaryButton("取消");
        Button logout=logoutButton("确认退出");
        actions.addView(cancel,new LinearLayout.LayoutParams(0,dp(52),1f));
        gapH(actions,10);
        actions.addView(logout,new LinearLayout.LayoutParams(0,dp(52),1f));
        sheet.addView(actions,matchWrap());
        cancel.setOnClickListener(v->dialog.dismiss());
        logout.setOnClickListener(v->{ dialog.dismiss(); logoutRemote(); });
        showBottomDialog(dialog,sheet);
    }

    private void logoutRemote() {
        String old=token; clearSessionLocal(); showLogin(null); if(old==null||old.isEmpty())return; io.execute(()->{try{ApiClient.request(baseUrl,"/logout","POST",old,null);}catch(Exception ignored){}});
    }

    private void forceLogout(String code,String message) {
        clearSessionLocal(); String msg="ACCOUNT_DISABLED".equals(code)?"账户已停用":"TOKEN_EXPIRED".equals(code)?"登录已过期，请重新登录":(message==null?"登录状态已失效":message); showLogin(msg);
    }

    private void clearSessionLocal() {
        VpnCoreService.stop(this);
        clearPendingCore();
        token=""; username=""; bootstrap=null; catalog=new NodeCatalog(); selectedNode=null; SecureTokenStore.clear(prefs); prefs.edit().remove("selected_node_id").remove("initial_best_node_scan_v1").remove("manual_node_selected").apply();
    }

    private String apiMessage(Exception e) {
        if(e instanceof ApiClient.ApiException){ApiClient.ApiException a=(ApiClient.ApiException)e;if("RATE_LIMITED".equals(a.code)&&a.retryAfter>0)return a.getMessage()+"（约 "+a.retryAfter+" 秒）";return a.getMessage();} return e.getMessage()==null?"操作失败，请稍后重试":e.getMessage();
    }

    private void showInfoSheet(String title,String body,String buttonText) {
        Dialog dialog=bottomDialog();LinearLayout sheet=sheet();sheet.addView(text(title,21,p.ink,true),matchWrap());gap(sheet,8);TextView b=text(body,13,p.muted,false);b.setLineSpacing(dp(3),1f);sheet.addView(b,matchWrap());gap(sheet,18);Button ok=primaryButton(buttonText);ok.setOnClickListener(v->dialog.dismiss());sheet.addView(ok,matchWrap());showBottomDialog(dialog,sheet);
    }

    private void runBestNodeScanInPicker(Dialog dialog, TextView action, TextView status, SparseArray<TextView> latencyBadges, SparseArray<SelectionDotView> tails, SparseArray<View> items, boolean[] pickerChanged) {
        if(nodeScanRunning){status.setVisibility(View.VISIBLE);status.setText("后台节点测速正在进行，请稍候…");return;}
        List<NodeCatalog.Node> nodes=new ArrayList<>(); for(NodeCatalog.Country c:catalog.countries)nodes.addAll(c.nodes);
        if(nodes.isEmpty()){status.setVisibility(View.VISIBLE);status.setText("当前没有可测速节点");return;}
        final CoreState.Snapshot initialCore=CoreState.read(this);
        final boolean connectedScan=initialCore.state==CoreState.RUNNING;
        nodeScanRunning=true; beginScanAction(action,connectedScan); status.setVisibility(View.VISIBLE); status.setTextColor(p.muted); status.setText(connectedScan?"正在验证当前隧道与候选节点入口…":"正在准备节点测速…");
        String scanToken=token;
        io.execute(()->{
            List<NodeProbeResult> results=probeNodes(nodes,2200,(result,completed,total)->runOnUiThread(()->{
                if(!dialog.isShowing())return;
                status.setText("已完成 "+completed+" / "+total+" · "+result.node.name);
                TextView badge=latencyBadges.get(result.node.id);
                if(badge!=null){
                    badge.setText(result.error==null?result.latencyMs+" ms":probeFailureLabel(result.error));
                    badge.setTextColor(result.error==null?latencyColor(result.latencyMs):result.error instanceof UdpProbeUnavailableException?p.warningText:p.danger);
                    popResult(badge);
                }
            }));
            NodeCatalog.Node bestNode=null;long bestMs=Long.MAX_VALUE;int udpSkipped=0;
            for(NodeProbeResult result:results){
                if(result.error==null){prefs.edit().putLong("node_latency_"+result.node.id,result.latencyMs).apply();if(result.latencyMs<bestMs){bestMs=result.latencyMs;bestNode=result.node;}}
                else if(result.error instanceof UdpProbeUnavailableException)udpSkipped++;
            }
            final NodeCatalog.Node winner=bestNode; final long winnerMs=bestMs; final int skippedUdp=udpSkipped;
            runOnUiThread(()->{
                nodeScanRunning=false;
                finishScanAction(action,winner!=null,connectedScan);
                if(!scanToken.equals(token)||token.isEmpty()) return;
                if(!dialog.isShowing()) return;
                if(connectedScan){
                    NodeProbeResult current=null;
                    for(NodeProbeResult result:results)if(result.node.id==initialCore.nodeId){current=result;break;}
                    action.setText("重新检测");
                    if(current!=null&&current.error==null){
                        status.setText("当前节点网络正常 · "+current.latencyMs+" ms；其他节点显示入口连通性，可直接点选切换");
                        status.setTextColor(p.success);
                    }else{
                        status.setText("当前隧道检测异常；请先断开后重新连接");
                        status.setTextColor(p.danger);
                    }
                    // Do not rank a live end-to-end tunnel against another
                    // node's bare TCP handshake, then switch on that invalid
                    // comparison. Tapping a candidate still performs the
                    // existing guarded hot switch with rollback.
                    return;
                }
                if(winner==null){status.setText(skippedUdp>0?"UDP 节点需连接后实测，未参与入口优选":"测速完成 · 暂无可用节点");status.setTextColor(skippedUdp>0?p.warningText:p.danger);return;}
                if(!reconfigureConnectedNode(winner)){status.setText("节点可用，但切换配置失败");status.setTextColor(p.danger);return;}
                selectedNode=catalog.find(winner.id);
                if(selectedNode!=null)prefs.edit().putInt("selected_node_id",selectedNode.id).putBoolean("manual_node_selected",true).putBoolean("initial_best_node_scan_v1",true).apply();
                pickerChanged[0]=true;
                for(int i=0;i<tails.size();i++){
                    int id=tails.keyAt(i); boolean chosen=selectedNode!=null&&selectedNode.id==id;
                    SelectionDotView tail=tails.valueAt(i); tail.setActive(chosen);
                    View item=items.get(id); if(item!=null)item.setBackground(roundRect(chosen?p.accentSoft:p.surfaceAlt,16,chosen?p.accent:0,chosen?1:0));
                }
                status.setText("已选择 "+winner.name+" · "+winnerMs+" ms"+(skippedUdp>0?" · UDP 节点需连接实测":"")); status.setTextColor(p.success);
                View winnerItem=items.get(winner.id); if(winnerItem!=null)highlightWinner(winnerItem);
            });
        });
    }

    private static final class UdpProbeUnavailableException extends Exception {}
    private static final class NodeProbeException extends Exception {final String label;NodeProbeException(String label,Throwable cause){super(label,cause);this.label=label;}}
    private interface ProbeProgress {void onResult(NodeProbeResult result,int completed,int total);}
    private static final class NodeProbeResult {
        final NodeCatalog.Node node;final long latencyMs;final Exception error;
        private NodeProbeResult(NodeCatalog.Node node,long latencyMs,Exception error){this.node=node;this.latencyMs=latencyMs;this.error=error;}
        static NodeProbeResult success(NodeCatalog.Node node,long latencyMs){return new NodeProbeResult(node,latencyMs,null);}
        static NodeProbeResult failure(NodeCatalog.Node node,Exception error){return new NodeProbeResult(node,0L,error);}
    }

    private void switchThemeWithFade(String value) {
        if(value.equals(prefs.getString("theme","system"))) return;
        FrameLayout host=currentRoot;
        if(host==null || host.getWidth()<=0 || host.getHeight()<=0){
            prefs.edit().putString("theme",value).apply(); refreshPalette(); applySystemBars(); showShell(TAB_MINE); return;
        }

        // Snapshot the current fully-rendered frame, rebuild the target theme underneath it,
        // then fade the snapshot away. This prevents the one-frame full-black/full-white flash.
        Bitmap snapshot;
        try {
            snapshot=Bitmap.createBitmap(host.getWidth(),host.getHeight(),Bitmap.Config.ARGB_8888);
            Canvas canvas=new Canvas(snapshot); host.draw(canvas);
        } catch(Exception e) {
            prefs.edit().putString("theme",value).apply(); refreshPalette(); applySystemBars(); showShell(TAB_MINE); return;
        }

        ViewGroup decor=(ViewGroup)getWindow().getDecorView();
        ImageView ghost=new ImageView(this); ghost.setImageBitmap(snapshot); ghost.setScaleType(ImageView.ScaleType.FIT_XY); ghost.setAlpha(1f);
        decor.addView(ghost,new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,ViewGroup.LayoutParams.MATCH_PARENT));

        prefs.edit().putString("theme",value).apply();
        refreshPalette(); applySystemBars(); showShell(TAB_MINE);
        ghost.bringToFront();
        ghost.animate().alpha(0f).setStartDelay(35).setDuration(360).setInterpolator(new android.view.animation.AccelerateDecelerateInterpolator()).withEndAction(()->{
            try { decor.removeView(ghost); } catch(Exception ignored) {}
            if(!snapshot.isRecycled()) snapshot.recycle();
        }).start();
    }

    private void maybeCheckForUpdates() {
        if(token==null || token.isEmpty() || updateCheckRunning) return;
        // Development builds keep update checks manual; formal releases check silently.
        if(BuildConfig.VERSION_NAME.contains("-dev")) return;
        long now=System.currentTimeMillis();
        long last=prefs.getLong("last_update_check_at",0L);
        long intervalSeconds=prefs.getLong("app_update_check_interval_seconds",UPDATE_CHECK_INTERVAL_MS/1000L);
        long interval=Math.max(3600L,Math.min(7L*24L*3600L,intervalSeconds))*1000L;
        if(now-last<interval) return;
        checkForUpdates(false);
    }

    private void checkForUpdates(boolean userInitiated) {
        if(updateCheckRunning){ if(userInitiated) toast("正在检查更新…"); return; }
        updateCheckRunning=true;
        if(userInitiated) toast("正在检查更新…");
        io.execute(()->{
            try {
                AppUpdateChecker.Result result=checkUpdateSources();
                prefs.edit().putLong("last_update_check_at",System.currentTimeMillis()).apply();
                runOnUiThread(()->{
                    updateCheckRunning=false;
                    if(isFinishing()) return;
                    if(result.updateAvailable) showUpdateSheet(result);
                    else if(userInitiated) {
                        if(!result.statusMessage.isEmpty()) toast(result.statusMessage);
                        else if(!result.hasRelease) toast("GitHub 还没有正式 Release");
                        else toast("当前已是最新版本");
                    }
                });
            } catch(Exception e) {
                runOnUiThread(()->{
                    updateCheckRunning=false;
                    if(userInitiated) toast("检查更新失败，请稍后重试");
                });
            }
        });
    }

    private AppUpdateChecker.Result checkUpdateSources() throws Exception {
        // Panel v1.2.1 owns update policy; GitHub is used only when the endpoint is unavailable.
        if(baseUrl!=null && !baseUrl.isEmpty()) {
            try {
                String query="/app/update?version_name="+Uri.encode(BuildConfig.VERSION_NAME)+"&version_code="+BuildConfig.VERSION_CODE;
                JSONObject json=ApiClient.request(baseUrl,query,"GET",null,null);
                if(!json.optBoolean("enabled",true)) {
                    return new AppUpdateChecker.Result(true,false,"","","",false,"服务器已暂停 App 更新检查");
                }
                String version=json.optString("latest_version_name",json.optString("version_name",json.optString("version",""))).trim();
                if(!version.isEmpty()) {
                    String notes=json.optString("release_notes",json.optString("releaseNotes",json.optString("changelog",json.optString("notes",json.optString("message",""))))).trim();
                    String url=json.optString("apk_url",json.optString("download_url",json.optString("release_url",json.optString("url","")))).trim();
                    boolean newer=json.has("update_available")?json.optBoolean("update_available",false):
                            (json.optInt("version_code",0)>BuildConfig.VERSION_CODE || AppUpdateChecker.compareVersions(version,BuildConfig.VERSION_NAME)>0);
                    boolean force=json.optBoolean("force_update",false)||json.optBoolean("must_update",false);
                    return new AppUpdateChecker.Result(true,newer,AppUpdateChecker.normalizeVersion(version),notes,url,force,"");
                }
            } catch(Exception panelUnavailable) {
                // Panel owns update policy when reachable. A transient DNS/TLS/5xx
                // failure must not suppress the signed GitHub Release fallback.
            }
        }
        return AppUpdateChecker.check();
    }

    private void showUpdateSheet(AppUpdateChecker.Result result) {
        Dialog dialog=bottomDialog(); LinearLayout sheet=sheet();
        boolean enforce=result.forceUpdate && isSafeExternalUrl(result.pageUrl);
        dialog.setCancelable(!enforce); dialog.setCanceledOnTouchOutside(!enforce);
        LinearLayout updateHeader=row(); updateHeader.setGravity(Gravity.CENTER_VERTICAL);
        BrandMarkView updateLogo=brandMark(); updateHeader.addView(updateLogo,new LinearLayout.LayoutParams(dp(38),dp(38)));
        LinearLayout updateText=column(); LinearLayout.LayoutParams updateTextLp=new LinearLayout.LayoutParams(0,ViewGroup.LayoutParams.WRAP_CONTENT,1f); updateTextLp.leftMargin=dp(10);
        TextView tag=text("XVPN UPDATE",10,p.accent,true); tag.setLetterSpacing(.15f); updateText.addView(tag,matchWrap()); gap(updateText,4);
        updateText.addView(text((enforce?"需要更新 ":"发现新版本 ") + result.version,21,p.ink,true),matchWrap()); updateHeader.addView(updateText,updateTextLp);
        sheet.addView(updateHeader,matchWrap()); gap(sheet,10);
        String notes=result.notes==null?"":result.notes.trim();
        if(notes.isEmpty()) notes="新的正式版本已经发布。";
        if(notes.length()>700) notes=notes.substring(0,700)+"…";
        TextView body=text(notes,12,p.muted,false); body.setLineSpacing(dp(3),1f); sheet.addView(body,matchWrap()); gap(sheet,18);
        LinearLayout actions=row(); Button open=primaryButton(enforce?"立即更新":"查看更新");
        if(!enforce){Button later=secondaryButton("稍后");actions.addView(later,new LinearLayout.LayoutParams(0,dp(50),1f));gapH(actions,10);later.setOnClickListener(v->dialog.dismiss());}
        actions.addView(open,new LinearLayout.LayoutParams(0,dp(50),1f)); sheet.addView(actions,matchWrap());
        open.setOnClickListener(v->{if(!enforce)dialog.dismiss();openExternalUrl(result.pageUrl);});
        showBottomDialog(dialog,sheet);
    }

    private void openExternalUrl(String url) {
        if(!isSafeExternalUrl(url)){toast("更新地址不可用");return;}
        try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url))); }
        catch(Exception e){ toast("无法打开更新页面"); }
    }

    private boolean isSafeExternalUrl(String url) {
        if(url==null || url.trim().isEmpty())return false;
        try{String scheme=Uri.parse(url.trim()).getScheme();return "https".equalsIgnoreCase(scheme)||(BuildConfig.DEBUG&&"http".equalsIgnoreCase(scheme));}catch(Exception ignored){return false;}
    }

    private void showAccountSummary() {
        Dialog dialog=bottomDialog(); LinearLayout sheet=sheet();
        TextView tag=text("PRIVATE NETWORK",10,p.subtle,true); tag.setLetterSpacing(.15f); sheet.addView(tag,matchWrap()); gap(sheet,7);
        sheet.addView(text(username.isEmpty()?"XVPN 用户":username,22,p.ink,true),matchWrap()); gap(sheet,14);
        LinearLayout info=column(); info.setPadding(dp(15),dp(10),dp(15),dp(10)); info.setBackground(roundRect(p.surfaceAlt,18,0,0));
        info.addView(detailLine("账户状态","正常"),matchWrap()); info.addView(divider()); info.addView(detailLine("客户端版本",BuildConfig.VERSION_NAME),matchWrap()); sheet.addView(info,matchWrap()); gap(sheet,16);
        Button done=primaryButton("完成"); done.setOnClickListener(v->dialog.dismiss()); sheet.addView(done,matchWrap()); showBottomDialog(dialog,sheet);
    }

    private View sessionNotice(String message) {
        LinearLayout notice = row();
        notice.setGravity(Gravity.CENTER_VERTICAL);
        notice.setPadding(dp(13), dp(11), dp(13), dp(11));
        boolean successNotice = message != null && message.contains("成功");
        int noticeColor = successNotice ? p.success : p.warningText;
        int bg = successNotice
                ? (p.dark ? 0xFF142B28 : 0xFFEEFBF7)
                : (p.dark ? 0xFF211E1C : 0xFFFFF8EE);
        int border = successNotice
                ? (p.dark ? 0xFF28534B : 0xFFCDEFE5)
                : (p.dark ? 0xFF493A2C : 0xFFFFE3BF);
        notice.setBackground(roundRect(bg, 15, border, 1));

        TextView dot = text("●", 8, noticeColor, true);
        dot.setGravity(Gravity.CENTER);
        notice.addView(dot, new LinearLayout.LayoutParams(dp(18), ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView label = text(message, 12, noticeColor, true);
        label.setLineSpacing(dp(2), 1f);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        lp.leftMargin = dp(3);
        notice.addView(label, lp);
        return notice;
    }

    private String reauthMessage(String code) {
        if ("ACCOUNT_DISABLED".equals(code)) return "账户已停用，请联系管理员";
        if ("TOKEN_EXPIRED".equals(code)) return "登录已过期，请重新登录";
        return "登录状态已更新，请重新登录";
    }

    private android.graphics.drawable.Drawable authBackBg() {
        GradientDrawable g=new GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT,new int[]{p.dark?0xFF1E2A4A:0xFFEDF3FF,p.dark?0xFF292545:0xFFF3EEFF});
        g.setCornerRadius(dp(18)); g.setStroke(dp(1),p.dark?0x405D82FF:0x305D82FF); return g;
    }

    private android.graphics.drawable.Drawable premiumHeaderBg(float radius) {
        GradientDrawable base=roundRect(p.surface,radius,p.dark?0xFF2A3550:0xFFE3E9F4,1);
        GradientDrawable glow=roundRect(p.dark?0x181E4FFF:0x0F5D82FF,radius+1,0,0);
        LayerDrawable l=new LayerDrawable(new android.graphics.drawable.Drawable[]{glow,base});
        l.setLayerInset(0,0,dp(2),0,0); l.setLayerInset(1,0,0,0,dp(2)); return l;
    }

    private android.graphics.drawable.Drawable profileCardBg(float radius) {
        GradientDrawable gradient=horizontalGradient(p.accent,p.purple,radius);
        GradientDrawable edge=roundRect(Color.TRANSPARENT,radius,0x42FFFFFF,1);
        LayerDrawable layers=new LayerDrawable(new android.graphics.drawable.Drawable[]{gradient,edge}); return ripple(layers,0x22FFFFFF);
    }

    // ----- UI helpers -----
    private LinearLayout column(){LinearLayout v=new LinearLayout(this);v.setOrientation(LinearLayout.VERTICAL);return v;}
    private LinearLayout row(){LinearLayout v=new LinearLayout(this);v.setOrientation(LinearLayout.HORIZONTAL);return v;}
    private ScrollView polishedScrollView(boolean fillViewport){ScrollView v=new ScrollView(this);v.setFillViewport(fillViewport);v.setClipToPadding(false);v.setVerticalScrollBarEnabled(false);v.setHorizontalScrollBarEnabled(false);v.setFadingEdgeLength(0);v.setOverScrollMode(View.OVER_SCROLL_NEVER);return v;}
    private TextView text(String s,float sp,int color,boolean bold){TextView t=new TextView(this);t.setText(s);t.setTextSize(sp);t.setTextColor(color);t.setTypeface(Typeface.create("sans",bold?Typeface.BOLD:Typeface.NORMAL));t.setIncludeFontPadding(false);return t;}
    private TextView pill(String s,int bg,int fg,float sp){TextView t=text(s,sp,fg,true);t.setGravity(Gravity.CENTER);t.setPadding(dp(10),dp(6),dp(10),dp(6));t.setBackground(roundRect(bg,14,0,0));return t;}
    private BrandMarkView brandMark(){
        return new BrandMarkView(this,p.dark);
    }
    private EditText input(String hint,boolean password){EditText e=new EditText(this);e.setHint(hint);e.setHintTextColor(p.subtle);e.setTextColor(p.ink);e.setTextSize(14);e.setSingleLine(true);e.setPadding(dp(14),0,dp(14),0);e.setBackground(roundRect(p.field,16,p.border,1));e.setMinHeight(dp(55));e.setInputType(password?InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD:InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_NORMAL);return e;}
    private FrameLayout passwordField(EditText field) {
        FrameLayout box=new FrameLayout(this);
        field.setPadding(dp(14),0,dp(66),0);
        box.addView(field,new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,ViewGroup.LayoutParams.MATCH_PARENT));
        TextView toggle=text("显示",11,p.accent,true); toggle.setGravity(Gravity.CENTER); toggle.setPadding(dp(10),0,dp(10),0);
        FrameLayout.LayoutParams tlp=new FrameLayout.LayoutParams(dp(58),ViewGroup.LayoutParams.MATCH_PARENT,Gravity.END|Gravity.CENTER_VERTICAL);
        box.addView(toggle,tlp); pressMotion(toggle,.92f);
        final boolean[] visible={false};
        toggle.setOnClickListener(v->{
            v.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);
            visible[0]=!visible[0];
            int pos=field.getSelectionStart();
            field.setInputType(InputType.TYPE_CLASS_TEXT | (visible[0]?InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD:InputType.TYPE_TEXT_VARIATION_PASSWORD));
            field.setTypeface(Typeface.create("sans",Typeface.NORMAL));
            if(pos<0) pos=field.length();
            field.setSelection(Math.min(pos,field.length()));
            toggle.setText(visible[0]?"隐藏":"显示");
        });
        return box;
    }

    private Button primaryButton(String label){Button b=new Button(this);b.setAllCaps(false);b.setText(label);b.setTextSize(14);b.setTextColor(Color.WHITE);b.setTypeface(Typeface.DEFAULT,Typeface.BOLD);b.setMinHeight(dp(54));b.setBackground(ripple(horizontalGradient(p.accent,p.purple,17),0x33FFFFFF));return flatButton(b);}
    private Button secondaryButton(String label){Button b=new Button(this);b.setAllCaps(false);b.setText(label);b.setTextSize(14);b.setTextColor(p.ink);b.setTypeface(Typeface.DEFAULT,Typeface.BOLD);b.setMinHeight(dp(54));b.setBackground(ripple(roundRect(p.surface,17,p.border,1),0x11000000));return flatButton(b);}
    private Button logoutButton(String label){Button b=new Button(this);b.setAllCaps(false);b.setText(label);b.setTextSize(14);b.setTextColor(p.danger);b.setTypeface(Typeface.DEFAULT,Typeface.BOLD);b.setMinHeight(dp(54));b.setBackground(ripple(roundRect(p.surface,17,p.danger,1),p.dark?0x22FF6B77:0x12D54848));return flatButton(b);}
    private Button flatButton(Button b){b.setElevation(0f);b.setStateListAnimator(null);b.setPadding(dp(14),0,dp(14),0);return b;}
    private boolean motionEnabled(){return ValueAnimator.areAnimatorsEnabled();}
    private void pressMotion(View v,float pressedScale){
        v.setOnTouchListener((view,event)->{
            if(!motionEnabled())return false;
            if(event.getAction()==MotionEvent.ACTION_DOWN)view.animate().scaleX(pressedScale).scaleY(pressedScale).alpha(.92f).setDuration(85).start();
            else if(event.getAction()==MotionEvent.ACTION_UP||event.getAction()==MotionEvent.ACTION_CANCEL)view.animate().scaleX(1f).scaleY(1f).alpha(1f).setDuration(150).start();
            return false;
        });
    }
    private void reveal(View v,long delay,float fromDp){if(!motionEnabled())return;v.setAlpha(0f);v.setTranslationY(dp(fromDp));v.post(()->v.animate().alpha(1f).translationY(0f).setStartDelay(delay).setDuration(300).setInterpolator(new android.view.animation.DecelerateInterpolator()).start());}
    private void beginScanAction(TextView action,boolean connectedCheck){
        Object previous=action.getTag();if(previous instanceof Animator)((Animator)previous).cancel();
        final String verb=connectedCheck?"检测中":"优选中";
        action.animate().cancel();action.setEnabled(false);action.setTextColor(p.accent);action.setText(verb+"…");
        if(!motionEnabled())return;
        final int[] lastDots={-1};
        ValueAnimator scan=ValueAnimator.ofFloat(0f,1f);scan.setDuration(760);scan.setRepeatCount(ValueAnimator.INFINITE);scan.setRepeatMode(ValueAnimator.REVERSE);scan.setInterpolator(new android.view.animation.LinearInterpolator());
        scan.addUpdateListener(a->{float phase=(float)a.getAnimatedValue();int dots=1+Math.min(2,(int)(phase*3f));if(dots!=lastDots[0]){lastDots[0]=dots;StringBuilder label=new StringBuilder(verb);for(int i=0;i<dots;i++)label.append('·');action.setText(label.toString());}float lift=(float)Math.sin(phase*Math.PI);float scale=1f+.018f*lift;action.setScaleX(scale);action.setScaleY(scale);action.setAlpha(.86f+.14f*lift);});
        action.setTag(scan);scan.start();
    }
    private void finishScanAction(TextView action,boolean success,boolean connectedCheck){
        Object running=action.getTag();if(running instanceof Animator)((Animator)running).cancel();action.setTag(null);action.animate().cancel();action.setEnabled(true);action.setText(connectedCheck?"重新检测":"重新优选");action.setTextColor(success?p.success:p.accent);action.setAlpha(1f);
        if(!motionEnabled()){action.setScaleX(1f);action.setScaleY(1f);return;}
        action.setScaleX(.91f);action.setScaleY(.91f);action.animate().scaleX(1f).scaleY(1f).setDuration(300).setInterpolator(new OvershootInterpolator(.72f)).start();
        action.postDelayed(()->{if(action.getTag()==null&&action.isAttachedToWindow())action.setTextColor(p.accent);},720);
    }
    private void popResult(View view){
        if(!motionEnabled())return;view.animate().cancel();view.setAlpha(.55f);view.setScaleX(.84f);view.setScaleY(.84f);view.animate().alpha(1f).scaleX(1f).scaleY(1f).setDuration(260).setInterpolator(new OvershootInterpolator(.75f)).start();
    }
    private void highlightWinner(View view){
        view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);if(!motionEnabled())return;view.animate().cancel();view.animate().scaleX(1.018f).scaleY(1.018f).translationX(dp(3)).setDuration(140).withEndAction(()->view.animate().scaleX(1f).scaleY(1f).translationX(0f).setDuration(260).setInterpolator(new OvershootInterpolator(.5f)).start()).start();
    }
    private android.graphics.drawable.Drawable appNoticeBg(){
        GradientDrawable fill=roundRect(p.surface,20,p.border,1);
        GradientDrawable edge=roundRect(Color.TRANSPARENT,20,p.dark?0x665D82FF:0x405D82FF,1);
        return new LayerDrawable(new android.graphics.drawable.Drawable[]{fill,edge});
    }
    private android.graphics.drawable.Drawable ambientTrackBg(float radius){
        GradientDrawable fill=roundRect(p.surfaceAlt,radius,p.dark?0xFF273148:0xFFE5EAF3,1);
        GradientDrawable edge=roundRect(Color.TRANSPARENT,radius,p.dark?0x5C5D82FF:0x345D82FF,1);
        LayerDrawable layers=new LayerDrawable(new android.graphics.drawable.Drawable[]{fill,edge});
        return layers;
    }
private android.graphics.drawable.Drawable floatingCardBg(float radius){
        return roundRect(p.surface,radius,p.dark?0xFF263149:0xFFE4E9F3,1);
    }
    private GradientDrawable horizontalGradient(int start,int end,float radius){GradientDrawable g=new GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT,new int[]{start,end});g.setCornerRadius(dp(radius));return g;}
    private GradientDrawable roundRect(int color,float radius,int strokeColor,float strokeDp){GradientDrawable g=new GradientDrawable();g.setColor(color);g.setCornerRadius(dp(radius));if(strokeDp>0)g.setStroke(dp(strokeDp),strokeColor);return g;}
    private android.graphics.drawable.Drawable cardBg(float radius){GradientDrawable g=roundRect(p.surface,radius,p.border,1);return ripple(g,p.dark?0x16FFFFFF:0x09000000);}
    private RippleDrawable ripple(android.graphics.drawable.Drawable content,int color){return new RippleDrawable(android.content.res.ColorStateList.valueOf(color),content,null);}
    private View divider(){View v=new View(this);v.setBackgroundColor(p.border);v.setLayoutParams(new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,dp(1)));return v;}
    private void gap(LinearLayout p,int dp){Space s=new Space(this);p.addView(s,new LinearLayout.LayoutParams(1,dp(dp)));}
    private void gapH(LinearLayout p,int dp){Space s=new Space(this);p.addView(s,new LinearLayout.LayoutParams(dp(dp),1));}
    private void toast(String message){
        if(currentRoot==null){ return; }
        if(activeNotice!=null){
            try{ ((ViewGroup)activeNotice.getParent()).removeView(activeNotice); }catch(Exception ignored){}
            activeNotice=null;
        }
        LinearLayout notice=row(); notice.setGravity(Gravity.CENTER_VERTICAL); notice.setPadding(dp(12),dp(9),dp(14),dp(9)); notice.setBackground(appNoticeBg());
        BrandMarkView icon=brandMark(); notice.addView(icon,new LinearLayout.LayoutParams(dp(30),dp(30)));
        TextView label=text(message,12,p.ink,true); LinearLayout.LayoutParams tlp=new LinearLayout.LayoutParams(0,ViewGroup.LayoutParams.WRAP_CONTENT,1f); tlp.leftMargin=dp(9); notice.addView(label,tlp);
        FrameLayout.LayoutParams lp=new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,ViewGroup.LayoutParams.WRAP_CONTENT,Gravity.BOTTOM); lp.leftMargin=dp(44); lp.rightMargin=dp(44); lp.bottomMargin=dp(token!=null&&!token.isEmpty()?104:34); currentRoot.addView(notice,lp); activeNotice=notice;
        notice.setAlpha(0f); notice.setTranslationY(dp(14)); notice.setScaleX(.97f); notice.setScaleY(.97f);
        notice.animate().alpha(1f).translationY(0f).scaleX(1f).scaleY(1f).setDuration(180).setInterpolator(new android.view.animation.DecelerateInterpolator()).start();
        notice.postDelayed(()->{
            if(activeNotice!=notice)return;
            notice.animate().alpha(0f).translationY(dp(10)).setDuration(180).withEndAction(()->{
                try{ if(notice.getParent()!=null)((ViewGroup)notice.getParent()).removeView(notice); }catch(Exception ignored){}
                if(activeNotice==notice)activeNotice=null;
            }).start();
        },2100);
    }
    @SuppressWarnings("deprecation")
    private int statusBarInset(){
        android.view.WindowInsets insets=getWindow().getDecorView().getRootWindowInsets();
        if(insets==null)return dp(24);
        if(Build.VERSION.SDK_INT>=Build.VERSION_CODES.R)return insets.getInsets(android.view.WindowInsets.Type.statusBars()).top;
        return insets.getStableInsetTop();
    }
    private void animateIn(View v,float fromDp){v.setAlpha(0f);v.setTranslationY(dp(fromDp));v.animate().alpha(1f).translationY(0f).setDuration(320).start();}
    private int dp(float v){return Math.round(v*getResources().getDisplayMetrics().density);}
    private LinearLayout.LayoutParams matchWrap(){return new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,ViewGroup.LayoutParams.WRAP_CONTENT);}
    private LinearLayout.LayoutParams wrapWrap(){return new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,ViewGroup.LayoutParams.WRAP_CONTENT);}
    private FrameLayout.LayoutParams matchMatch(){return new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,ViewGroup.LayoutParams.MATCH_PARENT);}
    private void setBusy(Button b,ProgressBar busy,boolean value,String busyText,String normal){b.setEnabled(!value);b.setText(value?busyText:normal);b.setAlpha(value?.78f:1f);busy.setVisibility(value?View.VISIBLE:View.GONE);}

    private Dialog bottomDialog(){Dialog d=new PremiumBottomDialog();d.requestWindowFeature(Window.FEATURE_NO_TITLE);d.setContentView(new FrameLayout(this));Window w=d.getWindow();if(w!=null){w.setBackgroundDrawable(new ColorDrawable(Color.TRANSPARENT));w.setLayout(WindowManager.LayoutParams.MATCH_PARENT,WindowManager.LayoutParams.WRAP_CONTENT);w.setGravity(Gravity.BOTTOM);w.setDimAmount(0f);w.addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND);}return d;}
    private LinearLayout sheet(){
        LinearLayout s=column();s.setPadding(dp(22),dp(11),dp(22),dp(30));
        GradientDrawable fill=new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,p.dark?new int[]{0xFF171E2A,0xFF11161F}:new int[]{0xFFFFFFFF,0xFFFAFBFF});fill.setCornerRadius(dp(28));
        GradientDrawable rim=roundRect(Color.TRANSPARENT,28,p.dark?0x705D82FF:0x385D82FF,1);s.setBackground(new LayerDrawable(new android.graphics.drawable.Drawable[]{fill,rim}));
        FrameLayout handleSlot=new FrameLayout(this);View handle=new View(this);handle.setBackground(roundRect(p.dark?0x806F7E9B:0x405D82FF,3,0,0));FrameLayout.LayoutParams hp=new FrameLayout.LayoutParams(dp(42),dp(4),Gravity.CENTER);handleSlot.addView(handle,hp);s.addView(handleSlot,new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,dp(9)));gap(s,11);
        return s;
    }
    private void showBottomDialog(Dialog d,LinearLayout sheet){
        ScrollView scroll=polishedScrollView(false); scroll.addView(sheet,matchWrap()); d.setContentView(scroll);
        if(d instanceof PremiumBottomDialog)((PremiumBottomDialog)d).setMotionTarget(sheet);
        d.setOnShowListener(x->{
            Window w=d.getWindow();
            if(w!=null){
                int maxH=(int)(getResources().getDisplayMetrics().heightPixels*.84f);
                int width=getResources().getDisplayMetrics().widthPixels;
                sheet.measure(View.MeasureSpec.makeMeasureSpec(width,View.MeasureSpec.EXACTLY),View.MeasureSpec.makeMeasureSpec(maxH,View.MeasureSpec.AT_MOST));
                int desired=Math.min(sheet.getMeasuredHeight(),maxH);
                w.setLayout(WindowManager.LayoutParams.MATCH_PARENT,desired); w.setGravity(Gravity.BOTTOM);
            }
            sheet.setElevation(dp(p.dark?22:14));
            if(!motionEnabled()){sheet.setAlpha(1f);sheet.setTranslationY(0f);sheet.setScaleX(1f);sheet.setScaleY(1f);if(w!=null)w.setDimAmount(p.dark?.38f:.25f);return;}
            sheet.setPivotX(sheet.getMeasuredWidth()/2f);sheet.setPivotY(sheet.getMeasuredHeight());sheet.setAlpha(.24f);sheet.setTranslationY(dp(52));sheet.setScaleX(.968f);sheet.setScaleY(.968f);
            sheet.animate().alpha(1f).translationY(0f).scaleX(1f).scaleY(1f).setDuration(360).setInterpolator(new PathInterpolator(.16f,1f,.30f,1f)).start();
            if(w!=null){ValueAnimator dim=ValueAnimator.ofFloat(0f,p.dark?.38f:.25f);dim.setDuration(260);dim.setInterpolator(new android.view.animation.DecelerateInterpolator());dim.addUpdateListener(a->{if(d.isShowing()&&d.getWindow()!=null)d.getWindow().setDimAmount((float)a.getAnimatedValue());});dim.start();}
        });
        d.show();
    }

    private final class PremiumBottomDialog extends Dialog {
        private View motionTarget;private boolean exiting;private ValueAnimator dimExit;
        PremiumBottomDialog(){super(MainActivity.this);}
        void setMotionTarget(View target){motionTarget=target;}
        @Override public void dismiss(){
            if(exiting||!isShowing()||motionTarget==null||!motionEnabled()||MainActivity.this.isFinishing()){finishDismiss();return;}
            exiting=true;motionTarget.animate().cancel();motionTarget.animate().alpha(0f).translationY(dp(24)).scaleX(.985f).scaleY(.985f).setDuration(170).setInterpolator(new android.view.animation.AccelerateInterpolator()).withEndAction(this::finishDismiss).start();
            Window window=getWindow();if(window!=null){float start=window.getAttributes().dimAmount;dimExit=ValueAnimator.ofFloat(start,0f);dimExit.setDuration(170);dimExit.addUpdateListener(a->{if(isShowing()&&getWindow()!=null)getWindow().setDimAmount((float)a.getAnimatedValue());});dimExit.start();}
        }
        private void finishDismiss(){if(dimExit!=null){dimExit.cancel();dimExit=null;}try{super.dismiss();}finally{exiting=false;}}
    }

    private static final class Palette {
        final boolean dark; final int bg,surface,surfaceAlt,field,ink,muted,subtle,border,accent,purple,accentSoft,success,successSoft,warningBg,warningText,danger;
        Palette(boolean dark){this.dark=dark;if(dark){bg=0xFF0B0E14;surface=0xFF131821;surfaceAlt=0xFF1B2230;field=0xFF171D28;ink=0xFFF4F6FB;muted=0xFFA7B1C6;subtle=0xFF77839A;border=0xFF273146;accent=0xFF6487FF;purple=0xFF9B79F8;accentSoft=0xFF222D50;success=0xFF5FD6B0;successSoft=0xFF173A34;warningBg=0xFF372B24;warningText=0xFFFFC38C;danger=0xFFFF7B86;}else{bg=0xFFF7F8FC;surface=0xFFFDFEFF;surfaceAlt=0xFFF0F3F9;field=0xFFF7F9FC;ink=0xFF121A2A;muted=0xFF71809A;subtle=0xFFA1ABC0;border=0xFFE6EAF2;accent=0xFF557EFF;purple=0xFF9A7CF8;accentSoft=0xFFEDF1FF;success=0xFF22B78B;successSoft=0xFFE5F8F2;warningBg=0xFFFFF4E8;warningText=0xFF9B5B18;danger=0xFFD54848;}}
    }
}
