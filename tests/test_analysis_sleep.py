"""Test per le Analisi Sonno (Fase 3c - Step 2):
- Architettura del sonno (efficienza, % fasi, risvegli, latenza al primo Deep e REM, cicli stimati, ipnogramma)
- Regolarità degli orari (deviazione standard orari, metà del sonno, social jetlag feriali/weekend, SRI)
- Fisiologia notturna (FC minima e orario, SpO2 sotto soglia configurabile) e debito di sonno a 7/14 giorni
"""

from __future__ import annotations

from datetime import date

from app import synthetic
from app.analysis import sleep
from app.config import AnalysisConfig


def test_sleep_architecture_hypnogram_cycles_and_latencies() -> None:
    daily_rows, _ = synthetic.generate_synthetic_dataset(
        days=30,
        end_date=date(2026, 10, 30),
        seed=42,
    )
    target_row = daily_rows[-1]
    sleep_raw = synthetic.generate_synthetic_sleep_raw(date(2026, 10, 30), target_row, seed=42)

    arch = sleep.compute_sleep_architecture(target_row, sleep_raw)
    assert arch["calculable"] is True
    assert arch["quality_flag"] == "good"
    val = arch["value"]
    assert 85.0 <= val["efficiency_pct"] <= 100.0
    assert abs((val["deep_pct"] + val["light_pct"] + val["rem_pct"]) - 100.0) <= 1.0
    assert val["awakenings_count"] == 2
    assert val["estimated_cycles"] == 4
    assert val["latency_to_deep_min"] is not None and 10.0 <= val["latency_to_deep_min"] <= 30.0
    assert val["latency_to_rem_min"] is not None and 50.0 <= val["latency_to_rem_min"] <= 100.0
    assert len(val["hypnogram"]) >= 10


def test_sleep_architecture_not_calculable_when_missing() -> None:
    res = sleep.compute_sleep_architecture({"day": "2026-10-30", "sleep_total_min": None}, None)
    assert res["calculable"] is False
    assert res["value"] is None
    assert res["label"] == "non calcolabile"


def test_sleep_regularity_and_sri() -> None:
    daily_rows, _ = synthetic.generate_synthetic_dataset(
        days=30,
        end_date=date(2026, 10, 30),
        seed=42,
    )
    reg = sleep.compute_sleep_regularity(daily_rows, target_day="2026-10-30", window_days=30)
    assert reg["calculable"] is True
    val = reg["value"]
    assert val["bedtime_sd_min"] > 0
    assert val["midsleep_mean_hhmm"] is not None
    assert val["social_jetlag_min"] is not None and val["social_jetlag_min"] >= 0
    assert val["sri_score"] is not None and 50.0 <= val["sri_score"] <= 100.0


def test_nocturnal_physiology_spo2_threshold_and_sleep_debt() -> None:
    daily_rows, _ = synthetic.generate_synthetic_dataset(
        days=30,
        end_date=date(2026, 10, 30),
        seed=42,
    )
    target_row = daily_rows[-1]
    sleep_raw = synthetic.generate_synthetic_sleep_raw(date(2026, 10, 30), target_row, seed=42)

    # Usiamo una soglia SpO2 alta (96%) per verificare che conti effettivamente i minuti sotto soglia
    cfg = AnalysisConfig(spo2_threshold_pct=96.0, sleep_need_min=480.0)
    phys = sleep.compute_nocturnal_physiology_and_debt(daily_rows, "2026-10-30", sleep_raw, config=cfg)
    assert phys["calculable"] is True
    val = phys["value"]
    assert val["min_hr_bpm"] is not None and 40.0 <= val["min_hr_bpm"] <= 65.0
    assert val["min_hr_time_hhmm"] is not None
    assert val["spo2_below_threshold_min"] is not None and val["spo2_below_threshold_min"] > 0.0
    assert val["sleep_debt_7d_min"] is not None and val["sleep_debt_7d_min"] >= 0.0
    assert val["sleep_debt_14d_hours"] is not None


def test_hypnogram_timezone_offset_applied() -> None:
    """Verifica che il delta tra ora GMT e ora locale del dispositivo Garmin (+1h o +2h)
    venga applicato correttamente agli intervalli dell'ipnogramma per mostrare gli orari reali di sonno e sveglia.
    """
    # Esempio: addormentamento 23:15 GMT -> 00:15 Locale (+1h = +3600000 ms), sveglia 07:13 GMT -> 08:13 Locale
    payload = {
        "dailySleepDTO": {
            "sleepStartTimestampGMT": 1793315731000,
            "sleepStartTimestampLocal": 1793319331000,  # +1h
        },
        "sleepLevels": [
            {
                "startGMT": "2026-10-29T23:15:31.0",
                "endGMT": "2026-10-29T23:45:31.0",
                "activityLevel": 1.0,
            },
            {
                "startGMT": "2026-10-30T07:00:31.0",
                "endGMT": "2026-10-30T07:13:31.0",
                "activityLevel": 1.0,
            },
        ],
    }
    intervals = sleep.extract_hypnogram_intervals(payload)
    assert len(intervals) == 2
    # Invece di 23:15, deve essere convertito a 00:15
    assert intervals[0]["start_iso"] == "00:15"
    assert intervals[0]["end_iso"] == "00:45"
    # L'ultimo intervallo (sveglia) deve essere 08:13 anziché 07:13 GMT
    assert intervals[1]["start_iso"] == "08:00"
    assert intervals[1]["end_iso"] == "08:13"


