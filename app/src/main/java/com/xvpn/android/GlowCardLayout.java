package com.xvpn.android;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Shader;
import android.widget.LinearLayout;

final class GlowCardLayout extends LinearLayout {
    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint edge = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.DITHER_FLAG);
    private final Paint glow = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.DITHER_FLAG);
    private final boolean dark;
    private final float radiusDp;
    private float pulse;
    private ValueAnimator animator;
    private final RectF body = new RectF();
    private final RectF auraBounds = new RectF();
    private Shader gradient;
    private float radius;

    GlowCardLayout(Context context, boolean dark, float radiusDp) {
        super(context);
        this.dark = dark;
        this.radiusDp = radiusDp;
        setWillNotDraw(false);
    }

    void setContentPadding(int left, int top, int right, int bottom) {
        setPadding(left, top, right, bottom);
    }

    @Override protected void onAttachedToWindow() {
        super.onAttachedToWindow();
        if (!ValueAnimator.areAnimatorsEnabled()) return;
        animator = ValueAnimator.ofFloat(0f, 1f, 0f);
        animator.setDuration(3400);
        animator.setRepeatCount(ValueAnimator.INFINITE);
        animator.addUpdateListener(a -> { pulse = (float) a.getAnimatedValue(); invalidate(); });
        animator.start();
    }

    @Override protected void onSizeChanged(int w, int h, int oldw, int oldh) {
        float fillInset = dp(4.8f);
        float glowInset = dp(2.6f);
        body.set(fillInset, fillInset, w - fillInset, h - fillInset);
        auraBounds.set(glowInset, glowInset, w - glowInset, h - glowInset);
        radius = dp(radiusDp);
        gradient = new LinearGradient(body.left, body.top, body.right, body.bottom,
                new int[]{dark ? 0xA05D82FF : 0x805D82FF, dark ? 0x788C70F8 : 0x669C84F8, dark ? 0x945D82FF : 0x765D82FF},
                new float[]{0f, .56f, 1f}, Shader.TileMode.CLAMP);
    }

    @Override protected void onDetachedFromWindow() {
        if (animator != null) { animator.cancel(); animator = null; }
        super.onDetachedFromWindow();
    }

    @Override protected void onDraw(Canvas canvas) {
        // Native anti-aliased gradient rings keep the edge crisp on high-density
        // screens. The old software blur produced a furry fringe on light cards.
        glow.setStyle(Paint.Style.STROKE);
        glow.setShader(gradient);
        glow.setStrokeWidth(dp(5.4f + pulse * .6f));
        glow.setAlpha(Math.round((dark ? 34 : 27) + pulse * 7f));
        canvas.drawRoundRect(auraBounds, radius + dp(1f), radius + dp(1f), glow);
        glow.setStrokeWidth(dp(2.5f));
        glow.setAlpha(Math.round((dark ? 78 : 58) + pulse * 9f));
        canvas.drawRoundRect(auraBounds, radius, radius, glow);
        glow.setShader(null);

        fill.setStyle(Paint.Style.FILL);
        fill.setColor(dark ? 0xFF131821 : 0xFFFDFEFF);
        canvas.drawRoundRect(body, radius, radius, fill);

        edge.setStyle(Paint.Style.STROKE);
        edge.setStrokeWidth(dp(1.05f));
        edge.setShader(gradient);
        edge.setAlpha(Math.round((dark ? 176 : 142) + pulse * 8f));
        canvas.drawRoundRect(body, radius, radius, edge);
        edge.setShader(null);

        super.onDraw(canvas);
    }

    private float dp(float v) { return v * getResources().getDisplayMetrics().density; }
}
