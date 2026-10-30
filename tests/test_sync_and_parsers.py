"""Test per i parser Garmin, la sincronizzazione incrementale/idempotente (inclusi gli endpoint estesi di Fase 2b)
e il server Flask.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from app import db, parsers, sync, synthetic
from app.garmin_client import GarminRateLimiter, SafeGarminClient
from app.web import create_app


class FakeGarminAPI:
    """Simula le risposte di python-garminconnect senza fare alcuna chiamata di rete."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.logged_in = False
        self.details_calls = 0
        self.zones_calls = 0

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
            "bodyBatteryDrainedValue": 60,
            "totalSteps": 8420,
            "activeKilocalories": 410.0,
            "moderateIntensityMinutes": 20,
            "vigorousIntensityMinutes": 15,
        }

    def get_sleep_data(self, cdate: str) -> dict[str, Any]:
        if cdate == "2026-10-29":
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
            },
            "sleepLevels": [
                {"startGMT": "2026-10-29T23:00:00.0", "endGMT": "2026-10-29T23:30:00.0", "activityLevel": 1.0}
            ],
        }

    def get_hrv_data(self, cdate: str) -> dict[str, Any]:
        if cdate == "2026-10-29":
            return {"hrvSummary": {"lastNightAvg": None}}
        return {
            "hrvSummary": {"lastNightAvg": 65.0},
            "hrvReadings": [
                {"hrvValue": 60, "readingTimeGMT": "2026-10-30T01:00:00.0"},
                {"hrvValue": 65, "readingTimeGMT": "2026-10-30T01:05:00.0"},
                {"hrvValue": 70, "readingTimeGMT": "2026-10-30T01:10:00.0"},
            ],
        }

    def get_heart_rates(self, cdate: str) -> dict[str, Any]:
        return {
            "calendarDate": cdate,
            "heartRateValues": [
                [1761780000000, 52],
                [1761780120000, 53],
                [1761780240000, 51],
            ],
        }

    def get_respiration_data(self, cdate: str) -> dict[str, Any]:
        if cdate == "2026-10-29":
            return {}
        return {
            "avgSleepRespirationValue": 14.3,
            "avgWakingRespirationValue": 16.1,
            "respirationValuesArray": [[1761780000000, 14.0], [1761780120000, 14.5]],
        }

    def get_spo2_data(self, cdate: str) -> dict[str, Any]:
        if cdate == "2026-10-29":
            return {}
        return {
            "averageSpO2": 96.0,
            "lowestSpO2": 92.0,
            "spO2SingleValues": [[1761780000000, 96], [1761780060000, 95]],
        }

    def get_body_battery(self, startdate: str, enddate: str | None = None) -> list[dict[str, Any]]:
        return [
            {
                "date": startdate,
                "charged": 64,
                "drained": 60,
                "bodyBatteryValuesArray": [[1761780000000, 24], [1761806400000, 88]],
            }
        ]

    def get_training_readiness(self, cdate: str) -> list[dict[str, Any]]:
        if cdate == "2026-10-29":
            return []
        return [{"calendarDate": cdate, "score": 78, "inputContext": "AFTER_WAKEUP_RESET"}]

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
        self.zones_calls += 1
        return [
            {"zoneNumber": 1, "secsInZone": 300.0},
            {"zoneNumber": 2, "secsInZone": 1200.0},
            {"zoneNumber": 3, "secsInZone": 900.0},
            {"zoneNumber": 4, "secsInZone": 300.0},
            {"zoneNumber": 5, "secsInZone": 0.0},
        ]

    def get_activity_details(self, activity_id: str, maxchart: int = 2000, maxpoly: int = 4000) -> dict[str, Any]:
        self.details_calls += 1
        return synthetic.generate_synthetic_activity_details(
            activity_id=int(activity_id),
            day_date=date(2026, 10, 30),
            duration_s=2700.0,
            hr_avg=151.0,
        )


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


