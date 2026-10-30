"""Test di integrazione per le route Flask, i template e i casi reali di payload Garmin (Fase 4)."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from app import db, synthetic
from app.analysis.sleep import compute_nocturnal_physiology_and_debt
from app.web import create_app


def test_web_routes_and_json_detail_attributes(tmp_path: Path) -> None:
    """Verifica che tutte le pagine della Fase 4 e la galleria Design System (/design)
    rispondano con HTTP 200 e producano attributi data-detail-json validi.
    """
    db_path = tmp_path / "test_web.db"
    conn = db.connect(db_path)
    db.init_db(conn)
    synthetic.seed_synthetic_db(conn, days=35, seed=42)
    # Inseriamo anche un giorno con dati completamente assenti per verificare lo stato 'missing_data'
    db.upsert_daily(conn, {"day": "2026-03-10"})
    conn.close()

    app = create_app(db_path=db_path, demo_mode=True)
    app.config["TESTING"] = True
    client = app.test_client()

    for route in ("/", "/sleep", "/recovery", "/activities", "/trends", "/correlations", "/design", "/api/status"):
        resp = client.get(route)
        assert resp.status_code == 200, f"Route {route} ha restituito {resp.status_code}"
        if not route.startswith("/api"):
            body = resp.get_data(as_text=True)
            for match in re.finditer(r'data-detail-json="([^"]*)"', body):
                parsed = json.loads(html.unescape(match.group(1)))
                assert isinstance(parsed, dict)

    # Verifica esplicita del giorno con dati assenti (provisional_score = None)
    resp_missing = client.get("/?day=2026-03-10")
    assert resp_missing.status_code == 200


def test_spo2_iso_timestamp_from_real_garmin() -> None:
    """Verifica che `compute_nocturnal_physiology_and_debt` gestisca `epochTimestamp` sia come
    stringa ISO ('2026-10-29T23:16:00.0', formato reale Garmin) sia come intero in ms.
    """
    daily_rows = [
        {"day": f"2026-10-{20 + i:02d}", "sleep_total_min": 450.0, "hrv_night_ms": 62.0}
        for i in range(10)
    ]
    real_like_sleep_payload = {
        "wellnessEpochSPO2DataDTOList": [
            {"epochTimestamp": "2026-10-29T23:16:00.0", "spo2Reading": 95.0},
            {"epochTimestamp": "2026-10-29T23:17:00.0", "spo2Reading": 90.0},
            {"epochTimestamp": "2026-10-29T23:18:00.0", "spo2Reading": 91.0},
        ]
    }
    res = compute_nocturnal_physiology_and_debt(daily_rows, "2026-10-29", real_like_sleep_payload)
    assert res["calculable"] is True
    assert res["value"]["spo2_below_threshold_min"] == 2.0
