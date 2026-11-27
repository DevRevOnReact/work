package dev.blyadota.jobpilot.assistant;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Base64;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.UUID;

final class DraftAttempt {
    private DraftAttempt() {}

    // Keep only the identifier and a digest. A retry after an ambiguous network
    // failure or an Activity restart addresses the same server-side draft.
    static synchronized String begin(Context context, String payload) throws NoSuchAlgorithmException, IOException {
        SharedPreferences prefs = context.getSharedPreferences("draft-attempt", Context.MODE_PRIVATE);
        String digest = Base64.encodeToString(MessageDigest.getInstance("SHA-256")
                .digest(payload.getBytes(StandardCharsets.UTF_8)), Base64.NO_WRAP);
        String id = prefs.getString("id", null);
        if (id != null && digest.equals(prefs.getString("digest", null))) return id;
        id = UUID.randomUUID().toString();
        if (!prefs.edit().putString("id", id).putString("digest", digest).commit())
            throw new IOException("Cannot record draft attempt");
        return id;
    }

    static synchronized void acknowledge(Context context, String id) {
        SharedPreferences prefs = context.getSharedPreferences("draft-attempt", Context.MODE_PRIVATE);
        if (id.equals(prefs.getString("id", null))) prefs.edit().clear().apply();
    }
}
