"""Test per lo schema SQLite, l'idempotenza degli upsert e la conservazione dei NULL."""

from __future__ import annotations

from pathlib import Path

from app import db


def test_init_and_raw_upsert_idempotent(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "test.db")
    db.init_db(conn)

    payload1 = {"sleepTimeSeconds": 27000}
    payload2 = {"sleepTimeSeconds": 28800}

    db.upsert_raw(conn, "2026-10-01", "sleep", payload1, fetched_at="2026-10-01T08:00:00Z")
    db.upsert_raw(conn, "2026-10-01", "sleep", payload2, fetched_at="2026-10-01T09:00:00Z")
    conn.commit()

    cur = conn.execute("SELECT COUNT(*) AS cnt FROM raw_garmin WHERE day = '2026-10-01' AND endpoint = 'sleep'")
    assert cur.fetchone()["cnt"] == 1

    saved = db.get_raw(conn, "2026-10-01", "sleep")
    assert saved == payload2
    assert db.get_raw(conn, "2026-10-02", "sleep") is None
    conn.close()


def test_daily_preserves_nulls_and_merges_on_upsert(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "test.db")
    db.init_db(conn)

    # Inseriamo solo il sonno; HRV e RHR devono restare NULL (mai 0!)
    db.upsert_daily(
        conn,
        {
            "day": "2026-10-10",
            "sleep_total_min": 450.0,
        },
    )
    conn.commit()

    rows = db.fetch_daily_range(conn, "2026-10-10", "2026-10-10")
    assert len(rows) == 1
    assert rows[0]["sleep_total_min"] == 450.0
    assert rows[0]["hrv_night_ms"] is None
    assert rows[0]["rhr_bpm"] is None

    # Secondo upsert sullo stesso giorno con HRV: il sonno resta 450.0 e HRV viene valorizzata
    db.upsert_daily(
        conn,
        {
            "day": "2026-10-10",
            "hrv_night_ms": 64.5,
        },
    )
    conn.commit()

    rows = db.fetch_daily_range(conn, "2026-10-10", "2026-10-10")
    assert len(rows) == 1
    assert rows[0]["sleep_total_min"] == 450.0
    assert rows[0]["hrv_night_ms"] == 64.5
    assert rows[0]["rhr_bpm"] is None
    conn.close()


def test_activities_upsert_idempotent(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "test.db")
    db.init_db(conn)

    act = {
        "activity_id": 12345,
        "day": "2026-10-15",
        "start_local": "2026-10-15T18:00:00",
        "type": "running",
        "name": "Corsa serale",
        "duration_s": 2400.0,
        "distance_m": 8000.0,
        "hr_avg": 150.0,
        "hr_max": 174.0,
        "training_load": 85.0,
    }
    db.upsert_activity(conn, act)
    db.upsert_activity(conn, {**act, "training_load": 90.0})
    conn.commit()

    acts = db.fetch_activities_range(conn, "2026-10-01", "2026-10-30")
    assert len(acts) == 1
    assert acts[0]["training_load"] == 90.0
    assert acts[0]["z1_s"] is None
    conn.close()


def test_daily_tags_toggle_and_fetch(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "test.db")
    db.init_db(conn)

    t1 = db.toggle_day_tag(conn, "2026-10-29", "Studio Esame")
    assert t1 == ["studio_esame"]

    t2 = db.toggle_day_tag(conn, "2026-10-29", "cena_tardi")
    assert t2 == ["cena_tardi", "studio_esame"]

    # Secondo toggle dello stesso tag lo rimuove
    t3 = db.toggle_day_tag(conn, "2026-10-29", "studio_esame")
    assert t3 == ["cena_tardi"]

    all_map = db.fetch_all_tags_by_day(conn)
    assert all_map == {"2026-10-29": ["cena_tardi"]}
    conn.close()


