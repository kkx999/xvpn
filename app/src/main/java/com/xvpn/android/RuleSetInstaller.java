package com.xvpn.android;

import android.content.Context;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.security.MessageDigest;

/** Installs the bundled, version-pinned sing-box rule sets into the core base directory. */
final class RuleSetInstaller {
    private static final String GEOSITE_ASSET = "rules/geosite-geolocation-cn.srs";
    private static final String GEOIP_ASSET = "rules/geoip-cn.srs";
    private static final String GEOSITE_SHA256 = "9cccc08ff669d707d7662ed78cc0a3b2626c4f16a6d151a9167dadb44d3da7b8";
    private static final String GEOIP_SHA256 = "0acf5dad38fba9db2dade29ce5e4edc6902220944f30628ae46ed16cb0ec5edd";

    private RuleSetInstaller() {}

    static Paths ensureInstalled(Context context) throws Exception {
        File directory = new File(context.getFilesDir(), "route-rules-v1");
        if (!directory.isDirectory() && !directory.mkdirs()) {
            throw new IllegalStateException("无法创建分流规则目录");
        }
        File geosite = install(context, GEOSITE_ASSET,
                new File(directory, "geosite-geolocation-cn.srs"), GEOSITE_SHA256);
        File geoip = install(context, GEOIP_ASSET,
                new File(directory, "geoip-cn.srs"), GEOIP_SHA256);
        return new Paths(geosite.getAbsolutePath(), geoip.getAbsolutePath());
    }

    private static File install(Context context, String assetName, File target, String expectedSha) throws Exception {
        if (target.isFile() && expectedSha.equals(sha256(target))) return target;

        File staging = new File(target.getParentFile(), target.getName() + ".pending");
        try (InputStream input = context.getAssets().open(assetName);
             FileOutputStream output = new FileOutputStream(staging, false)) {
            byte[] buffer = new byte[16 * 1024];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                if (read > 0) output.write(buffer, 0, read);
            }
            output.getFD().sync();
        }
        if (!expectedSha.equals(sha256(staging))) {
            staging.delete();
            throw new IllegalStateException("内置分流规则校验失败");
        }
        if (target.exists() && !target.delete()) {
            staging.delete();
            throw new IllegalStateException("无法更新分流规则");
        }
        if (!staging.renameTo(target)) {
            staging.delete();
            throw new IllegalStateException("无法安装分流规则");
        }
        return target;
    }

    private static String sha256(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (FileInputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[16 * 1024];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                if (read > 0) digest.update(buffer, 0, read);
            }
        }
        StringBuilder out = new StringBuilder(64);
        for (byte value : digest.digest()) out.append(String.format("%02x", value & 0xff));
        return out.toString();
    }

    static final class Paths {
        final String geositeCn;
        final String geoipCn;

        Paths(String geositeCn, String geoipCn) {
            this.geositeCn = geositeCn;
            this.geoipCn = geoipCn;
        }
    }
}
