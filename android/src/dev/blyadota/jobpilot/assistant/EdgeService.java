package dev.blyadota.jobpilot.assistant;

import android.app.*;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.os.Binder;
import android.os.Bundle;
import android.os.ResultReceiver;
import android.os.IBinder;
import android.provider.Settings;
import android.view.*;
import android.widget.*;

public final class EdgeService extends Service {
    static final String STOP="stop";
    private WindowManager manager;
    private View window;
    private WindowManager.LayoutParams params;
    private int y;
    private boolean open;
    static boolean running;
    private final android.os.Handler timer=new android.os.Handler(android.os.Looper.getMainLooper());
    private EdgeComposer composer;
    private String captureId;
    private final android.content.BroadcastReceiver screenOff = new android.content.BroadcastReceiver() {
        @Override public void onReceive(android.content.Context context, Intent intent) {
            captureId = null;
            timer.removeCallbacks(captureTimeout);
            stopService(new Intent(EdgeService.this,ScreenCaptureService.class));
            composer.discardPreview();
            if (running) show(false);
        }
    };
    private final Runnable captureTimeout=()->finishCapture(captureId,null,"Время ожидания снимка истекло.");
    final class FrameReceiver extends Binder {
        boolean accepts(String id){return id!=null && id.equals(captureId);}
        void deliver(String id,Bitmap image,String error){finishCapture(id,image,error);}
    }

