package com.xvpn.android;

import android.content.Context;
import android.graphics.drawable.BitmapDrawable;
import android.widget.ImageView;

/**
 * Density-aware in-app rendering of the approved XVPN ice-crystal mark.
 *
 * The artwork is transparent (there is no launcher plate), so the same mark can
 * sit naturally on Aurora, cards, sheets and both themes.  Supplying native
 * density variants also avoids repeatedly shrinking the 1024 px launcher art.
 */
public final class BrandMarkView extends ImageView {
    public BrandMarkView(Context context, boolean dark) {
        super(context);
        setImageResource(R.drawable.xvpn_brand_mark);
        setScaleType(ScaleType.CENTER_INSIDE);
        setAdjustViewBounds(true);
        setContentDescription("XVPN");
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);

        if (getDrawable() instanceof BitmapDrawable) {
            BitmapDrawable bitmap = (BitmapDrawable) getDrawable();
            bitmap.setAntiAlias(true);
            bitmap.setFilterBitmap(true);
            bitmap.setDither(true);
        }
    }
}
