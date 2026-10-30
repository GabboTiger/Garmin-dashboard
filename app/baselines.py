"""Calcolo delle baseline personali (mediana mobile a 30 giorni e MAD).

Per ogni metrica x al giorno d, la finestra di riferimento è [d - window_days, d - 1]
(il giorno d è escluso per evitare di confrontare il valore odierno con sé stesso).

Formule:
  - mediana: m = median(x_i)
  - MAD (Median Absolute Deviation): mad = median(|x_i - m|)
  - deviazione standard robusta: sigma_mad = 1.4826 * mad
    (il fattore 1 / Phi^-1(0.75) ≈ 1.4826 rende sigma_mad uno stimatore consistente
    della deviazione standard sotto distribuzione normale, ma immune agli outlier).
  - z-score robusto: z = (x_d - m) / max(sigma_mad, min_sigma)
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np

from app.config import AnalysisConfig, DEFAULT_CONFIG

MAD_TO_SD_FACTOR = 1.4826
# Soglia minima di dispersione per evitare divisioni per zero se una metrica è quasi costante
MIN_ROBUST_SIGMA = 1e-3
# Entro |z| <= IN_LINE_Z_BAND il valore viene descritto come "in linea con la tua norma"
IN_LINE_Z_BAND = 0.5


def compute_metric_baseline(
    daily_rows: list[dict[str, Any]],
    target_day: str,
    metric: str,
    window_days: int | None = None,
    min_valid_days: int | None = None,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Calcola la baseline personale (mediana e MAD su 30 giorni precedenti) per `metric` al giorno `target_day`.

    Restituisce un dizionario con:
      - metric: nome della metrica
      - day: data target 'YYYY-MM-DD'
      - value: valore del giorno target (o None se assente)
      - status: 'ok' | 'calibrating' | 'missing_value'
      - n_days: numero di giorni validi trovati nella finestra [d - window_days, d - 1]
      - min_required_days: numero minimo di giorni richiesto (default 14)
      - median: mediana personale (o None se n_days == 0)
      - mad: Median Absolute Deviation (o None)
      - robust_sd: 1.4826 * MAD (o None)
      - band_low: median - 1.0 * robust_sd (banda di normalità inferiore)
      - band_high: median + 1.0 * robust_sd (banda di normalità superiore)
      - delta: value - median (scostamento assoluto dalla mediana)
      - z_robust: (value - median) / robust_sd (scostamento normalizzato, solo se status == 'ok')
      - direction_arrow: '▲' | '▼' | '•' | '—'
      - direction_text: 'sopra la tua norma' | 'sotto la tua norma' | 'in linea' | 'calibrazione in corso' | 'dato assente'
    """
    w_days = window_days if window_days is not None else config.baseline_window_days
    min_days = min_valid_days if min_valid_days is not None else config.baseline_min_valid_days

    target_dt = date.fromisoformat(target_day)
    start_dt = target_dt - timedelta(days=w_days)
    start_str = start_dt.isoformat()

    target_val: float | None = None
    history_vals: list[float] = []

    for row in daily_rows:
        d_str = row.get("day")
        if not d_str:
            continue
        raw_v = row.get(metric)
        val = float(raw_v) if (raw_v is not None and not isinstance(raw_v, bool)) else None
        if d_str == target_day:
            target_val = val
        elif start_str <= d_str < target_day and val is not None:
            history_vals.append(val)

    n_valid = len(history_vals)
    if n_valid == 0:
        median_val = None
        mad_val = None
        robust_sd = None
        band_low = None
        band_high = None
    else:
        arr = np.asarray(history_vals, dtype=float)
        median_val = float(np.median(arr))
        mad_val = float(np.median(np.abs(arr - median_val)))
        if mad_val > 0.0:
            robust_sd = max(MAD_TO_SD_FACTOR * mad_val, MIN_ROBUST_SIGMA)
        else:
            # Se almeno il 50% dei valori coincide con la mediana (MAD == 0), usa la SD campionaria o MIN_ROBUST_SIGMA
            sample_sd = float(np.std(arr, ddof=1)) if n_valid > 1 else 0.0
            robust_sd = max(sample_sd, MIN_ROBUST_SIGMA)
        band_low = round(median_val - robust_sd, 2)
        band_high = round(median_val + robust_sd, 2)
        median_val = round(median_val, 2)
        mad_val = round(mad_val, 2)
        robust_sd = round(robust_sd, 3)

    if n_valid < min_days:
        status = "calibrating"
        delta = round(target_val - median_val, 2) if (target_val is not None and median_val is not None) else None
        return {
            "metric": metric,
            "day": target_day,
            "value": target_val,
            "status": status,
            "n_days": n_valid,
            "min_required_days": min_days,
            "window_days": w_days,
            "median": median_val,
            "mad": mad_val,
            "robust_sd": robust_sd,
            "band_low": band_low,
            "band_high": band_high,
            "delta": delta,
            "z_robust": None,
            "direction_arrow": "—",
            "direction_text": "calibrazione in corso" if target_val is not None else "dato assente",
        }

    if target_val is None or median_val is None or robust_sd is None:
        return {
            "metric": metric,
            "day": target_day,
            "value": None,
            "status": "missing_value",
            "n_days": n_valid,
            "min_required_days": min_days,
            "window_days": w_days,
            "median": median_val,
            "mad": mad_val,
            "robust_sd": robust_sd,
            "band_low": band_low,
            "band_high": band_high,
            "delta": None,
            "z_robust": None,
            "direction_arrow": "—",
            "direction_text": "dato assente",
        }

    delta = round(target_val - median_val, 2)
    z_val = round((target_val - median_val) / max(robust_sd, MIN_ROBUST_SIGMA), 2)

    if z_val > IN_LINE_Z_BAND:
        arrow = "▲"
        text = "sopra la tua norma"
    elif z_val < -IN_LINE_Z_BAND:
        arrow = "▼"
        text = "sotto la tua norma"
    else:
        arrow = "•"
        text = "in linea"

    return {
        "metric": metric,
        "day": target_day,
        "value": target_val,
        "status": "ok",
        "n_days": n_valid,
        "min_required_days": min_days,
        "window_days": w_days,
        "median": median_val,
        "mad": mad_val,
        "robust_sd": robust_sd,
        "band_low": band_low,
        "band_high": band_high,
        "delta": delta,
        "z_robust": z_val,
        "direction_arrow": arrow,
        "direction_text": text,
    }


def compute_all_baselines_for_day(
    daily_rows: list[dict[str, Any]],
    target_day: str,
    metrics: tuple[str, ...] = (
        "hrv_night_ms",
        "rhr_bpm",
        "sleep_total_min",
        "sleep_score",
        "stress_avg",
        "bb_max",
        "bb_charged",
        "steps",
        "respiration_sleep_brpm",
        "spo2_sleep_avg_pct",
    ),
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> dict[str, dict[str, Any]]:
    """Calcola le baseline di tutte le metriche principali per `target_day`."""
    return {
        m: compute_metric_baseline(daily_rows, target_day=target_day, metric=m, config=config)
        for m in metrics
    }
