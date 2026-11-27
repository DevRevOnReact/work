package dev.blyadota.jobpilot.assistant;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Locale;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class DeviceKey {
    private static final String ALIAS = "jobpilot.phone.v1";
    private static final int B64 = Base64.NO_WRAP | Base64.NO_PADDING | Base64.URL_SAFE;
    private DeviceKey() {}
    static synchronized String secret(Context context) throws Exception {
        SharedPreferences prefs = context.getSharedPreferences("identity", Context.MODE_PRIVATE);
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (!store.containsAlias(ALIAS)) {
            KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
            generator.generateKey();
        }
        SecretKey key = (SecretKey) store.getKey(ALIAS, null);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        String sealed = prefs.getString("sealed", null);
        if (sealed != null) {
            String[] fields = sealed.split("\\.", 2);
            cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(128, Base64.decode(fields[0], B64)));
            return new String(cipher.doFinal(Base64.decode(fields[1], B64)), StandardCharsets.US_ASCII);
        }
        byte[] random = new byte[32];
        new SecureRandom().nextBytes(random);
        String secret = "jp1_" + Base64.encodeToString(random, B64);
        cipher.init(Cipher.ENCRYPT_MODE, key);
        String value = Base64.encodeToString(cipher.getIV(), B64) + "." + Base64.encodeToString(
                cipher.doFinal(secret.getBytes(StandardCharsets.US_ASCII)), B64);
        if (!prefs.edit().putString("sealed", value).commit()) throw new java.io.IOException("Device storage unavailable");
        return secret;
    }
    static String fingerprint(Context context) throws Exception {
        byte[] bytes = MessageDigest.getInstance("SHA-256").digest(secret(context).getBytes(StandardCharsets.US_ASCII));
        StringBuilder text = new StringBuilder();
        for (byte b : bytes) text.append(String.format(Locale.ROOT,"%02x", b & 255));
        return text.toString();
    }
}
