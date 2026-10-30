"""Analisi algoritmiche per Recupero e Tendenze (Fase 3d - Step 2):
- Banda HRV individuale: trasformazione logaritmica ln(rMSSD), media mobile a 7 giorni confrontata
  con media ± k * SD a 60 giorni (k configurabile, default 0.75) e coefficiente di variazione (CV)
- Analisi di tendenza per qualsiasi metrica giornaliera:
  * Media mobile semplice (SMA) ed esponenziale (EWMA)
  * Pendenza non parametrica di Theil-Sen accoppiata al test di significatività di Mann-Kendall (con correzione per i pareggi)
  * Rilevamento di punti di cambio di regime tramite Page's CUSUM (Cumulative Sum)
  * Rilevamento di anomalie giornaliere tramite z-score robusto (mediana e 1.4826 * MAD)
  * Profilo del ciclo settimanale (Lunedì..Domenica)
- Ricarica notturna del Body Battery (assoluta e velocità oraria pt/h di sonno)
- Studio di evento (Event Study): effetto delle sedute dure vs facili vs riposo sul recupero
  della notte successiva (HRV, RHR, Body Battery) con intervallo di confidenza al 95% via bootstrap
  non parametrico e conteggio degli eventi.

Ogni funzione è pura e riporta nella docstring: formula, parametri, dati minimi e limiti.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

import numpy as np

from app.analysis.activity import compute_trimp
from app.analysis.common import make_not_calculable, make_result
from app.config import AnalysisConfig, DEFAULT_CONFIG

WEEKDAY_NAMES_IT = ("Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica")


def compute_hrv_band(
    daily_rows: list[dict[str, Any]],
    target_day: str | None = None,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Calcola la banda individuale dell'HRV secondo il metodo di Plews/Buchheit:
    - Trasformazione logaritmica naturale y_t = ln(HRV_t) per normalizzare l'asimmetria di rMSSD
    - Media mobile a 7 giorni (ln_rmssd_7d) e suo equivalente in ms (exp(ln_rmssd_7d))
    - Banda di normalità individuale a 60 giorni: media_60(y) ± k * SD_60(y), con k configurabile
    - Coefficiente di variazione a 7 giorni: CV_7d (%) = (SD_7(HRV) / Media_7(HRV)) * 100

    Parametri:
      - config.hrv_short_window_days: finestra breve (default 7 giorni)
      - config.hrv_long_window_days: finestra baseline lunga (default 60 giorni)
      - config.hrv_band_k: larghezza banda in unità di SD (default 0.75)

    Dati minimi e limiti:
      - Richiede almeno `config.hrv_min_long_days` (21) letture HRV valide negli ultimi 60 giorni
        e almeno 4 letture negli ultimi 7 giorni.
    """
    hw = {
        "title": "Banda HRV Individuale (ln rMSSD 7g vs 60g ± k·SD) e Coefficiente di Variazione",
        "formula_latex": (
            r"y_t = \ln(\text{HRV}_t), \quad \bar{y}_{7,t} \;\text{vs}\; \bar{y}_{60,t} \pm k \cdot \text{SD}_{60}(y), \quad "
            r"\text{CV}_{7,t} = \frac{\text{SD}_7(\text{HRV})}{\overline{\text{HRV}}_7} \times 100"
        ),
        "parameters": {
            "Finestra breve / lunga": f"{config.hrv_short_window_days} giorni / {config.hrv_long_window_days} giorni",
            "Moltiplicatore banda k": f"{config.hrv_band_k}",
        },
        "limits": (
            "Un calo di ln(rMSSD) sotto la banda accompagnato da CV ridotto può indicare affaticamento "
            "parasimpatico; un aumento sopra banda può indicare iper-compensazione o saturazione vagale."
        ),
    }

    ordered = sorted((r for r in daily_rows if r.get("day")), key=lambda x: str(x["day"]))
    if not ordered:
        return make_not_calculable(unit="ln(ms)", reason="Nessun dato giornaliero disponibile.", how_it_works=hw)

    if target_day is None:
        target_day = str(ordered[-1]["day"])

    up_to = [r for r in ordered if str(r["day"]) <= target_day]
    valid_total = [r for r in up_to if r.get("hrv_night_ms") is not None and float(r["hrv_night_ms"]) > 0]
    if len(valid_total) < config.hrv_min_long_days:
        return make_not_calculable(
            unit="ln(ms)",
            reason=f"Letture HRV insufficienti ({len(valid_total)} < {config.hrv_min_long_days} giorni minimi per la banda a 60g).",
            n_samples=len(valid_total),
            how_it_works=hw,
        )

    k_mult = float(config.hrv_band_k)
    series: list[dict[str, Any]] = []

    for idx, r in enumerate(up_to):
        d_str = str(r["day"])
        d_dt = date.fromisoformat(d_str)
        s7_str = (d_dt - timedelta(days=config.hrv_short_window_days - 1)).isoformat()
        s60_str = (d_dt - timedelta(days=config.hrv_long_window_days - 1)).isoformat()

        w7 = [
            float(x["hrv_night_ms"])
            for x in up_to[: idx + 1]
            if s7_str <= str(x["day"]) <= d_str and x.get("hrv_night_ms") is not None and float(x["hrv_night_ms"]) > 0
        ]
        w60 = [
            float(x["hrv_night_ms"])
            for x in up_to[: idx + 1]
            if s60_str <= str(x["day"]) <= d_str and x.get("hrv_night_ms") is not None and float(x["hrv_night_ms"]) > 0
        ]

        raw_hrv = float(r["hrv_night_ms"]) if (r.get("hrv_night_ms") is not None and float(r["hrv_night_ms"]) > 0) else None
        ln_val = round(math.log(raw_hrv), 3) if raw_hrv is not None else None

        if len(w7) >= 3:
            ln_w7 = np.log(np.asarray(w7, dtype=float))
            ln_mean_7 = round(float(np.mean(ln_w7)), 3)
            ms_mean_7 = round(float(np.mean(w7)), 1)
            cv_7_pct = round(float(np.std(w7, ddof=1) / np.mean(w7) * 100.0), 1)
        else:
            ln_mean_7 = None
            ms_mean_7 = None
            cv_7_pct = None

        if len(w60) >= config.hrv_min_long_days:
            ln_w60 = np.log(np.asarray(w60, dtype=float))
            m60 = float(np.mean(ln_w60))
            sd60 = float(np.std(ln_w60, ddof=1))
            ln_low = round(m60 - k_mult * sd60, 3)
            ln_high = round(m60 + k_mult * sd60, 3)
            ms_low = round(math.exp(ln_low), 1)
            ms_high = round(math.exp(ln_high), 1)
        else:
            ln_low = None
            ln_high = None
            ms_low = None
            ms_high = None

        status_band = "in_band"
        if ln_mean_7 is not None and ln_low is not None and ln_high is not None:
            if ln_mean_7 < ln_low:
                status_band = "below_band"
            elif ln_mean_7 > ln_high:
                status_band = "above_band"

        series.append(
            {
                "day": d_str,
                "hrv_ms": raw_hrv,
                "ln_rmssd": ln_val,
                "ln_rmssd_7d": ln_mean_7,
                "hrv_7d_ms": ms_mean_7,
                "cv_7d_pct": cv_7_pct,
                "ln_band_low": ln_low,
                "ln_band_high": ln_high,
                "ms_band_low": ms_low,
                "ms_band_high": ms_high,
                "band_status": status_band,
            }
        )

    latest = series[-1]
    if latest["ln_rmssd_7d"] is None or latest["ln_band_low"] is None:
        return make_not_calculable(
            unit="ln(ms)",
            reason="Finestra recente (7g) o storica (60g) incompleta alla data selezionata.",
            n_samples=len(valid_total),
            how_it_works=hw,
        )

    return make_result(
        value={
            "latest": latest,
            "series": series,
        },
        unit="ln(ms)",
        n_samples=len(valid_total),
        quality_flag="good" if len(valid_total) >= 45 else "partial",
        assumptions=[
            f"Banda calcolata su trasformazione logaritmica ln(rMSSD) con k = {k_mult} SD su finestra mobile fino a 60 giorni."
        ],
        how_it_works=hw,
    )


