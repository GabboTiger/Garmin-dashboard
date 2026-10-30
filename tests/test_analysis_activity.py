"""Test per le Analisi Attività (Fase 3b - Step 2):
- Recupero FC a 60 s e 120 s (HRR) entro tolleranza dichiarata (±2.0 bpm) e casi 'non calcolabile'
- Deriva cardiaca su sedute costanti >= 30 min entro tolleranza dichiarata (±1.2%) e rifiuto sedute brevi/variabili
- TRIMP (Banister serie temporale, FC media, fallback zone)
- Distribuzione settimanale nelle 5 zone FC
- Carico acuto/cronico (ATL 7g, CTL 28g, TSB, ACWR)
- Efficienza velocità/FC (metri per battito)
"""

from __future__ import annotations

from datetime import date

from app import synthetic
from app.analysis import activity


def test_hrr_recovers_known_synthetic_signal_within_tolerance() -> None:
    """Verifica che `compute_heart_rate_recovery` ritrovi il calo HRR noto (24 bpm a 60s, 39 bpm a 120s)
    entro una tolleranza di ±2.0 bpm.
    """
    payload = synthetic.generate_synthetic_activity_details(
        activity_id=501,
        day_date=date(2026, 10, 30),
        duration_s=2400.0,
        hr_avg=155.0,
        hrr60_drop=synthetic.KNOWN_HRR_60_BPM,
        hrr120_drop=synthetic.KNOWN_HRR_120_BPM,
        include_post_recovery=True,
        step_s=5,
        seed=42,
    )
    res = activity.compute_heart_rate_recovery(payload)
    assert res["calculable"] is True
    assert res["quality_flag"] == "good"
    assert res["n_samples"] >= 25
    assert abs(res["value"]["hrr_60_bpm"] - synthetic.KNOWN_HRR_60_BPM) <= 2.0
    assert abs(res["value"]["hrr_120_bpm"] - synthetic.KNOWN_HRR_120_BPM) <= 2.0
    assert "formula_latex" in res["how_it_works"]


def test_hrr_returns_not_calculable_when_stopped_immediately_or_low_res() -> None:
    """Se l'orologio viene fermato subito a fine corsa (nessuna coda post-esercizio) o il passo è > 25 s,
    deve restituire 'non calcolabile' e value=None.
    """
    no_post_payload = synthetic.generate_synthetic_activity_details(
        activity_id=502,
        day_date=date(2026, 10, 30),
        duration_s=2400.0,
        hr_avg=155.0,
        include_post_recovery=False,
        step_s=5,
    )
    res_no_post = activity.compute_heart_rate_recovery(no_post_payload)
    assert res_no_post["calculable"] is False
    assert res_no_post["value"] is None
    assert res_no_post["label"] == "non calcolabile"
    assert "interrotta subito" in (res_no_post["reason"] or "")

    low_res_payload = synthetic.generate_synthetic_activity_details(
        activity_id=503,
        day_date=date(2026, 10, 30),
        duration_s=2400.0,
        hr_avg=155.0,
        include_post_recovery=True,
        step_s=60,  # Risoluzione troppo bassa (60 s > 25 s)
    )
    res_low_res = activity.compute_heart_rate_recovery(low_res_payload)
    assert res_low_res["calculable"] is False
    assert res_low_res["quality_flag"] == "low_resolution"


def test_cardiac_drift_recovers_known_synthetic_signal_within_tolerance() -> None:
    """Verifica che `compute_cardiac_drift` ritrovi la deriva cardiaca nota (+6.0%) entro ±1.2%."""
    payload = synthetic.generate_synthetic_activity_details(
        activity_id=601,
        day_date=date(2026, 10, 30),
        duration_s=3000.0,  # 50 min (> 30 min minimi)
        hr_avg=152.0,
        speed_mps=3.3,
        drift_pct=synthetic.KNOWN_CARDIAC_DRIFT_PCT,
        include_post_recovery=True,
        step_s=5,
        seed=42,
    )
    res = activity.compute_cardiac_drift(payload)
    assert res["calculable"] is True
    assert res["quality_flag"] == "good"
    drift_measured = res["value"]["hr_drift_pct"]
    assert abs(drift_measured - synthetic.KNOWN_CARDIAC_DRIFT_PCT) <= 1.2

    # Seduta troppo breve (20 min < 30 min) -> non calcolabile
    short_payload = synthetic.generate_synthetic_activity_details(
        activity_id=602,
        day_date=date(2026, 10, 30),
        duration_s=1200.0,
        hr_avg=150.0,
        include_post_recovery=False,
    )
    res_short = activity.compute_cardiac_drift(short_payload)
    assert res_short["calculable"] is False
    assert res_short["value"] is None


