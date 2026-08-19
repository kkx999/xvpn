package com.xvpn.android;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONObject;

import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Base64;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/** Builds the validated sing-box 1.13 profile consumed by {@link VpnCoreService}. */
final class SingBoxConfigBuilder {
    static final int TUN_MTU = 1400;
    static final String NETWORK_PROFILE = "IPv4 · MTU 1400 · 加密 DNS";
    private static final Set<String> SUPPORTED_TYPES = new HashSet<>(Arrays.asList(
            "vless", "trojan", "vmess", "shadowsocks", "hysteria2", "tuic", "anytls"));

    private SingBoxConfigBuilder() {}

    static JSONObject build(Context context, NodeCatalog.Node node, RouteMode mode) throws Exception {
        return build(node, mode, RuleSetInstaller.ensureInstalled(context));
    }

    static JSONObject build(NodeCatalog.Node node, RouteMode mode, RuleSetInstaller.Paths rulePaths) throws Exception {
        if (node == null || node.config == null || node.config.trim().isEmpty()) {
            throw new IllegalArgumentException("节点配置为空");
        }
        JSONObject proxy = parseOutbound(node.config.trim());
        proxy.put("tag", "proxy");

        JSONObject root = new JSONObject();
        root.put("log", new JSONObject().put("level", "warn").put("timestamp", true));
        root.put("dns", buildDns(mode));

        JSONObject tun = new JSONObject()
                .put("type", "tun")
                .put("tag", "tun-in")
                // IPv4-only is intentional for 1.0.1. It avoids broken AAAA paths and
                // prevents a local carrier IPv6 route from escaping the VPN on devices
                // whose node does not provide a working IPv6 egress.
                .put("address", new JSONArray().put("172.19.0.1/30"))
                .put("mtu", TUN_MTU)
                .put("auto_route", true)
                .put("strict_route", true)
                .put("endpoint_independent_nat", true)
                .put("stack", "mixed");
        root.put("inbounds", new JSONArray().put(tun));
        root.put("outbounds", new JSONArray()
                .put(proxy)
                .put(new JSONObject().put("type", "direct").put("tag", "direct"))
                .put(new JSONObject().put("type", "block").put("tag", "block")));
        root.put("route", buildRoute(mode, rulePaths));
        root.put("experimental", new JSONObject()
                .put("cache_file", new JSONObject().put("enabled", true).put("store_rdrc", true)));
        return root;
    }

    private static JSONObject parseOutbound(String raw) throws Exception {
        if (raw.startsWith("{")) return parseJsonOutbound(raw);
        int separator = raw.indexOf("://");
        if (separator <= 0) throw unsupported("");
        String scheme = aliasType(raw.substring(0, separator).toLowerCase(Locale.ROOT));
        switch (scheme) {
            case "vless": return parseVless(raw);
            case "trojan": return parseTrojan(raw);
            case "vmess": return parseVmess(raw);
            case "shadowsocks": return parseShadowsocks(raw);
            case "hysteria2": return parseHysteria2(raw);
            case "tuic": return parseTuic(raw);
            case "anytls": return parseAnyTls(raw);
            default: throw unsupported(scheme);
        }
    }

    private static JSONObject parseJsonOutbound(String raw) throws Exception {
        JSONObject outbound = new JSONObject(raw);
        String type = aliasType(first(outbound.optString("type"), outbound.optString("protocol"))
                .toLowerCase(Locale.ROOT));
        if (!SUPPORTED_TYPES.contains(type)) throw unsupported(type);
        outbound.put("type", type);
        outbound.put("tag", "proxy");
        outbound.remove("protocol");
        outbound.remove("name");
        outbound.remove("remarks");
        outbound.remove("ps");
        // A Panel node is a single outbound, so references to another local outbound are invalid.
        outbound.remove("detour");
        normalizeJsonPort(outbound);
        endpointFromOutbound(outbound);
        return outbound;
    }

    private static void normalizeJsonPort(JSONObject outbound) throws Exception {
        Object value = outbound.opt("server_port");
        if (value instanceof String && !((String) value).trim().isEmpty()) {
            outbound.put("server_port", parsePort((String) value));
        }
    }

