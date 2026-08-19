package com.xvpn.android;

import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class SecureTokenStore {
    private static final String ANDROID_KEYSTORE = "AndroidKeyStore";
    private static final String KEY_ALIAS = "xvpn_mobile_token_v1";
    private static final String PREF_TOKEN = "token_enc";
    private static final String PREF_IV = "token_iv";
    private static final String LEGACY_TOKEN = "token";

    private SecureTokenStore() {}

    static String load(SharedPreferences prefs) {
        try {
            String encrypted = prefs.getString(PREF_TOKEN, "");
            String iv = prefs.getString(PREF_IV, "");
            if (!encrypted.isEmpty() && !iv.isEmpty()) {
                SecretKey key = getOrCreateKey();
                Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
                cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(128, Base64.decode(iv, Base64.NO_WRAP)));
                return new String(cipher.doFinal(Base64.decode(encrypted, Base64.NO_WRAP)), StandardCharsets.UTF_8);
            }
            String legacy = prefs.getString(LEGACY_TOKEN, "");
            if (!legacy.isEmpty()) {
                save(prefs, legacy);
                prefs.edit().remove(LEGACY_TOKEN).apply();
                return legacy;
            }
        } catch (Exception ignored) {
            clear(prefs);
        }
        return "";
    }

    static void save(SharedPreferences prefs, String token) throws Exception {
        if (token == null || token.isEmpty()) {
            clear(prefs);
            return;
        }
        SecretKey key = getOrCreateKey();
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key);
        byte[] encrypted = cipher.doFinal(token.getBytes(StandardCharsets.UTF_8));
        prefs.edit()
                .putString(PREF_TOKEN, Base64.encodeToString(encrypted, Base64.NO_WRAP))
                .putString(PREF_IV, Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP))
                .remove(LEGACY_TOKEN)
                .apply();
    }

    static void clear(SharedPreferences prefs) {
        prefs.edit().remove(PREF_TOKEN).remove(PREF_IV).remove(LEGACY_TOKEN).apply();
    }

    private static SecretKey getOrCreateKey() throws Exception {
        KeyStore keyStore = KeyStore.getInstance(ANDROID_KEYSTORE);
        keyStore.load(null);
        if (keyStore.containsAlias(KEY_ALIAS)) {
            return (SecretKey) keyStore.getKey(KEY_ALIAS, null);
        }
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE);
        generator.init(new KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build());
        return generator.generateKey();
    }
}
