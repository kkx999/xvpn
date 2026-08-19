package com.xvpn.android;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.View;
import android.view.animation.DecelerateInterpolator;

/** A small, optically-centered refresh control drawn without font glyphs. */
final class RefreshActionView extends View {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final boolean dark;
    private final int accent;
    private final int soft;
    private ValueAnimator spinner;
    private final RectF arcBounds = new RectF();

    RefreshActionView(Context context, boolean dark, int accent, int soft) {
        super(context);
        this.dark = dark;
        this.accent = accent;
        this.soft = soft;
        setClickable(true);
        setFocusable(true);
        setContentDescription("刷新节点");
    }

    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float cx = getWidth() / 2f;
        float cy = getHeight() / 2f;
        float halo = dp(15.5f);

        paint.setStyle(Paint.Style.FILL);
        paint.setColor(soft);
        paint.setAlpha(dark ? 165 : 205);
        canvas.drawCircle(cx, cy, halo, paint);
        paint.setAlpha(255);

        float r = dp(8.2f);
        arcBounds.set(cx-r,cy-r,cx+r,cy+r);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeCap(Paint.Cap.ROUND);
        paint.setStrokeJoin(Paint.Join.ROUND);
        paint.setStrokeWidth(dp(2.15f));
        paint.setColor(accent);
        canvas.drawArc(arcBounds, -72f, 278f, false, paint);

        // Small arrow head at the end of the arc.
        float ax = cx + r * .39f;
        float ay = cy - r * .92f;
        canvas.drawLine(ax, ay, ax - dp(4.0f), ay + dp(.5f), paint);
        canvas.drawLine(ax, ay, ax - dp(.8f), ay + dp(3.9f), paint);
        paint.setStyle(Paint.Style.FILL);
    }

    void startRefreshMotion() {
        if (spinner != null && spinner.isRunning()) return;
        if (!ValueAnimator.areAnimatorsEnabled()) { setRotation(0f); return; }
        animate().cancel();
        setRotation(0f);
        animate().scaleX(.94f).scaleY(.94f).setDuration(120).start();
        spinner = ValueAnimator.ofFloat(0f, 360f);
        spinner.setDuration(760);
        spinner.setRepeatCount(ValueAnimator.INFINITE);
        spinner.setInterpolator(new android.view.animation.LinearInterpolator());
        spinner.addUpdateListener(a -> setRotation((float) a.getAnimatedValue()));
        spinner.start();
    }

    void stopRefreshMotion() {
        if (spinner != null) {
            spinner.cancel();
            spinner = null;
        }
        animate().cancel();
        if (!ValueAnimator.areAnimatorsEnabled()) { setRotation(0f); setScaleX(1f); setScaleY(1f); return; }
        animate().rotation(360f).scaleX(1f).scaleY(1f).setDuration(230)
                .setInterpolator(new DecelerateInterpolator())
                .withEndAction(() -> setRotation(0f)).start();
    }

    @Override protected void onDetachedFromWindow() {
        if (spinner != null) {
            spinner.cancel();
            spinner = null;
        }
        super.onDetachedFromWindow();
    }

    private float dp(float v) { return v * getResources().getDisplayMetrics().density; }
}
