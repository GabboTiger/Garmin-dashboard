"""Analisi algoritmiche per le Attività (Fase 3b - Step 2):
- Recupero della frequenza cardiaca a 60 s e 120 s (HRR60, HRR120) con controllo risoluzione e defaticamento
- Tempo per zona FC e distribuzione settimanale (incluso indice di polarizzazione)
- TRIMP (Banister esponenziale da serie FC o media, con fallback a zone)
- Carico acuto (ATL 7g), cronico (CTL 28g), rapporto ACWR e bilancio Fitness/Fatica (TSB)
- Deriva cardiaca (Cardiac Drift / Decoupling) su sedute costanti >= 30 minuti
- Efficienza velocità/FC (Efficiency Factor, metri per battito)

Ogni funzione è pura e riporta nella docstring: formula, parametri, dati minimi e limiti.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

import numpy as np

from app.analysis.common import make_not_calculable, make_result
from app.config import AnalysisConfig, DEFAULT_CONFIG


def extract_activity_timeseries(details_payload: dict[str, Any] | None) -> list[dict[str, float]]:
    """Estrae una lista ordinata di campioni `{elapsed_s, timestamp_ms, hr, speed_mps, distance_m}`
    dal payload `get_activity_details`.
    """
    if not isinstance(details_payload, dict):
        return []
    descriptors = details_payload.get("metricDescriptors")
    metrics_list = details_payload.get("activityDetailMetrics")
    if not isinstance(descriptors, list) or not isinstance(metrics_list, list):
        return []

    idx_map: dict[str, int] = {}
    for d in descriptors:
        if isinstance(d, dict) and d.get("key") is not None and d.get("metricsIndex") is not None:
            idx_map[str(d["key"])] = int(d["metricsIndex"])

    hr_idx = idx_map.get("directHeartRate")
    if hr_idx is None:
        return []
    el_idx = idx_map.get("sumElapsedDuration")
    ts_idx = idx_map.get("directTimestamp")
    sp_idx = idx_map.get("directSpeed")
    dist_idx = idx_map.get("sumDistance")

    samples: list[dict[str, float]] = []
    first_ts_ms: float | None = None

    for item in metrics_list:
        if not isinstance(item, dict):
            continue
        arr = item.get("metrics")
        if not isinstance(arr, list) or len(arr) <= hr_idx:
            continue
        hr_val = arr[hr_idx]
        if hr_val is None:
            continue
        ts_ms = float(arr[ts_idx]) if (ts_idx is not None and len(arr) > ts_idx and arr[ts_idx] is not None) else None
        if first_ts_ms is None and ts_ms is not None:
            first_ts_ms = ts_ms
        if el_idx is not None and len(arr) > el_idx and arr[el_idx] is not None:
            el_s = float(arr[el_idx])
        elif ts_ms is not None and first_ts_ms is not None:
            el_s = (ts_ms - first_ts_ms) / 1000.0
        else:
            continue

        speed_mps = (
            float(arr[sp_idx])
            if (sp_idx is not None and len(arr) > sp_idx and arr[sp_idx] is not None)
            else 0.0
        )
        dist_m = (
            float(arr[dist_idx])
            if (dist_idx is not None and len(arr) > dist_idx and arr[dist_idx] is not None)
            else 0.0
        )
        samples.append(
            {
                "elapsed_s": el_s,
                "timestamp_ms": ts_ms or 0.0,
                "hr": float(hr_val),
                "speed_mps": max(0.0, speed_mps),
                "distance_m": max(0.0, dist_m),
            }
        )

    samples.sort(key=lambda s: s["elapsed_s"])
    return samples


def compute_heart_rate_recovery(
    details_payload: dict[str, Any] | None,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Calcola il recupero della frequenza cardiaca a 60 s (HRR60) e 120 s (HRR120) dopo la fine dello sforzo.

    Formula:
      HRR_60  = FC(t_end) - FC(t_end + 60 s)   [bpm]
      HRR_120 = FC(t_end) - FC(t_end + 120 s)  [bpm]
      dove t_end è l'ultimo istante di movimento attivo prima dell'arresto (speed <= 0.35 m/s).

    Parametri:
      - details_payload: risposta grezza di `get_activity_details`
      - config.hrr_max_sample_gap_s: passo temporale massimo accettabile (default 25 s)

    Dati minimi e limiti:
      - Richiede serie FC ad alta risoluzione (passo mediano <= 25 s) che prosegua almeno 60 s dopo la fine
        dello sforzo attivo. Se l'orologio viene messo in stop nello stesso istante in cui ci si ferma,
        restituisce `calculable = False` ("non calcolabile").
      - Se negli ultimi 5 minuti prima dell'arresto la velocità media è scesa di oltre il 30% rispetto
        al corpo centrale della seduta (defaticamento lento), restituisce `calculable = False` con
        `quality_flag = "cooldown_detected"`, perché l'HRR standard richiede una cessazione netta dello sforzo.
    """
    hw = {
        "title": "Recupero della Frequenza Cardiaca (HRR 60 s / 120 s)",
        "formula_latex": r"\text{HRR}_{60} = \text{FC}(t_{\text{fine}}) - \text{FC}(t_{\text{fine}} + 60\,\text{s}), \quad \text{HRR}_{120} = \text{FC}(t_{\text{fine}}) - \text{FC}(t_{\text{fine}} + 120\,\text{s})",
        "parameters": {
            "Passo massimo tra campioni": f"{config.hrr_max_sample_gap_s} s",
            "Soglia velocità arresto": "0.35 m/s",
        },
        "limits": (
            "Richiede che la registrazione dell'attività resti attiva per 1-2 minuti da fermi a fine sforzo. "
            "Non confrontabile se preceduto da un lungo defaticamento al passo."
        ),
    }

    samples = extract_activity_timeseries(details_payload)
    if len(samples) < 20:
        return make_not_calculable(
            unit="bpm",
            reason="Serie temporale dell'attività assente o con meno di 20 campioni.",
            n_samples=len(samples),
            how_it_works=hw,
        )

    diffs = [
        samples[i + 1]["elapsed_s"] - samples[i]["elapsed_s"]
        for i in range(len(samples) - 1)
        if samples[i + 1]["elapsed_s"] > samples[i]["elapsed_s"]
    ]
    median_step_s = float(np.median(diffs)) if diffs else 999.0
    if median_step_s > config.hrr_max_sample_gap_s:
        return make_not_calculable(
            unit="bpm",
            reason=f"Risoluzione temporale insufficiente (passo mediano {median_step_s:.1f} s > {config.hrr_max_sample_gap_s} s).",
            n_samples=len(samples),
            quality_flag="low_resolution",
            how_it_works=hw,
        )

    # Identifica la fine dello sforzo attivo (`t_end`): ultimo campione con velocità > 0.35 m/s
    active_indices = [i for i, s in enumerate(samples) if s["speed_mps"] > 0.35]
    if not active_indices:
        return make_not_calculable(
            unit="bpm",
            reason="Impossibile distinguere la fine dello sforzo attivo dall'inizio del recupero (velocità assente).",
            n_samples=len(samples),
            how_it_works=hw,
        )

    end_idx = active_indices[-1]
    t_end = samples[end_idx]["elapsed_s"]
    hr_end = samples[end_idx]["hr"]
    total_end = samples[-1]["elapsed_s"]
    post_duration_s = total_end - t_end

    if post_duration_s < 55.0:
        return make_not_calculable(
            unit="bpm",
            reason=(
                f"Registrazione interrotta subito a fine sforzo ({post_duration_s:.0f} s di coda da fermi; "
                "servono almeno 60 s per HRR60)."
            ),
            n_samples=len(samples) - end_idx,
            how_it_works=hw,
        )

    # Verifica se c'è stato un defaticamento marcato negli ultimi 5 minuti prima di t_end
    core_speeds = [s["speed_mps"] for s in samples[:end_idx] if 300.0 <= s["elapsed_s"] <= max(300.0, t_end - 300.0)]
    tail_speeds = [s["speed_mps"] for s in samples[: end_idx + 1] if max(0.0, t_end - 240.0) <= s["elapsed_s"] <= t_end]
    if len(core_speeds) >= 10 and len(tail_speeds) >= 5:
        mean_core = float(np.mean(core_speeds))
        mean_tail = float(np.mean(tail_speeds))
        if mean_core > 1.0 and mean_tail < 0.70 * mean_core:
            return make_not_calculable(
                unit="bpm",
                reason="Rilevato defaticamento prima dello stop (velocità finale < 70% della velocità centrale): HRR non standard.",
                n_samples=len(samples) - end_idx,
                quality_flag="cooldown_detected",
                how_it_works=hw,
            )

    def _hr_at_offset(offset_s: float) -> float | None:
        target_t = t_end + offset_s
        candidates = [s for s in samples[end_idx:] if abs(s["elapsed_s"] - target_t) <= max(12.0, median_step_s * 1.5)]
        if not candidates:
            return None
        best = min(candidates, key=lambda s: abs(s["elapsed_s"] - target_t))
        return float(best["hr"])

    hr_60 = _hr_at_offset(60.0)
    if hr_60 is None:
        return make_not_calculable(
            unit="bpm",
            reason="Campione FC a +60 s dalla fine dello sforzo mancante.",
            n_samples=len(samples) - end_idx,
            how_it_works=hw,
        )

    hrr_60 = round(hr_end - hr_60, 1)
    hr_120 = _hr_at_offset(120.0)
    hrr_120 = round(hr_end - hr_120, 1) if hr_120 is not None else None

    q_flag = "good" if hrr_120 is not None else "partial"
    post_samples = len(samples) - end_idx
    return make_result(
        value={"hrr_60_bpm": hrr_60, "hrr_120_bpm": hrr_120, "hr_end_bpm": round(hr_end, 1)},
        unit="bpm",
        n_samples=post_samples,
        quality_flag=q_flag,
        assumptions=[
            f"Fine dello sforzo attivo individuata a t = {t_end:.0f} s (FC = {hr_end:.1f} bpm).",
            f"Risoluzione post-esercizio: 1 campione ogni {median_step_s:.1f} s.",
        ],
        how_it_works=hw,
    )