def test_trimp_zones_ewma_and_efficiency() -> None:
    daily_rows, activities = synthetic.generate_synthetic_dataset(
        days=90,
        end_date=date(2026, 10, 30),
        seed=42,
    )
    sample_act = next(a for a in activities if a["type"] == "running")

    # 1. TRIMP e Efficienza velocità/FC
    trimp_res = activity.compute_trimp(sample_act, hr_rest=51.0)
    assert trimp_res["calculable"] is True
    assert trimp_res["value"] > 20.0

    ef_res = activity.compute_speed_hr_efficiency(sample_act)
    assert ef_res["calculable"] is True
    assert 0.8 <= ef_res["value"] <= 2.2  # m/battito realistici per la corsa

    # 2. Distribuzione settimanale zone
    zones_res = activity.compute_weekly_zone_distribution(activities, end_day="2026-10-30", window_days=7)
    assert zones_res["calculable"] is True
    assert abs(sum(zones_res["value"]["zone_pct"]) - 100.0) <= 0.5

    # 3. Carico acuto/cronico (EWMA 7 e 28 giorni)
    load_res = activity.compute_acute_chronic_load(daily_rows, activities, target_day="2026-10-30")
    assert load_res["calculable"] is True
    assert load_res["n_samples"] == 90
    assert load_res["value"]["ctl_28d"] > 0
    assert len(load_res["value"]["series"]) == 90


def test_foster_monotony_strain_and_whatif_simulator() -> None:
    """Verifica il calcolo di Monotonia e Strain di Foster (Idea 3) e il simulatore What-If a 7 giorni (Idea 6)."""
    daily_rows, activities = synthetic.generate_synthetic_dataset(
        days=45,
        end_date=date(2026, 10, 30),
        seed=42,
    )
    foster = activity.compute_foster_monotony_strain(daily_rows, activities, target_day="2026-10-30")
    assert foster["calculable"] is True
    assert foster["value"]["monotony_7d"] >= 0.0
    assert foster["value"]["foster_strain_7d"] >= 0.0
    assert len(foster["value"]["series"]) >= 30

    # Verifica caso non calcolabile (< 7 giorni)
    foster_short = activity.compute_foster_monotony_strain(daily_rows[:4], activities, target_day=daily_rows[3]["day"])
    assert foster_short["calculable"] is False

    # Simulatore deterministico a 7 giorni: settimana di riposo totale deve far scendere ATL e salire TSB
    sim_rest = activity.simulate_future_load(
        atl_0=60.0,
        ctl_0=50.0,
        planned_loads=[0.0] * 7,
        start_day="2026-10-30",
    )
    assert sim_rest["calculable"] is True
    assert len(sim_rest["value"]["forecast"]) == 7
    assert sim_rest["value"]["end_atl_7d"] < 60.0
    assert sim_rest["value"]["end_tsb_form"] > (50.0 - 60.0)
    assert sim_rest["value"]["simulated_monotony"] == 0.0

    # Settimana di sovraccarico piatto (7 giorni identici da 120 TRIMP) -> Monotonia plafonata a 4.0 e ATL sale
    sim_overload = activity.simulate_future_load(
        atl_0=45.0,
        ctl_0=45.0,
        planned_loads=[120.0] * 7,
        start_day="2026-10-30",
    )
    assert sim_overload["value"]["end_atl_7d"] > 45.0
    assert sim_overload["value"]["end_tsb_form"] < 0.0
    assert sim_overload["value"]["simulated_monotony"] == 4.0


def test_extract_activity_chart_series_downsamples_and_detects_hrr() -> None:
    """Verifica l'estrazione del tracciato secondo-per-secondo (Idea 2) per il pannello laterale."""
    details = synthetic.generate_synthetic_activity_details(
        activity_id=777,
        day_date=date(2026, 10, 30),
        duration_s=2700.0,
        hr_avg=152.0,
        speed_mps=3.2,
        include_post_recovery=True,
        seed=42,
    )
    series = activity.extract_activity_chart_series(details, max_points=200)
    assert series["available"] is True
    assert series["n_raw_samples"] >= 100
    assert len(series["elapsed_min"]) <= 202
    assert len(series["hr_bpm"]) == len(series["elapsed_min"])
    assert series["half1_hr_mean"] is not None
    assert series["half2_hr_mean"] is not None
    assert len(series["hrr_markers"]) >= 1



