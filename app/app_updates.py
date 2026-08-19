import hashlib
import json
import os
import re
from datetime import datetime, timezone

import requests

from .settings_store import get_settings, set_settings
from .version import APP_VERSION

DEFAULT_ANDROID_REPO = "kkx999/XVPN-Android"
CACHE_SECONDS = 600
CHECK_INTERVAL_SECONDS = 43200
MAX_RELEASE_NOTES = 12000
HISTORY_CACHE_SECONDS = 3600
HISTORY_PAGE_SIZE = 100
HISTORY_MAX_RELEASES = 1000


def _now():
    return datetime.now(timezone.utc)


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _github_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"XVPN-Panel/{APP_VERSION}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("XVPN_GITHUB_TOKEN", "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _download_headers(headers):
    result = {"User-Agent": headers.get("User-Agent", "XVPN-Panel")}
    if headers.get("Authorization"):
        result["Authorization"] = headers["Authorization"]
    return result


def _repo_name(app=None):
    # Repository is admin-editable and persisted in system_settings.
    # Environment/config remains a compatibility fallback for older installs.
    settings = get_settings(app)
    configured = str(settings.get("app_update_repository", "")).strip()
    if configured:
        return configured
    if app is not None:
        configured = str(app.config.get("ANDROID_UPDATE_REPOSITORY", "")).strip()
        if configured:
            return configured
    return os.environ.get("XVPN_ANDROID_REPOSITORY", DEFAULT_ANDROID_REPO).strip() or DEFAULT_ANDROID_REPO


def _valid_repo(repo):
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo or ""))


def _normalize_version(value):
    value = str(value or "").strip().lstrip("vV")
    return value


def _version_key(value):
    value = _normalize_version(value)
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-._]?([A-Za-z]+)(?:[-._]?([0-9]+(?:[._-][0-9]+)*))?)?", value)
    if not match:
        return None
    major, minor, patch = map(int, match.group(1, 2, 3))
    label = (match.group(4) or "").lower()
    number_text = match.group(5) or "0"
    numbers = tuple(int(part) for part in re.split(r"[._-]", number_text))
    rank = {"dev": 10, "alpha": 20, "a": 20, "beta": 30, "b": 30, "rc": 40, "": 100}.get(label, 50)
    return major, minor, patch, rank, numbers, label