    private static JSONObject parseVless(String raw) throws Exception {
        ParsedUri uri = parseUri(raw, "VLESS");
        if (uri.userInfo.isEmpty()) throw new IllegalArgumentException("VLESS 缺少 UUID");
        JSONObject out = baseOutbound("vless", uri).put("uuid", uri.userInfo);

        String flow = query(uri.q, "flow");
        if (!flow.isEmpty()) out.put("flow", flow);
        String packetEncoding = query(uri.q, "packetEncoding", "packet_encoding");
        if (!packetEncoding.isEmpty()) out.put("packet_encoding", packetEncoding);

        String security = query(uri.q, "security").toLowerCase(Locale.ROOT);
        if ("xtls".equals(security)) security = "tls";
        if (!security.isEmpty() && !"none".equals(security)) {
            if (!"tls".equals(security) && !"reality".equals(security)) {
                throw new IllegalArgumentException("暂不支持该 VLESS security：" + security);
            }
            out.put("tls", buildTls(uri.q, security, true, true));
        }
        JSONObject transport = buildTransport(uri.q, query(uri.q, "type", "network"));
        if (transport != null) out.put("transport", transport);
        return out;
    }

    private static JSONObject parseTrojan(String raw) throws Exception {
        ParsedUri uri = parseUri(raw, "Trojan");
        if (uri.userInfo.isEmpty()) throw new IllegalArgumentException("Trojan 缺少密码");
        JSONObject out = baseOutbound("trojan", uri).put("password", uri.userInfo);
        String security = first(query(uri.q, "security"), "tls").toLowerCase(Locale.ROOT);
        if ("xtls".equals(security)) security = "tls";
        if (!"tls".equals(security) && !"reality".equals(security)) {
            throw new IllegalArgumentException("暂不支持该 Trojan security：" + security);
        }
        out.put("tls", buildTls(uri.q, security, true, true));
        JSONObject transport = buildTransport(uri.q, query(uri.q, "type", "network"));
        if (transport != null) out.put("transport", transport);
        return out;
    }

    private static JSONObject parseVmess(String raw) throws Exception {
        String encoded = raw.substring(raw.indexOf("://") + 3);
        int hash = encoded.indexOf('#');
        if (hash >= 0) encoded = encoded.substring(0, hash);
        JSONObject source;
        try {
            source = new JSONObject(decodeBase64Text(decodeComponent(encoded)));
        } catch (Exception error) {
            throw new IllegalArgumentException("VMess 分享链接不是有效的 Base64 JSON");
        }
        String host = source.optString("add", source.optString("server", "")).trim();
        int port = flexiblePort(source.opt("port"));
        String uuid = source.optString("id", source.optString("uuid", "")).trim();
        validateAddress("VMess", host, port);
        if (uuid.isEmpty()) throw new IllegalArgumentException("VMess 缺少 UUID");

        JSONObject out = new JSONObject()
                .put("type", "vmess")
                .put("tag", "proxy")
                .put("server", host)
                .put("server_port", port)
                .put("uuid", uuid)
                .put("security", first(source.optString("scy"), "auto"));
        int alterId = flexibleInt(source.opt("aid"), 0);
        if (alterId > 0) out.put("alter_id", alterId);

        Map<String, String> q = new LinkedHashMap<>();
        put(q, "host", source.optString("host"));
        put(q, "path", source.optString("path"));
        put(q, "serviceName", source.optString("serviceName", source.optString("service_name")));
        put(q, "sni", source.optString("sni"));
        put(q, "alpn", source.optString("alpn"));
        put(q, "fp", source.optString("fp"));
        put(q, "allowInsecure", source.optString("allowInsecure"));
        String network = first(source.optString("net"), source.optString("network"));
        JSONObject transport = buildTransport(q, network);
        if (transport != null) out.put("transport", transport);

        String tlsMode = source.optString("tls", "").trim().toLowerCase(Locale.ROOT);
        if (!tlsMode.isEmpty() && !"none".equals(tlsMode)) {
            if (!"tls".equals(tlsMode) && !"reality".equals(tlsMode)) tlsMode = "tls";
            out.put("tls", buildTls(q, tlsMode, true, true));
        }
        String packetEncoding = source.optString("packetEncoding", source.optString("packet_encoding", ""));
        if (!packetEncoding.trim().isEmpty()) out.put("packet_encoding", packetEncoding.trim());
        return out;
    }

