package dev.blyadota.jobpilot.assistant;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.*;
import android.provider.Settings;
import android.text.InputFilter;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.io.*;
import java.util.*;
import java.util.concurrent.*;
import static dev.blyadota.jobpilot.assistant.PanelUi.*;

public final class AssistantActivity extends Activity {
    private enum Page {WRITE, HISTORY, DEVICE, RESULT}
    private final ExecutorService io=Executors.newSingleThreadExecutor();
    private final Handler handler=new Handler(Looper.getMainLooper());
    private AssistantApi api;
    private LinearLayout root, body;
    private TextView status;
    private EditText prompt, instruction, response;
    private Spinner language, resume;
    private final List<String> resumeIds=new ArrayList<>();
    private final String[] languageIds={"en"};
    private String question="", wishes="", lang="en", resumeId="", selected="";
    private String fileDraft="", selectedFileName="";
    private Page page=Page.WRITE;
    private JSONObject overview;
    private Uri attachment;
    private boolean audio, visible, loading, working, canInsert, needsPair, checkOverlay;
    private final Runnable poll=this::refresh;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        api=new AssistantApi(this);
        if(state!=null) {selected=state.getString("selected","");fileDraft=state.getString("fileDraft","");
            try {page=Page.valueOf(state.getString("page","WRITE"));}catch(IllegalArgumentException ignored){}}
        readIntent(getIntent(),state==null);
        root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setBackgroundColor(BG);
        root.setOnApplyWindowInsetsListener((v,i)->{v.setPadding(i.getSystemWindowInsetLeft(),i.getSystemWindowInsetTop(),
                i.getSystemWindowInsetRight(),i.getSystemWindowInsetBottom());return i;});
        setContentView(root);render();
    }
    @Override protected void onSaveInstanceState(Bundle out){
        super.onSaveInstanceState(out);out.putString("selected",selected);out.putString("page",page.name());out.putString("fileDraft",fileDraft);
    }
    @Override protected void onNewIntent(Intent i){super.onNewIntent(i); remember();setIntent(i);readIntent(i,true);render();refresh();}
    private void readIntent(Intent i,boolean apply) {
        String action=i.getAction();
        canInsert=Intent.ACTION_PROCESS_TEXT.equals(action) && !i.getBooleanExtra(Intent.EXTRA_PROCESS_TEXT_READONLY,true);
        if(!apply)return;
        if(Intent.ACTION_SEND.equals(action) || Intent.ACTION_PROCESS_TEXT.equals(action)){
            page=Page.WRITE;
            CharSequence text=i.getCharSequenceExtra(Intent.ACTION_SEND.equals(action)?Intent.EXTRA_TEXT:Intent.EXTRA_PROCESS_TEXT);
            if(text!=null)question=text.subSequence(0,Math.min(4000,text.length())).toString();
            if(Intent.ACTION_SEND.equals(action)){
                Object shared=i.getParcelableExtra(Intent.EXTRA_STREAM);
                if(shared instanceof Uri && "content".equals(((Uri)shared).getScheme())){
                    attachment=(Uri)shared;audio=i.getType()!=null && i.getType().startsWith("audio/");
                }
            }
        }else if("dev.blyadota.jobpilot.history".equals(action)){
            page=Page.HISTORY;String id=i.getStringExtra("draft");
            if(id!=null && id.matches("[a-f0-9-]{36}")){selected=id;page=Page.RESULT;}
        }else if("dev.blyadota.jobpilot.compose".equals(action))page=Page.WRITE;
        else if("dev.blyadota.jobpilot.device".equals(action))page=Page.DEVICE;
    }
    @Override protected void onResume(){
        super.onResume();visible=true;
        if(checkOverlay){checkOverlay=false;if(Settings.canDrawOverlays(this)) enableEdge();}
        refresh();
    }
    @Override protected void onPause(){visible=false;handler.removeCallbacks(poll);super.onPause();}
    @Override protected void onDestroy(){handler.removeCallbacksAndMessages(null);io.shutdown();super.onDestroy();}
    private void schedule(long delay){handler.removeCallbacks(poll);if(visible && !needsPair)handler.postDelayed(poll,delay);}
    private String failure(Exception e){
        if(e instanceof AssistantApi.Failure){
            int s=((AssistantApi.Failure)e).status;
            if(s==401)return "Телефон ещё не подключён или доступ отозван.";
            if(s==429)return "Очередь занята. Повторите запрос позже.";
            if(s==413)return "Файл слишком большой.";
            if(s==422)return "Проверьте текст или качество вложения.";
            if(s==404)return "Файл или черновик уже удалён.";
        }
        return "Не удалось завершить запрос. Проверьте соединение и повторите.";
    }
    private void refresh(){
        handler.removeCallbacks(poll);
        if(loading || working)return;
        loading=true;
        io.execute(()->{
            try{
                JSONObject value=api.overview();
                runOnUiThread(()->{if(isDestroyed())return;loading=false;
                    boolean redraw=overview==null || needsPair || page==Page.HISTORY || page==Page.DEVICE ||
                        (page==Page.RESULT && !terminal(find(selected)));
                    if(needsPair && page==Page.DEVICE && (!question.isEmpty() || attachment!=null))page=Page.WRITE;
                    overview=value;needsPair=false;
                    if(redraw){remember();render();} else connection("Подключено");
                    schedule(page==Page.RESULT && !terminal(find(selected))?3000:45000);
                });
            }catch(Exception e){runOnUiThread(()->{if(isDestroyed())return;loading=false;
                if(e instanceof AssistantApi.Failure && ((AssistantApi.Failure)e).status==401){
                    remember();
                    if(!needsPair || page==Page.RESULT || page==Page.HISTORY)page=Page.DEVICE;
                    needsPair=true;overview=null;render();
                }else{connection(failure(e));schedule(15000);}
            });}
        });
    }
    private interface Work<T>{T run() throws Exception;}
    private interface Done<T>{void accept(T value) throws Exception;}
    private <T> void task(String progress,Work<T> op,Done<T> done){
        if(working)return;
        working=true;connection(progress);handler.removeCallbacks(poll);
        io.execute(()->{try{T result=op.run();runOnUiThread(()->{if(isDestroyed())return;working=false;
            try{done.accept(result);}catch(Exception e){message(failure(e));}schedule(3000);});}
            catch(Exception e){runOnUiThread(()->{if(isDestroyed())return;working=false;message(failure(e));connection(failure(e));refresh();});}});
    }
    private void connection(String s){if(status!=null)status.setText(s);}
    private void message(String s){Toast.makeText(this,s,Toast.LENGTH_LONG).show();}
    private void remember(){
        if(prompt!=null)question=prompt.getText().toString();
        if(instruction!=null)wishes=instruction.getText().toString();
        if(language!=null && language.getSelectedItemPosition()>=0)lang=languageIds[language.getSelectedItemPosition()];
        if(resume!=null && resume.getSelectedItemPosition()>=0 && resume.getSelectedItemPosition()<resumeIds.size())
            resumeId=resumeIds.get(resume.getSelectedItemPosition());
    }
    private void go(Page next){remember();page=next;render();if(next==Page.HISTORY)refresh();}
    private void render(){
        prompt=null;instruction=null;response=null;language=null;resume=null;root.removeAllViews();
        LinearLayout header=new LinearLayout(this);header.setPadding(dp(this,20),dp(this,14),dp(this,20),dp(this,6));
        header.setGravity(Gravity.CENTER_VERTICAL);root.addView(header);
        header.addView(text(this,"JobPilot",25,TEXT,true),new LinearLayout.LayoutParams(0,-2,1));
        status=text(this,needsPair?"Требуется вход":overview==null?"Подключение…":"Подключено",12,MUTED,false);
        status.setGravity(Gravity.RIGHT);header.addView(status,new LinearLayout.LayoutParams(0,-2,1));
        LinearLayout nav=new LinearLayout(this);nav.setPadding(dp(this,12),dp(this,5),dp(this,12),dp(this,5));root.addView(nav);
        tab(nav,"Ответ",Page.WRITE);tab(nav,"История",Page.HISTORY);tab(nav,"Панель",Page.DEVICE);
        ScrollView scroll=new ScrollView(this);scroll.setFillViewport(true);root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        body=new LinearLayout(this);body.setOrientation(LinearLayout.VERTICAL);body.setFocusableInTouchMode(true);
        body.setPadding(dp(this,20),dp(this,12),dp(this,20),dp(this,40));scroll.addView(body);
        switch(page){case DEVICE:device();break;case HISTORY:history();break;case RESULT:result();break;default:compose();}
    }
    private void tab(LinearLayout row,String title,Page target){
        Button b=button(this,title,page==target || (target==Page.HISTORY && page==Page.RESULT),v->go(target));
        LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(0,-2,1);p.setMargins(dp(this,2),0,dp(this,2),0);row.addView(b,p);
    }
    private void heading(String name){add(body,text(this,name,25,TEXT,true),6);}
    private void caption(String name){add(body,text(this,name,14,MUTED,false),12);}
    private void action(String name,boolean primary,View.OnClickListener listener){add(body,button(this,name,primary,listener),9);}
    private EditText field(String label,String initial,int lines,int limit){
        caption(label);EditText t=new EditText(this);t.setTextColor(TEXT);t.setTextSize(16);
        t.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE|
                android.text.InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        t.setGravity(Gravity.TOP|Gravity.START);t.setMinLines(lines);t.setMaxLines(9);t.setFilters(new InputFilter[]{new InputFilter.LengthFilter(limit)});
        t.setPadding(dp(this,12),dp(this,10),dp(this,12),dp(this,10));t.setBackground(shape(this,CARD,12));
        t.setSaveEnabled(false);t.setText(initial);t.setContentDescription(label);add(body,t,5);return t;
    }
    private Spinner choice(String title,List<String> values,int index){
        caption(title);Spinner s=new Spinner(this);ArrayAdapter<String> adapter=new ArrayAdapter<>(this,android.R.layout.simple_spinner_item,values);
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);s.setAdapter(adapter);s.setSelection(Math.max(0,index));
        s.setContentDescription(title);add(body,s,3);return s;
    }
    private void compose(){
        heading("Ответ рекрутеру");
        prompt=field("Сообщение рекрутера",question,4,4000);
        if(attachment!=null)action(audio?"Распознать выбранное аудио":"Распознать выбранный скриншот",false,v->recognize());
        else action("Добавить скриншот или аудио",false,v->{Intent i=new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("*/*")
            .putExtra(Intent.EXTRA_MIME_TYPES,new String[]{"image/jpeg","image/png","audio/*"});i.addCategory(Intent.CATEGORY_OPENABLE);startActivityForResult(i,21);});
        instruction=field("Пожелание к ответу",wishes,2,800);
        language=choice("Язык",Arrays.asList("English"),0);
        resumeIds.clear();resumeIds.add("");List<String> names=new ArrayList<>();names.add("Подобрать на сервере");
        JSONArray all=overview==null?null:overview.optJSONArray("resumes");
        if(all!=null)for(int n=0;n<all.length();n++){JSONObject item=all.optJSONObject(n);if(item!=null){names.add(item.optString("filename"));resumeIds.add(item.optString("id"));}}
        resume=choice("Резюме",names,resumeIds.indexOf(resumeId));
        action(needsPair?"Подключить телефон":"Подготовить ответ",true,v->{if(needsPair)go(Page.DEVICE);else submit();});
    }
    private void recognize(){
        if(needsPair){go(Page.DEVICE);return;}
        final Uri uri=attachment;final boolean isAudio=audio;
        task("Распознаём на сервере…",()->{byte[] bytes;try(InputStream in=getContentResolver().openInputStream(uri)){
            bytes=AssistantApi.bounded(in,AssistantApi.MEDIA_LIMIT);}return api.recognize(bytes,isAudio);},
            value->{remember();question=clip(question.isEmpty()?value:question+"\n"+value,4000);attachment=null;page=Page.WRITE;render();});
    }
    private void submit(){
        remember();if(question.trim().isEmpty()){prompt.setError("Введите вопрос");return;}
        try{
            JSONObject req=new JSONObject().put("question",question.trim()).put("instruction",wishes.trim()).put("language",lang);
            if(!resumeId.isEmpty())req.put("resume_id",resumeId);
            task("Передаём на сервер…",()->{
                String id=DraftAttempt.begin(this,req.toString());
                req.put("id",id);
                JSONObject ack=api.create(req);
                DraftJob.schedule(getApplicationContext(),id);
                return ack;
            },ack->{
                selected=ack.getString("id");DraftAttempt.acknowledge(this,selected);
                page=Page.RESULT;render();refresh();});
        }catch(JSONException e){message("Не удалось подготовить запрос.");}
    }
    private JSONObject find(String id){
        JSONArray rows=overview==null?null:overview.optJSONArray("drafts");
        if(rows!=null)for(int n=0;n<rows.length();n++){JSONObject d=rows.optJSONObject(n);if(d!=null && id.equals(d.optString("id")))return d;}
        return null;
    }
    private static boolean terminal(JSONObject d){return d!=null && ("ready".equals(d.optString("status")) || "failed".equals(d.optString("status")));}
    private static String clip(String s,int max){return s.substring(0,Math.min(max,s.length()));}
    private static String state(JSONObject d){
        switch(d.optString("status")){case "ready":return "Готово";case "failed":return "Требует внимания";case "working":return "Готовим";default:return "В очереди";}
    }
    private void history(){
        heading("Недавние ответы");JSONArray rows=overview==null?null:overview.optJSONArray("drafts");
        if(rows==null || rows.length()==0)caption("Здесь появятся общие с веб-версией черновики за последние семь дней.");
        if(rows!=null)for(int n=0;n<rows.length();n++){JSONObject d=rows.optJSONObject(n);if(d==null)continue;
            action(state(d)+"  ·  "+clip(d.optString("question").replace('\n',' '),86),false,v->{selected=d.optString("id");go(Page.RESULT);});}
        action("Обновить",false,v->refresh());
    }
    private void result(){
        JSONObject d=find(selected);heading("Готовый ответ");
        if(d==null || !terminal(d)){caption(d==null?"Синхронизация с сервером…":state(d)+". Можно закрыть приложение.");
            action("Обновить",false,v->refresh());return;}
        JSONObject result=d.optJSONObject("result");if(result==null)return;
        caption(clip(d.optString("question"),500));
        if("failed".equals(d.optString("status"))){caption(result.optString("error","Попробуйте снова."));action("Вернуться к вопросу",true,v->{question=d.optString("question");wishes=d.optString("instruction");go(Page.WRITE);});return;}
        JSONObject variants=result.optJSONObject("variants");if(variants==null)return;
        response=field("Текст для отправки",AssistantApi.recommended(variants),4,4000);
        action(canInsert?"Вставить в исходное поле":"Скопировать ответ",true,v->{
            String answer=response.getText().toString();
            if(canInsert){setResult(RESULT_OK,new Intent().putExtra(Intent.EXTRA_PROCESS_TEXT,answer));finish();}
            else{ClipboardManager clipboard=getSystemService(ClipboardManager.class);ClipData clip=ClipData.newPlainText("JobPilot",answer);
                if(Build.VERSION.SDK_INT>=33){PersistableBundle flags=new PersistableBundle();flags.putBoolean(ClipDescription.EXTRA_IS_SENSITIVE,true);clip.getDescription().setExtras(flags);}
                clipboard.setPrimaryClip(clip);message("Ответ скопирован.");}
        });
        JSONObject r=result.optJSONObject("resume");if(r!=null){caption(r.optString("filename"));action("Сохранить резюме",false,v->{
            fileDraft=selected;selectedFileName=r.optString("filename","resume.pdf").replaceAll("[/\\\\]","_");
            String mime=java.net.URLConnection.guessContentTypeFromName(selectedFileName);
            Intent save=new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType(mime==null?"application/octet-stream":mime)
                .putExtra(Intent.EXTRA_TITLE,selectedFileName);startActivityForResult(save,22);});}
        action("Уточнить вопрос",false,v->{question=d.optString("question");wishes=d.optString("instruction");go(Page.WRITE);});
    }
    private void device(){
        heading("Личная панель");
        if(overview==null || needsPair){
            caption("Подключите этот телефон через браузер и сверьте код на обоих экранах.");
            TextView fingerprint=text(this,"",20,ACCENT,true);add(body,fingerprint,10);
            io.execute(()->{try{String hash=DeviceKey.fingerprint(this);String shortCode=hash.substring(0,12).toUpperCase(Locale.ROOT);
                runOnUiThread(()->fingerprint.setText(shortCode.substring(0,4)+" "+shortCode.substring(4,8)+" "+shortCode.substring(8)));}catch(Exception e){runOnUiThread(()->message("Хранилище ключей Android недоступно."));}});
            action("Подключить через браузер",true,v->task("Открываем кабинет…",()->DeviceKey.fingerprint(this),
                    hash->browser("/android#"+hash)));
            action("Проверить подключение",false,v->refresh());
        }else {
            caption("Телефон подключён к помощнику на jobs.blyadota.dev.");
            action("Управлять доступом",false,v->browser("/android"));
        }
        caption("Боковая вкладка открывает помощника поверх текущего приложения. Её можно перетащить вдоль левого края.");
        action(EdgeService.running?"Выключить боковую вкладку":"Включить боковую вкладку",true,v->{
            if(EdgeService.running){stopService(new Intent(this,EdgeService.class));handler.postDelayed(()->{if(page==Page.DEVICE)render();},350);}
            else if(!Settings.canDrawOverlays(this)){checkOverlay=true;startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,Uri.parse("package:"+getPackageName())));}
            else enableEdge();
        });
        if(Build.VERSION.SDK_INT>=33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)
            action("Разрешить уведомления о готовности",false,v->requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},31));
        caption("Для передачи вопроса выделите текст в другом приложении или выберите «Поделиться» → JobPilot.");
    }
    private void browser(String path){startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(AssistantApi.ORIGIN+path)).addCategory(Intent.CATEGORY_BROWSABLE));}
    private void enableEdge(){
        try{startForegroundService(new Intent(this,EdgeService.class));handler.postDelayed(()->{if(!isDestroyed() && page==Page.DEVICE)render();},400);}
        catch(RuntimeException e){message("Система не разрешила включить панель.");}
    }
    @Override public void onRequestPermissionsResult(int code,String[] permissions,int[] results){
        super.onRequestPermissionsResult(code,permissions,results);if(code==31 && page==Page.DEVICE)render();
    }
    @Override protected void onActivityResult(int code,int result,Intent data){
        super.onActivityResult(code,result,data);if(result!=RESULT_OK||data==null||data.getData()==null)return;
        Uri uri=data.getData();if(!"content".equals(uri.getScheme()))return;
        if(code==21){remember();attachment=uri;String type=getContentResolver().getType(uri);audio=type!=null&&type.startsWith("audio/");page=Page.WRITE;render();}
        if(code==22 && !fileDraft.isEmpty()){
            String id=fileDraft;task("Загружаем файл…",()->{byte[] bytes=api.file(id);
                try(OutputStream out=getContentResolver().openOutputStream(uri,"wt")){if(out==null)throw new IOException();out.write(bytes);}return true;},
                ok->{fileDraft="";connection("Файл сохранён");});
        }
    }
    @Override public void onBackPressed(){if(page==Page.RESULT){go(Page.HISTORY);return;}super.onBackPressed();}
}
