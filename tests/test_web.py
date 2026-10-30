"""Test di integrazione per le route Flask, i template, l'export CSV e i casi reali Garmin (Fasi 4 e 5)."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from app import db, synthetic
from app.analysis.sleep import compute_nocturnal_physiology_and_debt
from app.web import create_app


def test_web_routes_and_json_detail_attributes(tmp_path: Path) -> None:
    """Verifica che tutte le pagine (Fasi 4 e 5), le finestre temporali, i lag e gli export CSV
    rispondano con HTTP 200 e producano attributi data-detail-json validi.
    """
    db_path = tmp_path / "test_web.db"
    conn = db.connect(db_path)
    db.init_db(conn)
    synthetic.seed_synthetic_db(conn, days=35, seed=42)
    # Inseriamo anche un giorno con dati completamente assenti per verificare lo stato 'missing_data'
    db.upsert_daily(conn, {"day": "2026-03-10"})
    conn.commit()
    conn.close()

    app = create_app(db_path=db_path, demo_mode=True)
    app.config["TESTING"] = True
    client = app.test_client()

    routes = (
        "/",
        "/sleep",
        "/recovery",
        "/activities",
        "/trends",
        "/trends?window=7&m1=sleep_total_min&m2=hrv_night_ms",
        "/trends?window=90",
        "/correlations?lag=0",
        "/correlations?lag=1",
        "/correlations?lag=2",
        "/design",
        "/api/status",
    )

    for route in routes:
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

    # Verifica esportazione CSV giornaliero (NULL preservato come stringa vuota, mai 0)
    csv_daily = client.get("/export/daily.csv")
    assert csv_daily.status_code == 200
    assert "text/csv" in csv_daily.content_type
    daily_text = csv_daily.get_data(as_text=True)
    assert "2026-03-10,," in daily_text

    # Verifica esportazione CSV attività
    csv_acts = client.get("/export/activities.csv")
    assert csv_acts.status_code == 200
    assert "activity_id,day,start_local" in csv_acts.get_data(as_text=True)

    # Verifica tema scuro fisso, calendario con giorni disponibili e sezioni del pannello laterale
    home_html = client.get("/").get_data(as_text=True)
    assert 'data-theme="dark"' in home_html
    assert 'id="theme-toggle-btn"' not in home_html
    assert 'id="calendar-popover"' in home_html
    assert 'id="drawer-kpi-grid"' in home_html
    assert 'id="drawer-zones-bars"' in home_html
    avail_match = re.search(r'data-available-days="([^"]*)"', home_html)
    assert avail_match is not None
    avail_days = json.loads(html.unescape(avail_match.group(1)))
    assert isinstance(avail_days, list) and len(avail_days) >= 35

    # Verifica arricchimento tabella attività e payload KPI nel drawer
    acts_html = client.get("/activities").get_data(as_text=True)
    assert "mini-zone-stack" in acts_html
    assert '"kpis":' in html.unescape(acts_html)
    assert '"zones_bars":' in html.unescape(acts_html)

    # Verifica API Fase 6B: toggle tag giornaliero, tracciato 1s attività e stato sync
    tag_resp = client.post("/api/day/2026-10-30/tags", json={"tag": "studio_esame"})
    assert tag_resp.status_code == 200
    assert "studio_esame" in tag_resp.get_json()["tags"]

    ts_resp = client.get("/api/activity/100001/timeseries")
    assert ts_resp.status_code == 200
    ts_payload = ts_resp.get_json()
    assert ts_payload["activity_id"] == 100001
    assert ts_payload["available"] is True
    assert len(ts_payload["hr_bpm"]) > 10

    sync_st = client.get("/api/sync/status")
    assert sync_st.status_code == 200
    assert "status" in sync_st.get_json()



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