def _theil_sen_and_mann_kendall(values: list[float], times_days: list[float]) -> dict[str, Any]:
    """Calcola lo stimatore di pendenza robusto di Theil-Sen e il test non parametrico di Mann-Kendall."""
    n = len(values)
    if n < 6:
        return {"slope_per_day": None, "slope_per_week": None, "mk_z": None, "mk_p_value": None, "trend_direction": "insufficient_data"}

    slopes: list[float] = []
    s_stat = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            dt = times_days[j] - times_days[i]
            if dt <= 0:
                continue
            dy = values[j] - values[i]
            slopes.append(dy / dt)
            if dy > 0:
                s_stat += 1
            elif dy < 0:
                s_stat -= 1

    slope_day = float(np.median(slopes)) if slopes else 0.0
    slope_week = slope_day * 7.0

    # Varianza di S corretta per i valori pareggiati (ties)
    _, tie_counts = np.unique(np.round(values, 4), return_counts=True)
    tie_term = sum(int(t) * (int(t) - 1) * (2 * int(t) + 5) for t in tie_counts if t > 1)
    var_s = (n * (n - 1) * (2 * n + 5) - tie_term) / 18.0

    if var_s <= 0:
        z_mk = 0.0
    elif s_stat > 0:
        z_mk = (s_stat - 1.0) / math.sqrt(var_s)
    elif s_stat < 0:
        z_mk = (s_stat + 1.0) / math.sqrt(var_s)
    else:
        z_mk = 0.0

    # P-value a due code tramite funzione degli errori complementare erfc(|z| / sqrt(2))
    p_val = math.erfc(abs(z_mk) / math.sqrt(2.0))
    if p_val < 0.05:
        direction = "increasing" if s_stat > 0 else "decreasing"
    else:
        direction = "stable"

    return {
        "slope_per_day": round(slope_day, 4),
        "slope_per_week": round(slope_week, 3),
        "mk_s": int(s_stat),
        "mk_z": round(z_mk, 2),
        "mk_p_value": round(p_val, 4),
        "trend_direction": direction,
    }


