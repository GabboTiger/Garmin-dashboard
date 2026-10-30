"""Generatore di dati sintetici (90 giorni) con correlazioni e segnali noti per la modalità demo e i test.

Segnali e correlazioni inseriti deliberatamente nel modello generativo:
1. Correlazioni giornaliere (lag 0 e lag 1):
   - Sonno totale al giorno t -> HRV notturna più alta al giorno t+1 (lag = 1, correlazione positiva).
   - Carico di allenamento / seduta dura al giorno t -> RHR più alta e HRV più bassa al giorno t+1 (lag = 1).
   - Stress medio al giorno t -> Body Battery massima più bassa nello stesso giorno t (lag = 0, correlazione negativa).
2. Segnali noti intra/post-attività (salvati anche in `raw_garmin` per le analisi di Fase 3b):
   - Recupero FC post-esercizio (HRR) noto: su sedute con recupero registrato al secondo,
     calo esatto di ~24 bpm a +60 s e ~39 bpm a +120 s (più lieve rumore ±1.5 bpm).
   - Deriva cardiaca nota: sulle sedute di corsa costante >= 40 min, deriva FC tra prima e seconda metà
     pari a circa +6.0% a velocità costante (~3.2 m/s).
3. Segnali noti del sonno (salvati in `raw_garmin` per le analisi di Fase 3c):
   - Ipnogramma a intervalli (`sleepLevels`), serie FC notturna (`sleepHeartRate`),
     letture HRV ogni 5 min (`hrvReadings`), respirazione e SpO2 notturna.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np

from app.db import replace_daily, upsert_activity, upsert_raw

DEFAULT_DAYS = 90
DEFAULT_SEED = 42

# Costanti dei segnali sintetici noti (usate nei test di Fase 3b/3c/3d per verificare le tolleranze)
KNOWN_HRR_60_BPM = 24.0
KNOWN_HRR_120_BPM = 39.0
KNOWN_CARDIAC_DRIFT_PCT = 6.0


def _day_epoch_ms(d: date, hour: int, minute: int = 0, second: int = 0) -> int:
    dt = datetime(d.year, d.month, d.day, hour, minute, second, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def generate_synthetic_activity_details(
    activity_id: int,
    day_date: date,
    duration_s: float,
    hr_avg: float,
    speed_mps: float = 3.2,
    drift_pct: float = KNOWN_CARDIAC_DRIFT_PCT,
    hrr60_drop: float = KNOWN_HRR_60_BPM,
    hrr120_drop: float = KNOWN_HRR_120_BPM,
    include_post_recovery: bool = True,
    step_s: int = 5,
    seed: int = 42,
) -> dict[str, Any]:
    """Genera un payload compatibile con `get_activity_details(activity_id)` contenente:
    - riscaldamento nei primi 10 min;
    - tratto centrale a velocità costante (`speed_mps`) con deriva cardiaca controllata (`drift_pct` tra 1ª e 2ª metà);
    - se `include_post_recovery=True`, 150 s finali a velocità 0 (fermo post-esercizio) con calo HRR noto
      di `hrr60_drop` a +60 s e `hrr120_drop` a +120 s rispetto alla FC di fine sforzo.
    """
    rng = np.random.default_rng(seed + int(activity_id))
    start_ms = _day_epoch_ms(day_date, 18, 0, 0)

    active_dur_s = int(max(600, round(duration_s)))
    warmup_s = min(600, active_dur_s // 4)
    steady_s = active_dur_s - warmup_s
    half_steady_s = max(1, steady_s // 2)

    # Calibra hr_first_half e hr_second_half in modo che (hr_second / hr_first - 1)*100 == drift_pct
    drift_ratio = 1.0 + (drift_pct / 100.0)
    hr_first = hr_avg / (0.5 * (1.0 + drift_ratio))
    hr_second = hr_first * drift_ratio

    descriptors = [
        {"metricsIndex": 0, "key": "directTimestamp", "unit": {"key": "ms"}},
        {"metricsIndex": 1, "key": "sumElapsedDuration", "unit": {"key": "second"}},
        {"metricsIndex": 2, "key": "directHeartRate", "unit": {"key": "bpm"}},
        {"metricsIndex": 3, "key": "directSpeed", "unit": {"key": "mps"}},
        {"metricsIndex": 4, "key": "sumDistance", "unit": {"key": "meter"}},
    ]

    metrics_list: list[dict[str, Any]] = []
    dist_m = 0.0

    # 1. Fase attiva dell'esercizio (campionata ogni `step_s` secondi)
    for t_s in range(0, active_dur_s + 1, step_s):
        ts_ms = start_ms + t_s * 1000
        if t_s < warmup_s:
            frac = t_s / max(1, warmup_s)
            v = speed_mps * (0.75 + 0.25 * frac) + float(rng.normal(0, 0.03))
            hr = (hr_first - 18.0) + 18.0 * frac + float(rng.normal(0, 0.6))
        else:
            steady_elapsed = t_s - warmup_s
            # Tratto costante con rampa lineare tra metà 1 e metà 2 così la media della 2ª metà è +drift_pct%
            progress = steady_elapsed / max(1, steady_s)
            v = speed_mps + float(rng.normal(0, 0.02))
            hr = hr_first + (hr_second - hr_first) * (2.0 * progress - 0.5) + float(rng.normal(0, 0.4))

        if t_s > 0:
            dist_m += max(0.0, v) * step_s

        metrics_list.append(
            {
                "metrics": [
                    ts_ms,
                    float(t_s),
                    round(float(hr), 1),
                    round(float(max(0.0, v)), 3),
                    round(float(dist_m), 1),
                ]
            }
        )

    hr_end = float(metrics_list[-1]["metrics"][2])

    # 2. Fase di recupero post-esercizio (150 s a velocità 0, se inclusa)
    if include_post_recovery:
        for post_s in range(step_s, 150 + step_s, step_s):
            t_s = active_dur_s + post_s
            ts_ms = start_ms + t_s * 1000
            if post_s <= 60:
                drop = hrr60_drop * (post_s / 60.0)
            elif post_s <= 120:
                drop = hrr60_drop + (hrr120_drop - hrr60_drop) * ((post_s - 60.0) / 60.0)
            else:
                drop = hrr120_drop + 4.0 * ((post_s - 120.0) / 30.0)
            hr_post = hr_end - drop + float(rng.normal(0, 0.25))
            metrics_list.append(
                {
                    "metrics": [
                        ts_ms,
                        float(t_s),
                        round(float(hr_post), 1),
                        0.0,
                        round(float(dist_m), 1),
                    ]
                }
            )

    return {
        "activityId": activity_id,
        "metricDescriptors": descriptors,
        "activityDetailMetrics": metrics_list,
    }


def generate_synthetic_sleep_raw(
    day_date: date,
    row: dict[str, Any],
    seed: int = 42,
) -> dict[str, Any]:
    """Genera un payload compatibile con `get_sleep_data(day)` completo di ipnogramma (`sleepLevels`),
    battito notturno (`sleepHeartRate`), SpO2 e respirazione notturna.
    """
    day_str = day_date.isoformat()
    if row.get("sleep_total_min") is None:
        return {"dailySleepDTO": {"calendarDate": day_str, "sleepTimeSeconds": None}}

    rng = np.random.default_rng(seed + int(day_date.strftime("%Y%m%d")))
    tot_min = float(row["sleep_total_min"])
    deep_min = float(row["sleep_deep_min"] or 80.0)
    light_min = float(row["sleep_light_min"] or 240.0)
    rem_min = float(row["sleep_rem_min"] or 95.0)
    awake_min = float(row["sleep_awake_min"] or 15.0)

    # Costruisce un ipnogramma realistico a 4-5 cicli NREM -> REM (activityLevel: 0=Deep, 1=Light, 2=REM, 3=Awake)
    # Inizio sonno la sera precedente intorno alle 23:15
    prev_day = day_date - timedelta(days=1)
    start_hhmm = str(row.get("sleep_start_local") or "23:15")
    hh, mm = (int(x) for x in start_hhmm.split(":"))
    if hh >= 18:
        start_dt = datetime(prev_day.year, prev_day.month, prev_day.day, hh, mm, tzinfo=timezone.utc)
    else:
        start_dt = datetime(day_date.year, day_date.month, day_date.day, hh, mm, tzinfo=timezone.utc)

    # Sequenza deterministica di blocchi (minuti, livello: 1=Light, 0=Deep, 2=REM, 3=Awake)
    # Latenza al primo Deep = 18 min; Latenza al primo REM = 72 min; 4 cicli completi
    blocks = [
        (18, 1),  # Light iniziale (18 min -> primo Deep a +18 min)
        (32, 0),  # Deep 1
        (22, 1),  # Light
        (20, 2),  # REM 1 (a +72 min dall'addormentamento)
        (4, 3),   # Risveglio breve 1 (4 min)
        (28, 1),  # Light
        (28, 0),  # Deep 2
        (24, 1),  # Light
        (26, 2),  # REM 2
        (35, 1),  # Light
        (18, 0),  # Deep 3
        (25, 1),  # Light
        (30, 2),  # REM 3
        (5, 3),   # Risveglio breve 2 (5 min)
        (45, 1),  # Light
        (28, 2),  # REM 4
        (20, 1),  # Light finale
    ]
    scale = (tot_min + awake_min) / sum(b[0] for b in blocks)
    sleep_levels: list[dict[str, Any]] = []
    cur_dt = start_dt
    for dur_m, lvl in blocks:
        scaled_s = max(60, int(round(dur_m * scale * 60)))
        nxt_dt = cur_dt + timedelta(seconds=scaled_s)
        sleep_levels.append(
            {
                "startGMT": cur_dt.strftime("%Y-%m-%dT%H:%M:%S.0"),
                "endGMT": nxt_dt.strftime("%Y-%m-%dT%H:%M:%S.0"),
                "activityLevel": float(lvl),
            }
        )
        cur_dt = nxt_dt

    end_dt = cur_dt
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    # Serie FC notturna ogni 5 minuti con nadir (minimo) intorno a metà notte
    rhr = float(row.get("rhr_bpm") or 52.0)
    n_hr_pts = max(12, int((end_ms - start_ms) // (5 * 60 * 1000)))
    sleep_hr: list[dict[str, Any]] = []
    for k in range(n_hr_pts):
        ts = start_ms + k * 5 * 60 * 1000
        phase = k / max(1, n_hr_pts - 1)
        # Curva a U con minimo a phase ~ 0.55
        hr_val = rhr - 3.0 + 7.5 * ((phase - 0.55) ** 2) / 0.30 + float(rng.normal(0, 0.7))
        sleep_hr.append({"startGMT": ts, "value": round(float(hr_val), 1)})

    # Serie SpO2 notturna (1 campione ogni 5 min)
    spo2_avg = float(row.get("spo2_sleep_avg_pct") or 95.5)
    spo2_epochs: list[dict[str, Any]] = []
    for k in range(n_hr_pts):
        ts = start_ms + k * 5 * 60 * 1000
        val = round(float(np.clip(rng.normal(spo2_avg, 1.4), 89.0, 99.0)), 0)
        spo2_epochs.append({"epochTimestamp": ts, "spo2Reading": val})

    return {
        "dailySleepDTO": {
            "calendarDate": day_str,
            "sleepTimeSeconds": int(round(tot_min * 60)),
            "deepSleepSeconds": int(round(deep_min * 60)),
            "lightSleepSeconds": int(round(light_min * 60)),
            "remSleepSeconds": int(round(rem_min * 60)),
            "awakeSleepSeconds": int(round(awake_min * 60)),
            "sleepStartTimestampLocal": start_ms,
            "sleepEndTimestampLocal": end_ms,
            "sleepStartTimestampGMT": start_ms,
            "sleepEndTimestampGMT": end_ms,
            "sleepScores": {"overall": {"value": row.get("sleep_score")}},
            "averageRespirationValue": row.get("respiration_sleep_brpm"),
            "averageSpO2Value": row.get("spo2_sleep_avg_pct"),
            "lowestSpO2Value": row.get("spo2_sleep_min_pct"),
            "avgOvernightHrv": row.get("hrv_night_ms"),
        },
        "sleepLevels": sleep_levels,
        "sleepHeartRate": sleep_hr,
        "wellnessEpochSPO2DataDTOList": spo2_epochs,
    }


def generate_synthetic_dataset(
    days: int = DEFAULT_DAYS,
    end_date: date | None = None,
    seed: int = DEFAULT_SEED,
    include_missing: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Genera tuple (daily_rows, activity_rows) per `days` giorni fino a `end_date`."""
    if end_date is None:
        end_date = date.today()
    rng = np.random.default_rng(seed)

    start_date = end_date - timedelta(days=days - 1)
    dates = [start_date + timedelta(days=i) for i in range(days)]

    sleep_total = np.zeros(days)
    sleep_deep = np.zeros(days)
    sleep_rem = np.zeros(days)
    sleep_light = np.zeros(days)
    sleep_awake = np.zeros(days)
    sleep_score = np.zeros(days)
    sleep_start_str: list[str | None] = []
    sleep_end_str: list[str | None] = []

    stress_avg = np.zeros(days)
    daily_load = np.zeros(days)
    steps = np.zeros(days, dtype=int)
    calories_active = np.zeros(days)
    intensity_min = np.zeros(days)

    activities: list[dict[str, Any]] = []
    activity_id_counter = 100000

    for i, d in enumerate(dates):
        day_str = d.isoformat()
        is_weekend = d.weekday() >= 5

        base_sleep = 465.0 if is_weekend else 435.0
        tot = float(np.clip(rng.normal(base_sleep, 48.0), 300.0, 570.0))
        deep_frac = float(np.clip(rng.normal(0.18, 0.03), 0.10, 0.28))
        rem_frac = float(np.clip(rng.normal(0.22, 0.035), 0.12, 0.32))
        deep = round(tot * deep_frac, 1)
        rem = round(tot * rem_frac, 1)
        light = round(max(tot - deep - rem, 60.0), 1)
        awake = round(float(np.clip(rng.exponential(18.0), 3.0, 65.0)), 1)

        sleep_total[i] = round(tot, 1)
        sleep_deep[i] = deep
        sleep_rem[i] = rem
        sleep_light[i] = light
        sleep_awake[i] = awake

        bed_offset_min = int(np.clip(rng.normal(0 if not is_weekend else 35, 25), -75, 120))
        bed_total_min = (23 * 60 + 15 + bed_offset_min) % (24 * 60)
        wake_total_min = int((bed_total_min + tot + awake) % (24 * 60))
        sleep_start_str.append(f"{bed_total_min // 60:02d}:{bed_total_min % 60:02d}")
        sleep_end_str.append(f"{wake_total_min // 60:02d}:{wake_total_min % 60:02d}")

        dur_component = np.clip((tot - 300.0) / (480.0 - 300.0), 0.0, 1.0) * 60.0
        stage_component = np.clip((deep_frac + rem_frac - 0.25) / 0.20, 0.0, 1.0) * 40.0
        sleep_score[i] = round(float(np.clip(dur_component + stage_component + rng.normal(0, 3), 35, 98)), 0)

        has_activity = rng.random() < (0.80 if is_weekend else 0.60)
        if has_activity:
            act_types = [
                ("running", "Corsa", 0.50),
                ("cycling", "Ciclismo", 0.25),
                ("strength_training", "Forza", 0.25),
            ]
            probs = [p for _, _, p in act_types]
            idx_type = int(rng.choice(len(act_types), p=probs))
            act_type, act_label, _ = act_types[idx_type]

            if act_type == "running":
                dur_min = float(np.clip(rng.normal(52 if is_weekend else 38, 14), 20, 110))
                speed_m_per_min = float(rng.normal(190.0, 15.0))
                dist_m = round(dur_min * speed_m_per_min, 0)
                hr_avg = round(float(np.clip(rng.normal(152, 10), 125, 178)), 0)
                hr_max = round(float(np.clip(hr_avg + rng.uniform(14, 26), hr_avg + 5, 198)), 0)
            elif act_type == "cycling":
                dur_min = float(np.clip(rng.normal(85 if is_weekend else 55, 22), 30, 180))
                speed_m_per_min = float(rng.normal(430.0, 40.0))
                dist_m = round(dur_min * speed_m_per_min, 0)
                hr_avg = round(float(np.clip(rng.normal(140, 11), 115, 168)), 0)
                hr_max = round(float(np.clip(hr_avg + rng.uniform(15, 28), hr_avg + 5, 192)), 0)
            else:
                dur_min = float(np.clip(rng.normal(48, 10), 25, 75))
                dist_m = 0.0
                hr_avg = round(float(np.clip(rng.normal(124, 9), 100, 150)), 0)
                hr_max = round(float(np.clip(hr_avg + rng.uniform(18, 32), hr_avg + 8, 178)), 0)

            dur_s = round(dur_min * 60.0, 0)

            if hr_avg >= 155:
                zone_weights = np.array([0.08, 0.22, 0.35, 0.25, 0.10])
            elif hr_avg >= 138:
                zone_weights = np.array([0.15, 0.42, 0.28, 0.12, 0.03])
            else:
                zone_weights = np.array([0.45, 0.35, 0.15, 0.05, 0.00])

            z_secs = np.round(dur_s * zone_weights)
            trimp_load = float(
                (z_secs[0] * 1 + z_secs[1] * 2 + z_secs[2] * 3 + z_secs[3] * 4.5 + z_secs[4] * 6.5) / 60.0
            )
            load_val = round(trimp_load * float(rng.uniform(0.95, 1.05)), 1)

            activity_id_counter += 1
            start_hour = 8 if is_weekend else 18
            start_min = int(rng.integers(0, 50))
            activities.append(
                {
                    "activity_id": activity_id_counter,
                    "day": day_str,
                    "start_local": f"{day_str}T{start_hour:02d}:{start_min:02d}:00",
                    "type": act_type,
                    "name": f"{act_label} mattutina" if is_weekend else f"{act_label} serale",
                    "duration_s": dur_s,
                    "distance_m": dist_m,
                    "hr_avg": hr_avg,
                    "hr_max": hr_max,
                    "training_load": load_val,
                    "z1_s": float(z_secs[0]),
                    "z2_s": float(z_secs[1]),
                    "z3_s": float(z_secs[2]),
                    "z4_s": float(z_secs[3]),
                    "z5_s": float(z_secs[4]),
                }
            )
            daily_load[i] = load_val
            intensity_min[i] = round(float((z_secs[2] + 2.0 * (z_secs[3] + z_secs[4])) / 60.0), 0)
            extra_steps = int(dist_m / 0.78) if act_type == "running" else int(rng.integers(800, 2200))
            steps[i] = int(np.clip(rng.normal(6500, 1400) + extra_steps, 2500, 24000))
            calories_active[i] = round(float(220.0 + load_val * 3.8 + rng.normal(0, 35)), 0)
        else:
            daily_load[i] = 0.0
            intensity_min[i] = round(float(np.clip(rng.exponential(8.0), 0.0, 30.0)), 0)
            steps[i] = int(np.clip(rng.normal(5800, 1600), 1800, 12000))
            calories_active[i] = round(float(np.clip(rng.normal(210, 55), 80, 420)), 0)

        stress_avg[i] = round(
            float(np.clip(28.0 + 0.05 * daily_load[i] + rng.normal(0, 7.5), 14.0, 68.0)),
            1,
        )

    hrv_night = np.zeros(days)
    hrv_sd = np.zeros(days)
    rhr_bpm = np.zeros(days)
    bb_max = np.zeros(days)
    bb_min = np.zeros(days)
    bb_charged = np.zeros(days)
    bb_drained = np.zeros(days)
    resp_sleep = np.zeros(days)
    resp_day = np.zeros(days)
    spo2_avg = np.zeros(days)
    spo2_min = np.zeros(days)
    tr_score = np.zeros(days)

    for i in range(days):
        prev_sleep = sleep_total[i - 1] if i > 0 else 440.0
        prev_load = daily_load[i - 1] if i > 0 else 60.0

        sleep_delta = prev_sleep - 440.0
        load_delta = prev_load - 60.0

        hrv_val = 62.0 + 0.095 * sleep_delta - 0.055 * load_delta + rng.normal(0, 3.2)
        hrv_night[i] = round(float(np.clip(hrv_val, 36.0, 96.0)), 1)
        hrv_sd[i] = round(float(np.clip(rng.normal(9.5, 2.2), 4.0, 18.0)), 2)

        rhr_val = 52.0 - 0.015 * sleep_delta + 0.032 * load_delta + rng.normal(0, 1.1)
        rhr_bpm[i] = round(float(np.clip(rhr_val, 44.0, 66.0)), 1)

        bb_max_val = 95.0 - 0.85 * (stress_avg[i] - 25.0) + 0.03 * (sleep_total[i] - 440.0) + rng.normal(0, 4.0)
        bb_max[i] = round(float(np.clip(bb_max_val, 42.0, 100.0)), 0)
        bb_min[i] = round(float(np.clip(bb_max[i] - rng.uniform(45.0, 72.0), 5.0, bb_max[i] - 10.0)), 0)
        bb_charged[i] = round(float(np.clip(bb_max[i] - bb_min[i] + rng.normal(0, 5.0), 20.0, 90.0)), 0)
        bb_drained[i] = round(float(np.clip(bb_charged[i] + rng.normal(0, 8.0), 20.0, 95.0)), 0)

        resp_sleep[i] = round(float(np.clip(14.2 + 0.004 * load_delta + rng.normal(0, 0.45), 12.0, 17.5)), 1)
        resp_day[i] = round(float(np.clip(resp_sleep[i] + rng.normal(1.6, 0.5), 13.0, 20.0)), 1)
        spo2_avg[i] = round(float(np.clip(rng.normal(96.0, 0.8), 93.0, 99.0)), 1)
        spo2_min[i] = round(float(np.clip(spo2_avg[i] - rng.uniform(2.0, 6.0), 88.0, spo2_avg[i] - 1.0)), 0)
        tr_score[i] = round(
            float(np.clip(0.45 * sleep_score[i] + 0.55 * ((hrv_night[i] - 35.0) / 55.0 * 100.0), 15.0, 99.0)),
            0,
        )

    daily_rows: list[dict[str, Any]] = []
    missing_sleep_days = {12, 47} if include_missing and days > 50 else set()
    missing_hrv_days = {12, 29, 63} if include_missing and days > 65 else set()

    for i, d in enumerate(dates):
        day_str = d.isoformat()
        no_sleep = i in missing_sleep_days
        no_hrv = i in missing_hrv_days

        row: dict[str, Any] = {
            "day": day_str,
            "sleep_total_min": None if no_sleep else float(sleep_total[i]),
            "sleep_deep_min": None if no_sleep else float(sleep_deep[i]),
            "sleep_light_min": None if no_sleep else float(sleep_light[i]),
            "sleep_rem_min": None if no_sleep else float(sleep_rem[i]),
            "sleep_awake_min": None if no_sleep else float(sleep_awake[i]),
            "sleep_score": None if no_sleep else float(sleep_score[i]),
            "sleep_start_local": None if no_sleep else sleep_start_str[i],
            "sleep_end_local": None if no_sleep else sleep_end_str[i],
            "rhr_bpm": float(rhr_bpm[i]),
            "hrv_night_ms": None if no_hrv else float(hrv_night[i]),
            "hrv_night_sd_ms": None if no_hrv else float(hrv_sd[i]),
            "stress_avg": float(stress_avg[i]),
            "bb_max": float(bb_max[i]),
            "bb_min": float(bb_min[i]),
            "bb_charged": float(bb_charged[i]),
            "bb_drained": float(bb_drained[i]),
            "steps": int(steps[i]),
            "calories_active": float(calories_active[i]),
            "intensity_min": float(intensity_min[i]),
            "respiration_sleep_brpm": None if no_sleep else float(resp_sleep[i]),
            "respiration_day_brpm": float(resp_day[i]),
            "spo2_sleep_avg_pct": None if no_sleep else float(spo2_avg[i]),
            "spo2_sleep_min_pct": None if no_sleep else float(spo2_min[i]),
            "training_readiness_score": None if (no_sleep or no_hrv) else float(tr_score[i]),
        }
        daily_rows.append(row)

    return daily_rows, activities


