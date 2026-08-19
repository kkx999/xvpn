package com.xvpn.android;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.view.View;

/** Small theme-native chevron used instead of font glyph arrows. */
final class ChevronView extends View {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);

    ChevronView(Context context, int color) {
        super(context);
        paint.setColor(color);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeCap(Paint.Cap.ROUND);
        paint.setStrokeJoin(Paint.Join.ROUND);
        paint.setStrokeWidth(dp(1.8f));
    }

    void setExpanded(boolean expanded) {
        setRotation(expanded ? 180f : 0f);
        invalidate();
    }

    void animateExpanded(boolean expanded) {
        if (!ValueAnimator.areAnimatorsEnabled()) {
            animate().cancel();
            setExpanded(expanded);
            return;
        }
        animate().rotation(expanded ? 180f : 0f)
                .setDuration(180)
                .setInterpolator(new android.view.animation.AccelerateDecelerateInterpolator())
                .start();
    }

    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float cx = getWidth() / 2f;
        float cy = getHeight() / 2f;
        float dx = dp(4.2f);
        float dy = dp(2.8f);
        canvas.drawLine(cx - dx, cy - dy, cx, cy + dy, paint);
        canvas.drawLine(cx, cy + dy, cx + dx, cy - dy, paint);
    }

    private float dp(float v) { return v * getResources().getDisplayMetrics().density; }
}
