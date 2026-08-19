package com.xvpn.android;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.RadialGradient;
import android.graphics.Shader;
import android.graphics.SweepGradient;
import android.view.MotionEvent;
import android.view.View;
import android.view.animation.DecelerateInterpolator;
import android.view.animation.LinearInterpolator;
import android.view.animation.OvershootInterpolator;

/**
 * Connection control with a restrained crystalline response. The looping
 * motion is decorative only; every state remains distinct when Android's
 * animator scale is disabled.
 */
final class ConnectOrbView extends View {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.DITHER_FLAG);
    private final boolean dark;
    private boolean pressed;
    private float phase;
    private float statePulse;
    private ValueAnimator loop;
    private ValueAnimator transition;
    private int connectionState = CoreState.STOPPED;
    private Shader glowShader;
    private Shader bodyShader;
    private Shader refractionShader;
    private Shader sweepShader;
    private int shaderState = -1;
    private int shaderWidth;

    ConnectOrbView(Context context, boolean dark) {
        super(context);
        this.dark = dark;
        setClickable(true);
        setFocusable(true);
        updateAccessibilityLabel();
        setOnTouchListener((v, event) -> {
            if (!motionEnabled()) return false;
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    pressed = true;
                    animate().cancel();
                    animate().scaleX(.962f).scaleY(.962f).translationY(dp(2.1f))
                            .setDuration(95).setInterpolator(new DecelerateInterpolator()).start();
                    invalidate();
                    break;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    pressed = false;
                    animate().cancel();
                    animate().scaleX(1f).scaleY(1f).translationY(0f).setDuration(270)
                            .setInterpolator(new OvershootInterpolator(.58f)).start();
                    invalidate();
                    break;
                default:
                    break;
            }
            return false;
        });
    }

    void setConnectionState(int state) {
        if (connectionState == state) return;
        connectionState = state;
        shaderState = -1;
        updateAccessibilityLabel();
        if (transition != null) transition.cancel();
        if (motionEnabled() && isAttachedToWindow()) {
            transition = ValueAnimator.ofFloat(0f, 1f);
            transition.setDuration(state == CoreState.RUNNING ? 540 : 360);
            transition.setInterpolator(new DecelerateInterpolator());
            transition.addUpdateListener(a -> {
                float t = (float) a.getAnimatedValue();
                statePulse = (float) Math.sin(Math.PI * t);
                invalidate();
            });
            transition.start();
        } else {
            statePulse = 0f;
            invalidate();
        }
    }

    private boolean motionEnabled() {
        return ValueAnimator.areAnimatorsEnabled();
    }

    private void updateAccessibilityLabel() {
        setContentDescription("VPN " + stateLabel());
    }

    @Override protected void onAttachedToWindow() {
        super.onAttachedToWindow();
        if (!motionEnabled()) return;
        loop = ValueAnimator.ofFloat(0f, 1f);
        loop.setDuration(2800);
        loop.setRepeatCount(ValueAnimator.INFINITE);
        loop.setInterpolator(new LinearInterpolator());
        loop.addUpdateListener(a -> { phase = (float) a.getAnimatedValue(); invalidate(); });
        loop.start();
    }

    @Override protected void onDetachedFromWindow() {
        if (loop != null) { loop.cancel(); loop = null; }
        if (transition != null) { transition.cancel(); transition = null; }
        super.onDetachedFromWindow();
    }

    @Override protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
        int desired = Math.round(dp(308));
        int size = Math.min(resolveSize(desired, widthMeasureSpec), resolveSize(desired, heightMeasureSpec));
        setMeasuredDimension(size, size);
    }

    @Override protected void onSizeChanged(int w, int h, int oldw, int oldh) {
        shaderState = -1;
    }

    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float width = getWidth();
        float cx = width / 2f, cy = getHeight() / 2f;
        float radius = width * .350f;
        boolean active = connectionState == CoreState.RUNNING;
        boolean switching = connectionState == CoreState.SWITCHING;
        boolean moving = connectionState == CoreState.STARTING || switching || connectionState == CoreState.STOPPING;
        boolean error = connectionState == CoreState.ERROR;
        float wave = motionEnabled() ? (.5f + .5f * (float) Math.sin(phase * Math.PI * 2f)) : .35f;
        float glowRadius = radius + dp(32f);
        ensureShaders(cx, cy, radius, glowRadius);

        paint.setStyle(Paint.Style.FILL);
        paint.setShader(glowShader);
        paint.setAlpha(Math.min(255, 205 + Math.round(wave * (active ? 34f : 18f) + statePulse * 16f)));
        canvas.drawCircle(cx, cy, glowRadius, paint);
        paint.setAlpha(255);
        paint.setShader(null);

        // Frosted body: a cool diagonal crystal in light mode and a deep,
        // still-readable glass surface in dark mode.
        paint.setShader(bodyShader);
        canvas.drawCircle(cx, cy, radius * (pressed ? .991f : 1f), paint);
        paint.setShader(null);

        // Soft internal refraction instead of a software blur, so the edge
        // stays clean on xxhdpi and xxxhdpi screens.
        paint.setShader(refractionShader);
        canvas.drawCircle(cx, cy, radius - dp(2), paint);
        paint.setShader(null);

        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeCap(Paint.Cap.ROUND);
        paint.setStrokeWidth(dp(5.0f + statePulse * 1.1f));
        paint.setColor(active ? (dark ? 0x705FD6B0 : 0x5222B78B)
                : switching ? (dark ? 0x4E9B79FF : 0x3C8B70FF)
                : error ? (dark ? 0x42FF7B86 : 0x30D54848)
                : (dark ? 0x365E87FF : 0x295F87FF));
        canvas.drawCircle(cx, cy, radius + dp(4), paint);

        paint.setStrokeWidth(dp(1.55f));
        paint.setColor(active ? (dark ? 0xE55FD6B0 : 0xB522B78B)
                : error ? (dark ? 0xC7FF8C95 : 0x90D54848)
                : (dark ? 0xA07396FF : 0x806A8CFF));
        canvas.drawCircle(cx, cy, radius + dp(1.1f), paint);

        // Moving states get an unambiguous progress sweep. Connected keeps a
        // much slower glint, giving life without looking continuously busy.
        float start = (connectionState == CoreState.STOPPING ? -1f : 1f) * phase * 360f - 92f;
        if (moving || active) {
            paint.setStrokeWidth(dp(moving ? 2.8f : 1.35f));
            paint.setShader(sweepShader);
            canvas.drawArc(cx-radius-dp(2.5f), cy-radius-dp(2.5f), cx+radius+dp(2.5f), cy+radius+dp(2.5f),
                    start, moving ? 104f : 72f, false, paint);
            paint.setShader(null);
        }

        // One controlled specular edge gives the semi-transparent ice body
        // definition in both themes without an over-bright halo.
        paint.setStrokeWidth(dp(1.15f));
        paint.setColor(dark ? 0x42FFFFFF : 0x9AFFFFFF);
        canvas.drawArc(cx-radius+dp(9), cy-radius+dp(9), cx+radius-dp(9), cy+radius-dp(9), 205f, 105f, false, paint);

        float iconCy = cy - dp(11);
        int ink = error ? (dark ? 0xFFFFA2AA : 0xFFD54848)
                : active ? (dark ? 0xFFE8FFF8 : 0xFF178B6D)
                : switching ? (dark ? 0xFFE9E2FF : 0xFF7866D4)
                : (dark ? 0xFFD5DEFF : 0xFF8EA3D1);
        paint.setColor(ink);
        paint.setStrokeWidth(dp(5.0f));
        canvas.drawArc(cx - dp(24), iconCy - dp(25), cx + dp(24), iconCy + dp(23), -45, 270, false, paint);
        canvas.drawLine(cx, iconCy - dp(32), cx, iconCy - dp(7), paint);

        paint.setStyle(Paint.Style.FILL);
        paint.setTextAlign(Paint.Align.CENTER);
        paint.setFakeBoldText(true);
        paint.setTextSize(dp(16.2f));
        paint.setColor(error ? (dark ? 0xFFFFA2AA : 0xFFD54848)
                : active ? (dark ? 0xFFE9FFF9 : 0xFF14795F)
                : switching ? (dark ? 0xFFE8E2FF : 0xFF6757BE)
                : (dark ? 0xFFE9EDF7 : 0xFF63728E));
        canvas.drawText(stateLabel(), cx, cy + dp(58), paint);
        paint.setFakeBoldText(false);
    }

    private void ensureShaders(float cx, float cy, float radius, float glowRadius) {
        if (shaderState == connectionState && shaderWidth == getWidth() && glowShader != null) return;
        shaderState = connectionState;
        shaderWidth = getWidth();
        boolean active = connectionState == CoreState.RUNNING;
        boolean switching = connectionState == CoreState.SWITCHING;
        boolean error = connectionState == CoreState.ERROR;
        int outerRing;
        if (error) outerRing = dark ? 0x8AFF7B86 : 0x68D54848;
        else if (active) outerRing = dark ? 0xB05FD6B0 : 0x9022B78B;
        else if (switching) outerRing = dark ? 0x9A9B7CFF : 0x788B70FF;
        else outerRing = dark ? 0x7A648BFF : 0x5D6286FF;
        glowShader = new RadialGradient(cx, cy, glowRadius,
                new int[]{0x00000000, 0x00000000, outerRing,
                        active ? (dark ? 0x385FD6B0 : 0x2C22B78B) : (dark ? 0x3A785DEB : 0x298A72F3),
                        active ? (dark ? 0x145FD6B0 : 0x1022B78B) : (dark ? 0x12648BFF : 0x0C648BFF), 0x00000000},
                new float[]{0f, .59f, .72f, .805f, .91f, 1f}, Shader.TileMode.CLAMP);
        int bodyA = dark ? (active ? 0xFF18332F : 0xFF1C2533) : (active ? 0xFFF3FFFB : 0xFFFFFFFF);
        int bodyB = dark ? (active ? 0xFF111F20 : 0xFF111720) : (active ? 0xFFF9FCFF : 0xFFF8FAFE);
        bodyShader = new LinearGradient(cx - radius, cy - radius, cx + radius, cy + radius,
                new int[]{bodyA, bodyB}, null, Shader.TileMode.CLAMP);
        refractionShader = new RadialGradient(cx - radius * .34f, cy - radius * .38f, radius * 1.36f,
                new int[]{active ? (dark ? 0x245FD6B0 : 0x36FFFFFF) : (dark ? 0x245E8DFF : 0x30FFFFFF),
                        active ? (dark ? 0x1450B493 : 0x1822B78B) : (dark ? 0x0F9C7CFF : 0x129B7CFF), 0x00000000},
                new float[]{0f, .52f, 1f}, Shader.TileMode.CLAMP);
        int[] sweepColors = active
                ? new int[]{0x005FD6B0, dark ? 0xF05FD6B0 : 0xD522B78B, 0x005FD6B0}
                : switching
                ? new int[]{0x009B7CFF, dark ? 0xE39B7CFF : 0xC18B70FF, 0x005D82FF}
                : new int[]{0x005D82FF, dark ? 0xD86FDFFF : 0xB85CCFFF, 0x005D82FF};
        sweepShader = new SweepGradient(cx, cy, sweepColors, null);
    }

    private String stateLabel() {
        switch (connectionState) {
            case CoreState.STARTING: return "正在连接…";
            case CoreState.SWITCHING: return "正在切换…";
            case CoreState.RUNNING: return "点击断开";
            case CoreState.STOPPING: return "正在断开…";
            case CoreState.ERROR: return "重新连接";
            default: return "点击连接";
        }
    }

    private float dp(float value) { return value * getResources().getDisplayMetrics().density; }
}