def compute_trimp(
    activity_row: dict[str, Any],
    details_payload: dict[str, Any] | None = None,
    hr_rest: float | None = None,
    hr_max: float | None = None,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Calcola il TRIMP (Training Impulse) di un'attività.

    Formula:
      1. Metodo esponenziale di Banister (preferito se disponibile serie FC o FC media):
         HR_r = clip((FC - FC_rest) / (FC_max - FC_rest), 0, 1)
         TRIMP = sum_i (dt_i [min] * HR_{r,i} * a * exp(b * HR_{r,i}))
         con a = 0.64, b = 1.92 (default configurabile in `app/config.py`).
      2. Metodo a zone di Edwards/Lucia (fallback se manca FC ma sono presenti z1_s..z5_s):
         TRIMP_zone = sum_{k=1..5} (t_{Z_k} [min] * w_k)

    Parametri:
      - activity_row: riga normalizzata della tabella `activities`
      - details_payload: serie temporale opzionale per integrazione campione-per-campione
      - hr_rest, hr_max: FC a riposo del giorno e FC massima (fallback su `config`)

    Dati minimi e limiti:
      - Richiede durata > 0 e almeno una misura di intensità cardiaca (serie FC, FC media o zone FC).
      - Sensibile alla scelta di FC_max: se FC_max è sottostimata, il termine esponenziale sovrastima il carico.
    """
    hw = {
        "title": "TRIMP (Training Impulse di Banister / Zone)",
        "formula_latex": (
            r"\text{HR}_r = \frac{\text{FC} - \text{FC}_{\text{rest}}}{\text{FC}_{\text{max}} - \text{FC}_{\text{rest}}}, \quad "
            r"\text{TRIMP} = \sum_i \Delta t_i \cdot \text{HR}_{r,i} \cdot a \, e^{b \cdot \text{HR}_{r,i}}"
        ),
        "parameters": {
            "Costanti Banister (a, b)": f"({config.trimp_banister_a}, {config.trimp_banister_b})",
            "FC riposo / FC max default": f"{config.hr_rest_default:.0f} / {config.hr_max_default:.0f} bpm",
        },
        "limits": "Pesa esponenzialmente l'intensità aerobica/anaerobica; sottostima il carico neuromuscolare puro (es. pesi massimali).",
    }

    dur_s = activity_row.get("duration_s")
    if dur_s is None or float(dur_s) <= 0:
        return make_not_calculable(
            unit="TRIMP",
            reason="Durata dell'attività assente o nulla.",
            how_it_works=hw,
        )

    f_rest = float(hr_rest if (hr_rest is not None and hr_rest > 30) else config.hr_rest_default)
    act_hr_max = activity_row.get("hr_max")
    f_max = float(
        max(
            hr_max or config.hr_max_default,
            float(act_hr_max) if act_hr_max is not None else 0.0,
            f_rest + 40.0,
        )
    )
    a_const = config.trimp_banister_a
    b_const = config.trimp_banister_b

    samples = extract_activity_timeseries(details_payload)
    if len(samples) >= 10:
        trimp_sum = 0.0
        for i in range(len(samples) - 1):
            dt_min = max(0.0, (samples[i + 1]["elapsed_s"] - samples[i]["elapsed_s"]) / 60.0)
            if dt_min > 5.0:
                continue
            hr_mid = 0.5 * (samples[i]["hr"] + samples[i + 1]["hr"])
            hrr_frac = float(np.clip((hr_mid - f_rest) / (f_max - f_rest), 0.0, 1.0))
            trimp_sum += dt_min * hrr_frac * a_const * math.exp(b_const * hrr_frac)
        return make_result(
            value=round(trimp_sum, 1),
            unit="TRIMP",
            n_samples=len(samples),
            quality_flag="good",
            assumptions=[
                f"Integrazione campione-per-campione di Banister con FC_rest={f_rest:.0f} bpm, FC_max={f_max:.0f} bpm."
            ],
            how_it_works=hw,
            extra={"method": "banister_timeseries"},
        )

    hr_avg = activity_row.get("hr_avg")
    if hr_avg is not None and float(hr_avg) > 0:
        dur_min = float(dur_s) / 60.0
        hrr_frac = float(np.clip((float(hr_avg) - f_rest) / (f_max - f_rest), 0.0, 1.0))
        trimp_val = dur_min * hrr_frac * a_const * math.exp(b_const * hrr_frac)
        return make_result(
            value=round(trimp_val, 1),
            unit="TRIMP",
            n_samples=1,
            quality_flag="partial",
            assumptions=[
                f"Calcolato da FC media dell'attività ({float(hr_avg):.0f} bpm) per assenza della serie temporale completa."
            ],
            how_it_works=hw,
            extra={"method": "banister_mean_hr"},
        )

    z_vals = [activity_row.get(f"z{k}_s") for k in range(1, 6)]
    if any(z is not None and float(z) > 0 for z in z_vals):
        weights = config.trimp_zone_weights
        z_trimp = sum((float(z_vals[i] or 0.0) / 60.0) * weights[i] for i in range(5))
        return make_result(
            value=round(z_trimp, 1),
            unit="TRIMP",
            n_samples=5,
            quality_flag="partial",
            assumptions=["Calcolato come somma pesata dei minuti nelle 5 zone FC (fallback Edwards)."],
            how_it_works=hw,
            extra={"method": "zones_fallback"},
        )

    return make_not_calculable(
        unit="TRIMP",
        reason="Frequenza cardiaca e zone FC assenti per questa attività.",
        how_it_works=hw,
    )


def compute_weekly_zone_distribution(
    activities_rows: list[dict[str, Any]],
    end_day: str,
    window_days: int = 7,
) -> dict[str, Any]:
    """Calcola il tempo totale trascorso nelle 5 zone di FC e la distribuzione percentuale
    negli ultimi `window_days` giorni fino a `end_day`.

    Formula:
      pct_k = (T_{Z_k} / sum_{j=1..5} T_{Z_j}) * 100
      polarization_ratio = (T_{Z_1} + T_{Z_2}) / max(T_{Z_4} + T_{Z_5}, 1)

    Dati minimi e limiti:
      - Richiede almeno 1 attività con zone FC valorizzate nella finestra selezionata.
    """
    hw = {
        "title": "Distribuzione Settimanale nelle Zone di FC",
        "formula_latex": r"p_{Z_k} = \frac{\sum t_{Z_k}}{\sum_{j=1}^5 \sum t_{Z_j}} \times 100",
        "parameters": {"Finestra": f"{window_days} giorni fino a {end_day}"},
        "limits": "Riflette solo le attività in cui il cardiofrequenzimetro era attivo.",
    }
    end_dt = date.fromisoformat(end_day)
    start_str = (end_dt - timedelta(days=max(1, window_days) - 1)).isoformat()

    in_window = [a for a in activities_rows if start_str <= str(a.get("day") or "") <= end_day]
    z_totals_min = [0.0, 0.0, 0.0, 0.0, 0.0]
    acts_with_zones = 0

    for a in in_window:
        z_secs = [a.get(f"z{k}_s") for k in range(1, 6)]
        if any(z is not None and float(z) > 0 for z in z_secs):
            acts_with_zones += 1
            for k in range(5):
                z_totals_min[k] += float(z_secs[k] or 0.0) / 60.0

    total_min = sum(z_totals_min)
    if acts_with_zones == 0 or total_min <= 0.0:
        return make_not_calculable(
            unit="min",
            reason=f"Nessuna attività con zone di frequenza cardiaca nei {window_days} giorni selezionati.",
            n_samples=0,
            how_it_works=hw,
        )

    pcts = [round((m / total_min) * 100.0, 1) for m in z_totals_min]
    low_intensity_pct = round(pcts[0] + pcts[1], 1)
    high_intensity_pct = round(pcts[3] + pcts[4], 1)

    return make_result(
        value={
            "zone_minutes": [round(m, 1) for m in z_totals_min],
            "zone_pct": pcts,
            "total_zone_minutes": round(total_min, 1),
            "low_intensity_z1_z2_pct": low_intensity_pct,
            "moderate_z3_pct": pcts[2],
            "high_intensity_z4_z5_pct": high_intensity_pct,
        },
        unit="min",
        n_samples=acts_with_zones,
        quality_flag="good",
        assumptions=[f"Aggregazione su {acts_with_zones} attività tra {start_str} e {end_day}."],
        how_it_works=hw,
    )


def compute_acute_chronic_load(
    daily_rows: list[dict[str, Any]],
    activities_rows: list[dict[str, Any]],
    target_day: str | None = None,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Calcola la serie storica e il valore al giorno `target_day` di:
    - Carico Acuto (ATL, media mobile esponenziale a 7 giorni: fatica)
    - Carico Cronico (CTL, media mobile esponenziale a 28 giorni: fitness)
    - Bilancio Fitness/Fatica (TSB = CTL - ATL: freschezza)
    - Rapporto Carico Acuto/Cronico (ACWR = ATL / CTL)

    Formula:
      alpha_A = 2 / (tau_A + 1),   alpha_C = 2 / (tau_C + 1)
      ATL_t = alpha_A * L_t + (1 - alpha_A) * ATL_{t-1}
      CTL_t = alpha_C * L_t + (1 - alpha_C) * CTL_{t-1}
      TSB_t = CTL_t - ATL_t
      ACWR_t = ATL_t / CTL_t

    Dati minimi e limiti:
      - Richiede almeno 14 giorni di calendario per stabilizzare l'ATL e 28 giorni per una stima piena del CTL.
      - Con meno di 14 giorni restituisce `calculable = False` ("non calcolabile").
    """
    hw = {
        "title": "Carico Acuto/Cronico (EWMA 7/28 giorni) e Modello Fitness/Fatica",
        "formula_latex": (
            r"\text{ATL}_t = \alpha_7 L_t + (1-\alpha_7)\text{ATL}_{t-1}, \quad "
            r"\text{CTL}_t = \alpha_{28} L_t + (1-\alpha_{28})\text{CTL}_{t-1}, \quad "
            r"\text{TSB}_t = \text{CTL}_t - \text{ATL}_t"
        ),
        "parameters": {
            "Costante Acuta (ATL)": f"{config.atl_tau_days} giorni",
            "Costante Cronica (CTL)": f"{config.ctl_tau_days} giorni",
        },
        "limits": "I primi 28 giorni risentono della condizione iniziale; ACWR è instabile se il carico cronico è prossimo a zero.",
    }

    if not daily_rows:
        return make_not_calculable(unit="TRIMP/g", reason="Nessun giorno presente in archivio.", how_it_works=hw)

    ordered_days = sorted(r["day"] for r in daily_rows if r.get("day"))
    if target_day is None:
        target_day = ordered_days[-1]

    days_up_to = [d for d in ordered_days if d <= target_day]
    if len(days_up_to) < 14:
        return make_not_calculable(
            unit="TRIMP/g",
            reason=f"Storico insufficiente ({len(days_up_to)} giorni < 14 giorni minimi per EWMA carico).",
            n_samples=len(days_up_to),
            how_it_works=hw,
        )

    rhr_by_day = {r["day"]: r.get("rhr_bpm") for r in daily_rows if r.get("day")}
    load_by_day: dict[str, float] = {d: 0.0 for d in days_up_to}

    for act in activities_rows:
        d = act.get("day")
        if d not in load_by_day:
            continue
        t_res = compute_trimp(act, hr_rest=rhr_by_day.get(d), config=config)
        if t_res["calculable"] and t_res["value"] is not None:
            load_by_day[d] += float(t_res["value"])
        elif act.get("training_load") is not None:
            load_by_day[d] += float(act["training_load"])

    alpha_a = 2.0 / (config.atl_tau_days + 1.0)
    alpha_c = 2.0 / (config.ctl_tau_days + 1.0)

    # Inizializza con la media dei primi 7 giorni per evitare bias da partenza a zero
    init_load = float(np.mean([load_by_day[d] for d in days_up_to[: min(7, len(days_up_to))]]))
    atl = init_load
    ctl = init_load

    series: list[dict[str, Any]] = []
    for d in days_up_to:
        l_t = load_by_day[d]
        atl = alpha_a * l_t + (1.0 - alpha_a) * atl
        ctl = alpha_c * l_t + (1.0 - alpha_c) * ctl
        tsb = ctl - atl
        acwr = round(atl / ctl, 2) if ctl >= 5.0 else None
        series.append(
            {
                "day": d,
                "daily_load": round(l_t, 1),
                "atl_7d": round(atl, 1),
                "ctl_28d": round(ctl, 1),
                "tsb_form": round(tsb, 1),
                "acwr": acwr,
            }
        )

    latest = series[-1]
    q_flag = "good" if len(days_up_to) >= config.ctl_tau_days else "partial"
    return make_result(
        value={
            "day": latest["day"],
            "daily_load": latest["daily_load"],
            "atl_7d": latest["atl_7d"],
            "ctl_28d": latest["ctl_28d"],
            "tsb_form": latest["tsb_form"],
            "acwr": latest["acwr"],
            "series": series,
        },
        unit="TRIMP/g",
        n_samples=len(days_up_to),
        quality_flag=q_flag,
        assumptions=[
            "I giorni senza attività registrate hanno carico strutturato L_t = 0.",
            f"Serie calcolata su {len(days_up_to)} giorni fino a {target_day}.",
        ],
        how_it_works=hw,
    )


def compute_cardiac_drift(
    details_payload: dict[str, Any] | None,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Calcola la deriva cardiaca (Cardiac Drift %) e il disaccoppiamento aerobico (Pa:Hr %)
    su sedute a passo/potenza costante di durata >= 30 minuti.

    Formula:
      Esclusi i primi `warmup_min` (10 min) e gli ultimi `cooldown_min` (5 min) di movimento attivo,
      si divide il tratto centrale costante in prima metà (1) e seconda metà (2):
        Drift_FC (%) = ((FC_2 / FC_1) - 1) * 100
        Decoupling (%) = (1 - (v_2 / FC_2) / (v_1 / FC_1)) * 100

    Parametri:
      - config.cardiac_drift_min_duration_min: durata minima totale (default 30 min)
      - config.cardiac_drift_max_speed_cv: massimo CV della velocità nel tratto centrale (default 0.12 = 12%)

    Dati minimi e limiti:
      - Se la durata attiva è < 30 min o il coefficiente di variazione della velocità supera il 12%
        (seduta di ripetute, fartlek o percorso molto collinare), restituisce `calculable = False`.
    """
    hw = {
        "title": "Deriva Cardiaca (Cardiac Drift) e Disaccoppiamento Aerobico",
        "formula_latex": (
            r"\text{Deriva FC (\%)} = \left(\frac{\overline{\text{FC}}_2}{\overline{\text{FC}}_1} - 1\right)\times 100, \quad "
            r"\text{Pa:Hr (\%)} = \left(1 - \frac{\bar{v}_2 / \overline{\text{FC}}_2}{\bar{v}_1 / \overline{\text{FC}}_1}\right)\times 100"
        ),
        "parameters": {
            "Durata minima seduta": f"{config.cardiac_drift_min_duration_min:.0f} min",
            "Finestra esclusa": f"Primi {config.cardiac_drift_warmup_min:.0f} min (riscaldamento), ultimi {config.cardiac_drift_cooldown_min:.0f} min",
            "Max CV velocità consentito": f"{config.cardiac_drift_max_speed_cv * 100:.0f}%",
        },
        "limits": "Valido solo su terreno pianeggiante e ad andatura costante; temperatura e disidratazione aumentano la deriva.",
    }

    samples = extract_activity_timeseries(details_payload)
    active = [s for s in samples if s["speed_mps"] > 0.5]
    if len(active) < 30:
        return make_not_calculable(
            unit="%",
            reason="Serie temporale con velocità e FC non disponibile o seduta statica.",
            n_samples=len(active),
            how_it_works=hw,
        )

    t_start = active[0]["elapsed_s"]
    t_end = active[-1]["elapsed_s"]
    active_dur_min = (t_end - t_start) / 60.0

    if active_dur_min < config.cardiac_drift_min_duration_min:
        return make_not_calculable(
            unit="%",
            reason=f"Durata attiva ({active_dur_min:.1f} min) inferiore ai {config.cardiac_drift_min_duration_min:.0f} min richiesti.",
            n_samples=len(active),
            how_it_works=hw,
        )

    w_start = t_start + config.cardiac_drift_warmup_min * 60.0
    w_end = t_end - config.cardiac_drift_cooldown_min * 60.0
    if w_end <= w_start + 600.0:
        w_start = t_start + 0.20 * (t_end - t_start)
        w_end = t_end

    steady = [s for s in active if w_start <= s["elapsed_s"] <= w_end]
    if len(steady) < 20:
        return make_not_calculable(
            unit="%",
            reason="Campioni insufficienti nella finestra centrale costante.",
            n_samples=len(steady),
            how_it_works=hw,
        )

    speeds = np.asarray([s["speed_mps"] for s in steady], dtype=float)
    mean_speed = float(np.mean(speeds))
    speed_cv = float(np.std(speeds, ddof=1) / mean_speed) if mean_speed > 0 else 1.0

    if speed_cv > config.cardiac_drift_max_speed_cv:
        return make_not_calculable(
            unit="%",
            reason=f"Andatura non costante (CV velocità = {speed_cv * 100:.1f}% > {config.cardiac_drift_max_speed_cv * 100:.0f}%, es. ripetute o variazioni).",
            n_samples=len(steady),
            how_it_works=hw,
        )

    mid_idx = len(steady) // 2
    half1 = steady[:mid_idx]
    half2 = steady[mid_idx:]

    hr1 = float(np.mean([s["hr"] for s in half1]))
    hr2 = float(np.mean([s["hr"] for s in half2]))
    v1 = float(np.mean([s["speed_mps"] for s in half1]))
    v2 = float(np.mean([s["speed_mps"] for s in half2]))

    drift_pct = round(((hr2 / hr1) - 1.0) * 100.0, 2)
    ef1 = (v1 * 60.0) / hr1
    ef2 = (v2 * 60.0) / hr2
    decoupling_pct = round((1.0 - (ef2 / ef1)) * 100.0, 2)

    return make_result(
        value={
            "hr_drift_pct": drift_pct,
            "aerobic_decoupling_pct": decoupling_pct,
            "hr_first_half_bpm": round(hr1, 1),
            "hr_second_half_bpm": round(hr2, 1),
            "speed_cv_pct": round(speed_cv * 100.0, 1),
        },
        unit="%",
        n_samples=len(steady),
        quality_flag="good",
        assumptions=[
            f"Tratto costante analizzato da {w_start / 60.0:.1f} a {w_end / 60.0:.1f} min (CV velocità {speed_cv * 100:.1f}%)."
        ],
        how_it_works=hw,
    )


def compute_speed_hr_efficiency(activity_row: dict[str, Any]) -> dict[str, Any]:
    """Calcola l'Efficienza Velocità/FC (Efficiency Factor) di un'attività aerobica con spostamento.

    Formula:
      EF = v_media [m/min] / FC_media [bpm] = (distanza_m / durata_s * 60) / FC_media   [m / battito]
      Rappresenta quanti metri vengono percorsi per singolo battito cardiaco.

    Dati minimi e limiti:
      - Richiede distanza > 500 m, durata > 10 min e FC media > 0.
      - Confrontabile solo tra attività dello stesso sport (es. corsa su strada con corsa su strada).
    """
    hw = {
        "title": "Efficienza Velocità / Frequenza Cardiaca (Metri per Battito)",
        "formula_latex": r"\text{EF} = \frac{\bar{v}\;[\text{m/min}]}{\overline{\text{FC}}\;[\text{bpm}]} = \frac{\text{Distanza}\;[\text{m}]}{\text{Durata}\;[\text{min}] \times \overline{\text{FC}}}\quad\left[\frac{\text{m}}{\text{battito}}\right]",
        "parameters": {"Sport applicabili": "Corsa, Ciclismo, Camminata"},
        "limits": "Influenzato da pendenza, vento, fondo stradale e temperatura esterna.",
    }

    dist_m = activity_row.get("distance_m")
    dur_s = activity_row.get("duration_s")
    hr_avg = activity_row.get("hr_avg")

    if dist_m is None or float(dist_m) < 500.0:
        return make_not_calculable(
            unit="m/battito",
            reason="Attività senza distanza significativa (< 500 m, es. forza o indoor).",
            how_it_works=hw,
        )
    if dur_s is None or float(dur_s) < 600.0 or hr_avg is None or float(hr_avg) <= 40.0:
        return make_not_calculable(
            unit="m/battito",
            reason="Durata (< 10 min) o frequenza cardiaca media assente.",
            how_it_works=hw,
        )

    speed_m_per_min = float(dist_m) / (float(dur_s) / 60.0)
    ef = round(speed_m_per_min / float(hr_avg), 3)
    return make_result(
        value=ef,
        unit="m/battito",
        n_samples=1,
        quality_flag="good",
        assumptions=[f"Velocità media {speed_m_per_min:.1f} m/min divisa per FC media {float(hr_avg):.0f} bpm."],
        how_it_works=hw,
        extra={"speed_m_per_min": round(speed_m_per_min, 1)},
    )
