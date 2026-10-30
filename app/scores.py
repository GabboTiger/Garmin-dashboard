"""Calcolo dei tre punteggi personali (Recupero, Sforzo, Sonno) su scala 0-100 e loro scomposizione.

Regole (AGENTS.md):
- Tutte le costanti sono documentate in cima al modulo e le formule sono leggibili e ispezionabili.
- Ogni punteggio restituisce la scomposizione completa (componenti, valori, baseline, z-score, pesi
  originali e rinormalizzati, sotto-punteggi) e il blocco `how_it_works` per il pannello a 3 strati.
- Con meno di 14 giorni di storia valida nella finestra a 30 giorni, i punteggi basati su baseline
  personale mostrano `status = "calibrating"` ("calibrazione in corso") e `score = None`.
- Sono euristiche personali trasparenti, non indicazioni mediche.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

import numpy as np

from app import baselines
from app.config import AnalysisConfig, DEFAULT_CONFIG

# =============================================================================
# COSTANTI DOCUMENTATE DEI PUNTEGGI
# =============================================================================

DISCLAIMER_SCORES = (
    "Euristiche personali calcolate sulle tue mediane mobili a 30 giorni (stimatori da polso): "
    "non costituiscono indicazioni o diagnosi mediche."
)

# Pendenza k della funzione logistica che mappa uno z-score robusto in un sotto-punteggio [0, 100]:
#   subscore(z) = 100 / (1 + exp(-LOGISTIC_K * z))
# Con LOGISTIC_K = 0.85:
#   z =  0.0 (esattamente sulla tua mediana) -> 50.0 / 100... ma per un punteggio di stato giornaliero
#   essere in linea con la propria norma corrisponde a una buona condizione (~70/100).
#   Perciò applichiamo uno shift positivo LOGISTIC_CENTER_Z = -0.85:
#     z =  0.0 (in linea con la tua mediana) -> ~67 / 100
#     z = +1.0 (1 SD sopra la tua mediana)  -> ~83 / 100
#     z = +2.0 (2 SD sopra la tua mediana)  -> ~92 / 100
#     z = -1.0 (1 SD sotto la tua mediana)  -> ~47 / 100
#     z = -2.0 (2 SD sotto la tua mediana)  -> ~28 / 100
LOGISTIC_K = 0.85
LOGISTIC_SHIFT = 0.72

# --- Pesi del Punteggio di RECUPERO (somma = 1.00) ---
RECOVERY_WEIGHT_HRV = 0.35          # HRV notturna rispetto alla baseline (più alta = recupero migliore)
RECOVERY_WEIGHT_RHR = 0.25          # FC a riposo rispetto alla baseline (più bassa = recupero migliore)
RECOVERY_WEIGHT_SLEEP = 0.25        # Qualità/durata del sonno della notte appena conclusa
RECOVERY_WEIGHT_AUTONOMIC = 0.15    # Body Battery massima / stress medio rispetto alla baseline

# --- Pesi del Punteggio di SONNO (somma = 1.00) ---
SLEEP_WEIGHT_DURATION = 0.50        # Durata totale rispetto al fabbisogno personale (default 480 min)
SLEEP_WEIGHT_STAGES = 0.25          # Quota di sonno ristoratore (Profondo + REM, target ~38-42% del totale)
SLEEP_WEIGHT_REGULARITY = 0.25      # Regolarità del punto medio del sonno negli ultimi 7 giorni

# Target fisiologico per la frazione (Profondo + REM) / Sonno Totale
SLEEP_RESTORATIVE_TARGET_FRAC = 0.40
# Deviazione standard del punto medio del sonno (in minuti) per la scala di regolarità:
# SD <= 15 min -> ~100 pt; SD = 45 min -> ~70 pt; SD >= 90 min -> ~25 pt
SLEEP_MIDPOINT_REF_SD_MIN = 60.0

# --- Costanti del Punteggio di SFORZO (0-100) ---
# Scala saturante esponenziale: score = 100 * (1 - exp(-load / STRAIN_SCALE_LOAD))
# Con STRAIN_SCALE_LOAD = 135:
#   - Giorno senza allenamento strutturato (solo passi, load ~15) -> Sforzo ~11
#   - Seduta facile (40 min Z2, load ~80)                         -> Sforzo ~45
#   - Seduta media (60 min Z3, load ~150)                         -> Sforzo ~67
#   - Seduta dura / lungo (load ~260)                             -> Sforzo ~85
STRAIN_SCALE_LOAD = 135.0
STRAIN_STEPS_BASELINE_EQUIV = 10000.0
STRAIN_STEPS_MAX_BONUS_LOAD = 28.0


def z_to_subscore(z_val: float, higher_is_better: bool = True) -> float:
    """Converte uno z-score robusto in un sotto-punteggio in [0, 100] tramite funzione logistica."""
    signed_z = float(z_val) if higher_is_better else -float(z_val)
    exponent = -(LOGISTIC_K * signed_z + LOGISTIC_SHIFT)
    exponent = max(-20.0, min(20.0, exponent))
    val = 100.0 / (1.0 + math.exp(exponent))
    return round(float(np.clip(val, 0.0, 100.0)), 1)


def _hhmm_to_circular_minutes(hhmm: str | None) -> float | None:
    """Converte un orario 'HH:MM' in minuti rispetto alla mezzanotte, gestendo il passaggio 23:00 -> 01:00."""
    if not hhmm or ":" not in str(hhmm):
        return None
    try:
        parts = str(hhmm).strip().split(":")
        h, m = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None
    mins = h * 60 + m
    # Gli orari serali (>= 16:00) vengono rappresentati come minuti negativi prima della mezzanotte
    if mins >= 16 * 60:
        mins -= 24 * 60
    return float(mins)


def _compute_sleep_midpoint_sd_7d(daily_rows: list[dict[str, Any]], target_day: str) -> tuple[float | None, int]:
    """Calcola la deviazione standard (in minuti) della metà del sonno negli ultimi 7 giorni fino a `target_day`."""
    target_dt = date.fromisoformat(target_day)
    start_str = (target_dt - timedelta(days=6)).isoformat()

    midpoints: list[float] = []
    for r in daily_rows:
        d_str = r.get("day")
        if not d_str or not (start_str <= d_str <= target_day):
            continue
        bed_m = _hhmm_to_circular_minutes(r.get("sleep_start_local"))
        tot_m = r.get("sleep_total_min")
        awake_m = r.get("sleep_awake_min") or 0.0
        if bed_m is not None and tot_m is not None and tot_m > 0:
            midpoints.append(bed_m + 0.5 * (float(tot_m) + float(awake_m)))

    if len(midpoints) < 3:
        return None, len(midpoints)
    return round(float(np.std(midpoints, ddof=1)), 1), len(midpoints)


def compute_sleep_score(
    daily_rows: list[dict[str, Any]],
    target_day: str,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Calcola il Punteggio Sonno (0-100) e la sua scomposizione trasparente per `target_day`."""
    row = next((r for r in daily_rows if r.get("day") == target_day), None)
    base_sleep = baselines.compute_metric_baseline(daily_rows, target_day, "sleep_total_min", config=config)

    if row is None or row.get("sleep_total_min") is None:
        return {
            "name": "Sonno",
            "key": "sleep",
            "day": target_day,
            "score": None,
            "status": "missing_data",
            "status_label": "dato assente",
            "components": [],
            "disclaimer": DISCLAIMER_SCORES,
            "how_it_works": _sleep_how_it_works(config),
        }

    tot_min = float(row["sleep_total_min"])
    need_min = float(config.sleep_need_min)

    # 1. Componente Durata rispetto al bisogno personale (50%)
    ratio_need = tot_min / max(1.0, need_min)
    # A ratio = 1.0 (100% del bisogno) -> 95 pt; sopra 1.05 satura a 100; sotto scende linearmente/quadraticamente
    dur_subscore = round(float(np.clip(100.0 * min(1.0, (ratio_need / 1.02) ** 1.35), 0.0, 100.0)), 1)

    components: list[dict[str, Any]] = [
        {
            "key": "sleep_duration",
            "name": "Durata vs fabbisogno",
            "value": round(tot_min / 60.0, 2),
            "unit": "h",
            "baseline": round(need_min / 60.0, 2),
            "baseline_label": f"Fabbisogno {need_min / 60.0:.1f} h (mediana 30g: {(base_sleep['median'] or 0) / 60.0:.1f} h)"
            if base_sleep["median"]
            else f"Fabbisogno {need_min / 60.0:.1f} h",
            "z_robust": base_sleep["z_robust"],
            "weight_raw": SLEEP_WEIGHT_DURATION,
            "subscore": dur_subscore,
        }
    ]

    # 2. Componente Fasi: Profondo + REM su totale (25%)
    deep_m = row.get("sleep_deep_min")
    rem_m = row.get("sleep_rem_min")
    if deep_m is not None and rem_m is not None and tot_min > 0:
        restorative_frac = (float(deep_m) + float(rem_m)) / tot_min
        stage_subscore = round(
            float(np.clip(100.0 * (restorative_frac / SLEEP_RESTORATIVE_TARGET_FRAC), 20.0, 100.0)),
            1,
        )
        components.append(
            {
                "key": "sleep_stages",
                "name": "Fasi ristoratrici (Profondo + REM)",
                "value": round(restorative_frac * 100.0, 1),
                "unit": "%",
                "baseline": round(SLEEP_RESTORATIVE_TARGET_FRAC * 100.0, 1),
                "baseline_label": "Riferimento 40%",
                "z_robust": None,
                "weight_raw": SLEEP_WEIGHT_STAGES,
                "subscore": stage_subscore,
            }
        )

    # 3. Componente Regolarità orari (SD della metà del sonno su 7 giorni) (25%)
    mid_sd, n_mid = _compute_sleep_midpoint_sd_7d(daily_rows, target_day)
    if mid_sd is not None:
        reg_subscore = round(
            float(np.clip(100.0 * math.exp(-((mid_sd / SLEEP_MIDPOINT_REF_SD_MIN) ** 1.4)), 10.0, 100.0)),
            1,
        )
        components.append(
            {
                "key": "sleep_regularity",
                "name": "Regolarità orari (7 giorni)",
                "value": mid_sd,
                "unit": "min SD",
                "baseline": 20.0,
                "baseline_label": f"Variabilità punto medio su {n_mid} notti",
                "z_robust": None,
                "weight_raw": SLEEP_WEIGHT_REGULARITY,
                "subscore": reg_subscore,
            }
        )

    total_w = sum(c["weight_raw"] for c in components)
    for c in components:
        c["weight"] = round(c["weight_raw"] / total_w, 3)

    final_score = round(sum(c["subscore"] * c["weight"] for c in components), 0)

    # Se lo storico a 30 giorni ha meno di 14 notti valide, segnaliamo che la baseline è in calibrazione
    is_calibrating = base_sleep["n_days"] < config.baseline_min_valid_days
    return {
        "name": "Sonno",
        "key": "sleep",
        "day": target_day,
        "score": None if is_calibrating else int(final_score),
        "provisional_score": int(final_score),
        "status": "calibrating" if is_calibrating else "ok",
        "status_label": "calibrazione in corso" if is_calibrating else "calibrato",
        "n_history_days": base_sleep["n_days"],
        "min_required_days": config.baseline_min_valid_days,
        "components": components,
        "disclaimer": DISCLAIMER_SCORES,
        "how_it_works": _sleep_how_it_works(config),
    }


