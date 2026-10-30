"""Test per Recupero e Tendenze (Fase 3d - Step 2):
- Banda HRV individuale (ln rMSSD, media 7g vs media ± k*SD a 60g, CV)
- Pendenza di Theil-Sen, test di Mann-Kendall, punti di cambio con CUSUM, anomalie con z robusto, ciclo settimanale
- Ricarica notturna del Body Battery
- Effetto delle sedute dure vs facili/riposo sul recupero della notte successiva (bootstrap IC 95%)
"""

from __future__ import annotations

from datetime import date, timedelta

from app import synthetic
from app.analysis import recovery_trends


def test_hrv_band_ln_rmssd_and_cv() -> None:
    daily_rows, _ = synthetic.generate_synthetic_dataset(
        days=90,
        end_date=date(2026, 10, 30),
        seed=42,
    )
    band_res = recovery_trends.compute_hrv_band(daily_rows, target_day="2026-10-30")
    assert band_res["calculable"] is True
    latest = band_res["value"]["latest"]
    assert latest["ln_rmssd_7d"] is not None
    assert latest["ln_band_low"] < latest["ln_band_high"]
    assert latest["cv_7d_pct"] is not None and latest["cv_7d_pct"] > 0
    assert latest["band_status"] in ("in_band", "below_band", "above_band")


def test_theil_sen_mann_kendall_cusum_and_anomalies() -> None:
    # Costruiamo una serie di 35 giorni con un trend crescente netto, un cambio di regime al giorno 20 e 1 outlier estremo
    start = date(2026, 9, 25)
    rows = []
    for i in range(35):
        d_str = (start + timedelta(days=i)).isoformat()
        val = 50.0 + 0.5 * i + (12.0 if i >= 20 else 0.0)
        if i == 10:
            val = 140.0  # Outlier singolo: Theil-Sen lo ignora, z robusto lo segnala come anomalia!
        rows.append({"day": d_str, "hrv_night_ms": val})

    res = recovery_trends.compute_metric_trend_analysis(rows, metric="hrv_night_ms", unit="ms", window_days=35)
    assert res["calculable"] is True
    val = res["value"]
    ts_mk = val["theil_sen_mann_kendall"]
    assert ts_mk["trend_direction"] == "increasing"
    assert ts_mk["mk_p_value"] < 0.01
    assert ts_mk["slope_per_day"] > 0.4
    assert len(val["anomalies"]) >= 1
    assert any(a["day"] == (start + timedelta(days=10)).isoformat() for a in val["anomalies"])
    assert len(val["cusum_changepoints"]) >= 1
    assert len(val["weekly_cycle"]) == 7


def test_training_to_recovery_bootstrap_recovers_synthetic_hrv_drop() -> None:
    """Nel generatore sintetico le sedute dure riducono l'HRV e alzano la RHR nella notte successiva.
    Verifichiamo che l'analisi evento con bootstrap ritrovi questo effetto col segno atteso e IC 95% significativo.
    """
    daily_rows, activities = synthetic.generate_synthetic_dataset(
        days=90,
        end_date=date(2026, 10, 30),
        seed=42,
    )
    eff = recovery_trends.compute_training_to_recovery_effect(daily_rows, activities)
    assert eff["calculable"] is True
    val = eff["value"]
    assert val["n_hard_sessions"] >= 15
    assert val["n_easy_sessions"] + val["n_rest_days"] >= 30

    # Dopo sedute dure, l'HRV della notte successiva è più bassa (diff < 0 e limite superiore IC95% < 0)
    hrv_eff = val["hrv_next_night"]
    assert hrv_eff["diff_hard_vs_non_hard_ms"] < -2.0
    assert hrv_eff["ci95_high_ms"] < 0.0

    # E la FC a riposo della notte successiva è più alta (diff > 0 e limite inferiore IC95% > 0)
    rhr_eff = val["rhr_next_night"]
    assert rhr_eff["diff_hard_vs_non_hard_bpm"] > 1.0
    assert rhr_eff["ci95_low_bpm"] > 0.0


def test_multisport_comparison_profiles() -> None:
    """Verifica il profilo fisiologico Multi-Sport (Idea 4) su più discipline."""
    daily_rows, activities = synthetic.generate_synthetic_dataset(
        days=60,
        end_date=date(2026, 10, 30),
        seed=42,
    )
    res = recovery_trends.compute_multisport_comparison(daily_rows, activities)
    assert res["calculable"] is True
    sports = res["value"]["sports"]
    assert len(sports) >= 2
    for sp in sports:
        assert sp["sessions_count"] >= 1
        assert sp["trimp_per_min"] >= 0.0
        assert sp["next_night_hrv_ms"] is not None


def test_compute_tag_impacts_detects_hrv_difference() -> None:
    """Verifica che `compute_tag_impacts` (Idea 5) calcoli lo scostamento mediano su HRV, RHR, Sonno e Stress."""
    daily_rows, _ = synthetic.generate_synthetic_dataset(
        days=40,
        end_date=date(2026, 10, 30),
        seed=42,
    )
    tags_by_day: dict[str, list[str]] = {}
    for r in daily_rows:
        if r.get("hrv_night_ms") is not None and float(r["hrv_night_ms"]) < 55.0:
            tags_by_day[str(r["day"])] = ["cena_tardi"]

    res = recovery_trends.compute_tag_impacts(daily_rows, tags_by_day)
    assert res["calculable"] is True
    tags = res["value"]["tags"]
    assert len(tags) == 1
    assert tags[0]["tag"] == "cena_tardi"
    assert tags[0]["hrv_delta_ms"] < -3.0



