package com.xvpn.android;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.view.View;

/** Quiet selection marker for node rows; avoids a loud system-looking check glyph. */
final class SelectionDotView extends View {
    private final Paint ring = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint dot = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final int accent;
    private final int muted;
    private boolean active;

    SelectionDotView(Context context, int accent, int muted, boolean active) {
        super(context);
        this.accent = accent;
        this.muted = muted;
        this.active = active;
        ring.setStyle(Paint.Style.STROKE);
        ring.setStrokeWidth(dp(1.6f));
        dot.setStyle(Paint.Style.FILL);
    }

    void setActive(boolean value) {
        if (active == value) return;
        active = value;
        if (!ValueAnimator.areAnimatorsEnabled()) {
            animate().cancel();
            setScaleX(1f);
            setScaleY(1f);
            setAlpha(1f);
            invalidate();
            return;
        }
        animate().scaleX(.88f).scaleY(.88f).alpha(.7f).setDuration(80).withEndAction(() -> {
            invalidate();
            animate().scaleX(1f).scaleY(1f).alpha(1f).setDuration(130)
                    .setInterpolator(new android.view.animation.OvershootInterpolator(1.05f)).start();
        }).start();
    }

    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float cx = getWidth() / 2f;
        float cy = getHeight() / 2f;
        ring.setColor(active ? accent : muted);
        ring.setAlpha(active ? 210 : 92);
        canvas.drawCircle(cx, cy, dp(7f), ring);
        if (active) {
            dot.setColor(accent);
            dot.setAlpha(235);
            canvas.drawCircle(cx, cy, dp(3.2f), dot);
        }
    }

    private float dp(float v) { return v * getResources().getDisplayMetrics().density; }
}