    private static JSONObject parseShadowsocks(String raw) throws Exception {
        String body = raw.substring(raw.indexOf("://") + 3);
        int hash = body.indexOf('#');
        if (hash >= 0) body = body.substring(0, hash);
        String queryText = "";
        int question = body.indexOf('?');
        if (question >= 0) {
            queryText = body.substring(question + 1);
            body = body.substring(0, question);
        }
        Map<String, String> q = parseQuery(queryText);
        String credential;
        HostPort address;
        int at = body.lastIndexOf('@');
        if (at >= 0) {
            credential = decodeComponent(body.substring(0, at));
            address = parseHostPort(body.substring(at + 1), "Shadowsocks");
        } else {
            String decoded;
            try { decoded = decodeBase64Text(decodeComponent(body)); }
            catch (Exception error) { throw new IllegalArgumentException("Shadowsocks 分享链接格式错误"); }
            at = decoded.lastIndexOf('@');
            if (at <= 0) throw new IllegalArgumentException("Shadowsocks 缺少服务器地址");
            credential = decoded.substring(0, at);
            address = parseHostPort(decoded.substring(at + 1), "Shadowsocks");
        }
        if (!credential.contains(":")) {
            try { credential = decodeBase64Text(credential); }
            catch (Exception error) { throw new IllegalArgumentException("Shadowsocks 加密信息格式错误"); }
        }
        int colon = credential.indexOf(':');
        if (colon <= 0) throw new IllegalArgumentException("Shadowsocks 缺少加密方法或密码");
        String method = credential.substring(0, colon).trim();
        String password = credential.substring(colon + 1);
        if (password.isEmpty()) throw new IllegalArgumentException("Shadowsocks 缺少密码");

        JSONObject out = new JSONObject()
                .put("type", "shadowsocks")
                .put("tag", "proxy")
                .put("server", address.host)
                .put("server_port", address.port)
                .put("method", method)
                .put("password", password);
        String pluginValue = query(q, "plugin");
        if (!pluginValue.isEmpty()) {
            int separator = pluginValue.indexOf(';');
            out.put("plugin", separator < 0 ? pluginValue : pluginValue.substring(0, separator));
            if (separator >= 0 && separator + 1 < pluginValue.length()) {
                out.put("plugin_opts", pluginValue.substring(separator + 1));
            }
        }
        return out;
    }

    private static JSONObject parseHysteria2(String raw) throws Exception {
        ParsedUri uri = parseUri(raw, "Hysteria2");
        String password = first(uri.userInfo, query(uri.q, "auth", "password"));
        if (password.isEmpty()) throw new IllegalArgumentException("Hysteria2 缺少认证密码");
        JSONObject out = baseOutbound("hysteria2", uri).put("password", password);

        int up = positiveInt(query(uri.q, "upmbps", "up_mbps", "up"));
        int down = positiveInt(query(uri.q, "downmbps", "down_mbps", "down"));
        if (up > 0) out.put("up_mbps", up);
        if (down > 0) out.put("down_mbps", down);
        String obfsType = query(uri.q, "obfs");
        String obfsPassword = query(uri.q, "obfs-password", "obfs_password", "obfsPassword");
        if (!obfsType.isEmpty() || !obfsPassword.isEmpty()) {
            if (obfsType.isEmpty()) obfsType = "salamander";
            JSONObject obfs = new JSONObject().put("type", obfsType);
            if (!obfsPassword.isEmpty()) obfs.put("password", obfsPassword);
            out.put("obfs", obfs);
        }
        String multiPort = query(uri.q, "mport", "server_ports");
        if (!multiPort.isEmpty()) {
            String normalized = multiPort.trim();
            if (normalized.matches("[0-9]+-[0-9]+")) normalized = normalized.replace('-', ':');
            out.put("server_ports", new JSONArray().put(normalized));
            out.remove("server_port");
        }
        out.put("tls", buildTls(uri.q, "tls", false, false));
        return out;
    }

