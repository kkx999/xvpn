package com.xvpn.android;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RadialGradient;
import android.graphics.Shader;
import android.widget.FrameLayout;

final class AuroraView extends FrameLayout {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final boolean dark;
    private Shader topGlow;
    private Shader sideGlow;
    private float topX, topY, topRadius, sideX, sideY, sideRadius;

    AuroraView(Context context, boolean dark) {
        super(context);
        this.dark = dark;
        setWillNotDraw(false);
    }

    @Override protected void onSizeChanged(int w, int h, int oldw, int oldh) {
        topX=w*.79f;topY=h*.12f;topRadius=w*.72f;
        sideX=w*.16f;sideY=h*.42f;sideRadius=w*.62f;
        topGlow=new RadialGradient(topX,topY,topRadius,dark?0x306487FF:0x35557EFF,0x00000000,Shader.TileMode.CLAMP);
        sideGlow=new RadialGradient(sideX,sideY,sideRadius,dark?0x229B79F8:0x279A7CF8,0x00000000,Shader.TileMode.CLAMP);
    }

    @Override protected void onDraw(Canvas c) {
        super.onDraw(c);
        paint.setShader(topGlow);c.drawCircle(topX,topY,topRadius,paint);
        paint.setShader(sideGlow);c.drawCircle(sideX,sideY,sideRadius,paint);
        paint.setShader(null);
    }
}
