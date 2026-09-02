import ipaddress
import threading
import time

import requests


_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL_SECONDS = 6 * 60 * 60
_FAILURE_CACHE_TTL_SECONDS = 5 * 60

_HOSTING_HINTS = (
    "cloud", "hosting", "host", "server", "data center", "datacenter", "vps", "colo",
    "digitalocean", "amazon", "aws", "google cloud", "microsoft azure", "oracle",
    "alibaba cloud", "tencent cloud", "linode", "vultr", "hetzner", "ovh", "choopa",
    "leaseweb", "contabo", "netcup", "rackspace",
)
_RESIDENTIAL_HINTS = (
    "telecom", "unicom", "mobile", "broadband", "communications", "cable", "residential",
    "comcast", "verizon", "at&t", "spectrum", "orange", "vodafone", "deutsche telekom",
    "free sas", "proxad", "sk broadband", "kt corporation", "lg uplus", "chunghwa",
    "softbank", "kddi", "docomo",
)


def normalize_public_ip(value):
    try:
        address = ipaddress.ip_address(str(value or "").strip())
    except ValueError as exc:
        raise ValueError("IP 地址格式无效") from exc
    if not address.is_global:
        raise ValueError("只能查询公网 IP")
    return address.compressed


def _text(value):
    return str(value or "").strip()


def _object_name(value):
    if isinstance(value, dict):
        return _text(
            value.get("name") or value.get("org") or value.get("datacenter") or value.get("domain")
        )
    return _text(value)


def _country_code(payload):
    location = payload.get("location") if isinstance(payload.get("location"), dict) else {}
    code = _text(
        payload.get("cc") or payload.get("country_code") or
        location.get("country_code") or location.get("country_code_alpha2")
    ).upper()
    return code if len(code) == 2 and code.isalpha() else ""


def classify_payload(payload, ip, keyed=False):
    payload = payload if isinstance(payload, dict) else {}
    company = payload.get("company")
    asn = payload.get("asn")
    provider = _object_name(company) or _text(payload.get("company_name")) \
        or _text(payload.get("asn_org")) or _object_name(asn) \
        or _object_name(payload.get("datacenter"))
    company_type = _text(company.get("type") if isinstance(company, dict) else "").lower()
    asn_type = _text(asn.get("type") if isinstance(asn, dict) else "").lower()
    provider_lower = provider.lower()

    explicit = payload.get("is_datacenter")
    hosting_type = company_type in {"hosting", "datacenter", "cloud"} \
        or asn_type in {"hosting", "datacenter", "cloud"}
    residential_type = company_type in {"isp", "mobile isp"} or asn_type in {"isp", "mobile isp"}
    if explicit is True:
        classification, confidence = "datacenter", "high"
    elif hosting_type:
        classification, confidence = "datacenter", "medium"
    elif any(hint in provider_lower for hint in _HOSTING_HINTS):
        classification, confidence = "datacenter", "medium"
    elif residential_type or any(hint in provider_lower for hint in _RESIDENTIAL_HINTS):
        classification, confidence = "residential", "medium"
    else:
        classification, confidence = "unknown", "unknown"

    return {
        "ok": True,
        "ip": ip,
        "classification": classification,
        "type_label": {
            "datacenter": "机房 IP",
            "residential": "家宽 / 运营商 IP",
            "unknown": "类型待确认",
        }[classification],
        "provider": provider,
        "country_code": _country_code(payload),
        "confidence": confidence,
        "source": "ipapi.is-keyed" if keyed else "ipapi.is-anonymous",
    }


def classify_ip(value, api_key=""):
    ip = normalize_public_ip(value)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(ip)
        cached_ttl = _FAILURE_CACHE_TTL_SECONDS \
            if cached and cached[1].get("source") == "classification-unavailable" \
            else _CACHE_TTL_SECONDS
        if cached and now - cached[0] < cached_ttl:
            return dict(cached[1], cached=True)

    key = _text(api_key)
    body = {"q": ip}
    if key:
        body["key"] = key
    try:
        response = requests.post(
            "https://api.ipapi.is/",
            json=body,
            headers={"Accept": "application/json", "User-Agent": "XVPN-Panel/1.1"},
            timeout=(4, 6),
        )
        response.raise_for_status()
        result = classify_payload(response.json(), ip, bool(key))
    except (requests.RequestException, ValueError, TypeError):
        result = {
            "ok": True,
            "ip": ip,
            "classification": "unknown",
            "type_label": "类型待确认",
            "provider": "",
            "country_code": "",
            "confidence": "unknown",
            "source": "classification-unavailable",
        }

    with _CACHE_LOCK:
        _CACHE[ip] = (now, result)
        if len(_CACHE) > 512:
            oldest = min(_CACHE, key=lambda item: _CACHE[item][0])
            _CACHE.pop(oldest, None)
    return dict(result, cached=False)
