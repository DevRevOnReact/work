package dev.blyadota.jobpilot.assistant;

import android.app.*;
import android.content.*;
import android.content.pm.ServiceInfo;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.graphics.Point;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.view.Display;
import java.nio.ByteBuffer;

/** One system grant, one virtual display, one frame. The bitmap stays in this process. */
public final class ScreenCaptureService extends Service implements ServiceConnection {
    private final Handler main = new Handler(Looper.getMainLooper());
    private String request;
    private Intent grant;
    private EdgeService.FrameReceiver receiver;
    private MediaProjection projection;
    private VirtualDisplay display;
    private ImageReader reader;
    private boolean bound, finished;
    private static final String EMPTY = "Браузер показал пустой кадр. Откройте обычную вкладку или выберите готовый снимок.";
    private final MediaProjection.Callback stopped = new MediaProjection.Callback() {
        @Override public void onStop() { finish(null, "Android остановил захват экрана."); }
    };

    @Override public IBinder onBind(Intent intent) { return null; }
    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        NotificationManager notifications = getSystemService(NotificationManager.class);
        notifications.createNotificationChannel(new NotificationChannel("screenshot", "Разовый снимок", NotificationManager.IMPORTANCE_LOW));
        Notification notice = new Notification.Builder(this,"screenshot").setSmallIcon(R.drawable.notice)
            .setContentTitle("JobPilot делает один снимок экрана").setVisibility(Notification.VISIBILITY_SECRET).build();
        try {
            if (Build.VERSION.SDK_INT >= 29) startForeground(713, notice, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
            else startForeground(713, notice);
            if (request != null || intent == null) { finish(null, "Повторите создание снимка."); return START_NOT_STICKY; }
            request = intent.getStringExtra(CaptureConsentActivity.REQUEST);
            grant = intent.getParcelableExtra(CaptureConsentActivity.GRANT);
            bound = bindService(new Intent(this, EdgeService.class), this, 0);
            if (!bound || grant == null) finish(null, "Откройте боковую панель и повторите снимок.");
            else main.postDelayed(() -> finish(null, EMPTY), 6500);
        } catch (RuntimeException error) { finish(null, "Android не разрешил захват."); }
        return START_NOT_STICKY;
    }
    @Override public void onServiceConnected(ComponentName name, IBinder binder) {
        if (finished) return;
        receiver = (EdgeService.FrameReceiver)binder;
        if (!receiver.accepts(request)) { finish(null, "Снимок отменён."); return; }
        // Let the system permission activity disappear before opening the virtual display.
        main.postDelayed(this::capture, 420);
    }
    @Override public void onServiceDisconnected(ComponentName name) { receiver = null; finish(null, "Панель закрыта."); }
    @Override public void onNullBinding(ComponentName name) { finish(null, "Панель закрыта."); }

    private void capture() {
        try {
            if (receiver == null || !receiver.accepts(request)) { finish(null, "Снимок отменён."); return; }
            Display screen = getSystemService(DisplayManager.class).getDisplay(Display.DEFAULT_DISPLAY);
            Point size = new Point();
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Rect bounds = getSystemService(android.view.WindowManager.class)
                        .getMaximumWindowMetrics().getBounds();
                size.set(bounds.width(), bounds.height());
            } else screen.getRealSize(size);
            float scale = Math.min(1f, 1800f / Math.max(size.x,size.y));
            int w = Math.max(1,Math.round(size.x*scale)), h = Math.max(1,Math.round(size.y*scale));
            projection = getSystemService(MediaProjectionManager.class).getMediaProjection(Activity.RESULT_OK,grant);
            grant = null;
            if (projection == null) { finish(null, "Доступ к экрану отозван."); return; }
            projection.registerCallback(stopped, main);
            reader = ImageReader.newInstance(w,h,PixelFormat.RGBA_8888,2);
            reader.setOnImageAvailableListener(this::frame, main);
            display = projection.createVirtualDisplay("JobPilot single snapshot",w,h,getResources().getConfiguration().densityDpi,
                    DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,reader.getSurface(),null,main);
            if (display == null) finish(null, "Android не создал поверхность для снимка.");
        } catch (RuntimeException error) { finish(null, "Не удалось получить кадр. Попробуйте снова."); }
    }
    private void frame(ImageReader source) {
        if (finished) return;
        Bitmap bitmap;
        try (Image image = source.acquireLatestImage()) {
            if (image == null) return;
            Image.Plane plane = image.getPlanes()[0];
            int w = image.getWidth(), h = image.getHeight(), pitch = plane.getRowStride();
            if (plane.getPixelStride()!=4) throw new IllegalStateException("Pixel format");
            ByteBuffer buffer = plane.getBuffer();
            // Browsers can publish a black compositor surface before their first painted frame.
            // Leave the one authorized display open until it supplies visible content or times out.
            if (!CapturePixels.hasContent(buffer, w, h, pitch)) return;
            if (pitch != w*4) {
                ByteBuffer packed = ByteBuffer.allocateDirect(w*h*4);
                for (int row=0;row<h;row++) {
                    ByteBuffer line = buffer.duplicate();
                    line.position(row*pitch);line.limit(row*pitch+w*4);packed.put(line);
                }
                packed.flip(); buffer=packed;
            }
            bitmap=Bitmap.createBitmap(w,h,Bitmap.Config.ARGB_8888);
            bitmap.copyPixelsFromBuffer(buffer);
        } catch (RuntimeException error) { finish(null, "Не удалось прочитать снимок."); return; }
        finish(bitmap, null);
    }
    private void finish(Bitmap image, String error) {
        if (finished) return;
        finished=true; main.removeCallbacksAndMessages(null);
        if (reader != null) reader.setOnImageAvailableListener(null,null);
        if (display != null) {display.release();display=null;}
        if (reader != null) {reader.close();reader=null;}
        if (projection != null) {
            projection.unregisterCallback(stopped);projection.stop();projection=null;
        }
        grant=null;
        if(receiver!=null)receiver.deliver(request,image,error);
        if(bound){unbindService(this);bound=false;}
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }
    @Override public void onDestroy() {finish(null,"Захват остановлен.");super.onDestroy();}
}
