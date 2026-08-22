import base64
import json
from urllib.parse import parse_qs, unquote, urlsplit

SCHEMA = "xvpn.node.v1"
SUPPORTED = {"vless", "vmess", "trojan", "shadowsocks", "hysteria2", "tuic", "anytls"}
ALIASES = {"ss": "shadowsocks", "hy2": "hysteria2"}


def _last(query, *names, default=""):
    for name in names:
        values = query.get(name.lower())
        if values:
            return str(values[-1])
    return default


def _bool(value, default=False):
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _port(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError("节点端口无效")
    if port < 1 or port > 65535:
        raise ValueError("节点端口需为 1-65535")
    return port


def _b64_text(value, error="Base64 数据无效"):
    raw = value.strip()
    raw += "=" * (-len(raw) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return decoder(raw.encode()).decode("utf-8")
        except Exception:
            pass
    raise ValueError(error)


def original_name(raw, fallback="未命名节点"):
    raw = (raw or "").strip()
    if raw.lower().startswith("vmess://"):
        try:
            body = raw.split("://", 1)[1].split("#", 1)[0]
            obj = json.loads(_b64_text(body, "VMess Base64 数据无效"))
            value = str(obj.get("ps") or obj.get("remarks") or "").strip()
            if value:
                return value
        except Exception:
            pass
    if "#" in raw:
        value = unquote(raw.rsplit("#", 1)[1]).strip()
        if value:
            return value
    return fallback


def _base(protocol, server, port):
    server = str(server or "").strip().strip("[]")
    if not server:
        raise ValueError("节点服务器地址不能为空")
    return {
        "schema": SCHEMA,
        "protocol": protocol,
        "server": server,
        "port": _port(port),
        "auth": {},
        "tls": {"enabled": False},
        "transport": {"type": "tcp"},
        "options": {},
    }


def _apply_tls(profile, query, default=False):
    security = _last(query, "security").lower()
    enabled = security in {"tls", "xtls", "reality"} or _bool(_last(query, "tls"), default)
    tls = {"enabled": enabled}
    if enabled:
        sni = _last(query, "sni", "servername", "server_name")
        fingerprint = _last(query, "fp", "fingerprint", "client-fingerprint")
        if sni:
            tls["server_name"] = sni
        if fingerprint:
            tls["fingerprint"] = fingerprint
        if _last(query, "allowinsecure", "insecure"):
            tls["insecure"] = _bool(_last(query, "allowinsecure", "insecure"))
        if security == "reality" or _last(query, "pbk", "publickey"):
            reality = {"enabled": True}
            public_key = _last(query, "pbk", "publickey")
            short_id = _last(query, "sid", "shortid")
            if public_key:
                reality["public_key"] = public_key
            if short_id:
                reality["short_id"] = short_id
            tls["reality"] = reality
    profile["tls"] = tls


def _apply_transport(profile, query):
    kind = (_last(query, "type", "network", default="tcp") or "tcp").lower()
    transport = {"type": kind}
    path = _last(query, "path")
    host = _last(query, "host")
    service_name = _last(query, "servicename", "service_name")
    if path:
        transport["path"] = path
    if host:
        transport["host"] = host
    if service_name:
        transport["service_name"] = service_name
    profile["transport"] = transport


def _vmess(raw):
    body = raw.split("://", 1)[1].split("#", 1)[0]
    obj = json.loads(_b64_text(body, "VMess Base64 数据无效"))
    profile = _base("vmess", obj.get("add"), obj.get("port"))
    uuid = str(obj.get("id") or "").strip()
    if not uuid:
        raise ValueError("VMess UUID 不能为空")
    profile["auth"] = {"uuid": uuid, "alter_id": int(obj.get("aid") or 0)}
    if obj.get("scy"):
        profile["options"]["cipher"] = str(obj.get("scy"))
    profile["transport"] = {"type": str(obj.get("net") or "tcp").lower()}
    if obj.get("path"):
        profile["transport"]["path"] = str(obj.get("path"))
    if obj.get("host"):
        profile["transport"]["host"] = str(obj.get("host"))
    tls = str(obj.get("tls") or "").lower() in {"tls", "1", "true"}
    profile["tls"] = {"enabled": tls}
    if tls and obj.get("sni"):
        profile["tls"]["server_name"] = str(obj.get("sni"))
    if tls and obj.get("fp"):
        profile["tls"]["fingerprint"] = str(obj.get("fp"))
    return profile


def _shadowsocks(raw):
    parsed_full = urlsplit(raw)
    query = {k.lower(): v for k, v in parse_qs(parsed_full.query, keep_blank_values=True).items()}
    body = raw.split("://", 1)[1].split("#", 1)[0].split("?", 1)[0]
    method = password = ""
    host = None
    port = None

    if "@" in body:
        userinfo, endpoint = body.rsplit("@", 1)
        userinfo = unquote(userinfo)
        if ":" not in userinfo:
            userinfo = _b64_text(userinfo, "Shadowsocks 用户信息 Base64 无效")
        if ":" not in userinfo:
            raise ValueError("Shadowsocks 方法或密码无效")
        method, password = userinfo.split(":", 1)
        endpoint_url = urlsplit("ss://x@" + endpoint)
        host, port = endpoint_url.hostname, endpoint_url.port
    else:
        decoded = _b64_text(body, "Shadowsocks Base64 数据无效")
        if "@" not in decoded:
            raise ValueError("Shadowsocks Base64 节点格式无效")
        userinfo, endpoint = decoded.rsplit("@", 1)
        if ":" not in userinfo:
            raise ValueError("Shadowsocks 方法或密码无效")
        method, password = userinfo.split(":", 1)
        endpoint_url = urlsplit("ss://x@" + endpoint)
        host, port = endpoint_url.hostname, endpoint_url.port

    method = unquote(method).strip()
    password = unquote(password)
    if not method or password == "":
        raise ValueError("Shadowsocks 方法或密码不能为空")
    profile = _base("shadowsocks", host, port)
    profile["auth"] = {"method": method, "password": password}
    plugin = _last(query, "plugin")
    if plugin:
        profile["options"]["plugin"] = unquote(plugin)
    return profile


def canonical_profile(raw):
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("节点配置不能为空")

    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except Exception as exc:
            raise ValueError("节点 JSON 无效") from exc
        if obj.get("schema") != SCHEMA:
            raise ValueError("JSON 节点必须使用 xvpn.node.v1 标准")
        protocol = ALIASES.get(str(obj.get("protocol") or "").lower(), str(obj.get("protocol") or "").lower())
        if protocol not in SUPPORTED:
            raise ValueError("当前协议暂不支持")
        obj["protocol"] = protocol
        obj["server"] = str(obj.get("server") or "").strip()
        obj["port"] = _port(obj.get("port"))
        if not obj["server"]:
            raise ValueError("节点服务器地址不能为空")
        obj.setdefault("auth", {})
        obj.setdefault("tls", {"enabled": False})
        obj.setdefault("transport", {"type": "tcp"})
        obj.setdefault("options", {})
        return obj

    scheme = raw.split("://", 1)[0].lower() if "://" in raw else ""
    protocol = ALIASES.get(scheme, scheme)
    if protocol == "vmess":
        return _vmess(raw)
    if protocol == "shadowsocks":
        return _shadowsocks(raw)
    if protocol not in SUPPORTED:
        raise ValueError(f"暂不支持此节点协议：{scheme or '未知'}")

    parsed = urlsplit(raw)
    query = {k.lower(): v for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
    profile = _base(protocol, parsed.hostname, parsed.port)
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")

    if protocol == "vless":
        if not username:
            raise ValueError("VLESS UUID 不能为空")
        profile["auth"] = {"uuid": username}
        flow = _last(query, "flow")
        if flow:
            profile["options"]["flow"] = flow
        packet_encoding = _last(query, "packetencoding", "packet_encoding")
        if packet_encoding:
            profile["options"]["packet_encoding"] = packet_encoding
        _apply_tls(profile, query)
        _apply_transport(profile, query)
    elif protocol == "trojan":
        secret = username or password
        if not secret:
            raise ValueError("Trojan 密码不能为空")
        profile["auth"] = {"password": secret}
        _apply_tls(profile, query, True)
        _apply_transport(profile, query)
    elif protocol == "hysteria2":
        secret = username or password
        if not secret:
            raise ValueError("Hysteria2 密码不能为空")
        profile["auth"] = {"password": secret}
        profile["tls"] = {"enabled": True}
        sni = _last(query, "sni", "peer")
        if sni:
            profile["tls"]["server_name"] = sni
        if _last(query, "insecure"):
            profile["tls"]["insecure"] = _bool(_last(query, "insecure"))
        obfs = _last(query, "obfs")
        if obfs:
            profile["options"]["obfs"] = obfs
            obfs_password = _last(query, "obfs-password", "obfspassword")
            if obfs_password:
                profile["options"]["obfs_password"] = obfs_password
    elif protocol == "tuic":
        if not username or not password:
            raise ValueError("TUIC UUID 和密码不能为空")
        profile["auth"] = {"uuid": username, "password": password}
        profile["tls"] = {"enabled": True}
        sni = _last(query, "sni")
        if sni:
            profile["tls"]["server_name"] = sni
        if _last(query, "insecure"):
            profile["tls"]["insecure"] = _bool(_last(query, "insecure"))
        cc = _last(query, "congestion_control", "congestion-control")
        if cc:
            profile["options"]["congestion_control"] = cc
        alpn = _last(query, "alpn")
        if alpn:
            profile["options"]["alpn"] = [x.strip() for x in alpn.split(",") if x.strip()]
    elif protocol == "anytls":
        secret = username or password
        if not secret:
            raise ValueError("AnyTLS 密码不能为空")
        profile["auth"] = {"password": secret}
        _apply_tls(profile, query, True)
    return profile


def profile_details(profile):
    details = [str(profile.get("protocol") or "").upper()]
    tls = profile.get("tls") or {}
    if tls.get("enabled"):
        details.append("REALITY" if (tls.get("reality") or {}).get("enabled") else "TLS")
    flow = str((profile.get("options") or {}).get("flow") or "")
    if "vision" in flow.lower():
        details.append("Vision")
    transport = str((profile.get("transport") or {}).get("type") or "")
    if transport and transport != "tcp":
        details.append(transport.upper() if len(transport) <= 4 else transport)
    return list(dict.fromkeys(x for x in details if x))
