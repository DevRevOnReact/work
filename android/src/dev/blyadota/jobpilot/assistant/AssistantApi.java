package dev.blyadota.jobpilot.assistant;

import android.content.Context;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import javax.net.ssl.HttpsURLConnection;
import org.json.JSONObject;

final class AssistantApi {
    static final String ORIGIN = "https://jobs.blyadota.dev";
    static final int MEDIA_LIMIT = 4 * 1024 * 1024;
    static class Failure extends IOException {
        final int status;
        Failure(int status) { super("HTTP " + status); this.status = status; }
    }
    private final Context context;
    AssistantApi(Context context) { this.context = context.getApplicationContext(); }
    private byte[] call(String path, byte[] payload, String type, int limit) throws Exception {
        HttpsURLConnection c = (HttpsURLConnection) new URL(ORIGIN + "/api/phone" + path).openConnection();
        try {
            c.setInstanceFollowRedirects(false);
            c.setUseCaches(false);
            c.setConnectTimeout(10000);
            c.setReadTimeout(path.startsWith("/media/") ? 130000 : 35000);
            c.setRequestProperty("Authorization", "Bearer " + DeviceKey.secret(context));
            if (payload != null) {
                c.setRequestMethod("POST");
                c.setDoOutput(true);
                c.setFixedLengthStreamingMode(payload.length);
                c.setRequestProperty("Content-Type", type);
                try (OutputStream out = c.getOutputStream()) { out.write(payload); }
            }
            int status = c.getResponseCode();
            if (status < 200 || status >= 300) throw new Failure(status);
            try (InputStream input = c.getInputStream()) { return bounded(input, limit); }
        } finally { c.disconnect(); }
    }
    static byte[] bounded(InputStream in, int limit) throws IOException {
        if (in == null) throw new IOException("No input");
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[16384];
        int n;
        while ((n = in.read(buffer)) != -1) {
            if (out.size() + n > limit) throw new IOException("File too large");
            out.write(buffer,0,n);
        }
        return out.toByteArray();
    }
    private JSONObject json(String path, byte[] body, String type) throws Exception {
        return new JSONObject(new String(call(path,body,type,2*1024*1024), StandardCharsets.UTF_8));
    }
    static String recommended(JSONObject variants) {
        if (variants == null) return "";
        for (String key : new String[]{"professional", "brief", "warm"}) {
            String answer = variants.optString(key, "").trim();
            if (!answer.isEmpty()) return answer;
        }
        return "";
    }
    JSONObject overview() throws Exception { return json("",null,null); }
    JSONObject create(JSONObject body) throws Exception {
        return json("",body.toString().getBytes(StandardCharsets.UTF_8),"application/json");
    }
    String recognize(byte[] data, boolean audio) throws Exception {
        return json(audio ? "/media/audio" : "/media/image",data,"application/octet-stream").getString("text");
    }
    String status(String id) throws Exception {return json("/"+UUID.fromString(id)+"/status", null,null).getString("status");}
    JSONObject answers(String id) throws Exception {return json("/"+UUID.fromString(id)+"/answers",null,null);}
    byte[] file(String id) throws Exception {return call("/"+UUID.fromString(id)+"/file",null,null,11*1024*1024);}
}
