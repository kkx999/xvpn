import base64
import json
import uuid
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


def _uuid_value(value, label):
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} 不能为空")
    try:
        return str(uuid.UUID(text))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{label} 格式无效") from exc


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


def validate_profile(profile):
    """Normalize and validate the stable Panel -> Android node contract."""
    if not isinstance(profile, dict):
        raise ValueError("节点标准数据必须是 JSON 对象")
    if profile.get("schema") != SCHEMA:
        raise ValueError("JSON 节点必须使用 xvpn.node.v1 标准")

    protocol_raw = str(profile.get("protocol") or "").strip().lower()
    protocol = ALIASES.get(protocol_raw, protocol_raw)
    if protocol not in SUPPORTED:
        raise ValueError("当前协议暂不支持")
    profile["protocol"] = protocol

    server = str(profile.get("server") or "").strip().strip("[]")
    if not server:
        raise ValueError("节点服务器地址不能为空")
    if any(ch.isspace() for ch in server):
        raise ValueError("节点服务器地址格式无效")
    profile["server"] = server
    profile["port"] = _port(profile.get("port"))

    auth = profile.get("auth")
    tls = profile.get("tls")
    transport = profile.get("transport")
    options = profile.get("options")
    if not isinstance(auth, dict):
        raise ValueError("节点 auth 必须是对象")
    if not isinstance(tls, dict):
        raise ValueError("节点 tls 必须是对象")
    if not isinstance(transport, dict):
        raise ValueError("节点 transport 必须是对象")
    if not isinstance(options, dict):
        raise ValueError("节点 options 必须是对象")

    enabled = tls.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("tls.enabled 必须是布尔值")
    tls["enabled"] = enabled
    transport_type = str(transport.get("type") or "tcp").strip().lower()
    if not transport_type:
        raise ValueError("transport.type 不能为空")
    transport["type"] = transport_type

    if protocol in {"vless", "vmess"}:
        auth["uuid"] = _uuid_value(auth.get("uuid"), f"{protocol.upper()} UUID")
    elif protocol in {"trojan", "hysteria2", "anytls"}:
        if str(auth.get("password") or "") == "":
            raise ValueError(f"{protocol.upper()} 密码不能为空")
    elif protocol == "shadowsocks":
        if not str(auth.get("method") or "").strip() or str(auth.get("password") or "") == "":
            raise ValueError("Shadowsocks 方法或密码不能为空")
    elif protocol == "tuic":
        auth["uuid"] = _uuid_value(auth.get("uuid"), "TUIC UUID")
        if str(auth.get("password") or "") == "":
            raise ValueError("TUIC 密码不能为空")

    if protocol == "vmess":
        try:
            alter_id = int(auth.get("alter_id", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("VMess alter_id 无效") from exc
        if alter_id < 0:
            raise ValueError("VMess alter_id 不能为负数")
        auth["alter_id"] = alter_id

    if protocol in {"hysteria2", "tuic", "anytls"} and not enabled:
        raise ValueError(f"{protocol.upper()} 必须启用 TLS")

    reality = tls.get("reality")
    if reality is not None:
        if not isinstance(reality, dict):
            raise ValueError("tls.reality 必须是对象")
        reality_enabled = reality.get("enabled", False)
        if not isinstance(reality_enabled, bool):
            raise ValueError("tls.reality.enabled 必须是布尔值")
        if reality_enabled:
            if not enabled:
                raise ValueError("Reality 必须同时启用 TLS")
            if not str(reality.get("public_key") or "").strip():
                raise ValueError("Reality public_key 不能为空")
            if not str(tls.get("server_name") or "").strip():
                raise ValueError("Reality server_name/SNI 不能为空")

    profile["auth"] = auth
    profile["tls"] = tls
    profile["transport"] = transport
    profile["options"] = options
    return profile


def _vmess(raw):
    body = raw.split("://", 1)[1].split("#", 1)[0]
    try:
        obj = json.loads(_b64_text(body, "VMess Base64 数据无效"))
    except json.JSONDecodeError as exc:
        raise ValueError("VMess JSON 数据无效") from exc
    profile = _base("vmess", obj.get("add"), obj.get("port"))
    profile["auth"] = {
        "uuid": str(obj.get("id") or "").strip(),
        "alter_id": obj.get("aid") or 0,
    }
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
    return validate_profile(profile)


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
    profile = _base("shadowsocks", host, port)
    profile["auth"] = {"method": method, "password": password}
    plugin = _last(query, "plugin")
    if plugin:
        profile["options"]["plugin"] = unquote(plugin)
    return validate_profile(profile)


def canonical_profile(raw):
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("节点配置不能为空")

    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except Exception as exc:
            raise ValueError("节点 JSON 无效") from exc
        return validate_profile(obj)

    scheme = raw.split("://", 1)[0].lower() if "://" in raw else ""
    protocol = ALIASES.get(scheme, scheme)
    if protocol == "vmess":
        return _vmess(raw)
    if protocol == "shadowsocks":
        return _shadowsocks(raw)
    if protocol not in SUPPORTED:
        raise ValueError(f"暂不支持此节点协议：{scheme or '未知'}")

    try:
        parsed = urlsplit(raw)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("节点地址或端口格式无效") from exc
    query = {k.lower(): v for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
    profile = _base(protocol, host, port)
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")

    if protocol == "vless":
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
        profile["auth"] = {"password": username or password}
        _apply_tls(profile, query, True)
        _apply_transport(profile, query)
    elif protocol == "hysteria2":
        profile["auth"] = {"password": username or password}
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
        profile["auth"] = {"password": username or password}
        _apply_tls(profile, query, True)

    return validate_profile(profile)


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
