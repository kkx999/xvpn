package com.xvpn.android;

import android.graphics.Canvas;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Shader;
import android.content.Context;
import android.widget.FrameLayout;

/**
 * Lightweight edge ambience for important surfaces. It intentionally avoids the
 * large grey Android elevation shadow and keeps the glow close to the card edge.
 */
final class AmbientGlowFrameLayout extends FrameLayout {
    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint aura = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.DITHER_FLAG);
    private final Paint edge = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.DITHER_FLAG);
    private final boolean dark;
    private final float radiusDp;
    private final float intensity;
    private final RectF outer = new RectF();
    private final RectF body = new RectF();
    private Shader gradient;
    private float radius;

    AmbientGlowFrameLayout(Context context, boolean dark, float radiusDp, float intensity) {
        super(context);
        this.dark = dark;
        this.radiusDp = radiusDp;
        this.intensity = Math.max(.35f, Math.min(1f, intensity));
        setWillNotDraw(false);
        setClipToPadding(false);
        setClipChildren(false);
    }

    @Override protected void onSizeChanged(int w, int h, int oldw, int oldh) {
        float outerInset = dp(2.5f);
        float bodyInset = dp(4.5f);
        outer.set(outerInset, outerInset, w - outerInset, h - outerInset);
        body.set(bodyInset, bodyInset, w - bodyInset, h - bodyInset);
        radius = dp(radiusDp);
        gradient = new LinearGradient(outer.left, outer.top, outer.right, outer.bottom,
                new int[]{dark ? 0xA05D82FF : 0x805D82FF, dark ? 0x788C70F8 : 0x669C84F8, dark ? 0x945D82FF : 0x765D82FF},
                new float[]{0f, .58f, 1f}, Shader.TileMode.CLAMP);
    }

    @Override protected void onDraw(Canvas canvas) {
        // Keep the visible white/dark surface broad. The aura is painted as a
        // dedicated blue-purple ring outside the body instead of stealing width
        // from the card itself.
        // Two native anti-aliased rings produce a smooth falloff without the
        // software BlurMaskFilter fringe that looked furry on high-density OLEDs.
        aura.setStyle(Paint.Style.STROKE);
        aura.setStrokeWidth(dp(4.6f));
        aura.setShader(gradient);
        aura.setAlpha(Math.round((dark ? 35 : 28) * intensity));
        canvas.drawRoundRect(outer, radius + dp(.8f), radius + dp(.8f), aura);
        aura.setStrokeWidth(dp(2.4f));
        aura.setAlpha(Math.round((dark ? 78 : 58) * intensity));
        canvas.drawRoundRect(outer, radius, radius, aura);
        aura.setShader(null);

        fill.setStyle(Paint.Style.FILL);
        fill.setColor(dark ? 0xFF131821 : 0xFFFDFEFF);
        canvas.drawRoundRect(body, radius, radius, fill);

        // A clean blue-purple edge is always visible around the white body.
        edge.setStyle(Paint.Style.STROKE);
        edge.setStrokeWidth(dp(1.05f));
        edge.setShader(gradient);
        edge.setAlpha(Math.round((dark ? 178 : 142) * intensity));
        canvas.drawRoundRect(body, radius, radius, edge);
        edge.setShader(null);

        super.onDraw(canvas);
    }

    private float dp(float v) {
        return v * getResources().getDisplayMetrics().density;
    }
}