    private static JSONObject parseTuic(String raw) throws Exception {
        ParsedUri uri = parseUri(raw, "TUIC");
        String credential = uri.userInfo;
        int separator = credential.indexOf(':');
        String uuid = separator >= 0 ? credential.substring(0, separator) : credential;
        String password = separator >= 0 ? credential.substring(separator + 1) : query(uri.q, "password");
        if (uuid.isEmpty()) throw new IllegalArgumentException("TUIC 缺少 UUID");
        if (password.isEmpty()) throw new IllegalArgumentException("TUIC 缺少密码");
        JSONObject out = baseOutbound("tuic", uri).put("uuid", uuid).put("password", password);
        String congestion = query(uri.q, "congestion_control", "congestion-control", "congestion");
        if (!congestion.isEmpty()) out.put("congestion_control", congestion);
        String relay = query(uri.q, "udp_relay_mode", "udp-relay-mode");
        if (!relay.isEmpty()) out.put("udp_relay_mode", relay);
        String zeroRtt = query(uri.q, "zero_rtt_handshake", "zero-rtt-handshake", "reduce_rtt");
        if (!zeroRtt.isEmpty()) out.put("zero_rtt_handshake", truthy(zeroRtt));
        String heartbeat = query(uri.q, "heartbeat");
        if (!heartbeat.isEmpty()) out.put("heartbeat", heartbeat);
        out.put("tls", buildTls(uri.q, "tls", false, false));
        return out;
    }

    private static JSONObject parseAnyTls(String raw) throws Exception {
        ParsedUri uri = parseUri(raw, "AnyTLS");
        String password = first(uri.userInfo, query(uri.q, "password"));
        if (password.isEmpty()) throw new IllegalArgumentException("AnyTLS 缺少密码");
        JSONObject out = baseOutbound("anytls", uri).put("password", password);
        String checkInterval = query(uri.q, "idle_session_check_interval", "idle-session-check-interval");
        String idleTimeout = query(uri.q, "idle_session_timeout", "idle-session-timeout");
        int minimum = positiveInt(query(uri.q, "min_idle_session", "min-idle-session"));
        if (!checkInterval.isEmpty()) out.put("idle_session_check_interval", checkInterval);
        if (!idleTimeout.isEmpty()) out.put("idle_session_timeout", idleTimeout);
        if (minimum > 0) out.put("min_idle_session", minimum);
        out.put("tls", buildTls(uri.q, "tls", false, true));
        return out;
    }

    private static JSONObject baseOutbound(String type, ParsedUri uri) throws Exception {
        validateAddress(type, uri.host, uri.port);
        return new JSONObject()
                .put("type", type)
                .put("tag", "proxy")
                .put("server", uri.host)
                .put("server_port", uri.port);
    }

    private static JSONObject buildDns(RouteMode mode) throws Exception {
        JSONObject localTls = new JSONObject().put("enabled", true).put("server_name", "dns.alidns.com");
        JSONObject secureTls = new JSONObject().put("enabled", true).put("server_name", "dns.google");
        JSONArray servers = new JSONArray()
                .put(new JSONObject()
                        .put("type", "https")
                        .put("tag", "local-dns")
                        .put("server", "223.5.5.5")
                        .put("server_port", 443)
                        .put("path", "/dns-query")
                        // New-style DoH servers dial directly when detour is absent.
                        // Pointing this at an otherwise empty direct outbound is
                        // rejected by sing-box 1.13 as a meaningless detour.
                        .put("tls", localTls))
                .put(new JSONObject()
                        .put("type", "https")
                        .put("tag", "secure-dns")
                        .put("server", "8.8.8.8")
                        .put("server_port", 443)
                        .put("path", "/dns-query")
                        .put("tls", secureTls)
                        .put("detour", "proxy"));
        JSONObject dns = new JSONObject()
                .put("servers", servers)
                .put("final", "secure-dns")
                .put("strategy", "ipv4_only")
                .put("independent_cache", true)
                .put("cache_capacity", 4096)
                .put("reverse_mapping", true);
        JSONArray rules = new JSONArray().put(new JSONObject()
                .put("domain_suffix", new JSONArray().put("lan").put("local"))
                .put("action", "route")
                .put("server", "local-dns"));
        if (mode == RouteMode.SMART) {
            rules.put(new JSONObject()
                    .put("rule_set", new JSONArray().put("geosite-cn"))
                    .put("action", "route")
                    .put("server", "local-dns"));
            rules.put(new JSONObject()
                    .put("domain_suffix", new JSONArray().put("cn").put("中国"))
                    .put("action", "route")
                    .put("server", "local-dns"));
        }
        dns.put("rules", rules);
        return dns;
    }