def seed_synthetic_db(
    conn: sqlite3.Connection,
    days: int = DEFAULT_DAYS,
    end_date: date | None = None,
    seed: int = DEFAULT_SEED,
    include_missing: bool = True,
    populate_raw_series: bool = True,
) -> dict[str, int]:
    """Popola il database SQLite con i dati sintetici in modo idempotente, inclusi ipnogrammi e serie FC."""
    daily_rows, activities = generate_synthetic_dataset(
        days=days,
        end_date=end_date,
        seed=seed,
        include_missing=include_missing,
    )
    conn.execute("DELETE FROM activities;")
    conn.execute("DELETE FROM daily;")
    for r in daily_rows:
        replace_daily(conn, r)
        if populate_raw_series:
            d_obj = date.fromisoformat(r["day"])
            sleep_raw = generate_synthetic_sleep_raw(d_obj, r, seed=seed)
            upsert_raw(conn, r["day"], "sleep", sleep_raw)

    for idx, a in enumerate(activities):
        upsert_activity(conn, a)
        if populate_raw_series:
            d_obj = date.fromisoformat(a["day"])
            # Alterna attività con recupero post-esercizio registrato e attività fermate subito (per testare entrambi i casi)
            has_post_rec = (idx % 3) != 2
            details = generate_synthetic_activity_details(
                activity_id=int(a["activity_id"]),
                day_date=d_obj,
                duration_s=float(a["duration_s"] or 2400.0),
                hr_avg=float(a["hr_avg"] or 150.0),
                speed_mps=3.2 if a["type"] == "running" else (7.0 if a["type"] == "cycling" else 0.0),
                drift_pct=KNOWN_CARDIAC_DRIFT_PCT,
                hrr60_drop=KNOWN_HRR_60_BPM,
                hrr120_drop=KNOWN_HRR_120_BPM,
                include_post_recovery=has_post_rec,
                seed=seed,
            )
            upsert_raw(conn, a["day"], f"activity_details:{a['activity_id']}", details)

    conn.commit()
    return {"daily_days": len(daily_rows), "activities": len(activities)}
