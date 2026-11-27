package dev.blyadota.jobpilot.assistant;

import android.app.*;
import android.app.job.*;
import android.content.*;
import android.os.PersistableBundle;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class DraftJob extends JobService {
    private final ExecutorService executor=Executors.newFixedThreadPool(2);
    private final ConcurrentHashMap<Integer,JobParameters> active=new ConcurrentHashMap<>();
    static void schedule(Context c,String id) {
        PersistableBundle extras=new PersistableBundle();
        extras.putString("draft",id);extras.putLong("until",System.currentTimeMillis()+24*3600000L);
        JobInfo job=new JobInfo.Builder(id.hashCode() & Integer.MAX_VALUE,new ComponentName(c,DraftJob.class))
            .setExtras(extras).setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).setMinimumLatency(15000)
            .setBackoffCriteria(20000,JobInfo.BACKOFF_POLICY_EXPONENTIAL).build();
        c.getSystemService(JobScheduler.class).schedule(job);
    }
    @Override public boolean onStartJob(JobParameters p) {
        active.put(p.getJobId(),p);
        executor.execute(()->{
            boolean retry=false;
            try {
                String id=p.getExtras().getString("draft");
                String status=new AssistantApi(this).status(id);
                if(status.equals("ready") || status.equals("failed")){
                    if(active.get(p.getJobId())==p) notifyResult(p.getJobId(),id,status.equals("ready"));
                } else retry=true;
            }catch(AssistantApi.Failure f){retry=f.status!=401 && f.status!=404;}
             catch(Exception e){retry=true;}
            final boolean again=retry && System.currentTimeMillis()<p.getExtras().getLong("until");
            getMainExecutorCompat().post(()->{
                if(active.remove(p.getJobId(),p))jobFinished(p,again);
            });
        });
        return true;
    }
    private android.os.Handler getMainExecutorCompat(){return new android.os.Handler(getMainLooper());}
    private void notifyResult(int request,String id,boolean ready){
        NotificationManager nm=getSystemService(NotificationManager.class);
        if(!nm.areNotificationsEnabled())return;
        nm.createNotificationChannel(new NotificationChannel("drafts","Готовые ответы",NotificationManager.IMPORTANCE_DEFAULT));
        Intent i=new Intent(this,AssistantActivity.class).setAction("dev.blyadota.jobpilot.history").putExtra("draft",id);
        PendingIntent open=PendingIntent.getActivity(this,request,i,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        Notification n=new Notification.Builder(this,"drafts").setSmallIcon(R.drawable.notice).setContentTitle("JobPilot")
            .setContentText(ready?"Ответ готов. Нажмите, чтобы открыть.":"Запрос требует внимания.")
            .setAutoCancel(true).setContentIntent(open).setVisibility(Notification.VISIBILITY_PRIVATE).build();
        nm.notify("draft",request,n);
    }
    @Override public boolean onStopJob(JobParameters p){active.remove(p.getJobId(),p);return System.currentTimeMillis()<p.getExtras().getLong("until");}
    @Override public void onDestroy(){active.clear();executor.shutdownNow();super.onDestroy();}
}