def test_inspect_timeseries_resolution_computes_steps() -> None:
    hr_info = parsers.inspect_timeseries_resolution(
        "heart_rates",
        {"heartRateValues": [[1000000, 50], [1120000, 52], [1240000, 51]]},
    )
    assert hr_info["available"] is True
    assert hr_info["series"]["heartRateValues"]["n_samples"] == 3
    assert hr_info["series"]["heartRateValues"]["median_step_s"] == 120.0

    details_payload = synthetic.generate_synthetic_activity_details(
        activity_id=101,
        day_date=date(2026, 10, 30),
        duration_s=1800.0,
        hr_avg=148.0,
        step_s=5,
    )
    det_info = parsers.inspect_timeseries_resolution("activity_details:101", details_payload)
    assert det_info["available"] is True
    assert det_info["series"]["activityDetailMetrics"]["median_step_s"] == 5.0
    assert "directHeartRate" in det_info["series"]["activityDetailMetrics"]["available_metrics"]


def test_incremental_sync_caches_activity_details_and_extended_metrics(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "garmin.db")
    db.init_db(conn)

    fake_api = FakeGarminAPI()
    limiter = GarminRateLimiter(min_delay_s=0.0, max_requests=200)
    client = SafeGarminClient(
        token_dir=tmp_path / "tokens",
        rate_limiter=limiter,
        client_factory=lambda *a, **kw: fake_api,
    )
    client.login(email="test@example.com", password="secret", interactive=False)

    # 1. Primo sync di 4 giorni: scarica tutti e 4 i giorni e 1 volta i dettagli dell'attività 99001
    res1 = sync.sync_garmin_data(conn, client, days=4, end_date=date(2026, 10, 30))
    assert res1["status"] == "ok"
    assert res1["days_synced"] == 4
    assert res1["activities_synced"] == 1
    assert res1["activity_details_fetched"] == 1
    assert fake_api.details_calls == 1
    assert fake_api.zones_calls == 1

    # 2. Secondo sync immediato sugli stessi 4 giorni:
    #    - scarica solo gli ultimi 2 giorni;
    #    - NON riscarica `activity_details:99001` né `activity_hr_zones:99001` perché sono già in `raw_garmin`!
    res2 = sync.sync_garmin_data(conn, client, days=4, end_date=date(2026, 10, 30))
    assert res2["days_synced"] == 2
    assert res2["activity_details_fetched"] == 0
    assert fake_api.details_calls == 1
    assert fake_api.zones_calls == 1

    # Verifica le metriche estese (HRV SD, respirazione, SpO2, training readiness) e i NULL preservati
    rows = db.fetch_daily_range(conn, "2026-10-29", "2026-10-30")
    assert len(rows) == 2
    row_29, row_30 = rows[0], rows[1]
    assert row_29["sleep_total_min"] is None
    assert row_29["hrv_night_ms"] is None
    assert row_29["respiration_sleep_brpm"] is None
    assert row_29["training_readiness_score"] is None

    assert row_30["sleep_total_min"] == 460.0
    assert row_30["hrv_night_ms"] == 65.0
    assert row_30["hrv_night_sd_ms"] == 5.0
    assert row_30["respiration_sleep_brpm"] == 14.3
    assert row_30["respiration_day_brpm"] == 16.1
    assert row_30["spo2_sleep_avg_pct"] == 96.0
    assert row_30["spo2_sleep_min_pct"] == 92.0
    assert row_30["training_readiness_score"] == 78.0

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

    for route in ("/", "/sleep", "/recovery", "/activities", "/trends", "/correlations", "/design"):
        resp = test_client.get(route)
        assert resp.status_code == 200, f"Route {route} failed"
        html = resp.get_data(as_text=True)
        assert "Garmin Personal Dashboard" in html
        assert "MODALITÀ DEMO" in html

    api_resp = test_client.get("/api/status")
    assert api_resp.status_code == 200
    data = api_resp.get_json()
    assert data["demo_mode"] is True
    assert data["days_count"] == 90

    day_resp = test_client.get("/api/day/2026-10-30")
    assert day_resp.status_code == 200
    day_data = day_resp.get_json()
    assert "scores" in day_data
    assert "baselines" in day_data

