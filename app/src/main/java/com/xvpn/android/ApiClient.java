package com.xvpn.android;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

final class ApiClient {
    static final String DEFAULT_PANEL_BASE = "https://xvpn.666101.xyz";
    static final String LEGACY_PANEL_BASE = "https://xx.666101.xyz";
    // 1.0.0/1.0.1 pre-release builds used this value as the built-in endpoint.
    // Treat it as an old built-in setting, never as a user supplied Panel URL.
    static final String PREVIOUS_DEFAULT_PANEL_BASE = "https://xvpn666101.xyz";

    private ApiClient() {}

    static JSONObject request(String panelBaseUrl, String path, String method, String token, JSONObject body) throws ApiException {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(apiBase(panelBaseUrl) + path);
            conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(12000);
            conn.setReadTimeout(25000);
            conn.setRequestMethod(method);
            applyCommonHeaders(conn);
            conn.setUseCaches(false);
            if (token != null && !token.isEmpty()) {
                conn.setRequestProperty("Authorization", "Bearer " + token);
            }
            if (body != null) {
                byte[] raw = body.toString().getBytes(StandardCharsets.UTF_8);
                conn.setDoOutput(true);
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                conn.setFixedLengthStreamingMode(raw.length);
                try (OutputStream os = conn.getOutputStream()) {
                    os.write(raw);
                }
            }

            int status = conn.getResponseCode();
            InputStream stream = status >= 200 && status < 300 ? conn.getInputStream() : conn.getErrorStream();
            String text = readAll(stream);
            JSONObject json;
            try {
                json = text.isEmpty() ? new JSONObject() : new JSONObject(text);
            } catch (Exception parse) {
                json = new JSONObject();
                json.put("ok", false);
                json.put("message", text.isEmpty() ? ("HTTP " + status) : text);
            }
            if (status < 200 || status >= 300) {
                throw new ApiException(
                        status,
                        json.optString("code", "HTTP_" + status),
                        json.optString("message", "请求失败 (HTTP " + status + ")"),
                        json.optInt("retry_after", 0)
                );
            }
            return json;
        } catch (ApiException e) {
            throw e;
        } catch (Exception e) {
            throw new ApiException(0, "NETWORK_ERROR", friendlyNetworkError(e), 0);
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private static void applyCommonHeaders(HttpURLConnection conn) {
        // Cloudflare Browser Integrity Check may reject non-standard API client User-Agent values.
        // Use a normal Android mobile browser signature for transport compatibility, while keeping
        // XVPN identity in a separate application header for server-side diagnostics.
        conn.setRequestProperty("User-Agent", "Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36");
        conn.setRequestProperty("Accept", "application/json, text/plain, */*");
        conn.setRequestProperty("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8");
        conn.setRequestProperty("Cache-Control", "no-cache");
        conn.setRequestProperty("Pragma", "no-cache");
        conn.setRequestProperty("X-XVPN-Client", "android/" + BuildConfig.VERSION_NAME);
    }

    static String normalizePanelBase(String value) {
        String base = value == null ? "" : value.trim();
        while (base.endsWith("/")) base = base.substring(0, base.length() - 1);
        if (base.endsWith("/api/v1")) base = base.substring(0, base.length() - "/api/v1".length());
        while (base.endsWith("/")) base = base.substring(0, base.length() - 1);
        return base;
    }

    /** Migrates only XVPN's retired built-in endpoint; user-defined Panels are never rewritten. */
    static String migratePanelBase(String value) {
        String normalized = normalizePanelBase(value);
        if (normalized.isEmpty() || LEGACY_PANEL_BASE.equalsIgnoreCase(normalized)
                || PREVIOUS_DEFAULT_PANEL_BASE.equalsIgnoreCase(normalized)) {
            return DEFAULT_PANEL_BASE;
        }
        return normalized;
    }

    static String apiBase(String panelBaseUrl) {
        return normalizePanelBase(panelBaseUrl) + "/api/v1";
    }

    static boolean isValidPanelBase(String value) {
        try {
            URL url = new URL(normalizePanelBase(value));
            String scheme = url.getProtocol();
            return ("https".equalsIgnoreCase(scheme) || "http".equalsIgnoreCase(scheme))
                    && url.getHost() != null && !url.getHost().isEmpty()
                    && (url.getPath() == null || url.getPath().isEmpty() || "/".equals(url.getPath()))
                    && url.getQuery() == null && url.getRef() == null;
        } catch (Exception ignored) {
            return false;
        }
    }

    static boolean isHttps(String value) {
        return normalizePanelBase(value).toLowerCase(java.util.Locale.ROOT).startsWith("https://");
    }

    private static String readAll(InputStream in) throws Exception {
        if (in == null) return "";
        StringBuilder sb = new StringBuilder();
        try (BufferedReader br = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            String line;
            while ((line = br.readLine()) != null) sb.append(line);
        }
        return sb.toString();
    }

    private static String friendlyNetworkError(Exception e) {
        String name = e.getClass().getSimpleName();
        if (name.contains("UnknownHost")) return "无法解析服务器地址，请检查域名或网络";
        if (name.contains("Connect")) return "无法连接服务器，请检查地址和网络";
        if (name.contains("SocketTimeout")) return "连接服务器超时，请稍后重试";
        if (name.contains("SSL")) return "HTTPS 安全连接失败，请检查证书";
        return "网络请求失败，请检查网络后重试";
    }

    static final class ApiException extends Exception {
        final int status;
        final String code;
        final int retryAfter;

        ApiException(int status, String code, String message, int retryAfter) {
            super(message);
            this.status = status;
            this.code = code;
            this.retryAfter = retryAfter;
        }

        boolean isAuthFailure() {
            return "UNAUTHORIZED".equals(code) || "TOKEN_EXPIRED".equals(code) || "ACCOUNT_DISABLED".equals(code)
                    || "HTTP_401".equals(code) || "HTTP_403".equals(code);
        }
    }
}
