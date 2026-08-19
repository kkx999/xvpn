package com.xvpn.android;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

public final class SingBoxConfigBuilderTest {
    private static final String UUID = "11111111-1111-4111-8111-111111111111";
    private static final RuleSetInstaller.Paths RULES =
            new RuleSetInstaller.Paths("/rules/geosite-cn.srs", "/rules/geoip-cn.srs");

    @Test public void allSupportedProtocolsBuildInSmartAndGlobalModes() throws Exception {
        for (Sample sample : samples()) {
            for (RouteMode mode : RouteMode.values()) {
                NodeCatalog.Node node = new NodeCatalog.Node();
                node.id = 1;
                node.name = sample.type;
                node.protocol = sample.type;
                node.config = sample.uri;

                JSONObject root = SingBoxConfigBuilder.build(node, mode, RULES);
                JSONObject proxy = root.getJSONArray("outbounds").getJSONObject(0);
                assertEquals(sample.type, proxy.getString("type"));
                assertEquals("proxy", proxy.getString("tag"));
                assertEquals("example.com", proxy.getString("server"));
                assertEquals(443, SingBoxConfigBuilder.endpoint(node).port);
                assertBaseNetworkProfile(root);
                assertRoutingMode(root, mode);
            }
        }
    }

    @Test public void panelMigrationChangesOnlyRetiredBuiltInAddress() {
        assertEquals(ApiClient.DEFAULT_PANEL_BASE, ApiClient.migratePanelBase(""));
        assertEquals(ApiClient.DEFAULT_PANEL_BASE,
                ApiClient.migratePanelBase("https://xx.666101.xyz/api/v1/"));
        assertEquals(ApiClient.DEFAULT_PANEL_BASE,
                ApiClient.migratePanelBase("https://xvpn666101.xyz/"));
        assertEquals("https://panel.example.com",
                ApiClient.migratePanelBase("https://panel.example.com/"));
    }

    @Test public void stableReleaseWinsOverMatchingPrerelease() {
        assertTrue(AppUpdateChecker.compareVersions("1.0.0", "1.0.0-rc1") > 0);
        assertTrue(AppUpdateChecker.compareVersions("1.0.1", "1.0.0") > 0);
        assertEquals(0, AppUpdateChecker.compareVersions("v1.0.0", "1.0.0"));
    }

    private static void assertBaseNetworkProfile(JSONObject root) throws Exception {
        JSONObject tun = root.getJSONArray("inbounds").getJSONObject(0);
        assertEquals("tun", tun.getString("type"));
        assertEquals(1400, tun.getInt("mtu"));
        assertEquals("mixed", tun.getString("stack"));
        assertEquals(1, tun.getJSONArray("address").length());
        assertTrue(tun.getJSONArray("address").getString(0).contains("172.19.0.1/30"));

        JSONObject dns = root.getJSONObject("dns");
        assertEquals("ipv4_only", dns.getString("strategy"));
        JSONArray servers = dns.getJSONArray("servers");
        assertEquals("local-dns", servers.getJSONObject(0).getString("tag"));
        assertFalse(servers.getJSONObject(0).has("detour"));
        assertEquals("proxy", servers.getJSONObject(1).getString("detour"));

        JSONObject quicReject = findRule(root.getJSONObject("route").getJSONArray("rules"),
                "network", "udp");
        assertNotNull(quicReject);
        assertEquals(443, quicReject.getInt("port"));
        assertEquals("reject", quicReject.getString("action"));
    }

    private static void assertRoutingMode(JSONObject root, RouteMode mode) throws Exception {
        JSONObject route = root.getJSONObject("route");
        assertEquals("proxy", route.getString("final"));
        JSONArray rules = route.getJSONArray("rules");
        boolean hasGeosite = hasRuleSet(rules, "geosite-cn");
        boolean hasGeoip = hasRuleSet(rules, "geoip-cn");
        if (mode == RouteMode.SMART) {
            assertTrue(hasGeosite);
            assertTrue(hasGeoip);
        } else {
            assertFalse(hasGeosite);
            assertFalse(hasGeoip);
        }
    }

    private static JSONObject findRule(JSONArray rules, String key, String value) throws Exception {
        for (int i = 0; i < rules.length(); i++) {
            JSONObject rule = rules.getJSONObject(i);
            if (value.equals(rule.optString(key))) return rule;
        }
        return null;
    }

    private static boolean hasRuleSet(JSONArray rules, String tag) throws Exception {
        for (int i = 0; i < rules.length(); i++) {
            JSONArray values = rules.getJSONObject(i).optJSONArray("rule_set");
            if (values == null) continue;
            for (int j = 0; j < values.length(); j++) if (tag.equals(values.getString(j))) return true;
        }
        return false;
    }

    private static List<Sample> samples() {
        List<Sample> values = new ArrayList<>();
        values.add(new Sample("vless", "vless://" + UUID
                + "@example.com:443?security=tls&sni=example.com&type=ws&host=example.com&path=%2Fws"));
        values.add(new Sample("trojan",
                "trojan://password@example.com:443?security=tls&sni=example.com&type=grpc&serviceName=xvpn"));

        String vmess = new JSONObject()
                .put("v", "2").put("add", "example.com").put("port", "443")
                .put("id", UUID).put("aid", "0").put("scy", "auto")
                .put("net", "ws").put("host", "example.com").put("path", "/ws")
                .put("tls", "tls").put("sni", "example.com").toString();
        values.add(new Sample("vmess", "vmess://" + Base64.getEncoder()
                .encodeToString(vmess.getBytes(StandardCharsets.UTF_8))));

        values.add(new Sample("shadowsocks",
                "ss://YWVzLTI1Ni1nY206cGFzc3dvcmQ=@example.com:443#XVPN"));
        values.add(new Sample("hysteria2",
                "hysteria2://password@example.com:443?sni=example.com&insecure=1&obfs=salamander&obfs-password=test"));
        values.add(new Sample("tuic", "tuic://" + UUID
                + ":password@example.com:443?sni=example.com&allow_insecure=1&congestion_control=bbr"));
        values.add(new Sample("anytls",
                "anytls://password@example.com:443?sni=example.com&insecure=1"));
        return values;
    }

    private static final class Sample {
        final String type;
        final String uri;
        Sample(String type, String uri) { this.type = type; this.uri = uri; }
    }
}
