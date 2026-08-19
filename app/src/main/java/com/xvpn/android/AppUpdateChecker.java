package com.xvpn.android;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

final class AppUpdateChecker {
    private static final String LATEST_RELEASE_API = "https://api.github.com/repos/kkx999/XVPN-Android/releases/latest";

    private AppUpdateChecker() {}

    static Result check() throws Exception {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(LATEST_RELEASE_API).openConnection();
            conn.setConnectTimeout(9000);
            conn.setReadTimeout(12000);
            conn.setRequestMethod("GET");
            conn.setUseCaches(false);
            conn.setRequestProperty("Accept", "application/vnd.github+json");
            conn.setRequestProperty("User-Agent", "XVPN-Android/" + BuildConfig.VERSION_NAME);
            conn.setRequestProperty("X-GitHub-Api-Version", "2022-11-28");

            int status = conn.getResponseCode();
            InputStream stream = status >= 200 && status < 300 ? conn.getInputStream() : conn.getErrorStream();
            String text = readAll(stream);
            if (status == 404) return Result.noRelease();
            if (status < 200 || status >= 300) throw new Exception("GitHub Release HTTP " + status);

            JSONObject json = new JSONObject(text);
            String tag = json.optString("tag_name", "").trim();
            String version = normalizeVersion(tag);
            String notes = json.optString("body", "").trim();
            String page = json.optString("html_url", "").trim();
            boolean newer = !version.isEmpty() && compareVersions(version, BuildConfig.VERSION_NAME) > 0;
            return new Result(true, newer, version, notes, page);
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private static String readAll(InputStream in) throws Exception {
        if (in == null) return "";
        StringBuilder sb = new StringBuilder();
        try (BufferedReader br = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            String line;
            while ((line = br.readLine()) != null) sb.append(line).append('\n');
        }
        return sb.toString();
    }

    static String normalizeVersion(String value) {
        String v = value == null ? "" : value.trim();
        if (v.startsWith("v") || v.startsWith("V")) v = v.substring(1);
        return v;
    }

    static int compareVersions(String a, String b) {
        String aversion = normalizeVersion(a);
        String bversion = normalizeVersion(b);
        String[] aa = numericBase(aversion).split("\\.");
        String[] bb = numericBase(bversion).split("\\.");
        int max = Math.max(aa.length, bb.length);
        for (int i = 0; i < max; i++) {
            int av = i < aa.length ? safeInt(aa[i]) : 0;
            int bv = i < bb.length ? safeInt(bb[i]) : 0;
            if (av != bv) return Integer.compare(av, bv);
        }
        // A stable release is considered newer than a dev build with the same numeric base.
        boolean aDev = aversion.contains("-");
        boolean bDev = bversion.contains("-");
        if (aDev != bDev) return aDev ? -1 : 1;
        if (!aDev) return 0;

        List<String> aParts = prereleaseParts(aversion.substring(aversion.indexOf('-') + 1));
        List<String> bParts = prereleaseParts(bversion.substring(bversion.indexOf('-') + 1));
        int count = Math.max(aParts.size(), bParts.size());
        for (int i = 0; i < count; i++) {
            if (i >= aParts.size()) return -1;
            if (i >= bParts.size()) return 1;
            String ap = aParts.get(i), bp = bParts.get(i);
            boolean an = ap.matches("\\d+"), bn = bp.matches("\\d+");
            int compared;
            if (an && bn) compared = Long.compare(safeLong(ap), safeLong(bp));
            else if (an != bn) compared = an ? -1 : 1;
            else compared = ap.compareToIgnoreCase(bp);
            if (compared != 0) return compared;
        }
        return 0;
    }

    private static String numericBase(String v) {
        int dash = v.indexOf('-');
        return dash >= 0 ? v.substring(0, dash) : v;
    }

    private static int safeInt(String s) {
        try { return Integer.parseInt(s.replaceAll("[^0-9]", "")); }
        catch (Exception ignored) { return 0; }
    }

    private static long safeLong(String value) {
        try { return Long.parseLong(value); }
        catch (Exception ignored) { return 0L; }
    }

    private static List<String> prereleaseParts(String value) {
        List<String> out = new ArrayList<>();
        java.util.regex.Matcher matcher = java.util.regex.Pattern.compile("[A-Za-z]+|\\d+").matcher(value);
        while (matcher.find()) out.add(matcher.group());
        if (out.isEmpty()) out.add(value);
        return out;
    }

    static final class Result {
        final boolean hasRelease;
        final boolean updateAvailable;
        final String version;
        final String notes;
        final String pageUrl;
        final boolean forceUpdate;
        final String statusMessage;

        Result(boolean hasRelease, boolean updateAvailable, String version, String notes, String pageUrl) {
            this(hasRelease, updateAvailable, version, notes, pageUrl, false, "");
        }

        Result(boolean hasRelease, boolean updateAvailable, String version, String notes, String pageUrl,
               boolean forceUpdate, String statusMessage) {
            this.hasRelease = hasRelease;
            this.updateAvailable = updateAvailable;
            this.version = version;
            this.notes = notes;
            this.pageUrl = pageUrl;
            this.forceUpdate = forceUpdate;
            this.statusMessage = statusMessage == null ? "" : statusMessage;
        }

        static Result noRelease() { return new Result(false, false, "", "", "", false, ""); }
    }
}
