from flask import current_app

from . import api_core as core
from .crypto import decrypt_text
from .db import connect
from .node_profile import canonical_profile

# Authentication, version policy and traffic accounting stay isolated in
# api_core; the public node contract below is XVPN's Mihomo-native schema.
api_bp = core.api_bp


def _nodes_payload():
    with connect() as conn:
        rows = conn.execute(
            """SELECT n.*, COALESCE(co.sort_order, 999999) country_sort_order
               FROM nodes n
               LEFT JOIN country_orders co ON co.country_code=n.country_code
               WHERE n.status='enabled'
               ORDER BY country_sort_order, n.country_code, n.sort_order, n.id"""
        ).fetchall()

    countries = {}
    total = 0
    skipped = 0
    for row in rows:
        try:
            raw = decrypt_text(current_app, row["config_enc"])
            profile = canonical_profile(raw)
        except Exception:
            skipped += 1
            continue

        key = row["country_code"]
        countries.setdefault(
            key,
            {
                "country": row["country"],
                "country_code": key,
                "flag_emoji": "".join(chr(127397 + ord(ch)) for ch in key)
                if len(key) == 2 and key.isalpha() and key != "ZZ" else "🌐",
                "sort_order": row["country_sort_order"],
                "nodes": [],
            },
        )
        countries[key]["nodes"].append(
            {
                "id": row["id"],
                "name": row["name"],
                "display_name": row["name"],
                "country": row["country"],
                "country_code": row["country_code"],
                "region": row["region"],
                "protocol": profile["protocol"],
                "profile": profile,
                "sort_order": row["sort_order"],
            }
        )
        total += 1

    return {
        "ok": True,
        "schema": "xvpn.nodes.v1",
        "node_schema": "xvpn.node.v1",
        "core": "mihomo",
        "countries": list(countries.values()),
        "total": total,
        "skipped_invalid": skipped,
    }


# /nodes and /app/bootstrap in api_core resolve this symbol when handling the
# request, so both routes now use the same canonical contract.
core._nodes_payload = _nodes_payload
