"""Test per il generatore di dati sintetici e la verifica delle correlazioni note."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app import analysis, db, synthetic


def test_synthetic_dataset_shape_and_null_handling(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "demo.db")
    db.init_db(conn)

    summary = synthetic.seed_synthetic_db(conn, days=90, end_date=date(2026, 10, 30), seed=42)
    assert summary["daily_days"] == 90
    assert summary["activities"] > 30

    daily_rows = db.fetch_daily_range(conn)
    assert len(daily_rows) == 90

    # Verifica che ci siano alcuni NULL e che non siano stati convertiti in 0
    missing_sleep = [r for r in daily_rows if r["sleep_total_min"] is None]
    missing_hrv = [r for r in daily_rows if r["hrv_night_ms"] is None]
    assert len(missing_sleep) == 2
    assert len(missing_hrv) == 3

    completeness = analysis.compute_completeness(daily_rows)
    assert completeness["sleep_total_min"]["present_days"] == 88
    assert completeness["hrv_night_ms"]["present_days"] == 87
    conn.close()


def test_synthetic_known_correlations_recovered() -> None:
    daily_rows, activities = synthetic.generate_synthetic_dataset(
        days=90,
        end_date=date(2026, 10, 30),
        seed=42,
        include_missing=True,
    )
    df = analysis.build_daily_dataframe(daily_rows, activities)

    # 1. Poco sonno al giorno t -> HRV più bassa il giorno dopo (t+1), quindi correlazione positiva tra sonno(t) e HRV(t+1)
    sleep_to_next_hrv = analysis.spearman_corr_with_lag(df, "sleep_total_min", "hrv_night_ms", lag_days=1)
    assert sleep_to_next_hrv["n_days"] >= 80
    assert sleep_to_next_hrv["rho"] is not None
    assert sleep_to_next_hrv["rho"] > 0.45
    assert "causalità" in sleep_to_next_hrv["label"]

    # Verifica che a lag=0 l'effetto sonno -> HRV sia molto più debole che a lag=1
    sleep_to_same_hrv = analysis.spearman_corr_with_lag(df, "sleep_total_min", "hrv_night_ms", lag_days=0)
    assert sleep_to_same_hrv["rho"] is not None
    assert sleep_to_next_hrv["rho"] > sleep_to_same_hrv["rho"] + 0.25

    # 2. Carico allenamento al giorno t -> RHR più alta il giorno dopo (t+1) e HRV più bassa a (t+1)
    load_to_next_rhr = analysis.spearman_corr_with_lag(df, "training_load_daily", "rhr_bpm", lag_days=1)
    assert load_to_next_rhr["rho"] is not None
    assert load_to_next_rhr["rho"] > 0.40

    load_to_next_hrv = analysis.spearman_corr_with_lag(df, "training_load_daily", "hrv_night_ms", lag_days=1)
    assert load_to_next_hrv["rho"] is not None
    assert load_to_next_hrv["rho"] < -0.30

    # 3. Stress medio al giorno t -> Body Battery massima più bassa nello stesso giorno (lag=0)
    stress_to_bb = analysis.spearman_corr_with_lag(df, "stress_avg", "bb_max", lag_days=0)
    assert stress_to_bb["rho"] is not None
    assert stress_to_bb["rho"] < -0.55