    private static JSONObject buildRoute(RouteMode mode, RuleSetInstaller.Paths paths) throws Exception {
        JSONArray rules = new JSONArray()
                .put(new JSONObject().put("action", "sniff"))
                .put(new JSONObject().put("protocol", "dns").put("action", "hijack-dns"))
                // QUIC can stall on TCP-oriented or UDP-restricted nodes. Reject
                // only web QUIC so browsers retry immediately over TCP/TLS; the
                // core's Hysteria2/TUIC sockets bypass this TUN rule.
                .put(new JSONObject()
                        .put("network", "udp")
                        .put("port", 443)
                        .put("action", "reject"))
                .put(new JSONObject()
                        .put("ip_is_private", true)
                        .put("action", "route")
                        .put("outbound", "direct"))
                .put(new JSONObject()
                        .put("domain_suffix", new JSONArray().put("lan").put("local"))
                        .put("action", "route")
                        .put("outbound", "direct"));
        if (mode == RouteMode.SMART) {
            rules.put(new JSONObject()
                    .put("rule_set", new JSONArray().put("geosite-cn"))
                    .put("action", "route")
                    .put("outbound", "direct"));
            rules.put(new JSONObject()
                    .put("rule_set", new JSONArray().put("geoip-cn"))
                    .put("action", "route")
                    .put("outbound", "direct"));
            rules.put(new JSONObject()
                    .put("domain_suffix", new JSONArray().put("cn").put("中国"))
                    .put("action", "route")
                    .put("outbound", "direct"));
        }
        JSONArray ruleSets = new JSONArray()
                .put(new JSONObject().put("type", "local").put("tag", "geosite-cn")
                        .put("format", "binary").put("path", paths.geositeCn))
                .put(new JSONObject().put("type", "local").put("tag", "geoip-cn")
                        .put("format", "binary").put("path", paths.geoipCn));
        return new JSONObject()
                .put("rules", rules)
                .put("rule_set", ruleSets)
                .put("final", "proxy")
                .put("default_domain_resolver", "local-dns")
                .put("auto_detect_interface", true);
    }

    private static JSONObject buildTransport(Map<String, String> q, String rawType) throws Exception {
        String type = first(rawType, "tcp").toLowerCase(Locale.ROOT);
        if ("websocket".equals(type)) type = "ws";
        if ("h2".equals(type)) type = "http";
        if ("http-upgrade".equals(type)) type = "httpupgrade";
        if ("tcp".equals(type) || "raw".equals(type) || "none".equals(type)) return null;

        JSONObject transport = new JSONObject().put("type", type);
        String host = query(q, "host");
        String path = query(q, "path");
        switch (type) {
            case "ws":
                if (!path.isEmpty()) transport.put("path", path);
                if (!host.isEmpty()) transport.put("headers", new JSONObject().put("Host", host));
                int earlyData = positiveInt(query(q, "ed", "max_early_data"));
                if (earlyData > 0) transport.put("max_early_data", earlyData);
                String earlyHeader = query(q, "eh", "early_data_header_name");
                if (!earlyHeader.isEmpty()) transport.put("early_data_header_name", earlyHeader);
                return transport;
            case "httpupgrade":
                if (!path.isEmpty()) transport.put("path", path);
                if (!host.isEmpty()) transport.put("host", host);
                return transport;
            case "grpc":
                String serviceName = query(q, "serviceName", "service_name");
                if (!serviceName.isEmpty()) transport.put("service_name", serviceName);
                return transport;
            case "http":
                if (!path.isEmpty()) transport.put("path", path);
                if (!host.isEmpty()) {
                    JSONArray hosts = new JSONArray();
                    for (String value : host.split(",")) if (!value.trim().isEmpty()) hosts.put(value.trim());
                    if (hosts.length() > 0) transport.put("host", hosts);
                }
                return transport;
            case "quic":
                return transport;
            default:
                throw new IllegalArgumentException("暂不支持传输类型：" + type);
        }
    }