def _extract_version_code(text):
    if not text:
        return 0
    patterns = (
        r"(?im)\bversionCode\s*[:=]\s*(\d+)\b",
        r"(?im)\bversion\s*code\s*[:=]\s*(\d+)\b",
        r"(?im)\bversionCode\s+(\d+)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return max(0, int(match.group(1)))
            except ValueError:
                return 0
    return 0


def _fetch_text(url, headers=None, timeout=15):
    response = requests.get(url, headers=headers or {}, timeout=timeout)
    response.raise_for_status()
    return response.text


def _fetch_version_code_from_source(repo, tag, headers):
    safe_tag = requests.utils.quote(tag, safe="")
    candidates = (
        "app/build.gradle.kts",
        "app/build.gradle",
    )
    for path in candidates:
        url = f"https://raw.githubusercontent.com/{repo}/{safe_tag}/{path}"
        try:
            text = _fetch_text(url, headers=_download_headers(headers), timeout=12)
        except requests.RequestException:
            continue
        code = _extract_version_code(text)
        if code:
            return code
    return 0


def _parse_sha256sums(text, filename):
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        digest = parts[0].strip().lower()
        name = parts[-1].lstrip("*").strip()
        if name == filename and re.fullmatch(r"[0-9a-f]{64}", digest):
            return digest
    return ""


def _snapshot_from_release(release, repo, headers):
    tag = str(release.get("tag_name") or "").strip()
    version_name = _normalize_version(tag)
    if not tag or not version_name:
        raise ValueError("Latest Release 缺少有效 Tag")

    assets = release.get("assets") or []
    apk_candidates = []
    sums = None
    for asset in assets:
        name = str(asset.get("name") or "")
        if name == "SHA256SUMS.txt":
            sums = asset
        if name.lower().endswith(".apk"):
            apk_candidates.append(asset)

    def apk_rank(asset):
        name = str(asset.get("name") or "").lower()
        return (
            1 if "universal" in name else 0,
            1 if "xvpn" in name else 0,
            int(asset.get("size") or 0),
        )

    apk = max(apk_candidates, key=apk_rank) if apk_candidates else None
    if not apk:
        raise ValueError("Latest Release 未找到 APK 资产")
    if not sums:
        raise ValueError("Latest Release 缺少 SHA256SUMS.txt")

    apk_name = str(apk.get("name") or "").strip()
    apk_url = str(apk.get("browser_download_url") or "").strip()
    sums_url = str(sums.get("browser_download_url") or "").strip()
    if not apk_url or not sums_url:
        raise ValueError("Release 资产下载地址不完整")

    try:
        sums_text = _fetch_text(sums_url, headers=_download_headers(headers), timeout=15)
    except requests.RequestException as exc:
        raise ValueError(f"无法读取 SHA256SUMS.txt：{exc}") from exc
    sha256 = _parse_sha256sums(sums_text, apk_name)
    if not sha256:
        raise ValueError(f"SHA256SUMS.txt 中找不到 {apk_name} 的校验值")

    body = str(release.get("body") or "")
    version_code = _extract_version_code(body)
    if not version_code:
        version_code = _fetch_version_code_from_source(repo, tag, headers)

    published_at = str(release.get("published_at") or release.get("created_at") or "")
    return {
        "repository": repo,
        "tag": tag,
        "version_name": version_name,
        "version_code": version_code,
        "release_name": str(release.get("name") or tag)[:200],
        "release_notes": body[:MAX_RELEASE_NOTES],
        "release_url": str(release.get("html_url") or ""),
        "published_at": published_at,
        "apk_name": apk_name,
        "apk_url": apk_url,
        "apk_size": int(apk.get("size") or 0),
        "sha256": sha256,
        "source": "github_latest_release",
    }



def _history_item_from_release(release, repo, headers):
    tag = str(release.get("tag_name") or "").strip()
    version_name = _normalize_version(tag)
    if not tag or not version_name:
        return None

    assets = release.get("assets") or []
    has_apk = any(str(asset.get("name") or "").lower().endswith(".apk") for asset in assets)
    has_sums = any(str(asset.get("name") or "") == "SHA256SUMS.txt" for asset in assets)
    if not has_apk or not has_sums:
        return None

    body = str(release.get("body") or "")
    version_code = _extract_version_code(body)
    if not version_code:
        version_code = _fetch_version_code_from_source(repo, tag, headers)

    return {
        "repository": repo,
        "tag": tag,
        "version_name": version_name,
        "version_code": int(version_code or 0),
        "release_name": str(release.get("name") or tag)[:200],
        "release_url": str(release.get("html_url") or ""),
        "published_at": str(release.get("published_at") or release.get("created_at") or ""),
        "prerelease": bool(release.get("prerelease")),
        "selectable": bool(version_code),
    }


def _stored_history(settings, repo):
    raw = settings.get("app_update_release_history_json", "")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("repository") != repo:
        return None
    releases = data.get("releases")
    if not isinstance(releases, list):
        return None
    return data


def get_release_history(app=None, force=False):
    """Return all published GitHub Releases that look like installable XVPN Android builds.

    History is used only for the administrator's minimum-version selector. The
    Android update API still follows Latest Release for the actual APK.
    """
    settings = get_settings(app)
    repo = _repo_name(app)
    if not _valid_repo(repo):
        return {"ok": False, "error": "Android 更新仓库配置无效", "releases": [], "stale": True}

    cached = _stored_history(settings, repo)
    checked_at = _parse_iso(settings.get("app_update_history_checked_at"))
    now = _now()
    if not force and checked_at and (now - checked_at).total_seconds() < HISTORY_CACHE_SECONDS:
        stale = settings.get("app_update_history_stale", "0") == "1"
        warning = str(settings.get("app_update_history_warning", "") or "")
        if cached:
            result = {
                "ok": True,
                "releases": cached.get("releases", []),
                "cached": True,
                "stale": stale,
                "checked_at": checked_at.isoformat(timespec="seconds"),
                "truncated": bool(cached.get("truncated")),
                "skipped": int(cached.get("skipped") or 0),
            }
            if stale and warning:
                result["warning"] = warning
            return result
        if stale:
            return {
                "ok": False,
                "error": warning or "无法获取 Android 历史 Release",
                "releases": [],
                "stale": True,
                "checked_at": checked_at.isoformat(timespec="seconds"),
            }

    headers = _github_headers()
    releases = []
    skipped = 0
    truncated = False
    page = 1
    try:
        while len(releases) < HISTORY_MAX_RELEASES:
            url = f"https://api.github.com/repos/{repo}/releases"
            response = requests.get(
                url,
                headers=headers,
                params={"per_page": HISTORY_PAGE_SIZE, "page": page},
                timeout=18,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("GitHub Releases 返回格式无效")
            if not payload:
                break

            for release in payload:
                if release.get("draft"):
                    continue
                item = _history_item_from_release(release, repo, headers)
                if item is None:
                    skipped += 1
                    continue
                releases.append(item)
                if len(releases) >= HISTORY_MAX_RELEASES:
                    truncated = True
                    break

            if len(payload) < HISTORY_PAGE_SIZE or truncated:
                break
            page += 1

        checked = now.isoformat(timespec="seconds")
        stored = {
            "repository": repo,
            "releases": releases,
            "truncated": truncated,
            "skipped": skipped,
        }
        set_settings(
            {
                "app_update_history_checked_at": checked,
                "app_update_release_history_json": json.dumps(stored, ensure_ascii=False, separators=(",", ":")),
                "app_update_history_stale": "0",
                "app_update_history_warning": "",
            },
            app,
        )
        return {
            "ok": True,
            "releases": releases,
            "cached": False,
            "stale": False,
            "checked_at": checked,
            "truncated": truncated,
            "skipped": skipped,
        }
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        checked = now.isoformat(timespec="seconds")
        message = str(exc)[:500] or "无法获取 Android 历史 Release"
        set_settings({
            "app_update_history_checked_at": checked,
            "app_update_history_stale": "1",
            "app_update_history_warning": message,
        }, app)
        if cached:
            return {
                "ok": True,
                "releases": cached.get("releases", []),
                "cached": True,
                "stale": True,
                "checked_at": checked,
                "warning": message,
                "truncated": bool(cached.get("truncated")),
                "skipped": int(cached.get("skipped") or 0),
            }
        return {"ok": False, "error": message, "releases": [], "stale": True, "checked_at": checked}

def _stored_snapshot(settings):
    raw = settings.get("app_update_last_snapshot_json", "")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("version_name") else None


def get_app_release(app=None, force=False):
    settings = get_settings(app)
    repo = _repo_name(app)
    if not _valid_repo(repo):
        return {"ok": False, "error": "Android 更新仓库配置无效", "snapshot": _stored_snapshot(settings), "stale": True}

    checked_at = _parse_iso(settings.get("app_update_last_checked_at"))
    cached = _stored_snapshot(settings)
    # Never reuse a snapshot from a repository that is no longer selected.
    if cached and cached.get("repository") != repo:
        cached = None
        checked_at = None
    now = _now()
    if not force and checked_at and (now - checked_at).total_seconds() < CACHE_SECONDS:
        stale = settings.get("app_update_last_stale", "0") == "1"
        warning = str(settings.get("app_update_last_warning", "") or "")
        if cached:
            result = {
                "ok": True,
                "snapshot": cached,
                "cached": True,
                "stale": stale,
                "checked_at": checked_at.isoformat(timespec="seconds"),
            }
            if stale and warning:
                result["warning"] = warning
            return result
        if stale:
            return {
                "ok": False,
                "error": warning or "无法获取 Android Latest Release",
                "snapshot": None,
                "stale": True,
                "checked_at": checked_at.isoformat(timespec="seconds"),
            }

    headers = _github_headers()
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        response = requests.get(url, headers=headers, timeout=18)
        response.raise_for_status()
        payload = response.json()
        snapshot = _snapshot_from_release(payload, repo, headers)
        checked = now.isoformat(timespec="seconds")
        set_settings(
            {
                "app_update_last_checked_at": checked,
                "app_update_last_status": f"已同步 {snapshot['tag']}",
                "app_update_last_snapshot_json": json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                "app_update_last_stale": "0",
                "app_update_last_warning": "",
            },
            app,
        )
        return {"ok": True, "snapshot": snapshot, "cached": False, "stale": False, "checked_at": checked}
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        checked = now.isoformat(timespec="seconds")
        message = str(exc)[:500] or "无法获取 Android Latest Release"
        set_settings(
            {
                "app_update_last_checked_at": checked,
                "app_update_last_status": f"同步失败：{message}",
                "app_update_last_stale": "1",
                "app_update_last_warning": message,
            },
            app,
        )
        if cached:
            return {"ok": True, "snapshot": cached, "cached": True, "stale": True, "checked_at": checked, "warning": message}
        return {"ok": False, "error": message, "snapshot": None, "stale": True, "checked_at": checked}


def compare_client(snapshot, current_version_name="", current_version_code=0):
    latest_code = int(snapshot.get("version_code") or 0)
    try:
        current_code = max(0, int(current_version_code or 0))
    except (TypeError, ValueError):
        current_code = 0

    if latest_code and current_code:
        update_available = latest_code > current_code
    else:
        latest_key = _version_key(snapshot.get("version_name"))
        current_key = _version_key(current_version_name)
        update_available = bool(latest_key and current_key and latest_key > current_key)

    return update_available


def app_update_payload(app=None, current_version_name="", current_version_code=0):
    settings = get_settings(app)
    enabled = settings.get("app_update_enabled", "1") == "1"
    force_policy = settings.get("app_update_force", "0") == "1"
    try:
        min_version_code = max(0, int(settings.get("app_update_min_version_code", "0") or 0))
    except (TypeError, ValueError):
        min_version_code = 0

    if not enabled:
        return {
            "ok": True,
            "enabled": False,
            "update_available": False,
            "force_update": False,
            "must_update": False,
            "min_version_code": min_version_code,
            "check_interval_seconds": CHECK_INTERVAL_SECONDS,
            "message": "Panel 已暂停 App 更新提示",
        }

    result = get_app_release(app)
    if not result.get("ok") or not result.get("snapshot"):
        return {
            "ok": False,
            "code": "APP_UPDATE_SOURCE_UNAVAILABLE",
            "message": "暂时无法获取 App 最新版本信息",
            "detail": result.get("error", ""),
            "check_interval_seconds": CHECK_INTERVAL_SECONDS,
        }

    snapshot = dict(result["snapshot"])
    update_available = compare_client(snapshot, current_version_name, current_version_code)
    try:
        current_code = max(0, int(current_version_code or 0))
    except (TypeError, ValueError):
        current_code = 0
    below_minimum = bool(min_version_code and current_code and current_code < min_version_code)
    latest_code = int(snapshot.get("version_code") or 0)
    minimum_reachable = bool(not min_version_code or (latest_code and latest_code >= min_version_code))
    # Never trap a client in an impossible update loop if an administrator selected
    # a minimum build newer than the current Latest Release (or Latest later regressed).
    must_update = bool(update_available and (force_policy or (below_minimum and minimum_reachable)))

    # Keep both structured fields and stable flat aliases. The flat aliases make
    # older/in-progress Android update checkers easier to integrate without
    # duplicating GitHub parsing logic in the client.
    return {
        "ok": True,
        "enabled": True,
        "repository": snapshot.get("repository", _repo_name(app)),
        "update_available": update_available,
        "has_update": update_available,
        "force_policy_enabled": force_policy,
        "force_update": must_update,
        "must_update": must_update,
        "below_minimum": below_minimum,
        "min_version_code": min_version_code,
        "minimum_reachable": minimum_reachable,
        "policy_warning": "" if minimum_reachable else "最低允许运行版本高于当前 Latest Release，已暂停该最低版本强制策略",
        "check_interval_seconds": CHECK_INTERVAL_SECONDS,
        "cache_seconds": CACHE_SECONDS,
        "cached": bool(result.get("cached")),
        "stale": bool(result.get("stale")),
        "checked_at": result.get("checked_at"),
        "version_name": snapshot.get("version_name", ""),
        "version_code": int(snapshot.get("version_code") or 0),
        "latest_version": snapshot.get("version_name", ""),
        "latest_version_code": int(snapshot.get("version_code") or 0),
        "versionName": snapshot.get("version_name", ""),
        "versionCode": int(snapshot.get("version_code") or 0),
        "tag": snapshot.get("tag", ""),
        "apk_url": snapshot.get("apk_url", ""),
        "download_url": snapshot.get("apk_url", ""),
        "downloadUrl": snapshot.get("apk_url", ""),
        "sha256": snapshot.get("sha256", ""),
        "release_notes": snapshot.get("release_notes", ""),
        "releaseNotes": snapshot.get("release_notes", ""),
        "release_url": snapshot.get("release_url", ""),
        "releaseUrl": snapshot.get("release_url", ""),
        "forcePolicyEnabled": force_policy,
        "forceUpdate": must_update,
        "mustUpdate": must_update,
        "current": {
            "version_name": _normalize_version(current_version_name),
            "version_code": current_code,
        },
        "latest": snapshot,
    }
