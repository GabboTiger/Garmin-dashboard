"""Generatore di dati sintetici (90 giorni) con correlazioni note per la modalità demo e i test.

Correlazioni inserite deliberatamente nel modello generativo:
1. Sonno totale al giorno t -> HRV notturna più alta al giorno t+1 (lag = 1, correlazione positiva).
2. Carico di allenamento al giorno t -> FC a riposo (RHR) più alta al giorno t+1 (lag = 1, correlazione positiva)
   e HRV notturna più bassa al giorno t+1 (lag = 1, correlazione negativa).
3. Stress medio al giorno t -> Body Battery massima più bassa nello stesso giorno t (lag = 0, correlazione negativa).
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

import numpy as np

from app.db import replace_daily, upsert_activity

DEFAULT_DAYS = 90
DEFAULT_SEED = 42


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

    # 1. Generiamo prima il sonno, lo stress e le attività di ogni giorno t
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

        # Sonno totale: media ~450 min (7h30), con oscillazione settimanale e rumore
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

        # Orari di sonno (es. 23:15 -> 06:45)
        bed_offset_min = int(np.clip(rng.normal(0 if not is_weekend else 35, 25), -75, 120))
        bed_total_min = (23 * 60 + 15 + bed_offset_min) % (24 * 60)
        wake_total_min = int((bed_total_min + tot + awake) % (24 * 60))
        sleep_start_str.append(f"{bed_total_min // 60:02d}:{bed_total_min % 60:02d}")
        sleep_end_str.append(f"{wake_total_min // 60:02d}:{wake_total_min % 60:02d}")

        # Punteggio sonno Garmin sintetico
        dur_component = np.clip((tot - 300.0) / (480.0 - 300.0), 0.0, 1.0) * 60.0
        stage_component = np.clip((deep_frac + rem_frac - 0.25) / 0.20, 0.0, 1.0) * 40.0
        sleep_score[i] = round(float(np.clip(dur_component + stage_component + rng.normal(0, 3), 35, 98)), 0)

        # Attività del giorno (circa 65% dei giorni ha un'attività, nel weekend più lunga)
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

            # Suddivisione nelle 5 zone di FC
            if hr_avg >= 155:
                zone_weights = np.array([0.08, 0.22, 0.35, 0.25, 0.10])
            elif hr_avg >= 138:
                zone_weights = np.array([0.15, 0.42, 0.28, 0.12, 0.03])
            else:
                zone_weights = np.array([0.45, 0.35, 0.15, 0.05, 0.00])

            z_secs = np.round(dur_s * zone_weights)
            # Carico dell'attività coerente con i minuti pesati nelle zone
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

        # Stress medio (0-100): influenzato un po' dal carico del giorno e dal rumore
        stress_avg[i] = round(
            float(np.clip(28.0 + 0.05 * daily_load[i] + rng.normal(0, 7.5), 14.0, 68.0)),
            1,
        )

    # 2. Costruiamo HRV, RHR e Body Battery con le dipendenze temporali (lag 0 e lag 1)
    hrv_night = np.zeros(days)
    rhr_bpm = np.zeros(days)
    bb_max = np.zeros(days)
    bb_min = np.zeros(days)
    bb_charged = np.zeros(days)

    for i in range(days):
        prev_sleep = sleep_total[i - 1] if i > 0 else 440.0
        prev_load = daily_load[i - 1] if i > 0 else 60.0

        # Correlazione 1 & 2 (lag = 1):
        # - Più sonno a t-1 -> HRV più alta a t (+0.095 ms per minuto sopra 440)
        # - Più carico a t-1 -> HRV più bassa a t (-0.055 ms per unità di carico) e RHR più alta a t (+0.032 bpm)
        sleep_delta = prev_sleep - 440.0
        load_delta = prev_load - 60.0

        hrv_val = 62.0 + 0.095 * sleep_delta - 0.055 * load_delta + rng.normal(0, 3.2)
        hrv_night[i] = round(float(np.clip(hrv_val, 36.0, 96.0)), 1)

        rhr_val = 52.0 - 0.015 * sleep_delta + 0.032 * load_delta + rng.normal(0, 1.1)
        rhr_bpm[i] = round(float(np.clip(rhr_val, 44.0, 66.0)), 1)

        # Correlazione 3 (lag = 0):
        # - Stress più alto nel giorno t -> Body Battery massima più bassa nel giorno t
        bb_max_val = 95.0 - 0.85 * (stress_avg[i] - 25.0) + 0.03 * (sleep_total[i] - 440.0) + rng.normal(0, 4.0)
        bb_max[i] = round(float(np.clip(bb_max_val, 42.0, 100.0)), 0)
        bb_min[i] = round(float(np.clip(bb_max[i] - rng.uniform(45.0, 72.0), 5.0, bb_max[i] - 10.0)), 0)
        bb_charged[i] = round(float(np.clip(bb_max[i] - bb_min[i] + rng.normal(0, 5.0), 20.0, 90.0)), 0)

    # 3. Assemblaggio delle righe con alcuni NULL realistici (mai sostituiti da 0!)
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
            "stress_avg": float(stress_avg[i]),
            "bb_max": float(bb_max[i]),
            "bb_min": float(bb_min[i]),
            "bb_charged": float(bb_charged[i]),
            "steps": int(steps[i]),
            "calories_active": float(calories_active[i]),
            "intensity_min": float(intensity_min[i]),
        }
        daily_rows.append(row)

    return daily_rows, activities


def seed_synthetic_db(
    conn: sqlite3.Connection,
    days: int = DEFAULT_DAYS,
    end_date: date | None = None,
    seed: int = DEFAULT_SEED,
    include_missing: bool = True,
) -> dict[str, int]:
    """Popola il database SQLite con i dati sintetici in modo idempotente."""
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
    for a in activities:
        upsert_activity(conn, a)
    conn.commit()
    return {"daily_days": len(daily_rows), "activities": len(activities)}