    private static JSONObject buildTls(Map<String, String> q, String mode,
                                       boolean allowReality, boolean allowUtls) throws Exception {
        JSONObject tls = new JSONObject().put("enabled", true);
        String serverName = query(q, "sni", "serverName", "server_name", "peer");
        if (!serverName.isEmpty()) tls.put("server_name", serverName);
        if (truthy(query(q, "allowInsecure", "insecure", "skip-cert-verify"))) tls.put("insecure", true);
        String alpn = query(q, "alpn");
        if (!alpn.isEmpty()) {
            JSONArray values = new JSONArray();
            for (String item : alpn.split(",")) if (!item.trim().isEmpty()) values.put(item.trim());
            if (values.length() > 0) tls.put("alpn", values);
        }
        String fingerprint = query(q, "fp", "fingerprint");
        if (allowUtls && !fingerprint.isEmpty()) {
            tls.put("utls", new JSONObject().put("enabled", true).put("fingerprint", fingerprint));
        }
        if ("reality".equalsIgnoreCase(mode)) {
            if (!allowReality) throw new IllegalArgumentException("当前协议不支持 REALITY");
            String publicKey = query(q, "pbk", "publicKey", "public_key");
            if (publicKey.isEmpty()) throw new IllegalArgumentException("REALITY 节点缺少 public key");
            JSONObject reality = new JSONObject().put("enabled", true).put("public_key", publicKey);
            String shortId = query(q, "sid", "shortId", "short_id");
            if (!shortId.isEmpty()) reality.put("short_id", shortId);
            tls.put("reality", reality);
        }
        return tls;
    }

    static Endpoint endpoint(NodeCatalog.Node node) throws Exception {
        if (node == null || node.config == null || node.config.trim().isEmpty()) {
            throw new IllegalArgumentException("节点配置为空");
        }
        return endpointFromOutbound(parseOutbound(node.config.trim()));
    }

    private static Endpoint endpointFromOutbound(JSONObject outbound) throws Exception {
        String type = aliasType(outbound.optString("type"));
        String host = outbound.optString("server", "").trim();
        int port = flexiblePort(outbound.opt("server_port"));
        if (port <= 0 && "hysteria2".equals(type)) {
            JSONArray ports = outbound.optJSONArray("server_ports");
            if (ports != null && ports.length() > 0) port = firstPortInRange(ports.optString(0));
        }
        validateAddress(type, host, port);
        return new Endpoint(host, port, !"hysteria2".equals(type) && !"tuic".equals(type));
    }

    private static ParsedUri parseUri(String raw, String label) throws Exception {
        String body = raw.substring(raw.indexOf("://") + 3);
        int hash = body.indexOf('#');
        if (hash >= 0) body = body.substring(0, hash);
        String queryText = "";
        int question = body.indexOf('?');
        if (question >= 0) {
            queryText = body.substring(question + 1);
            body = body.substring(0, question);
        }
        int at = body.lastIndexOf('@');
        String userInfo = at >= 0 ? decodeComponent(body.substring(0, at)) : "";
        HostPort address = parseHostPort(at >= 0 ? body.substring(at + 1) : body, label);
        ParsedUri out = new ParsedUri();
        out.userInfo = userInfo.trim();
        out.host = address.host;
        out.port = address.port;
        out.q.putAll(parseQuery(queryText));
        return out;
    }

    private static HostPort parseHostPort(String value, String label) throws Exception {
        String input = value.trim();
        int slash = input.indexOf('/');
        if (slash >= 0) input = input.substring(0, slash);
        String host;
        int port;
        if (input.startsWith("[")) {
            int close = input.indexOf(']');
            if (close < 0 || close + 2 > input.length() || input.charAt(close + 1) != ':') {
                throw new IllegalArgumentException(label + " IPv6 地址格式错误");
            }
            host = input.substring(1, close).trim();
            port = parsePort(input.substring(close + 2));
        } else {
            int colon = input.lastIndexOf(':');
            if (colon <= 0) throw new IllegalArgumentException(label + " 缺少端口");
            host = input.substring(0, colon).trim();
            port = parsePort(input.substring(colon + 1));
        }
        validateAddress(label, host, port);
        return new HostPort(host, port);
    }

    private static Map<String, String> parseQuery(String query) {
        Map<String, String> result = new LinkedHashMap<>();
        for (String pair : query.split("&")) {
            if (pair.isEmpty()) continue;
            int eq = pair.indexOf('=');
            String key = decodeComponent(eq >= 0 ? pair.substring(0, eq) : pair).trim();
            String value = decodeComponent(eq >= 0 ? pair.substring(eq + 1) : "");
            if (key.isEmpty()) continue;
            result.put(key, value);
            result.put(key.toLowerCase(Locale.ROOT), value);
        }
        return result;
    }

