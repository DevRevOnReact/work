package dev.blyadota.jobpilot.assistant;

import android.content.Context;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

final class PanelUi {
    static final int BG=0xff141b21, CARD=0xff243038, TEXT=0xfff3f6f7, MUTED=0xffb4c2c8, ACCENT=0xff8ee4d0, PRIMARY=0xff14665b;
    private PanelUi() {}
    static int dp(Context c, int n) { return Math.round(n*c.getResources().getDisplayMetrics().density); }
    static GradientDrawable shape(Context c,int color,int radius) {
        GradientDrawable d=new GradientDrawable(); d.setColor(color); d.setCornerRadius(dp(c,radius)); return d;
    }
    static LinearLayout column(Context c,int padding) {
        LinearLayout v=new LinearLayout(c);
        v.setOrientation(LinearLayout.VERTICAL); int p=dp(c,padding); v.setPadding(p,p,p,p); return v;
    }
    static TextView text(Context c,String s,int size,int color,boolean bold) {
        TextView t=new TextView(c); t.setText(s); t.setTextSize(size); t.setTextColor(color);
        if(bold) t.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL)); return t;
    }
    static Button button(Context c,String s,boolean primary,View.OnClickListener listener) {
        Button b=new Button(c); b.setAllCaps(false); b.setText(s); b.setTextSize(15); b.setTextColor(primary?TEXT:ACCENT);
        b.setBackground(shape(c,primary?PRIMARY:CARD,12)); b.setMinHeight(dp(c,48));
        b.setPadding(dp(c,14),dp(c,7),dp(c,14),dp(c,7)); b.setOnClickListener(listener);
        b.setFilterTouchesWhenObscured(true); return b;
    }
    static void add(LinearLayout parent,View child,int gap) {
        LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,-2); p.topMargin=dp(parent.getContext(),gap); parent.addView(child,p);
    }
}
