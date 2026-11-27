package dev.blyadota.jobpilot.assistant;

import android.content.Context;
import android.graphics.*;
import android.view.MotionEvent;
import android.view.View;

/** Selection is stored in source pixels. An interrupted gesture restores the previous selection. */
final class ScreenshotPreview extends View {
    private final Bitmap bitmap;
    private final Rect selection, original = new Rect();
    private final RectF picture = new RectF();
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
    private final Runnable changed;
    private float scale;
    private int startX, startY, fixedX, fixedY;
    private boolean moving, resizing, dragging;

    ScreenshotPreview(Context context, Bitmap bitmap, Rect selection, Runnable changed) {
        super(context);
        this.bitmap = bitmap;
        this.selection = selection;
        this.changed = changed;
        setContentDescription("Область снимка");
        setFilterTouchesWhenObscured(true);
    }
    void selectAll() {
        selection.set(0,0,bitmap.getWidth(),bitmap.getHeight());
        invalidate();
        changed.run();
    }
    @Override protected void onSizeChanged(int w,int h,int oldW,int oldH) {
        scale = Math.min((float)w/bitmap.getWidth(), (float)h/bitmap.getHeight());
        float dx = (w-scale*bitmap.getWidth())/2, dy = (h-scale*bitmap.getHeight())/2;
        picture.set(dx,dy,w-dx,h-dy);
    }
    @Override protected void onDraw(Canvas c) {
        c.drawColor(0xff0e1014);
        paint.setColor(0xffffffff);
        paint.setStyle(Paint.Style.FILL);
        c.drawBitmap(bitmap,null,picture,paint);
        if (selection.isEmpty()) return;
        RectF box = new RectF(picture.left+selection.left*scale, picture.top+selection.top*scale,
                picture.left+selection.right*scale, picture.top+selection.bottom*scale);
        c.save(); c.clipRect(picture); c.clipOutRect(box); c.drawColor(0xab080c10); c.restore();
        paint.setColor(PanelUi.ACCENT);
        paint.setStrokeWidth(PanelUi.dp(getContext(),2));
        paint.setStyle(Paint.Style.STROKE);
        c.drawRect(box,paint);
        paint.setStyle(Paint.Style.FILL);
        for (float x : new float[]{box.left,box.right}) {
            for (float y : new float[]{box.top,box.bottom}) {
                c.drawCircle(x,y,PanelUi.dp(getContext(),5),paint);
            }
        }
    }
    private static int clamp(int n,int low,int high){return Math.max(low,Math.min(high,n));}
    @Override public boolean onTouchEvent(MotionEvent e) {
        int action=e.getActionMasked();
        if (action==MotionEvent.ACTION_DOWN && !picture.contains(e.getX(),e.getY())) return false;
        int x=clamp(Math.round((e.getX()-picture.left)/scale),0,bitmap.getWidth());
        int y=clamp(Math.round((e.getY()-picture.top)/scale),0,bitmap.getHeight());
        switch (action) {
            case MotionEvent.ACTION_DOWN:
                original.set(selection);
                startX=x; startY=y; fixedX=x; fixedY=y; dragging=true;
                int reach=Math.round(PanelUi.dp(getContext(),22)/scale);
                boolean left=Math.abs(x-selection.left)<Math.abs(x-selection.right);
                boolean top=Math.abs(y-selection.top)<Math.abs(y-selection.bottom);
                resizing=!selection.isEmpty() && Math.abs(x-(left?selection.left:selection.right))<=reach
                        && Math.abs(y-(top?selection.top:selection.bottom))<=reach;
                moving=!resizing && selection.contains(x,y);
                if (resizing) {fixedX=left?selection.right:selection.left; fixedY=top?selection.bottom:selection.top;}
                getParent().requestDisallowInterceptTouchEvent(true);
                return true;
            case MotionEvent.ACTION_MOVE:
            case MotionEvent.ACTION_UP:
                if (!dragging) return false;
                if (moving) {
                    selection.set(original);
                    selection.offset(clamp(x-startX,-original.left,bitmap.getWidth()-original.right),
                            clamp(y-startY,-original.top,bitmap.getHeight()-original.bottom));
                } else selection.set(Math.min(fixedX,x),Math.min(fixedY,y),Math.max(fixedX,x),Math.max(fixedY,y));
                if (action==MotionEvent.ACTION_UP) {
                    if (selection.width()<12 || selection.height()<12) selection.set(original);
                    endGesture(); performClick();
                }
                invalidate();
                return true;
            case MotionEvent.ACTION_CANCEL:
                selection.set(original); endGesture(); invalidate(); return true;
            default: return true;
        }
    }
    private void endGesture() {
        dragging=false;
        getParent().requestDisallowInterceptTouchEvent(false);
        changed.run();
    }
    @Override public boolean performClick(){super.performClick();return true;}
}
