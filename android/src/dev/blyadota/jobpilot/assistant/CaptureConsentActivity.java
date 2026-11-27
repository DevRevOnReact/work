package dev.blyadota.jobpilot.assistant;

import android.app.Activity;
import android.content.*;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.net.Uri;
import android.os.IBinder;
import java.io.InputStream;
import android.media.projection.MediaProjectionManager;
import android.os.Bundle;
import android.os.ResultReceiver;

/** Transparent consent trampoline in its own task so finishing reveals the original conversation. */
public final class CaptureConsentActivity extends Activity implements ServiceConnection {
    static final String REQUEST = "request", CANCEL = "cancel";
    static final String GRANT = "projection-grant";
    static final String PICK = "pick-image";
    private boolean bound;
    private Uri selected;
    private final BroadcastReceiver sleep = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            cancel();
            finishAndRemoveTask();
        }
    };
    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        registerReceiver(sleep,new IntentFilter(Intent.ACTION_SCREEN_OFF));
        if (state != null) return;
        try {
            if (getIntent().getBooleanExtra(PICK,false)) {
                Intent pick = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
                    .setType("image/*").putExtra(Intent.EXTRA_MIME_TYPES,new String[]{"image/png","image/jpeg","image/webp"});
                startActivityForResult(pick,2);
                return;
            }
            startActivityForResult(getSystemService(MediaProjectionManager.class).createScreenCaptureIntent(), 1);
        } catch (RuntimeException error) {
            cancel(); finishAndRemoveTask();
        }
    }
    private void cancel() {
        ResultReceiver receiver = getIntent().getParcelableExtra(CANCEL);
        if (receiver != null) receiver.send(RESULT_CANCELED, null);
    }
    @Override protected void onActivityResult(int code, int result, Intent grant) {
        super.onActivityResult(code, result, grant);
        if (isFinishing() || isDestroyed()) return;
        if (code == 2) {
            selected = result == RESULT_OK && grant != null ? grant.getData() : null;
            if (selected != null && "content".equals(selected.getScheme()))
                try { bound = bindService(new Intent(this,EdgeService.class),this,0); }
                catch (RuntimeException ignored) { bound = false; }
            if (!bound) { cancel(); finishAndRemoveTask(); }
            return;
        }
        if (code != 1) return;
        if (result == RESULT_OK && grant != null) {
            Intent capture = new Intent(this, ScreenCaptureService.class)
                .putExtra(REQUEST, getIntent().getStringExtra(REQUEST)).putExtra(GRANT, grant);
            try { startForegroundService(capture); }
            catch (RuntimeException error) { cancel(); }
        } else cancel();
        finishAndRemoveTask();
        overridePendingTransition(0, 0);
    }
    @Override public void onServiceConnected(ComponentName name, IBinder binder) {
        EdgeService.FrameReceiver target = (EdgeService.FrameReceiver) binder;
        final String id = getIntent().getStringExtra(REQUEST);
        if (!target.accepts(id)) { finishAndRemoveTask(); return; }
        final Uri uri = selected;
        new Thread(() -> {
            Bitmap frame = null;
            try (InputStream in = getContentResolver().openInputStream(uri)) {
                byte[] bytes = AssistantApi.bounded(in, 12 * 1024 * 1024);
                BitmapFactory.Options options = new BitmapFactory.Options();
                options.inJustDecodeBounds = true;
                BitmapFactory.decodeByteArray(bytes,0,bytes.length,options);
                int w = options.outWidth, h = options.outHeight;
                if (w > 0 && h > 0 && (long)w*h <= 64000000L) {
                    options.inJustDecodeBounds = false;
                    options.inSampleSize = 1;
                    while (Math.max(w,h) / options.inSampleSize > 2400) options.inSampleSize *= 2;
                    frame = BitmapFactory.decodeByteArray(bytes,0,bytes.length,options);
                }
            } catch (java.io.IOException | RuntimeException ignored) { /* user-facing error below */ }
            final Bitmap result = frame;
            runOnUiThread(() -> {
                if (!isDestroyed() && !isFinishing()) {
                    target.deliver(id,result,"Не удалось открыть изображение. Выберите скриншот до 12 МБ.");
                    finishAndRemoveTask();
                }
            });
        },"screenshot-import").start();
    }
    @Override public void onServiceDisconnected(ComponentName name) { cancel(); finishAndRemoveTask(); }
    @Override protected void onDestroy() {
        unregisterReceiver(sleep);
        if (bound) unbindService(this);
        super.onDestroy();
    }
}
