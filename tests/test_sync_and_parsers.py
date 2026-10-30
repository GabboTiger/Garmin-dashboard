"""Test per i parser Garmin, la sincronizzazione incrementale/idempotente e il server Flask."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from app import db, parsers, sync
from app.garmin_client import GarminRateLimiter, SafeGarminClient
from app.web import create_app


class FakeGarminAPI:
    """Simula le risposte di python-garminconnect senza fare alcuna chiamata di rete."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.logged_in = False

    def login(self, tokenstore: str | None = None) -> tuple[None, None]:
        self.logged_in = True
        if tokenstore:
            p = Path(tokenstore)
            p.mkdir(parents=True, exist_ok=True)
            (p / "garmin_tokens.json").write_text('{"access_token": "fake"}', encoding="utf-8")
        return (None, None)

    def get_user_summary(self, cdate: str) -> dict[str, Any]:
        return {
            "calendarDate": cdate,
            "restingHeartRate": 51,
            "averageStressLevel": 26 if cdate != "2026-10-29" else -1,  # -1 = non misurato
            "bodyBatteryHighestValue": 88,
            "bodyBatteryLowestValue": 24,
            "bodyBatteryChargedValue": 64,
            "totalSteps": 8420,
            "activeKilocalories": 410.0,
            "moderateIntensityMinutes": 20,
            "vigorousIntensityMinutes": 15,
        }

    def get_sleep_data(self, cdate: str) -> dict[str, Any]:
        if cdate == "2026-10-29":
            # Simula una notte in cui l'orologio non era indossato (valori nulli)
            return {"dailySleepDTO": {"sleepTimeSeconds": None}}
        return {
            "dailySleepDTO": {
                "sleepTimeSeconds": 27600,  # 460 min
                "deepSleepSeconds": 5400,   # 90 min
                "lightSleepSeconds": 15600, # 260 min
                "remSleepSeconds": 6600,    # 110 min
                "awakeSleepSeconds": 900,   # 15 min
                "sleepStartTimestampLocal": 1761778800000,
                "sleepEndTimestampLocal": 1761806400000,
                "sleepScores": {"overall": {"value": 84}},
            }
        }

    def get_hrv_data(self, cdate: str) -> dict[str, Any]:
        if cdate == "2026-10-29":
            return {"hrvSummary": {"lastNightAvg": None}}
        return {"hrvSummary": {"lastNightAvg": 65.0}}

    def get_activities_by_date(self, startdate: str, enddate: str | None = None) -> list[dict[str, Any]]:
        return [
            {
                "activityId": 99001,
                "startTimeLocal": f"{enddate or startdate} 18:15:00",
                "activityType": {"typeKey": "running"},
                "activityName": "Corsa al parco",
                "duration": 2700.0,
                "distance": 9100.0,
                "averageHR": 151.0,
                "maxHR": 176.0,
                "activityTrainingLoad": 92.5,
            }
        ]

    def get_activity_hr_in_timezones(self, activity_id: str) -> list[dict[str, Any]]:
        return [
            {"zoneNumber": 1, "secsInZone": 300.0},
            {"zoneNumber": 2, "secsInZone": 1200.0},
            {"zoneNumber": 3, "secsInZone": 900.0},
            {"zoneNumber": 4, "secsInZone": 300.0},
            {"zoneNumber": 5, "secsInZone": 0.0},
        ]


def test_parsers_handle_real_shapes_and_preserve_nulls() -> None:
    s_row = parsers.parse_summary_payload(
        "2026-10-29",
        {"restingHeartRate": 50, "averageStressLevel": -1, "totalSteps": None},
    )
    assert s_row["rhr_bpm"] == 50.0
    assert s_row["stress_avg"] is None  # -1 in Garmin significa non misurato -> None!
    assert s_row["steps"] is None

    sl_row = parsers.parse_sleep_payload(
        "2026-10-29",
        {"dailySleepDTO": {"sleepTimeSeconds": 0, "deepSleepSeconds": 0}},
    )
    assert sl_row["sleep_total_min"] is None
    assert sl_row["sleep_deep_min"] is None


def test_incremental_sync_and_reparse(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "garmin.db")
    db.init_db(conn)

    limiter = GarminRateLimiter(min_delay_s=0.0, max_requests=100)
    client = SafeGarminClient(
        token_dir=tmp_path / "tokens",
        rate_limiter=limiter,
        client_factory=FakeGarminAPI,
    )
    client.login(email="test@example.com", password="secret", interactive=False)

    # 1. Primo sync di 4 giorni: deve scaricare tutti e 4 i giorni
    res1 = sync.sync_garmin_data(conn, client, days=4, end_date=date(2026, 10, 30))
    assert res1["status"] == "ok"
    assert res1["days_synced"] == 4
    assert res1["activities_synced"] == 1

    # 2. Secondo sync immediato sugli stessi 4 giorni:
    #    essendo incrementale, i primi 2 giorni sono già in raw_garmin e aggiorna solo gli ultimi 2!
    to_fetch = sync.compute_days_to_fetch(conn, days=4, end_date=date(2026, 10, 30))
    assert to_fetch == ["2026-10-29", "2026-10-30"]

    # Verifica che il giorno 2026-10-29 abbia sonno e HRV a None (non 0) e il 2026-10-30 abbia i dati completi
    rows = db.fetch_daily_range(conn, "2026-10-29", "2026-10-30")
    assert len(rows) == 2
    row_29, row_30 = rows[0], rows[1]
    assert row_29["sleep_total_min"] is None
    assert row_29["hrv_night_ms"] is None
    assert row_29["stress_avg"] is None
    assert row_30["sleep_total_min"] == 460.0
    assert row_30["sleep_score"] == 84.0
    assert row_30["hrv_night_ms"] == 65.0
    assert row_30["intensity_min"] == 50.0  # 20 + 2*15

    # 3. Cancelliamo `daily` e `activities` e verifichiamo che `reparse_all_from_raw` ricostruisca tutto dal grezzo
    conn.execute("DELETE FROM daily;")
    conn.execute("DELETE FROM activities;")
    conn.commit()

    rep = sync.reparse_all_from_raw(conn)
    assert rep["days_reparsed"] == 4
    assert rep["activities_reparsed"] == 1

    acts = db.fetch_activities_range(conn)
    assert len(acts) == 1
    assert acts[0]["z2_s"] == 1200.0
    conn.close()


def test_web_app_demo_and_status_endpoint(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "demo.db", demo_mode=True)
    test_client = app.test_client()

    resp = test_client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Garmin Personal Dashboard" in html
    assert "MODALITÀ DEMO" in html

    api_resp = test_client.get("/api/status")
    assert api_resp.status_code == 200
    data = api_resp.get_json()
    assert data["demo_mode"] is True
    assert data["days_count"] == 90