    private static String query(Map<String, String> q, String... keys) {
        for (String key : keys) {
            String value = q.get(key);
            if (value == null) value = q.get(key.toLowerCase(Locale.ROOT));
            value = clean(value);
            if (!value.isEmpty()) return value;
        }
        return "";
    }

    private static void put(Map<String, String> q, String key, String value) {
        value = clean(value);
        if (!value.isEmpty()) {
            q.put(key, value);
            q.put(key.toLowerCase(Locale.ROOT), value);
        }
    }

    private static String decodeComponent(String value) {
        try {
            // In proxy URIs '+' is data, not an HTML form-space separator.
            return URLDecoder.decode(value.replace("+", "%2B"), "UTF-8");
        } catch (Exception ignored) {
            return value;
        }
    }

    private static String decodeBase64Text(String value) {
        String normalized = value == null ? "" : value.replaceAll("\\s", "").trim();
        normalized += "====".substring(0, (4 - normalized.length() % 4) % 4);
        try {
            return new String(Base64.getUrlDecoder().decode(normalized), StandardCharsets.UTF_8);
        } catch (IllegalArgumentException ignored) {
            return new String(Base64.getDecoder().decode(normalized), StandardCharsets.UTF_8);
        }
    }

    private static int parsePort(String value) {
        try {
            int port = Integer.parseInt(value.trim());
            return port >= 1 && port <= 65535 ? port : -1;
        } catch (Exception ignored) {
            return -1;
        }
    }

    private static int flexiblePort(Object value) {
        if (value instanceof Number) {
            int port = ((Number) value).intValue();
            return port >= 1 && port <= 65535 ? port : -1;
        }
        return parsePort(value == null ? "" : String.valueOf(value));
    }

    private static int flexibleInt(Object value, int fallback) {
        if (value instanceof Number) return ((Number) value).intValue();
        try { return Integer.parseInt(value == null ? "" : String.valueOf(value).trim()); }
        catch (Exception ignored) { return fallback; }
    }

    private static int positiveInt(String value) {
        try { return Math.max(0, Integer.parseInt(clean(value))); }
        catch (Exception ignored) { return 0; }
    }

    private static int firstPortInRange(String value) {
        String clean = clean(value);
        int separator = clean.indexOf(':');
        if (separator < 0) separator = clean.indexOf('-');
        return parsePort(separator < 0 ? clean : clean.substring(0, separator));
    }

    private static void validateAddress(String label, String host, int port) {
        if (clean(host).isEmpty() || port < 1 || port > 65535) {
            throw new IllegalArgumentException(first(label, "节点") + " 服务器地址无效");
        }
    }

    private static boolean truthy(String value) {
        return "1".equals(value) || "true".equalsIgnoreCase(value)
                || "yes".equalsIgnoreCase(value) || "on".equalsIgnoreCase(value);
    }

    private static String aliasType(String type) {
        type = clean(type).toLowerCase(Locale.ROOT);
        if ("hy2".equals(type)) return "hysteria2";
        if ("ss".equals(type)) return "shadowsocks";
        return type;
    }

    private static IllegalArgumentException unsupported(String type) {
        String detail = clean(type);
        return new IllegalArgumentException((detail.isEmpty() ? "当前节点" : detail.toUpperCase(Locale.ROOT))
                + " 暂不支持；本版支持 VLESS、Trojan、VMess、Shadowsocks、Hysteria2、TUIC、AnyTLS");
    }

    private static String first(String first, String fallback) {
        String value = clean(first);
        return value.isEmpty() ? clean(fallback) : value;
    }

    private static String clean(String value) { return value == null ? "" : value.trim(); }

    static final class Endpoint {
        final String host;
        final int port;
        final boolean tcpProbeSupported;
        Endpoint(String host, int port, boolean tcpProbeSupported) {
            this.host = host;
            this.port = port;
            this.tcpProbeSupported = tcpProbeSupported;
        }
    }

    private static final class ParsedUri {
        String userInfo;
        String host;
        int port;
        final Map<String, String> q = new LinkedHashMap<>();
    }

    private static final class HostPort {
        final String host;
        final int port;
        HostPort(String host, int port) { this.host = host; this.port = port; }
    }
}