def _detect_cusum_changepoints(
    days: list[str],
    values: list[float],
    drift_k: float,
    threshold_h: float,
) -> list[dict[str, Any]]:
    """Individua punti di cambio di regime tramite Page's CUSUM bilaterale su serie standardizzata in modo robusto."""
    if len(values) < 10:
        return []
    arr = np.asarray(values, dtype=float)
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    sigma = max(1.4826 * mad, float(np.std(arr, ddof=1)) if len(arr) > 1 else 1.0, 1e-4)

    s_pos = 0.0
    s_neg = 0.0
    changepoints: list[dict[str, Any]] = []

    for d_str, val in zip(days, values):
        z = (val - med) / sigma
        s_pos = max(0.0, s_pos + z - drift_k)
        s_neg = max(0.0, s_neg - z - drift_k)
        if s_pos >= threshold_h:
            changepoints.append({"day": d_str, "value": round(val, 2), "direction": "upward_shift", "cusum_stat": round(s_pos, 2)})
            s_pos = 0.0
        elif s_neg >= threshold_h:
            changepoints.append({"day": d_str, "value": round(val, 2), "direction": "downward_shift", "cusum_stat": round(s_neg, 2)})
            s_neg = 0.0

    return changepoints


def compute_metric_trend_analysis(
    daily_rows: list[dict[str, Any]],
    metric: str,
    unit: str = "",
    window_days: int = 30,
    target_day: str | None = None,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Esegue l'analisi statistica completa di una metrica giornaliera su `window_days` giorni:
    1. Media mobile semplice a 7 giorni (SMA7) ed esponenziale (EWMA7)
    2. Pendenza robusta di Theil-Sen (variazione per settimana) e test di Mann-Kendall (Z e p-value)
    3. Rilevamento dei punti di cambio di regime con Page's CUSUM
    4. Rilevamento di anomalie tramite z-score robusto (|z| > `config.robust_z_anomaly_threshold`)
    5. Ciclo settimanale (medie e deviazioni standard per giorno della settimana Lun..Dom)

    Dati minimi e limiti:
      - Richiede almeno 7 osservazioni valide nella finestra selezionata.
    """
    hw = {
        "title": f"Analisi di Tendenza ({metric}): Theil-Sen, Mann-Kendall, CUSUM e Anomalie",
        "formula_latex": (
            r"\beta_{\text{TS}} = \text{mediana}_{i<j}\frac{x_j - x_i}{t_j - t_i}, \quad "
            r"S_t^+ = \max(0, S_{t-1}^+ + z_t - k), \quad "
            r"z_{\text{rob},t} = \frac{x_t - m}{1.4826\cdot\text{MAD}}"
        ),
        "parameters": {
            "Finestra": f"{window_days} giorni",
            "Soglia anomalia |z robusto|": f"> {config.robust_z_anomaly_threshold}",
            "CUSUM (drift k, soglia h)": f"({config.cusum_drift_k}, {config.cusum_threshold_h})",
        },
        "limits": "Il test di Mann-Kendall assume osservazioni temporalmente ordinate; l'autocorrelazione a breve termine può ridurre i p-value.",
    }

    ordered = sorted((r for r in daily_rows if r.get("day")), key=lambda x: str(x["day"]))
    if not ordered:
        return make_not_calculable(unit=unit, reason="Archivio giornaliero vuoto.", how_it_works=hw)

    if target_day is None:
        target_day = str(ordered[-1]["day"])
    end_dt = date.fromisoformat(target_day)
    start_str = (end_dt - timedelta(days=max(7, window_days) - 1)).isoformat()

    in_win = [r for r in ordered if start_str <= str(r["day"]) <= target_day]
    valid_pts: list[tuple[str, float, float]] = []
    t0 = date.fromisoformat(str(in_win[0]["day"])) if in_win else end_dt

    for r in in_win:
        val = r.get(metric)
        if val is not None and not isinstance(val, bool):
            d_str = str(r["day"])
            t_days = float((date.fromisoformat(d_str) - t0).days)
            valid_pts.append((d_str, float(val), t_days))

    if len(valid_pts) < 7:
        return make_not_calculable(
            unit=unit,
            reason=f"Osservazioni valide insufficienti per {metric} ({len(valid_pts)} < 7 giorni minimi).",
            n_samples=len(valid_pts),
            how_it_works=hw,
        )

    v_days = [p[0] for p in valid_pts]
    v_vals = [p[1] for p in valid_pts]
    v_times = [p[2] for p in valid_pts]

    arr = np.asarray(v_vals, dtype=float)
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    robust_sd = max(1.4826 * mad, float(np.std(arr, ddof=1)) if len(arr) > 1 else 1.0, 1e-4)

    # Serie con SMA7, EWMA7 e z robusto
    alpha_7 = 2.0 / 8.0
    ewma_val = v_vals[0]
    series_out: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []

    for i, (d_str, val, _) in enumerate(valid_pts):
        sma_slice = v_vals[max(0, i - 6) : i + 1]
        sma7 = round(float(np.mean(sma_slice)), 2)
        ewma_val = alpha_7 * val + (1.0 - alpha_7) * ewma_val
        z_rob = round((val - med) / robust_sd, 2)
        is_anom = abs(z_rob) >= config.robust_z_anomaly_threshold
        pt_dict = {
            "day": d_str,
            "value": round(val, 2),
            "sma_7d": sma7,
            "ewma_7d": round(ewma_val, 2),
            "z_robust": z_rob,
            "is_anomaly": is_anom,
        }
        series_out.append(pt_dict)
        if is_anom:
            anomalies.append({"day": d_str, "value": round(val, 2), "z_robust": z_rob})

    ts_mk = _theil_sen_and_mann_kendall(v_vals, v_times)
    changepoints = _detect_cusum_changepoints(
        v_days,
        v_vals,
        drift_k=config.cusum_drift_k,
        threshold_h=config.cusum_threshold_h,
    )

    # Ciclo settimanale (Lun=0 .. Dom=6)
    by_weekday: dict[int, list[float]] = {k: [] for k in range(7)}
    for d_str, val, _ in valid_pts:
        wd = date.fromisoformat(d_str).weekday()
        by_weekday[wd].append(val)

    weekly_cycle: list[dict[str, Any]] = []
    for wd in range(7):
        w_list = by_weekday[wd]
        weekly_cycle.append(
            {
                "weekday_index": wd,
                "weekday_name": WEEKDAY_NAMES_IT[wd],
                "n_days": len(w_list),
                "mean": round(float(np.mean(w_list)), 2) if w_list else None,
                "median": round(float(np.median(w_list)), 2) if w_list else None,
            }
        )

    return make_result(
        value={
            "metric": metric,
            "median": round(med, 2),
            "mad": round(mad, 2),
            "robust_sd": round(robust_sd, 2),
            "theil_sen_mann_kendall": ts_mk,
            "cusum_changepoints": changepoints,
            "anomalies": anomalies,
            "weekly_cycle": weekly_cycle,
            "series": series_out,
        },
        unit=unit,
        n_samples=len(valid_pts),
        quality_flag="good" if len(valid_pts) >= 14 else "partial",
        assumptions=[f"Analisi effettuata su {len(valid_pts)} giorni validi tra {v_days[0]} e {v_days[-1]}."],
        how_it_works=hw,
    )


def compute_body_battery_recharge(
    daily_row: dict[str, Any] | None,
) -> dict[str, Any]:
    """Calcola la ricarica notturna/giornaliera del Body Battery e la velocità di ricarica per ora di sonno.

    Formula:
      Delta_BB = bb_charged  (oppure bb_max - bb_min se bb_charged non è riportato)
      Rate_BB_h = Delta_BB / (sleep_total_min / 60)   [punti / ora di sonno]
    """
    hw = {
        "title": "Ricarica Notturna del Body Battery",
        "formula_latex": r"\Delta\text{BB} = \text{BB}_{\text{caricata}}, \quad v_{\text{ricarica}} = \frac{\Delta\text{BB}}{T_{\text{sonno}}\;[\text{h}]}",
        "parameters": {"Scala Body Battery": "5 – 100 punti"},
        "limits": "Stima proprietaria Garmin basata su HRV, stress e movimento notturno.",
    }
    if not daily_row:
        return make_not_calculable(unit="pt", reason="Riga giornaliera assente.", how_it_works=hw)

    charged = daily_row.get("bb_charged")
    if charged is None and daily_row.get("bb_max") is not None and daily_row.get("bb_min") is not None:
        charged = float(daily_row["bb_max"]) - float(daily_row["bb_min"])

    if charged is None:
        return make_not_calculable(unit="pt", reason="Dati Body Battery assenti per il giorno selezionato.", how_it_works=hw)

    sleep_m = daily_row.get("sleep_total_min")
    rate_per_h = round(float(charged) / (float(sleep_m) / 60.0), 1) if (sleep_m is not None and float(sleep_m) > 60.0) else None

    return make_result(
        value={
            "bb_charged": round(float(charged), 0),
            "bb_max": daily_row.get("bb_max"),
            "bb_min": daily_row.get("bb_min"),
            "recharge_rate_pt_per_hour": rate_per_h,
        },
        unit="pt",
        n_samples=1,
        quality_flag="good" if rate_per_h is not None else "partial",
        assumptions=["Velocità di ricarica normalizzata sulle ore di sonno registrate."],
        how_it_works=hw,
    )


def compute_training_to_recovery_effect(
    daily_rows: list[dict[str, Any]],
    activities_rows: list[dict[str, Any]],
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Valuta l'effetto delle sedute di allenamento (Dure vs Facili vs Riposo al giorno t)
    sul recupero della notte successiva (HRV_{t+1} e RHR_{t+1}), stimando la differenza media
    e l'intervallo di confidenza al 95% tramite bootstrap non parametrico.

    Formula:
      - I giorni con attività vengono divisi sul 60° percentile del TRIMP giornaliero in:
        * Riposo (L_t = 0)
        * Seduta facile (0 < L_t < P_60)
        * Seduta dura (L_t >= P_60)
      - Per ogni categoria si raccoglie il valore di HRV_{t+1} e RHR_{t+1} (o la variazione rispetto
        alla mediana mobile) e si calcola l'effetto medio (Dura - Riposo/Facile) con IC 95% bootstrap
        su `config.bootstrap_iterations` (2000) ricampionamenti.

    Dati minimi e limiti:
      - Richiede almeno 5 eventi di seduta dura e 5 giorni di riposo/seduta facile con HRV_{t+1} valida.
    """
    hw = {
        "title": "Effetto Allenamento → Recupero Notte Successiva (Bootstrap IC 95%)",
        "formula_latex": (
            r"\Delta\overline{\text{HRV}} = \overline{\text{HRV}}_{t+1 \mid \text{Dura},t} - "
            r"\overline{\text{HRV}}_{t+1 \mid \text{Facile/Riposo},t}"
        ),
        "parameters": {
            "Soglia seduta dura": "≥ 60° percentile del carico TRIMP giornaliero",
            "Ricampionamenti bootstrap": f"{config.bootstrap_iterations} (IC {int(config.bootstrap_confidence * 100)}%)",
        },
        "limits": "Studio osservazionale intra-soggetto: fattori confondenti (es. sonno più breve o cene tardive nei giorni di gara) possono contribuire all'effetto.",
    }

    ordered = sorted((r for r in daily_rows if r.get("day")), key=lambda x: str(x["day"]))
    if len(ordered) < 20:
        return make_not_calculable(
            unit="ms",
            reason=f"Storico giornaliero insufficiente ({len(ordered)} < 20 giorni).",
            n_samples=len(ordered),
            how_it_works=hw,
        )

    rhr_by_day = {str(r["day"]): r.get("rhr_bpm") for r in ordered}
    load_by_day: dict[str, float] = {str(r["day"]): 0.0 for r in ordered}
    for act in activities_rows:
        d = str(act.get("day") or "")
        if d not in load_by_day:
            continue
        t_res = compute_trimp(act, hr_rest=rhr_by_day.get(d), config=config)
        if t_res["calculable"] and t_res["value"] is not None:
            load_by_day[d] += float(t_res["value"])
        elif act.get("training_load") is not None:
            load_by_day[d] += float(act["training_load"])

    positive_loads = [v for v in load_by_day.values() if v > 0]
    if len(positive_loads) < 8:
        return make_not_calculable(
            unit="ms",
            reason="Numero di giorni con allenamento insufficiente (< 8 sedute).",
            n_samples=len(positive_loads),
            how_it_works=hw,
        )

    hard_threshold = float(np.percentile(positive_loads, 60))

    hard_next_hrv: list[float] = []
    easy_next_hrv: list[float] = []
    rest_next_hrv: list[float] = []

    hard_next_rhr: list[float] = []
    easy_next_rhr: list[float] = []
    rest_next_rhr: list[float] = []

    for i in range(len(ordered) - 1):
        cur_r = ordered[i]
        nxt_r = ordered[i + 1]
        d_cur = date.fromisoformat(str(cur_r["day"]))
        d_nxt = date.fromisoformat(str(nxt_r["day"]))
        if (d_nxt - d_cur).days != 1:
            continue

        l_t = load_by_day[str(cur_r["day"])]
        nxt_hrv = nxt_r.get("hrv_night_ms")
        nxt_rhr = nxt_r.get("rhr_bpm")

        if l_t >= hard_threshold:
            if nxt_hrv is not None:
                hard_next_hrv.append(float(nxt_hrv))
            if nxt_rhr is not None:
                hard_next_rhr.append(float(nxt_rhr))
        elif l_t > 0:
            if nxt_hrv is not None:
                easy_next_hrv.append(float(nxt_hrv))
            if nxt_rhr is not None:
                easy_next_rhr.append(float(nxt_rhr))
        else:
            if nxt_hrv is not None:
                rest_next_hrv.append(float(nxt_hrv))
            if nxt_rhr is not None:
                rest_next_rhr.append(float(nxt_rhr))

    baseline_hrv_pool = easy_next_hrv + rest_next_hrv
    baseline_rhr_pool = easy_next_rhr + rest_next_rhr

    if len(hard_next_hrv) < 5 or len(baseline_hrv_pool) < 5:
        return make_not_calculable(
            unit="ms",
            reason=f"Eventi insufficienti per il bootstrap (sedute dure={len(hard_next_hrv)}, facili/riposo={len(baseline_hrv_pool)}; minimo 5 per gruppo).",
            n_samples=len(hard_next_hrv) + len(baseline_hrv_pool),
            how_it_works=hw,
        )

    rng = np.random.default_rng(config.bootstrap_seed)
    n_boot = config.bootstrap_iterations
    alpha = 1.0 - config.bootstrap_confidence

    def _bootstrap_diff_ci(group_a: list[float], group_b: list[float]) -> tuple[float, float, float]:
        arr_a = np.asarray(group_a, dtype=float)
        arr_b = np.asarray(group_b, dtype=float)
        obs_diff = float(np.mean(arr_a) - np.mean(arr_b))
        idx_a = rng.integers(0, len(arr_a), size=(n_boot, len(arr_a)))
        idx_b = rng.integers(0, len(arr_b), size=(n_boot, len(arr_b)))
        diffs = np.mean(arr_a[idx_a], axis=1) - np.mean(arr_b[idx_b], axis=1)
        ci_low = float(np.percentile(diffs, (alpha / 2.0) * 100.0))
        ci_high = float(np.percentile(diffs, (1.0 - alpha / 2.0) * 100.0))
        return round(obs_diff, 2), round(ci_low, 2), round(ci_high, 2)

    hrv_diff, hrv_ci_low, hrv_ci_high = _bootstrap_diff_ci(hard_next_hrv, baseline_hrv_pool)
    rhr_diff, rhr_ci_low, rhr_ci_high = (
        _bootstrap_diff_ci(hard_next_rhr, baseline_rhr_pool)
        if (len(hard_next_rhr) >= 5 and len(baseline_rhr_pool) >= 5)
        else (None, None, None)
    )

    total_events = len(hard_next_hrv) + len(easy_next_hrv) + len(rest_next_hrv)
    return make_result(
        value={
            "hard_load_threshold_trimp": round(hard_threshold, 1),
            "n_hard_sessions": len(hard_next_hrv),
            "n_easy_sessions": len(easy_next_hrv),
            "n_rest_days": len(rest_next_hrv),
            "hrv_next_night": {
                "mean_after_hard_ms": round(float(np.mean(hard_next_hrv)), 1),
                "mean_after_easy_ms": round(float(np.mean(easy_next_hrv)), 1) if easy_next_hrv else None,
                "mean_after_rest_ms": round(float(np.mean(rest_next_hrv)), 1) if rest_next_hrv else None,
                "diff_hard_vs_non_hard_ms": hrv_diff,
                "ci95_low_ms": hrv_ci_low,
                "ci95_high_ms": hrv_ci_high,
            },
            "rhr_next_night": {
                "mean_after_hard_bpm": round(float(np.mean(hard_next_rhr)), 1) if hard_next_rhr else None,
                "mean_after_easy_bpm": round(float(np.mean(easy_next_rhr)), 1) if easy_next_rhr else None,
                "mean_after_rest_bpm": round(float(np.mean(rest_next_rhr)), 1) if rest_next_rhr else None,
                "diff_hard_vs_non_hard_bpm": rhr_diff,
                "ci95_low_bpm": rhr_ci_low,
                "ci95_high_bpm": rhr_ci_high,
            },
        },
        unit="ms",
        n_samples=total_events,
        quality_flag="good",
        assumptions=[
            f"Soglia seduta dura fissata al 60° percentile TRIMP ({hard_threshold:.1f}).",
            f"Intervallo di confidenza al {int(config.bootstrap_confidence * 100)}% su {n_boot} ricampionamenti bootstrap.",
        ],
        how_it_works=hw,
    )


SPORT_GROUP_META: dict[str, dict[str, str]] = {
    "tennis": {"label": "🎾 Tennis", "color": "#FB923C"},
    "strength_training": {"label": "🏋️ Pesi / Forza", "color": "#818CF8"},
    "running": {"label": "🏃 Corsa", "color": "#2DD4BF"},
    "cycling": {"label": "🚴 Ciclismo", "color": "#38BDF8"},
    "swimming": {"label": "🏊 Nuoto", "color": "#60A5FA"},
    "walking": {"label": "🚶 Camminata", "color": "#A3E635"},
}


def _normalize_sport_key(raw_type: str | None) -> str:
    t = str(raw_type or "other").lower().strip()
    if t in ("tennis_v2", "tennis"):
        return "tennis"
    return t


def compute_multisport_comparison(
    daily_rows: list[dict[str, Any]],
    activities_rows: list[dict[str, Any]],
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Confronta il profilo fisiologico e il costo di recupero della notte successiva (t+1)
    tra le diverse discipline praticate (es. Tennis vs Pesi vs Corsa).

    Formula:
      Per ogni disciplina S con n_S >= 1 sedute:
        - Densità TRIMP = sum(TRIMP_i) / sum(Durata_min_i)   [TRIMP / min]
        - Quota Z4+Z5 (%) = sum(t_{Z4,i} + t_{Z5,i}) / sum(t_{Z1..Z5,i}) * 100
        - Delta HRV_{t+1} = Media(HRV_{d+1 | d in S}) - Media(HRV_{d+1 | d riposo})   [ms]
        - Delta RHR_{t+1} = Media(RHR_{d+1 | d in S}) - Media(RHR_{d+1 | d riposo})   [bpm]

    Dati minimi e limiti:
      - Richiede almeno 1 attività registrata e la tabella giornaliera per associare la notte successiva.
      - Se in uno stesso giorno vengono svolte due discipline diverse, la notte successiva riflette il carico combinato.
    """
    hw = {
        "title": "Profilo Fisiologico Multi-Sport (Tennis vs Pesi vs Corsa) e Risposta a t+1",
        "formula_latex": (
            r"\text{Densità}_S = \frac{\sum_{i \in S}\text{TRIMP}_i}{\sum_{i \in S} T_i\;[\text{min}]}, \quad "
            r"\Delta\text{HRV}_{S,t+1} = \overline{\text{HRV}}_{t+1 \mid S} - \overline{\text{HRV}}_{t+1 \mid \text{riposo}}"
        ),
        "parameters": {
            "Finestra risposta": "Notte successiva (lag +1 giorno)",
            "Riferimento baseline": "Notti successive ai giorni senza attività registrate",
        },
        "limits": "Confronto osservazionale: l'orario della seduta (es. Tennis serale vs Corsa mattutina) influenza il recupero notturno.",
    }

    if not activities_rows:
        return make_not_calculable(
            unit="TRIMP/min",
            reason="Nessuna attività presente in archivio per il confronto multi-sport.",
            n_samples=0,
            how_it_works=hw,
        )

    ordered_daily = sorted([r for r in daily_rows if r.get("day")], key=lambda r: str(r["day"]))
    daily_by_day: dict[str, dict[str, Any]] = {str(r["day"]): r for r in ordered_daily}
    rhr_by_day: dict[str, float | None] = {str(r["day"]): r.get("rhr_bpm") for r in ordered_daily}

    # Individua i giorni con attività per calcolare il pool di riferimento "post-riposo"
    active_days_set = {str(a.get("day")) for a in activities_rows if a.get("day")}
    rest_next_hrv: list[float] = []
    rest_next_rhr: list[float] = []

    for i in range(len(ordered_daily) - 1):
        d_cur = str(ordered_daily[i]["day"])
        d_nxt = str(ordered_daily[i + 1]["day"])
        if (date.fromisoformat(d_nxt) - date.fromisoformat(d_cur)).days != 1:
            continue
        if d_cur not in active_days_set:
            nxt_row = ordered_daily[i + 1]
            if nxt_row.get("hrv_night_ms") is not None:
                rest_next_hrv.append(float(nxt_row["hrv_night_ms"]))
            if nxt_row.get("rhr_bpm") is not None:
                rest_next_rhr.append(float(nxt_row["rhr_bpm"]))

    # Se non ci sono abbastanza giorni di riposo, usa la media globale come riferimento
    ref_hrv = (
        float(np.mean(rest_next_hrv))
        if len(rest_next_hrv) >= 2
        else float(np.mean([float(r["hrv_night_ms"]) for r in ordered_daily if r.get("hrv_night_ms") is not None] or [0.0]))
    )
    ref_rhr = (
        float(np.mean(rest_next_rhr))
        if len(rest_next_rhr) >= 2
        else float(np.mean([float(r["rhr_bpm"]) for r in ordered_daily if r.get("rhr_bpm") is not None] or [0.0]))
    )

    by_sport: dict[str, list[dict[str, Any]]] = {}
    for act in activities_rows:
        s_key = _normalize_sport_key(act.get("type"))
        by_sport.setdefault(s_key, []).append(act)

    sports_summary: list[dict[str, Any]] = []
    for s_key, acts in by_sport.items():
        meta = SPORT_GROUP_META.get(
            s_key,
            {"label": f"⚡ {s_key.replace('_', ' ').title()}", "color": "#94A3B8"},
        )
        durs_min: list[float] = []
        trimps: list[float] = []
        hrs_avg: list[float] = []
        hrs_max: list[float] = []
        z_low_s = 0.0
        z_mod_s = 0.0
        z_high_s = 0.0
        z_tot_s = 0.0

        nxt_hrvs: list[float] = []
        nxt_rhrs: list[float] = []
        nxt_bb_chg: list[float] = []
        nxt_deep_pct: list[float] = []

        for a in acts:
            d_str = str(a.get("day") or "")
            dur_s = float(a.get("duration_s") or 0.0)
            if dur_s > 0:
                durs_min.append(dur_s / 60.0)
            t_res = compute_trimp(a, hr_rest=rhr_by_day.get(d_str), config=config)
            if t_res["calculable"] and t_res["value"] is not None:
                trimps.append(float(t_res["value"]))
            elif a.get("training_load") is not None:
                trimps.append(float(a["training_load"]))

            if a.get("hr_avg") is not None:
                hrs_avg.append(float(a["hr_avg"]))
            if a.get("hr_max") is not None:
                hrs_max.append(float(a["hr_max"]))

            zs = [float(a.get(f"z{k}_s") or 0.0) for k in range(1, 6)]
            s_z = sum(zs)
            if s_z > 0:
                z_low_s += zs[0] + zs[1]
                z_mod_s += zs[2]
                z_high_s += zs[3] + zs[4]
                z_tot_s += s_z

            if d_str:
                nxt_day_str = (date.fromisoformat(d_str) + timedelta(days=1)).isoformat()
                nxt_r = daily_by_day.get(nxt_day_str)
                if nxt_r:
                    if nxt_r.get("hrv_night_ms") is not None:
                        nxt_hrvs.append(float(nxt_r["hrv_night_ms"]))
                    if nxt_r.get("rhr_bpm") is not None:
                        nxt_rhrs.append(float(nxt_r["rhr_bpm"]))
                    if nxt_r.get("bb_charged") is not None:
                        nxt_bb_chg.append(float(nxt_r["bb_charged"]))
                    tot_slp = float(nxt_r.get("sleep_total_min") or 0.0)
                    deep_slp = float(nxt_r.get("sleep_deep_min") or 0.0)
                    if tot_slp > 0 and deep_slp >= 0:
                        nxt_deep_pct.append((deep_slp / tot_slp) * 100.0)

        tot_dur_min = sum(durs_min)
        tot_trimp = sum(trimps)
        trimp_per_min = round(tot_trimp / tot_dur_min, 2) if tot_dur_min > 0 else 0.0
        mean_hrv_nxt = round(float(np.mean(nxt_hrvs)), 1) if nxt_hrvs else None
        mean_rhr_nxt = round(float(np.mean(nxt_rhrs)), 1) if nxt_rhrs else None

        sports_summary.append(
            {
                "sport_key": s_key,
                "label": meta["label"],
                "color": meta["color"],
                "sessions_count": len(acts),
                "avg_duration_min": round(float(np.mean(durs_min)), 1) if durs_min else 0.0,
                "avg_trimp": round(float(np.mean(trimps)), 1) if trimps else 0.0,
                "trimp_per_min": trimp_per_min,
                "avg_hr_bpm": round(float(np.mean(hrs_avg)), 0) if hrs_avg else None,
                "max_hr_bpm": round(float(np.max(hrs_max)), 0) if hrs_max else None,
                "low_z1_z2_pct": round((z_low_s / z_tot_s) * 100.0, 1) if z_tot_s > 0 else 0.0,
                "mod_z3_pct": round((z_mod_s / z_tot_s) * 100.0, 1) if z_tot_s > 0 else 0.0,
                "high_z4_z5_pct": round((z_high_s / z_tot_s) * 100.0, 1) if z_tot_s > 0 else 0.0,
                "next_night_hrv_ms": mean_hrv_nxt,
                "next_night_hrv_delta_ms": round(mean_hrv_nxt - ref_hrv, 1) if (mean_hrv_nxt is not None and ref_hrv > 0) else None,
                "next_night_rhr_bpm": mean_rhr_nxt,
                "next_night_rhr_delta_bpm": round(mean_rhr_nxt - ref_rhr, 1) if (mean_rhr_nxt is not None and ref_rhr > 0) else None,
                "next_night_bb_charged": round(float(np.mean(nxt_bb_chg)), 1) if nxt_bb_chg else None,
                "next_night_deep_pct": round(float(np.mean(nxt_deep_pct)), 1) if nxt_deep_pct else None,
            }
        )

    sports_summary.sort(key=lambda x: x["sessions_count"], reverse=True)

    return make_result(
        value={
            "sports": sports_summary,
            "reference_rest_hrv_ms": round(ref_hrv, 1) if ref_hrv > 0 else None,
            "reference_rest_rhr_bpm": round(ref_rhr, 1) if ref_rhr > 0 else None,
        },
        unit="TRIMP/min",
        n_samples=len(activities_rows),
        quality_flag="good" if len(sports_summary) >= 2 else "partial",
        assumptions=[
            f"Confronto su {len(activities_rows)} attività suddivise in {len(sports_summary)} discipline.",
            f"Riferimento post-riposo: HRV = {ref_hrv:.1f} ms, RHR = {ref_rhr:.1f} bpm.",
        ],
        how_it_works=hw,
    )

