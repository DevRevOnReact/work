package dev.blyadota.jobpilot.assistant;

import android.content.*;
import android.graphics.Bitmap;
import android.graphics.Rect;
import android.os.*;
import android.text.InputFilter;
import android.view.Gravity;
import android.view.View;
import android.widget.*;
import java.io.ByteArrayOutputStream;
import java.util.concurrent.*;
import org.json.JSONObject;
import static dev.blyadota.jobpilot.assistant.PanelUi.*;

/** The overlay owns only the currently edited question, preview and one server draft. */
final class EdgeComposer {
    private enum Mode { EDIT, PREVIEW, OCR, SUBMIT, WAIT, ANSWERS }
    private final EdgeService service;
    private final AssistantApi api;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());
    private final SharedPreferences session;
    private Mode mode = Mode.EDIT;
    private String question="", instruction="", draftId="", message="";
    private Bitmap screenshot;
    private final Rect crop=new Rect();
    private EditText questionView, instructionView;
    private JSONObject variants;
    private boolean visible, checking, closed, needsConnection;
    private final Runnable poll=this::check;

    EdgeComposer(EdgeService service) {
        this.service=service;api=new AssistantApi(service);
        session=service.getSharedPreferences("edge-draft",Context.MODE_PRIVATE);
        draftId=session.getString("id","");
        if(!draftId.isEmpty())mode=Mode.WAIT;
    }

    void detached() {
        if(questionView!=null)question=questionView.getText().toString();
        if(instructionView!=null)instruction=instructionView.getText().toString();
        questionView=null;instructionView=null;visible=false;main.removeCallbacks(poll);
    }
    private void redraw(){if(visible)service.refreshPanel();}
    void close(){detached();closed=true;screenshot=null;main.removeCallbacksAndMessages(null);worker.shutdown();}
    boolean isPreview() { return mode == Mode.PREVIEW; }
    void discardPreview() {
        if (mode == Mode.PREVIEW) { screenshot=null; crop.setEmpty(); mode=Mode.EDIT; message=""; }
    }
    void captureFinished(Bitmap frame,String error){
        if(frame!=null){screenshot=frame;crop.setEmpty();mode=Mode.PREVIEW;message="";needsConnection=false;}
        else message=error;
    }
    View view() {
        visible=true;
        ScrollView scroll=new ScrollView(service){
            @Override protected void onMeasure(int w,int h){
                int max=(int)(getResources().getDisplayMetrics().heightPixels*(isPreview()?0.94f:0.74f));
                if(MeasureSpec.getMode(h)!=MeasureSpec.UNSPECIFIED)max=Math.min(max,MeasureSpec.getSize(h));
                super.onMeasure(w,MeasureSpec.makeMeasureSpec(max,MeasureSpec.AT_MOST));
            }
        };
        scroll.setBackground(shape(service,BG,18));
        LinearLayout box=column(service,14);box.setFocusableInTouchMode(true);scroll.addView(box);
        LinearLayout header=new LinearLayout(service);header.setGravity(Gravity.CENTER_VERTICAL);box.addView(header);
        header.addView(text(service,isPreview()?"Область снимка":mode==Mode.ANSWERS?"Готовый ответ":"JobPilot",21,TEXT,true),new LinearLayout.LayoutParams(0,-2,1));
        Button hide=button(service,isPreview()?"Отмена":"Свернуть",false,v->service.collapse());header.addView(hide);
        if(!message.isEmpty())add(box,text(service,message,14,ACCENT,false),8);
        if(needsConnection)action(box,"Подключить телефон",false,v->service.navigate("device"));
        switch(mode){
            case EDIT:
                LinearLayout sources=new LinearLayout(service);add(box,sources,8);
                Button take=button(service,"Снять экран",true,v->service.requestCapture(false));
                Button pick=button(service,"Из файла",false,v->service.requestCapture(true));
                sources.addView(take,new LinearLayout.LayoutParams(0,-2,1));
                LinearLayout.LayoutParams other=new LinearLayout.LayoutParams(0,-2,1);
                other.leftMargin=dp(service,8);sources.addView(pick,other);
                questionView=field(box,"Текст сообщения",question,4000,2);
                instructionView=field(box,"Пожелание к ответу",instruction,800,1);
                action(box,"Подготовить ответ",true,v->submit());
                add(box,button(service,"История ответов",false,v->service.navigate("history")),6);
                break;
            case PREVIEW:
                add(box,text(service,"Обведите текст. Потяните за угол, чтобы изменить рамку.",14,MUTED,false),8);
                Button read=button(service,"Распознать область",true,v->recognize());
                Runnable update=()->{
                    boolean selected=!crop.isEmpty();
                    read.setEnabled(selected);read.setAlpha(selected?1f:0.45f);
                    boolean full=selected && crop.width()==screenshot.getWidth() && crop.height()==screenshot.getHeight();
                    read.setText(full?"Распознать весь снимок":"Распознать область");
                };
                ScreenshotPreview preview=new ScreenshotPreview(service,screenshot,crop,update);
                LinearLayout.LayoutParams imageSize=new LinearLayout.LayoutParams(-1,
                        Math.max(dp(service,120),(int)(service.getResources().getDisplayMetrics().heightPixels*0.55f)));
                imageSize.topMargin=dp(service,8);box.addView(preview,imageSize);
                action(box,"Выделить всё",false,v->preview.selectAll());
                add(box,read,7);update.run();
                break;
            case OCR: progress(box,"Распознаём текст…");break;
            case SUBMIT: progress(box,"Передаём вопрос…");break;
            case WAIT:
                progress(box,"Готовим ответ на сервере…");
                if(message.isEmpty())main.post(poll);
                else action(box,"Проверить ответ",false,v->{message="";check();});
                break;
            case ANSWERS:
                String answer=AssistantApi.recommended(variants);
                TextView body=text(service,answer,17,TEXT,false);
                int inset=dp(service,14);body.setPadding(inset,inset,inset,inset);
                body.setBackground(shape(service,CARD,14));add(box,body,10);
                action(box,"Скопировать ответ",true,v->copyAnswer(answer));
                action(box,"Уточнить запрос",false,v->{clearDraft();mode=Mode.EDIT;message="";redraw();});
                action(box,"Новый снимок",false,v->service.requestCapture(false));
                action(box,"Из файла",false,v->service.requestCapture(true));
                break;
        }
        return scroll;
    }
    private EditText field(LinearLayout parent,String label,String value,int max,int lines){
        EditText edit=new EditText(service);edit.setSingleLine(false);edit.setMinLines(lines);edit.setMaxLines(5);
        edit.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS);
        edit.setImeOptions(android.view.inputmethod.EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING);
        edit.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE
                |android.text.InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        edit.setImeOptions(edit.getImeOptions() | android.view.inputmethod.EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING);
        edit.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
        edit.setTextColor(TEXT);edit.setHintTextColor(MUTED);edit.setTextSize(16);edit.setBackground(shape(service,CARD,10));
        edit.setPadding(dp(service,12),dp(service,6),dp(service,12),dp(service,6));edit.setGravity(Gravity.TOP|Gravity.START);
        edit.setHint(label);edit.setContentDescription(label);edit.setSaveEnabled(false);edit.setFilters(new InputFilter[]{new InputFilter.LengthFilter(max)});
        edit.setText(value);add(parent,edit,7);return edit;
    }
    private void action(LinearLayout box,String label,boolean primary,View.OnClickListener click){add(box,button(service,label,primary,click),7);}
    private void progress(LinearLayout box,String text){
        LinearLayout line=new LinearLayout(service);line.setGravity(Gravity.CENTER_VERTICAL);add(box,line,14);
        ProgressBar spin=new ProgressBar(service);line.addView(spin,dp(service,22),dp(service,22));
        TextView label=PanelUi.text(service,text,15,TEXT,false);label.setPadding(dp(service,10),0,0,0);line.addView(label);
    }
    private void copyAnswer(String value) {
            ClipData clip=ClipData.newPlainText("JobPilot",value);
            if(Build.VERSION.SDK_INT>=33){PersistableBundle flags=new PersistableBundle();flags.putBoolean(ClipDescription.EXTRA_IS_SENSITIVE,true);clip.getDescription().setExtras(flags);}
            service.getSystemService(ClipboardManager.class).setPrimaryClip(clip);
            Toast.makeText(service,"Ответ скопирован",Toast.LENGTH_SHORT).show();service.collapse();

    }
    private void clearDraft(){draftId="";session.edit().remove("id").apply();}
    private void error(Exception err,String fallback){
        needsConnection=err instanceof AssistantApi.Failure && ((AssistantApi.Failure)err).status==401;
        message=needsConnection?"Подключите телефон к JobPilot.":fallback;
        if(err instanceof AssistantApi.Failure && ((AssistantApi.Failure)err).status==429)message="Сервер занят. Повторите чуть позже.";
    }
    private static boolean readable(Bitmap region) {
        int min=255,max=0;
        for (int y=0;y<region.getHeight();y+=Math.max(1,region.getHeight()/180)) {
            for (int x=0;x<region.getWidth();x+=Math.max(1,region.getWidth()/140)) {
                int pixel=region.getPixel(x,y);
                int value=Math.max((pixel>>16)&255,Math.max((pixel>>8)&255,pixel&255));
                min=Math.min(min,value);max=Math.max(max,value);
                if (max-min>=16) return true;
            }
        }
        return false;
    }
    private void recognize(){
        if (mode != Mode.PREVIEW || screenshot == null || crop.isEmpty()) return;
        final Bitmap source=screenshot;final Rect area=new Rect(crop);
        mode=Mode.OCR;message="";needsConnection=false;redraw();
        worker.execute(()->{
            try{
                Bitmap region=Bitmap.createBitmap(source,area.left,area.top,area.width(),area.height());
                if (!readable(region)) {
                    main.post(()->{
                        if (closed) return;
                        mode=Mode.PREVIEW;message="В рамке нет видимого текста. Измените область или отмените снимок.";redraw();
                    });
                    return;
                }
                ByteArrayOutputStream output=new ByteArrayOutputStream();
                if(!region.compress(Bitmap.CompressFormat.JPEG,92,output) || output.size()>AssistantApi.MEDIA_LIMIT)
                    throw new java.io.IOException("Image too large");
                String recognized=api.recognize(output.toByteArray(),false).trim();
                if(recognized.isEmpty())throw new java.io.IOException("No text");
                main.post(()->{if(closed)return;clearDraft();screenshot=null;crop.setEmpty();
                    question=recognized.substring(0,Math.min(4000,recognized.length()));mode=Mode.EDIT;
                    message="Проверьте распознанный текст.";redraw();});
            }catch(Exception err){main.post(()->{if(closed)return;mode=Mode.PREVIEW;
                error(err,"Не удалось прочитать текст. Выделите фрагмент крупнее или повторите снимок.");redraw();});}
        });
    }
    private void submit(){
        question=questionView.getText().toString().trim();instruction=instructionView.getText().toString().trim();
        if(question.isEmpty()){questionView.setError("Введите вопрос или снимите экран.");return;}
        final String input=question, wish=instruction;
        mode=Mode.SUBMIT;message="";needsConnection=false;redraw();
        worker.execute(()->{
            try{
                JSONObject body=new JSONObject().put("question",input).put("instruction",wish).put("language","en");
                String id=DraftAttempt.begin(service,body.toString());body.put("id",id);
                String accepted=api.create(body).getString("id");
                session.edit().putString("id",accepted).apply();DraftAttempt.acknowledge(service,accepted);
                DraftJob.schedule(service,accepted);
                main.post(()->{if(closed)return;draftId=accepted;mode=Mode.WAIT;redraw();});
            }catch(Exception err){main.post(()->{if(closed)return;mode=Mode.EDIT;error(err,"Запрос не подтверждён. Проверьте сеть и повторите.");redraw();});}
        });
    }
    private void check(){
        if(closed || !visible || checking || mode!=Mode.WAIT)return;
        checking=true;final String id=draftId;
        worker.execute(()->{
            try{
                JSONObject result=api.answers(id);
                main.post(()->{
                    checking=false;if(closed || !id.equals(draftId))return;
                    switch(result.optString("status")){
                        case "ready":
                            variants=result.optJSONObject("variants");
                            if(AssistantApi.recommended(variants).isEmpty()){clearDraft();mode=Mode.EDIT;message="Сервер вернул пустой ответ. Повторите запрос.";}
                            else{mode=Mode.ANSWERS;message=result.optString("note","");}
                            redraw();break;
                        case "failed":clearDraft();mode=Mode.EDIT;message=result.optString("error","Ошибка генерации.");redraw();break;
                        default:if(visible)main.postDelayed(poll,3200);
                    }
                });
            }catch(Exception err){main.post(()->{checking=false;if(closed)return;
                if(err instanceof AssistantApi.Failure && ((AssistantApi.Failure)err).status==404){clearDraft();mode=Mode.EDIT;}
                error(err,"Не удалось получить ответ. Проверьте соединение.");redraw();});}
        });
    }
}