    @Override public IBinder onBind(Intent intent) {return new FrameReceiver();}
    @Override public void onCreate() {
        super.onCreate(); manager=getSystemService(WindowManager.class);
        composer=new EdgeComposer(this);
        registerReceiver(screenOff,new android.content.IntentFilter(Intent.ACTION_SCREEN_OFF));
        y=getSharedPreferences("panel",MODE_PRIVATE).getInt("y",PanelUi.dp(this,175));
    }
    @Override public int onStartCommand(Intent intent,int flags,int id) {
        if(intent==null || STOP.equals(intent.getAction()) || !Settings.canDrawOverlays(this)) {
            captureId=null;stopService(new Intent(this,ScreenCaptureService.class));stopSelf();return START_NOT_STICKY;
        }
        NotificationManager nm=getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("edge","Боковая панель",NotificationManager.IMPORTANCE_LOW));
        PendingIntent launch=PendingIntent.getActivity(this,7,new Intent(this,AssistantActivity.class),PendingIntent.FLAG_IMMUTABLE);
        PendingIntent stop=PendingIntent.getService(this,8,new Intent(this,EdgeService.class).setAction(STOP),PendingIntent.FLAG_IMMUTABLE);
        Notification notification=new Notification.Builder(this,"edge").setSmallIcon(R.drawable.notice)
            .setContentTitle("Панель JobPilot включена").setContentText("Нажмите на вкладку у левого края экрана.")
            .setContentIntent(launch).setOngoing(true).setVisibility(Notification.VISIBILITY_SECRET)
            .addAction(new Notification.Action.Builder(null,"Выключить",stop).build()).build();
        startForeground(711,notification);running=true;if(captureId==null)show(false);return START_NOT_STICKY;
    }
    void navigate(String section) {
        // Launch while the user's overlay is visible.
        try { startActivity(new Intent(this,AssistantActivity.class).setAction("dev.blyadota.jobpilot."+section)
                 .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_SINGLE_TOP)); }
        catch (RuntimeException e) { Toast.makeText(this,"Откройте JobPilot из уведомления.",Toast.LENGTH_LONG).show(); }
        show(false);
    }
    void requestCapture(boolean pick){
        if(captureId!=null || getSystemService(android.app.KeyguardManager.class).isDeviceLocked())return;
        final String id=java.util.UUID.randomUUID().toString();
        captureId=id;
        ResultReceiver cancel=new ResultReceiver(timer){
            @Override protected void onReceiveResult(int code,Bundle data){finishCapture(id,null,"Снимок отменён.");}
        };
        try{
            startActivity(new Intent(this,CaptureConsentActivity.class)
                .putExtra(CaptureConsentActivity.PICK,pick).putExtra(CaptureConsentActivity.REQUEST,id).putExtra(CaptureConsentActivity.CANCEL,cancel)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_CLEAR_TASK|Intent.FLAG_ACTIVITY_NO_ANIMATION));
            removeWindow();
            timer.postDelayed(captureTimeout,120000);
        }catch(RuntimeException error){finishCapture(id,null,"Не удалось открыть разрешение Android.");}
    }
    private void finishCapture(String id,Bitmap frame,String error){
        if(id==null || !id.equals(captureId))return;
        timer.removeCallbacks(captureTimeout);captureId=null;
        composer.captureFinished(frame,error);show(true);
    }
    void refreshPanel(){if(open && captureId==null)show(true);}
    void collapse(){composer.discardPreview();show(false);}
    private void removeWindow(){
        composer.detached();
        if(window!=null && window.isAttachedToWindow()){
            if(open)getSystemService(android.view.inputmethod.InputMethodManager.class).hideSoftInputFromWindow(window.getWindowToken(),0);
            manager.removeViewImmediate(window);
        }
        window=null;
    }
    private void show(boolean expand) {
        if(!Settings.canDrawOverlays(this)){stopSelf();return;}
        removeWindow();
        open=expand;
        if(expand) {
            window=composer.view();
        } else {
            TextView tab=PanelUi.text(this,"j.",21,0xffffffff,true);
            tab.setGravity(Gravity.CENTER);tab.setBackground(PanelUi.shape(this,PanelUi.PRIMARY,12));
            tab.setContentDescription("Открыть панель JobPilot");tab.setOnClickListener(v->show(true));
            tab.setOnTouchListener(new View.OnTouchListener(){
                float down;int start;boolean moved;
                public boolean onTouch(View view,MotionEvent e){
                    switch(e.getActionMasked()){
                        case MotionEvent.ACTION_DOWN: down=e.getRawY();start=params.y;moved=false;return true;
                        case MotionEvent.ACTION_MOVE:
                            float delta=e.getRawY()-down;
                            if(Math.abs(delta)>ViewConfiguration.get(EdgeService.this).getScaledTouchSlop()) moved=true;
                            if(moved){y=clamp(start+(int)delta,64);params.y=y;manager.updateViewLayout(view,params);}return true;
                        case MotionEvent.ACTION_UP:
                            if(moved)getSharedPreferences("panel",MODE_PRIVATE).edit().putInt("y",y).apply();else view.performClick();return true;
                        default:return true;
                    }
                }
            });
            window=tab;
        }
        int width=Math.min(PanelUi.dp(this,composer.isPreview()?640:420),getResources().getDisplayMetrics().widthPixels-PanelUi.dp(this,16));
        params=new WindowManager.LayoutParams(expand?width:PanelUi.dp(this,34),expand?-2:PanelUi.dp(this,64),
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            (expand?0:WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE)|WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                |WindowManager.LayoutParams.FLAG_SECURE,PixelFormat.TRANSLUCENT);
        params.gravity=(expand?Gravity.BOTTOM:Gravity.TOP)|Gravity.LEFT;
        params.x=expand?PanelUi.dp(this,8):0;params.y=expand?PanelUi.dp(this,8):clamp(y,64);
        params.softInputMode=WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE|WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_HIDDEN;
        try {manager.addView(window,params);}
        catch(RuntimeException e){window=null;stopSelf();}
    }
    private int clamp(int value,int heightDp) {
        return Math.max(0,Math.min(value,getResources().getDisplayMetrics().heightPixels-PanelUi.dp(this,heightDp)));
    }
    @Override public void onDestroy(){
        unregisterReceiver(screenOff);
        running=false;captureId=null;timer.removeCallbacksAndMessages(null);
        removeWindow();composer.close();stopService(new Intent(this,ScreenCaptureService.class));
        stopForeground(STOP_FOREGROUND_REMOVE);super.onDestroy();
    }
}
