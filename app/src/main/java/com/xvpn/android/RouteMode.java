package com.xvpn.android;

enum RouteMode {
    SMART("smart", "智能分流"),
    GLOBAL("global", "全局代理");

    final String key;
    final String label;

    RouteMode(String key, String label) {
        this.key = key;
        this.label = label;
    }

    static RouteMode fromKey(String key) {
        return "global".equals(key) ? GLOBAL : SMART;
    }
}
