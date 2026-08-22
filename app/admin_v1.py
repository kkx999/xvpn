import json
import os
import re
import signal
import threading
from pathlib import Path

from flask import current_app, flash, redirect, render_template, request, session, url_for

from . import admin as legacy
from .backup_manager import list_backups
from .crypto import decrypt_text, encrypt_text
from .db import connect, transaction, utcnow
from .event_log import list_events
from .node_profile import canonical_profile, original_name, profile_details, validate_profile
from .settings_store import get_settings, set_settings


_RESERVED_ADMIN_PATHS = {"api", "static", "assets", "health"}
_ADMIN_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,47}$")


def _canonical(raw: str):
    profile = validate_profile(canonical_profile(raw))
    return profile, json.dumps(profile, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _node_view(row):
    item = dict(row)
    try:
        raw = decrypt_text(current_app, row["config_enc"])
        profile = validate_profile(canonical_profile(raw))
        item["protocol_details"] = profile_details(profile)
        item["config"] = json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception:
        item["protocol_details"] = [str(row["protocol"] or "INVALID").upper()]
        item["config"] = ""
    return item


@legacy.admin_required
def nodes():
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM nodes").fetchone()["c"]
        rows = conn.execute("SELECT * FROM nodes ORDER BY id DESC LIMIT 3").fetchall()
    return render_template("nodes.html", nodes=[_node_view(row) for row in rows], total_nodes=total)


@legacy.admin_required
def node_overview():
    with connect() as conn:
        rows = conn.execute(
            """SELECT n.* FROM nodes n
               LEFT JOIN country_orders co ON co.country_code=n.country_code
               ORDER BY COALESCE(co.sort_order,999999),n.country_code,n.sort_order,n.id"""
        ).fetchall()
        country_orders = legacy._active_country_orders(conn)
    items = [_node_view(row) for row in rows]
    return render_template(
        "node_overview.html",
        nodes=items,
        enabled_count=sum(1 for n in items if n["status"] == "enabled"),
        country_count=len(country_orders),
        protocols=sorted({str(n["protocol"] or "") for n in items if n["protocol"]}),
        country_orders=country_orders,
    )


@legacy.admin_required
def node_add():
    if not legacy.require_csrf():
        return "CSRF validation failed", 400
    raw = request.form.get("config", "").strip()
    if not raw:
        flash("节点配置不能为空", "error")
        return redirect(url_for("admin.nodes"))
    try:
        profile, canonical = _canonical(raw)
    except ValueError as exc:
        flash(f"节点配置无效：{exc}", "error")
        return redirect(url_for("admin.nodes"))

    code, country = legacy._country_from_form()
    source_name = original_name(raw)
    display_name = request.form.get("name", "").strip() or source_name
    now = utcnow()
    with transaction() as conn:
        legacy._ensure_country_order(conn, code)
        conn.execute(
            """INSERT INTO nodes(name,original_name,country,country_code,region,protocol,config_enc,sort_order,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                display_name, source_name, country, code, "", profile["protocol"],
                encrypt_text(current_app, canonical), legacy._to_int(request.form.get("sort_order"), 100),
                "enabled", now, now,
            ),
        )
    flash(f"节点已添加：{display_name}（{profile['protocol'].upper()} / {country}）", "success")
    return redirect(url_for("admin.nodes"))


@legacy.admin_required
def node_batch():
    if not legacy.require_csrf():
        return "CSRF validation failed", 400
    lines = [x.strip() for x in request.form.get("configs", "").splitlines() if x.strip()]
    if not lines:
        flash("没有可解析的节点", "error")
        return redirect(url_for("admin.nodes"))

    code, country = legacy._country_from_form()
    naming_mode = request.form.get("naming_mode", "original")
    prefix = request.form.get("name_prefix", "").strip()
    start_number = max(0, legacy._to_int(request.form.get("start_number"), 1))
    sort_base = legacy._to_int(request.form.get("sort_order"), 100)
    if naming_mode == "sequence" and not prefix:
        flash("使用自动编号时，请填写统一名称，例如“香港”", "error")
        return redirect(url_for("admin.nodes"))

    preview = []
    for idx, raw in enumerate(lines):
        try:
            profile, canonical = _canonical(raw)
        except ValueError as exc:
            flash(f"第 {idx + 1} 行节点无效：{exc}。本批次没有导入任何节点。", "error")
            return redirect(url_for("admin.nodes"))
        source_name = original_name(raw, f"节点 {idx + 1:02d}")
        display_name = f"{prefix}{start_number + idx:02d}" if naming_mode == "sequence" else source_name
        preview.append({
            "config": canonical,
            "original_name": source_name,
            "display_name": display_name,
            "protocol": profile["protocol"],
            "protocol_details": profile_details(profile),
            "sort_order": sort_base + idx,
        })

    return render_template("node_batch_preview.html", preview=preview, country=country, country_code=code)


@legacy.admin_required
def node_batch_confirm():
    if not legacy.require_csrf():
        return "CSRF validation failed", 400
    configs = request.form.getlist("config")
    names = request.form.getlist("display_name")
    source_names = request.form.getlist("original_name")
    sort_orders = request.form.getlist("sort_order")
    if not configs or len(configs) != len(names) or len(source_names) != len(configs):
        flash("批量导入数据不完整，请重新解析", "error")
        return redirect(url_for("admin.nodes"))

    validated = []
    for idx, raw in enumerate(configs):
        try:
            profile, canonical = _canonical(raw.strip())
        except ValueError as exc:
            flash(f"批量确认数据第 {idx + 1} 条已失效：{exc}。没有写入任何节点。", "error")
            return redirect(url_for("admin.nodes"))
        validated.append((profile, canonical))

    code, country = legacy._country_from_form()
    now = utcnow()
    with transaction() as conn:
        legacy._ensure_country_order(conn, code)
        for idx, (profile, canonical) in enumerate(validated):
            source_name = source_names[idx].strip() or f"节点 {idx + 1:02d}"
            display_name = names[idx].strip() or source_name
            conn.execute(
                """INSERT INTO nodes(name,original_name,country,country_code,region,protocol,config_enc,sort_order,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    display_name, source_name, country, code, "", profile["protocol"],
                    encrypt_text(current_app, canonical),
                    legacy._to_int(sort_orders[idx] if idx < len(sort_orders) else None, 100 + idx),
                    "enabled", now, now,
                ),
            )
    flash(f"已导入 {len(validated)} 个 Mihomo 标准节点", "success")
    return redirect(url_for("admin.nodes"))


@legacy.admin_required
def node_edit_page(node_id):
    with connect() as conn:
        node = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not node:
        return "Not Found", 404
    item = _node_view(node)
    return render_template("node_edit.html", node=item, return_to=request.args.get("return_to", ""))


@legacy.admin_required
def node_edit(node_id):
    if not legacy.require_csrf():
        return "CSRF validation failed", 400
    raw = request.form.get("config", "").strip()
    if not raw:
        flash("节点配置不能为空", "error")
        return redirect(url_for("admin.node_edit_page", node_id=node_id))
    try:
        profile, canonical = _canonical(raw)
    except ValueError as exc:
        flash(f"节点配置无效：{exc}", "error")
        return redirect(url_for("admin.node_edit_page", node_id=node_id))

    code, country = legacy._country_from_form()
    with transaction() as conn:
        current = conn.execute("SELECT original_name FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not current:
            return "Not Found", 404
        source_name = original_name(raw, current["original_name"] or "未命名节点")
        display_name = request.form.get("name", "").strip() or source_name
        legacy._ensure_country_order(conn, code)
        conn.execute(
            """UPDATE nodes SET name=?,original_name=?,country=?,country_code=?,protocol=?,config_enc=?,sort_order=?,updated_at=? WHERE id=?""",
            (
                display_name, source_name, country, code, profile["protocol"],
                encrypt_text(current_app, canonical), legacy._to_int(request.form.get("sort_order"), 100),
                utcnow(), node_id,
            ),
        )
    flash("节点已更新并重新通过 Mihomo 标准校验", "success")
    return redirect(legacy._node_return_url())


@legacy.admin_required
def settings():
    values = get_settings()
    backup_page = legacy._page_arg("backup_page")
    backup_per_page = 10
    all_backups = list_backups()
    backup_total = len(all_backups)
    backup_pages = max(1, (backup_total + backup_per_page - 1) // backup_per_page)
    backup_page = min(backup_page, backup_pages)
    backups = all_backups[(backup_page - 1) * backup_per_page:backup_page * backup_per_page]
    runtime_events = list_events(10)
    db_path = Path(current_app.config["DATABASE_PATH"])
    with connect() as conn:
        latest_traffic = conn.execute(
            """SELECT t.last_report_at,u.username,t.app_version
               FROM traffic_device_counters t JOIN users u ON u.id=t.user_id
               ORDER BY t.last_report_at DESC LIMIT 1"""
        ).fetchone()
        traffic_users = int(conn.execute("SELECT COUNT(DISTINCT user_id) FROM traffic_device_counters").fetchone()[0])
    system_info = {
        "host": request.host,
        "database": str(db_path),
        "database_size": db_path.stat().st_size if db_path.exists() else 0,
        "https": request.is_secure,
    }
    return render_template(
        "settings_v1.html",
        settings_values=values,
        backups=backups,
        system_info=system_info,
        runtime_events=runtime_events,
        latest_traffic=latest_traffic,
        traffic_users=traffic_users,
        backup_page=backup_page,
        backup_pages=backup_pages,
        backup_total=backup_total,
        telegram_configured=bool(values.get("telegram_bot_token_enc") and values.get("telegram_chat_id")),
    )


@legacy.admin_bp.post("/settings/admin-path", endpoint="admin_path_settings")
@legacy.admin_required
def admin_path_settings():
    if not legacy.require_csrf():
        return "CSRF validation failed", 400
    value = request.form.get("admin_path", "").strip().strip("/")
    if not _ADMIN_PATH_RE.fullmatch(value):
        flash("后台路径需为 3-48 位，只能使用字母、数字、- 和 _", "error")
        return redirect(url_for("admin.settings") + "#admin-path-section")
    if value.lower() in _RESERVED_ADMIN_PATHS:
        flash("该路径属于系统保留路径，请换一个", "error")
        return redirect(url_for("admin.settings") + "#admin-path-section")

    current = str(get_settings().get("admin_path") or current_app.config.get("ADMIN_PATH") or "admin").strip("/")
    if value == current:
        flash("后台路径没有变化", "error")
        return redirect(url_for("admin.settings") + "#admin-path-section")

    set_settings({"admin_path": value})
    session.clear()
    new_url = request.host_url.rstrip("/") + f"/{value}/login"

    # Gunicorn master automatically respawns this worker. Recreating the Flask app
    # makes the new Blueprint prefix effective without giving the web process sudo.
    if os.environ.get("XVPN_DISABLE_SELF_RELOAD") != "1":
        timer = threading.Timer(1.0, lambda: os.kill(os.getpid(), signal.SIGTERM))
        timer.daemon = True
        timer.start()

    return render_template("admin_path_changed.html", new_url=new_url, new_path=value)


def install_overrides(app):
    """Replace legacy node CRUD/settings views while keeping their endpoint names/UI URLs."""
    replacements = {
        "admin.nodes": nodes,
        "admin.node_overview": node_overview,
        "admin.node_add": node_add,
        "admin.node_batch": node_batch,
        "admin.node_batch_confirm": node_batch_confirm,
        "admin.node_edit_page": node_edit_page,
        "admin.node_edit": node_edit,
        "admin.settings": settings,
    }
    missing = [name for name in replacements if name not in app.view_functions]
    if missing:
        raise RuntimeError(f"Admin endpoint override failed; missing: {', '.join(missing)}")
    app.view_functions.update(replacements)
