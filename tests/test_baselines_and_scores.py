"""Test per le baseline personali (mediana a 30 giorni e MAD) e per i punteggi (Recupero, Sforzo, Sonno)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app import baselines, scores, synthetic


def _make_history_days(n_days: int, end_day: date = date(2026, 10, 30)) -> list[dict[str, Any]]:
    """Crea una serie di `n_days` giorni con valori noti per verificare a mano mediana e MAD."""
    start = end_day - timedelta(days=n_days - 1)
    rows: list[dict[str, Any]] = []
    for i in range(n_days):
        d_str = (start + timedelta(days=i)).isoformat()
        # Oscilla simmetricamente intorno a HRV = 60 ms (55, 60, 65) e RHR = 52 bpm (50, 52, 54)
        offset = (i % 3) - 1  # -1, 0, +1
        rows.append(
            {
                "day": d_str,
                "hrv_night_ms": 60.0 + 5.0 * offset,
                "rhr_bpm": 52.0 + 2.0 * offset,
                "sleep_total_min": 460.0,
                "sleep_deep_min": 90.0,
                "sleep_rem_min": 100.0,
                "sleep_light_min": 270.0,
                "sleep_awake_min": 15.0,
                "sleep_start_local": "23:15",
                "sleep_end_local": "07:10",
                "bb_max": 85.0,
                "stress_avg": 25.0,
                "steps": 8000,
            }
        )
    return rows


def test_baseline_calibrating_when_fewer_than_14_days() -> None:
    rows = _make_history_days(10, end_day=date(2026, 10, 30))
    b = baselines.compute_metric_baseline(rows, target_day="2026-10-30", metric="hrv_night_ms")
    assert b["status"] == "calibrating"
    assert b["n_days"] == 9  # 9 giorni precedenti al 2026-10-30
    assert b["z_robust"] is None
    assert b["direction_text"] == "calibrazione in corso"


def test_baseline_median_mad_and_outlier_robustness() -> None:
    rows = _make_history_days(25, end_day=date(2026, 10, 30))
    # Inseriamo 2 outlier estremi nello storico: la mediana (60.0) e il MAD (5.0) non devono spostarsi!
    rows[2]["hrv_night_ms"] = 180.0
    rows[5]["hrv_night_ms"] = 12.0
    # Impostiamo il giorno target (2026-10-30) ben sopra la norma: 75.0 ms
    rows[-1]["hrv_night_ms"] = 75.0

    b = baselines.compute_metric_baseline(rows, target_day="2026-10-30", metric="hrv_night_ms")
    assert b["status"] == "ok"
    assert b["n_days"] == 24
    assert b["median"] == 60.0
    assert b["mad"] == 5.0
    assert round(b["robust_sd"], 2) == round(1.4826 * 5.0, 2)
    assert b["z_robust"] > 1.8
    assert b["direction_arrow"] == "▲"
    assert b["direction_text"] == "sopra la tua norma"


def test_scores_calibration_guard_and_breakdown() -> None:
    # Con soli 10 giorni di storia, i punteggi devono restituire score=None e status='calibrating'
    short_rows = _make_history_days(10, end_day=date(2026, 10, 30))
    rec_short = scores.compute_recovery_score(short_rows, "2026-10-30")
    assert rec_short["status"] == "calibrating"
    assert rec_short["score"] is None
    assert rec_short["status_label"] == "calibrazione in corso"

    # Con 25 giorni di storia:
    rows = _make_history_days(25, end_day=date(2026, 10, 30))
    # Caso A: giornata di ottimo recupero (HRV alta = 74 ms, RHR bassa = 48 bpm, Sonno = 490 min, BB = 95)
    rows[-1].update(
        {
            "hrv_night_ms": 74.0,
            "rhr_bpm": 48.0,
            "sleep_total_min": 490.0,
            "bb_max": 95.0,
        }
    )
    rec_high = scores.compute_recovery_score(rows, "2026-10-30")
    assert rec_high["status"] == "ok"
    assert rec_high["score"] is not None
    assert rec_high["score"] >= 80
    assert len(rec_high["components"]) == 4
    assert abs(sum(c["weight"] for c in rec_high["components"]) - 1.0) < 0.01
    assert "formula_latex" in rec_high["how_it_works"]

    # Caso B: giornata di scarso recupero (HRV crollata = 44 ms, RHR alta = 58 bpm, Sonno = 320 min, BB = 55)
    rows[-1].update(
        {
            "hrv_night_ms": 44.0,
            "rhr_bpm": 58.0,
            "sleep_total_min": 320.0,
            "bb_max": 55.0,
        }
    )
    rec_low = scores.compute_recovery_score(rows, "2026-10-30")
    assert rec_low["score"] is not None
    assert rec_low["score"] < 45
    assert rec_high["score"] > rec_low["score"] + 35


def test_strain_and_sleep_scores_on_synthetic_data() -> None:
    daily_rows, activities = synthetic.generate_synthetic_dataset(
        days=90,
        end_date=date(2026, 10, 30),
        seed=42,
    )
    all_scores = scores.compute_all_scores_for_day(daily_rows, activities, "2026-10-30")
    for key in ("recovery", "strain", "sleep"):
        item = all_scores[key]
        assert item["status"] == "ok"
        assert item["score"] is not None
        assert 0 <= item["score"] <= 100
        assert len(item["components"]) >= 1
        assert "non costituiscono indicazioni" in item["disclaimer"]
