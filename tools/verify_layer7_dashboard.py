from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "--"
    return str(value).replace("\n", " ").replace("|", "\\|")


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(_cell(v) for v in row) + " |")
    return "\n".join(lines)


def _fetch_dashboard(url: str) -> tuple[dict[str, Any] | None, str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, str(exc)
    try:
        return json.loads(data), ""
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"


def _build_dashboard_direct() -> tuple[dict[str, Any] | None, str]:
    try:
        from dashboard_server import build_dashboard_payload

        return build_dashboard_payload(), "dashboard_server.build_dashboard_payload"
    except Exception as exc:
        return None, f"direct import failed: {exc}"


def _missing_fields(item: dict[str, Any], required: list[str]) -> list[str]:
    return [field for field in required if item.get(field) in (None, "")]


def _missing_alias(item: dict[str, Any], label: str, aliases: list[str]) -> str:
    for field in aliases:
        if item.get(field) not in (None, ""):
            return ""
    return label


def _iter_city_evidence(payload: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = payload.get("city_evidence")
    if isinstance(evidence, list):
        return [item for item in evidence if isinstance(item, dict)]
    if isinstance(evidence, dict):
        out = []
        for item in evidence.values():
            if isinstance(item, dict):
                out.append(item)
        return out
    cities = payload.get("cities")
    if isinstance(cities, list):
        out = []
        for city in cities:
            ev = city.get("evidence") if isinstance(city, dict) else None
            if isinstance(ev, dict):
                out.append(ev)
        return out
    return []


def build_report(url: str) -> str:
    payload, error = _fetch_dashboard(url)
    source = "http_api"
    if payload is None:
        payload, direct_error = _build_dashboard_direct()
        source = "direct_import"
        if payload is None:
            return f"""# Layer 7 Dashboard Contract Verification

Generated: {_utc_now().isoformat()}
URL: {url}

Local API could not be read: `{_cell(error)}`
Direct fallback failed: `{_cell(direct_error)}`

## Layer Conclusion

- Can enter next layer: no
- Blockers:
  - dashboard API unavailable, start backend manually before Layer 7 contract validation
"""

    weather_signals = payload.get("weather_signals") or payload.get("signals") or []
    signal_required = ["city", "target_date", "event_url", "yes_token_id", "action"]
    signal_issues = []
    if isinstance(weather_signals, list):
        for index, item in enumerate(weather_signals):
            if isinstance(item, dict):
                missing = _missing_fields(item, signal_required)
                if missing:
                    signal_issues.append([index, item.get("city"), item.get("target_date"), ", ".join(missing)])

    module_issues = []
    for ev in _iter_city_evidence(payload):
        city = ev.get("city") or ev.get("city_key")
        modules = ev.get("modules") or []
        if isinstance(modules, dict):
            modules = list(modules.values())
        if isinstance(modules, list):
            for module in modules:
                if not isinstance(module, dict):
                    continue
                ready = bool(module.get("ready"))
                rows = module.get("rows")
                count = module.get("count")
                row_count = rows if isinstance(rows, int) else count if isinstance(count, int) else len(rows) if isinstance(rows, list) else None
                if ready and (row_count is None or row_count <= 0):
                    module_issues.append([city, module.get("id") or module.get("name"), ready, row_count, "ready_without_rows"])

    recs = payload.get("recommendations") or {}
    rec_items = recs.get("items") if isinstance(recs, dict) else []
    rec_issues = []
    rec_required_aliases = {
        "city": ["city", "city_key"],
        "station_id": ["station_id"],
        "metar_age": ["metar_age_minutes", "metar_age_seconds"],
        "verification_status": ["verified_status", "verification_status", "settlement_verification_status"],
    }
    if isinstance(rec_items, list):
        for index, item in enumerate(rec_items):
            if not isinstance(item, dict):
                continue
            missing = [
                label
                for label, aliases in rec_required_aliases.items()
                if _missing_alias(item, label, aliases)
            ]
            kind = str(item.get("kind") or item.get("type") or "")
            is_observation = kind == "observation_only" or item.get("observation_only")
            if not is_observation and not (item.get("token_id") or item.get("yes_token_id")):
                missing.append("token_id/yes_token_id")
            if missing:
                rec_issues.append([index, item.get("city"), kind or "--", ", ".join(sorted(set(missing)))])

    p0 = []
    if signal_issues and weather_signals:
        p0.append(f"{len(signal_issues)} weather signal rows are missing required fields")
    if module_issues:
        p0.append(f"{len(module_issues)} city evidence modules are ready without row support")
    if rec_issues:
        p0.append(f"{len(rec_issues)} recommendation rows are missing required fields")
    blockers = p0 or ["No Layer 7 P0 contract blocker found"]

    return f"""# Layer 7 Dashboard Contract Verification

Generated: {_utc_now().isoformat()}
URL: {url}
Payload source: {source}

## Payload Top-Level

{_table(["key", "type"], [[key, type(value).__name__] for key, value in sorted(payload.items())[:80]])}

## Weather Signal Field Issues

{_table(["index", "city", "target_date", "missing"], signal_issues[:50]) if signal_issues else "No weather signal field issues found, or no weather_signals list is present."}

## City Evidence Module Issues

{_table(["city", "module", "ready", "rows", "issue"], module_issues[:50]) if module_issues else "No ready-without-rows city evidence module issues found."}

## Recommendation Field Issues

{_table(["index", "city", "kind", "missing"], rec_issues[:50]) if rec_issues else "No recommendation field issues found, or no recommendation items are present."}

## P0 Findings

{chr(10).join("- " + item for item in p0) if p0 else "None."}

## Layer Conclusion

- Can enter next layer: {"yes" if not p0 else "no"}
- Blockers:
{chr(10).join("  - " + item for item in blockers)}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765/api/dashboard")
    parser.add_argument("--out-dir", default=str(ROOT / "audits" / f"layer-verify-{_utc_now().date().isoformat()}"))
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "layer7_dashboard.md"
    path.write_text(build_report(args.url), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