def compute_recovery_score(
    daily_rows: list[dict[str, Any]],
    target_day: str,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Calcola il Punteggio di Recupero (0-100) confrontando HRV, FC a riposo, Sonno e Body Battery/Stress
    con le rispettive baseline personali a 30 giorni.
    """
    row = next((r for r in daily_rows if r.get("day") == target_day), None)
    if row is None:
        return {
            "name": "Recupero",
            "key": "recovery",
            "day": target_day,
            "score": None,
            "status": "missing_data",
            "status_label": "dato assente",
            "components": [],
            "disclaimer": DISCLAIMER_SCORES,
            "how_it_works": _recovery_how_it_works(config),
        }

    b_hrv = baselines.compute_metric_baseline(daily_rows, target_day, "hrv_night_ms", config=config)
    b_rhr = baselines.compute_metric_baseline(daily_rows, target_day, "rhr_bpm", config=config)
    b_bb = baselines.compute_metric_baseline(daily_rows, target_day, "bb_max", config=config)
    b_stress = baselines.compute_metric_baseline(daily_rows, target_day, "stress_avg", config=config)
    sleep_res = compute_sleep_score(daily_rows, target_day, config=config)

    # Verifica se abbiamo almeno 14 giorni di storia su HRV o RHR per ritenere calibrato il profilo
    max_hist_days = max(b_hrv["n_days"], b_rhr["n_days"])
    is_calibrating = max_hist_days < config.baseline_min_valid_days

    components: list[dict[str, Any]] = []

    # 1. HRV notturna (35%): z > 0 è positivo
    if b_hrv["value"] is not None and b_hrv["median"] is not None and b_hrv["robust_sd"] is not None:
        z_hrv = (b_hrv["value"] - b_hrv["median"]) / max(b_hrv["robust_sd"], baselines.MIN_ROBUST_SIGMA)
        components.append(
            {
                "key": "hrv_night_ms",
                "name": "HRV notturna vs baseline",
                "value": b_hrv["value"],
                "unit": "ms",
                "baseline": b_hrv["median"],
                "baseline_label": f"Mediana 30g: {b_hrv['median']} ms (±{b_hrv['robust_sd']:.1f})",
                "z_robust": round(z_hrv, 2),
                "weight_raw": RECOVERY_WEIGHT_HRV,
                "subscore": z_to_subscore(z_hrv, higher_is_better=True),
            }
        )

    # 2. FC a riposo (25%): z < 0 (FC più bassa del solito) è positivo
    if b_rhr["value"] is not None and b_rhr["median"] is not None and b_rhr["robust_sd"] is not None:
        z_rhr = (b_rhr["value"] - b_rhr["median"]) / max(b_rhr["robust_sd"], baselines.MIN_ROBUST_SIGMA)
        components.append(
            {
                "key": "rhr_bpm",
                "name": "FC a riposo vs baseline",
                "value": b_rhr["value"],
                "unit": "bpm",
                "baseline": b_rhr["median"],
                "baseline_label": f"Mediana 30g: {b_rhr['median']} bpm (±{b_rhr['robust_sd']:.1f})",
                "z_robust": round(z_rhr, 2),
                "weight_raw": RECOVERY_WEIGHT_RHR,
                "subscore": z_to_subscore(z_rhr, higher_is_better=False),
            }
        )

    # 3. Sonno della notte (25%)
    sleep_sub = sleep_res.get("provisional_score")
    if sleep_sub is not None:
        components.append(
            {
                "key": "sleep_score_internal",
                "name": "Qualità e durata del sonno",
                "value": round((row.get("sleep_total_min") or 0.0) / 60.0, 2),
                "unit": "h",
                "baseline": round(config.sleep_need_min / 60.0, 2),
                "baseline_label": f"Punteggio sonno: {sleep_sub}/100",
                "z_robust": None,
                "weight_raw": RECOVERY_WEIGHT_SLEEP,
                "subscore": float(sleep_sub),
            }
        )

    # 4. Ricarica Body Battery / Stress (15%)
    if b_bb["value"] is not None and b_bb["median"] is not None and b_bb["robust_sd"] is not None:
        z_bb = (b_bb["value"] - b_bb["median"]) / max(b_bb["robust_sd"], baselines.MIN_ROBUST_SIGMA)
        components.append(
            {
                "key": "bb_max",
                "name": "Body Battery mattutina",
                "value": b_bb["value"],
                "unit": "pt",
                "baseline": b_bb["median"],
                "baseline_label": f"Mediana 30g: {b_bb['median']} pt",
                "z_robust": round(z_bb, 2),
                "weight_raw": RECOVERY_WEIGHT_AUTONOMIC,
                "subscore": z_to_subscore(z_bb, higher_is_better=True),
            }
        )
    elif b_stress["value"] is not None and b_stress["median"] is not None and b_stress["robust_sd"] is not None:
        z_st = (b_stress["value"] - b_stress["median"]) / max(b_stress["robust_sd"], baselines.MIN_ROBUST_SIGMA)
        components.append(
            {
                "key": "stress_avg",
                "name": "Stress medio vs baseline",
                "value": b_stress["value"],
                "unit": "pt",
                "baseline": b_stress["median"],
                "baseline_label": f"Mediana 30g: {b_stress['median']} pt",
                "z_robust": round(z_st, 2),
                "weight_raw": RECOVERY_WEIGHT_AUTONOMIC,
                "subscore": z_to_subscore(z_st, higher_is_better=False),
            }
        )

    if not components:
        return {
            "name": "Recupero",
            "key": "recovery",
            "day": target_day,
            "score": None,
            "status": "missing_data",
            "status_label": "dato assente",
            "n_history_days": max_hist_days,
            "min_required_days": config.baseline_min_valid_days,
            "components": [],
            "disclaimer": DISCLAIMER_SCORES,
            "how_it_works": _recovery_how_it_works(config),
        }

    total_w = sum(c["weight_raw"] for c in components)
    for c in components:
        c["weight"] = round(c["weight_raw"] / total_w, 3)

    final_score = int(round(sum(c["subscore"] * c["weight"] for c in components), 0))

    return {
        "name": "Recupero",
        "key": "recovery",
        "day": target_day,
        "score": None if is_calibrating else final_score,
        "provisional_score": final_score,
        "status": "calibrating" if is_calibrating else "ok",
        "status_label": "calibrazione in corso" if is_calibrating else "calibrato",
        "n_history_days": max_hist_days,
        "min_required_days": config.baseline_min_valid_days,
        "components": components,
        "disclaimer": DISCLAIMER_SCORES,
        "how_it_works": _recovery_how_it_works(config),
    }


def compute_strain_score(
    daily_rows: list[dict[str, Any]],
    activities_rows: list[dict[str, Any]],
    target_day: str,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Calcola il Punteggio di Sforzo (0-100) del giorno `target_day` a partire dai minuti nelle
    5 zone di FC delle attività (TRIMP a zone) o dal training_load Garmin, più il movimento base (passi).
    """
    row = next((r for r in daily_rows if r.get("day") == target_day), None)
    day_acts = [a for a in activities_rows if a.get("day") == target_day]

    # Verifica calibrazione sullo storico giornaliero
    b_steps = baselines.compute_metric_baseline(daily_rows, target_day, "steps", config=config)
    is_calibrating = b_steps["n_days"] < config.baseline_min_valid_days

    weights = config.trimp_zone_weights
    zone_mins = [0.0, 0.0, 0.0, 0.0, 0.0]
    has_zones = False
    fallback_garmin_load = 0.0

    for a in day_acts:
        a_zones = [a.get(f"z{k}_s") for k in range(1, 6)]
        if any(z is not None and float(z) > 0 for z in a_zones):
            has_zones = True
            for idx in range(5):
                zone_mins[idx] += float(a_zones[idx] or 0.0) / 60.0
        elif a.get("training_load") is not None:
            fallback_garmin_load += float(a["training_load"])
        elif a.get("duration_s") is not None:
            # Stima conservativa dalla durata e FC media se mancano sia zone sia training_load
            dur_m = float(a["duration_s"]) / 60.0
            hr_avg = float(a.get("hr_avg") or 130.0)
            factor = max(1.0, (hr_avg - 90.0) / 25.0)
            fallback_garmin_load += dur_m * factor

    zone_trimp = sum(zone_mins[i] * weights[i] for i in range(5))
    exercise_load = zone_trimp + fallback_garmin_load

    steps_val = row.get("steps") if row else None
    steps_load = 0.0
    if steps_val is not None and steps_val > 0:
        steps_load = min(
            STRAIN_STEPS_MAX_BONUS_LOAD,
            (float(steps_val) / STRAIN_STEPS_BASELINE_EQUIV) * 16.0,
        )

    if not day_acts and steps_val is None:
        return {
            "name": "Sforzo",
            "key": "strain",
            "day": target_day,
            "score": None,
            "status": "missing_data",
            "status_label": "dato assente",
            "components": [],
            "disclaimer": DISCLAIMER_SCORES,
            "how_it_works": _strain_how_it_works(config),
        }

    total_load = round(exercise_load + steps_load, 1)
    raw_score = 100.0 * (1.0 - math.exp(-total_load / STRAIN_SCALE_LOAD))
    final_score = int(round(float(np.clip(raw_score, 0.0, 100.0)), 0))

    components: list[dict[str, Any]] = []
    if has_zones:
        components.append(
            {
                "key": "zone_trimp",
                "name": "Carico da zone FC (Z1–Z5)",
                "value": round(zone_trimp, 1),
                "unit": "TRIMP",
                "baseline": round(STRAIN_SCALE_LOAD, 0),
                "baseline_label": (
                    f"Z1-Z2: {zone_mins[0] + zone_mins[1]:.0f}m · "
                    f"Z3: {zone_mins[2]:.0f}m · "
                    f"Z4-Z5: {zone_mins[3] + zone_mins[4]:.0f}m"
                ),
                "z_robust": None,
                "weight": round(zone_trimp / max(1.0, total_load), 3),
                "subscore": round(100.0 * (1.0 - math.exp(-zone_trimp / STRAIN_SCALE_LOAD)), 1),
            }
        )
    if fallback_garmin_load > 0:
        components.append(
            {
                "key": "garmin_training_load",
                "name": "Carico attività (Garmin Load)",
                "value": round(fallback_garmin_load, 1),
                "unit": "pt",
                "baseline": round(STRAIN_SCALE_LOAD, 0),
                "baseline_label": f"{len(day_acts)} attività registrate",
                "z_robust": None,
                "weight": round(fallback_garmin_load / max(1.0, total_load), 3),
                "subscore": round(100.0 * (1.0 - math.exp(-fallback_garmin_load / STRAIN_SCALE_LOAD)), 1),
            }
        )
    if steps_val is not None:
        components.append(
            {
                "key": "daily_steps",
                "name": "Movimento giornaliero (passi)",
                "value": int(steps_val),
                "unit": "passi",
                "baseline": b_steps["median"],
                "baseline_label": f"Mediana 30g: {int(b_steps['median'])} passi" if b_steps["median"] else "Passi giornalieri",
                "z_robust": b_steps["z_robust"],
                "weight": round(steps_load / max(1.0, total_load), 3) if total_load > 0 else 1.0,
                "subscore": round(min(100.0, (float(steps_val) / STRAIN_STEPS_BASELINE_EQUIV) * 65.0), 1),
            }
        )

    return {
        "name": "Sforzo",
        "key": "strain",
        "day": target_day,
        "score": None if is_calibrating else final_score,
        "provisional_score": final_score,
        "total_load": total_load,
        "status": "calibrating" if is_calibrating else "ok",
        "status_label": "calibrazione in corso" if is_calibrating else "calibrato",
        "n_history_days": b_steps["n_days"],
        "min_required_days": config.baseline_min_valid_days,
        "components": components,
        "disclaimer": DISCLAIMER_SCORES,
        "how_it_works": _strain_how_it_works(config),
    }


def compute_all_scores_for_day(
    daily_rows: list[dict[str, Any]],
    activities_rows: list[dict[str, Any]],
    target_day: str,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> dict[str, dict[str, Any]]:
    """Restituisce i 3 punteggi (recovery, strain, sleep) con scomposizione per `target_day`."""
    return {
        "recovery": compute_recovery_score(daily_rows, target_day, config=config),
        "strain": compute_strain_score(daily_rows, activities_rows, target_day, config=config),
        "sleep": compute_sleep_score(daily_rows, target_day, config=config),
    }


def _recovery_how_it_works(config: AnalysisConfig) -> dict[str, Any]:
    return {
        "title": "Come si calcola il Punteggio di Recupero",
        "formula_latex": (
            r"z_i = \frac{x_i - m_{30}}{1.4826 \cdot \text{MAD}_{30}}, \quad "
            r"S_i = \frac{100}{1 + e^{-(0.85 \cdot s_i z_i + 0.72)}}, \quad "
            r"\text{Recupero} = \sum_i w_i S_i"
        ),
        "parameters": {
            "Finestra baseline": f"{config.baseline_window_days} giorni precedenti (minimo {config.baseline_min_valid_days} validi)",
            "Pesi componenti": "HRV notturna 35%, FC a riposo 25%, Sonno 25%, Body Battery/Stress 15%",
            "Segno z-score": "+1 per HRV e Body Battery (più alto = meglio), -1 per FC a riposo e Stress",
        },
        "limits": (
            "Se una metrica manca (es. orologio non indossato), i pesi delle componenti disponibili vengono "
            "rinormalizzati a 1. Con meno di 14 giorni di storico mostra 'calibrazione in corso'."
        ),
    }


def _strain_how_it_works(config: AnalysisConfig) -> dict[str, Any]:
    return {
        "title": "Come si calcola il Punteggio di Sforzo",
        "formula_latex": (
            r"L = \sum_{k=1}^{5} t_{Z_k}\,\text{min} \cdot w_k + L_{\text{passi}}, \quad "
            r"\text{Sforzo} = 100 \cdot \left(1 - e^{-L / 135}\right)"
        ),
        "parameters": {
            "Pesi zone FC (Z1..Z5)": str(config.trimp_zone_weights),
            "Costante di scala": f"{STRAIN_SCALE_LOAD} unità TRIMP",
            "Fallback": "Se le zone FC non sono disponibili, usa il Training Load dell'attività Garmin",
        },
        "limits": (
            "Curva esponenziale saturante: evita punteggi sopra 100 nelle giornate di carico estremo "
            "pur mantenendo alta sensibilità agli allenamenti moderati."
        ),
    }


def _sleep_how_it_works(config: AnalysisConfig) -> dict[str, Any]:
    return {
        "title": "Come si calcola il Punteggio del Sonno",
        "formula_latex": (
            r"\text{Sonno} = 0.50 \cdot S_{\text{durata}} + 0.25 \cdot S_{\text{fasi}} + 0.25 \cdot S_{\text{regolarità}}"
        ),
        "parameters": {
            "Fabbisogno personale": f"{config.sleep_need_min:.0f} min ({config.sleep_need_min / 60.0:.1f} h)",
            "Target fasi ristoratrici (Profondo + REM)": "40% della durata totale del sonno",
            "Regolarità": "Deviazione standard del punto medio del sonno sugli ultimi 7 giorni",
        },
        "limits": (
            "La classificazione delle fasi (Profondo, Leggero, REM) da sensore ottico da polso è una stima "
            "basata su movimento e variabilità cardiaca, non una polisonnografia."
        ),
    }
