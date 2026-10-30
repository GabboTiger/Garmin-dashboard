"""Analisi algoritmiche del Sonno (Fase 3c - Step 2):
- Architettura del sonno: efficienza, latenza, numero di risvegli (>= 2 min), % per fase,
  tempo dall'addormentamento al primo episodio di Profondo e di REM, cicli NREM->REM stimati, ipnogramma
- Regolarità degli orari: variabilità di addormentamento/risveglio, metà del sonno (mid-sleep),
  differenza feriali/weekend (social jetlag), Indice di Regolarità del Sonno (SRI, 0-100)
- Fisiologia notturna: FC minima notturna e suo orario, HRV e respirazione durante la notte,
  tempo trascorso sotto soglia SpO2 configurabile
- Debito di sonno cumulato (a 7 e 14 giorni) rispetto al fabbisogno personale configurabile

Ogni funzione è pura e riporta nella docstring: formula, parametri, dati minimi e limiti.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np

from app.analysis.common import make_not_calculable, make_result
from app.config import AnalysisConfig, DEFAULT_CONFIG

STAGE_MAP = {
    0: "deep",
    1: "light",
    2: "rem",
    3: "awake",
}


def _parse_iso_or_ms(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        ts_s = float(val) / 1000.0 if float(val) > 1e11 else float(val)
        return datetime.fromtimestamp(ts_s, tz=timezone.utc)
    s = str(val).strip()
    if not s:
        return None
    if s.endswith(".0"):
        s = s[:-2]
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def extract_hypnogram_intervals(sleep_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Estrae la sequenza temporale dell'ipnogramma (`sleepLevels`) da `get_sleep_data(day)`.

    Restituisce una lista ordinata di intervalli:
      `{start_iso, end_iso, start_offset_min, end_offset_min, duration_min, stage_code, stage_name}`
    dove `stage_code` in Garmin è: 0 = Deep, 1 = Light, 2 = REM, 3 = Awake.
    """
    if not isinstance(sleep_payload, dict):
        return []
    raw_levels = sleep_payload.get("sleepLevels")
    if not isinstance(raw_levels, list) or not raw_levels:
        return []

    parsed_items: list[tuple[datetime, datetime, int]] = []
    for item in raw_levels:
        if not isinstance(item, dict):
            continue
        st = _parse_iso_or_ms(item.get("startGMT"))
        en = _parse_iso_or_ms(item.get("endGMT"))
        lvl = item.get("activityLevel")
        if st is None or en is None or lvl is None or en <= st:
            continue
        code = int(round(float(lvl)))
        parsed_items.append((st, en, code))

    if not parsed_items:
        return []

    parsed_items.sort(key=lambda x: x[0])
    t0 = parsed_items[0][0]
    intervals: list[dict[str, Any]] = []
    for st, en, code in parsed_items:
        dur_m = (en - st).total_seconds() / 60.0
        st_off = (st - t0).total_seconds() / 60.0
        en_off = (en - t0).total_seconds() / 60.0
        intervals.append(
            {
                "start_iso": st.strftime("%H:%M"),
                "end_iso": en.strftime("%H:%M"),
                "start_offset_min": round(st_off, 1),
                "end_offset_min": round(en_off, 1),
                "duration_min": round(dur_m, 1),
                "stage_code": code,
                "stage_name": STAGE_MAP.get(code, "light"),
            }
        )
    return intervals


def compute_sleep_architecture(
    daily_row: dict[str, Any] | None,
    sleep_payload: dict[str, Any] | None = None,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Calcola le metriche di architettura del sonno per una notte:
    - Efficienza del sonno (%) = Sonno_Totale / (Sonno_Totale + Veglia_Intra_Sonno) * 100
    - Percentuali per fase (% Profondo, % Leggero, % REM rispetto al sonno totale)
    - Numero di risvegli intra-sonno di durata >= `config.sleep_awakening_min_duration` (default 2 min)
    - Tempo (latenza) dall'inizio del sonno al primo episodio di Sonno Profondo e di Sonno REM
    - Numero di cicli NREM -> REM stimati
    - Ipnogramma strutturato per il grafico a gradini

    Dati minimi e limiti:
      - Richiede `sleep_total_min > 0`. Se manca `sleepLevels` nel payload grezzo, calcola efficienza
        e percentuali dalle durate aggregate in `daily_row` e imposta `quality_flag = "partial"`.
    """
    hw = {
        "title": "Architettura del Sonno, Efficienza, Latenze e Cicli",
        "formula_latex": (
            r"\text{Efficienza (\%)} = \frac{T_{\text{sonno}}}{T_{\text{sonno}} + T_{\text{veglia}}} \times 100, \quad "
            r"p_{\text{fase}} = \frac{T_{\text{fase}}}{T_{\text{sonno}}} \times 100"
        ),
        "parameters": {
            "Soglia durata risveglio": f"≥ {config.sleep_awakening_min_duration:.0f} min",
            "Definizione ciclo": "Episodio REM ≥ 8 min preceduto da sonno NREM (separazione ≥ 40 min tra cicli)",
        },
        "limits": "Stima basata su accelerometria e variabilità cardiaca da polso; non sostituisce l'EEG.",
    }

    if not daily_row or daily_row.get("sleep_total_min") is None or float(daily_row["sleep_total_min"]) <= 0:
        return make_not_calculable(
            unit="%",
            reason="Dati del sonno assenti per la notte selezionata.",
            how_it_works=hw,
        )

    tot_min = float(daily_row["sleep_total_min"])
    deep_min = float(daily_row.get("sleep_deep_min") or 0.0)
    light_min = float(daily_row.get("sleep_light_min") or 0.0)
    rem_min = float(daily_row.get("sleep_rem_min") or 0.0)
    awake_min = float(daily_row.get("sleep_awake_min") or 0.0)

    time_in_bed_min = tot_min + awake_min
    efficiency_pct = round((tot_min / time_in_bed_min) * 100.0, 1) if time_in_bed_min > 0 else None

    deep_pct = round((deep_min / tot_min) * 100.0, 1)
    light_pct = round((light_min / tot_min) * 100.0, 1)
    rem_pct = round((rem_min / tot_min) * 100.0, 1)

    intervals = extract_hypnogram_intervals(sleep_payload)
    if not intervals:
        return make_result(
            value={
                "efficiency_pct": efficiency_pct,
                "deep_pct": deep_pct,
                "light_pct": light_pct,
                "rem_pct": rem_pct,
                "awakenings_count": None,
                "latency_to_deep_min": None,
                "latency_to_rem_min": None,
                "estimated_cycles": None,
                "hypnogram": [],
            },
            unit="%",
            n_samples=1,
            quality_flag="partial",
            assumptions=["Ipnogramma ad alta risoluzione non disponibile; calcolate solo percentuali ed efficienza aggregate."],
            how_it_works=hw,
        )

    # Individua il primo intervallo di sonno effettivo (stage != awake) per misurare le latenze
    sleep_onset_min = next((iv["start_offset_min"] for iv in intervals if iv["stage_name"] != "awake"), 0.0)

    first_deep = next(
        (iv["start_offset_min"] - sleep_onset_min for iv in intervals if iv["stage_name"] == "deep" and iv["duration_min"] >= 3.0),
        None,
    )
    first_rem = next(
        (iv["start_offset_min"] - sleep_onset_min for iv in intervals if iv["stage_name"] == "rem" and iv["duration_min"] >= 3.0),
        None,
    )

    # Conta i risvegli intra-sonno (escludendo eventuale veglia iniziale prima dell'addormentamento)
    awakenings = sum(
        1
        for iv in intervals
        if iv["stage_name"] == "awake"
        and iv["start_offset_min"] > sleep_onset_min
        and iv["duration_min"] >= config.sleep_awakening_min_duration
    )

    # Stima il numero di cicli NREM -> REM (episodi REM separati da almeno 40 min di NREM/veglia)
    cycles = 0
    last_rem_end: float | None = None
    for iv in intervals:
        if iv["stage_name"] == "rem" and iv["duration_min"] >= 5.0:
            if last_rem_end is None or (iv["start_offset_min"] - last_rem_end) >= 30.0:
                cycles += 1
            last_rem_end = iv["end_offset_min"]

    return make_result(
        value={
            "efficiency_pct": efficiency_pct,
            "deep_pct": deep_pct,
            "light_pct": light_pct,
            "rem_pct": rem_pct,
            "sleep_onset_latency_min": round(sleep_onset_min, 1),
            "awakenings_count": int(awakenings),
            "latency_to_deep_min": round(first_deep, 1) if first_deep is not None else None,
            "latency_to_rem_min": round(first_rem, 1) if first_rem is not None else None,
            "estimated_cycles": int(cycles),
            "hypnogram": intervals,
        },
        unit="%",
        n_samples=len(intervals),
        quality_flag="good",
        assumptions=[f"Ipnogramma analizzato su {len(intervals)} intervalli di stato."],
        how_it_works=hw,
    )


def _hhmm_to_rel_minutes(hhmm: str | None) -> float | None:
    """Converte 'HH:MM' in minuti relativi alla mezzanotte (es. 23:00 -> -60, 01:00 -> +60)."""
    if not hhmm or ":" not in str(hhmm):
        return None
    try:
        h, m = (int(x) for x in str(hhmm).strip().split(":")[:2])
    except ValueError:
        return None
    val = h * 60 + m
    if val >= 16 * 60:
        val -= 24 * 60
    return float(val)


def _rel_minutes_to_hhmm(rel_min: float) -> str:
    tot = int(round(rel_min)) % (24 * 60)
    return f"{tot // 60:02d}:{tot % 60:02d}"


def compute_sleep_regularity(
    daily_rows: list[dict[str, Any]],
    target_day: str,
    window_days: int = 30,
) -> dict[str, Any]:
    """Calcola la regolarità degli orari di sonno sugli ultimi `window_days` giorni fino a `target_day`:
    - Deviazione standard dell'orario di addormentamento e di risveglio (in minuti)
    - Orario medio della metà del sonno (Mid-sleep point)
    - Social Jetlag (differenza in minuti tra metà del sonno nei weekend e nei giorni feriali)
    - Indice di Regolarità del Sonno (SRI, 0-100): percentuale di sovrapposizione media delle finestre
      di sonno tra giorni consecutivi (t e t-1).

    Formula SRI su finestre giornaliere:
      Per due notti consecutive (t-1, t) con intervalli di sonno I_{t-1} e I_t su un ciclo di 24h (1440 min):
      concordance_t = (1440 - 2 * |I_t Δ I_{t-1}|) / 1440 * 100
      (dove |I_t Δ I_{t-1}| è la differenza simmetrica in minuti tra i due intervalli di sonno).

    Dati minimi e limiti:
      - Richiede almeno 7 notti con orari di inizio e fine validi nella finestra.
    """
    hw = {
        "title": "Regolarità del Sonno, Mid-Sleep, Social Jetlag e SRI",
        "formula_latex": (
            r"t_{\text{mid}} = t_{\text{inizio}} + \frac{1}{2}(t_{\text{fine}} - t_{\text{inizio}}), \quad "
            r"\text{Social Jetlag} = |\overline{t_{\text{mid, WE}}} - \overline{t_{\text{mid, feriali}}}|"
        ),
        "parameters": {"Finestra di analisi": f"{window_days} giorni fino a {target_day}"},
        "limits": "Non tiene conto di eventuali pisolini diurni brevi.",
    }

    target_dt = date.fromisoformat(target_day)
    start_str = (target_dt - timedelta(days=max(7, window_days) - 1)).isoformat()

    valid_nights: list[dict[str, Any]] = []
    for r in sorted(daily_rows, key=lambda x: x.get("day", "")):
        d_str = r.get("day")
        if not d_str or not (start_str <= d_str <= target_day):
            continue
        bed_m = _hhmm_to_rel_minutes(r.get("sleep_start_local"))
        tot_m = r.get("sleep_total_min")
        awake_m = r.get("sleep_awake_min") or 0.0
        if bed_m is None or tot_m is None or float(tot_m) <= 0:
            continue
        span_m = float(tot_m) + float(awake_m)
        wake_m = bed_m + span_m
        mid_m = bed_m + 0.5 * span_m
        d_obj = date.fromisoformat(d_str)
        valid_nights.append(
            {
                "day": d_str,
                "date_obj": d_obj,
                "is_weekend": d_obj.weekday() >= 5,
                "bed_m": bed_m,
                "wake_m": wake_m,
                "mid_m": mid_m,
            }
        )

    if len(valid_nights) < 7:
        return make_not_calculable(
            unit="min",
            reason=f"Notti con orari registrati insufficienti ({len(valid_nights)} < 7 notti minime).",
            n_samples=len(valid_nights),
            how_it_works=hw,
        )

    bed_arr = np.asarray([n["bed_m"] for n in valid_nights], dtype=float)
    wake_arr = np.asarray([n["wake_m"] for n in valid_nights], dtype=float)
    mid_arr = np.asarray([n["mid_m"] for n in valid_nights], dtype=float)

    bed_sd = round(float(np.std(bed_arr, ddof=1)), 1)
    wake_sd = round(float(np.std(wake_arr, ddof=1)), 1)
    mid_sd = round(float(np.std(mid_arr, ddof=1)), 1)
    mean_mid_hhmm = _rel_minutes_to_hhmm(float(np.mean(mid_arr)))

    we_mids = [n["mid_m"] for n in valid_nights if n["is_weekend"]]
    wd_mids = [n["mid_m"] for n in valid_nights if not n["is_weekend"]]
    social_jetlag_min = (
        round(abs(float(np.mean(we_mids)) - float(np.mean(wd_mids))), 1)
        if (len(we_mids) >= 2 and len(wd_mids) >= 3)
        else None
    )

    # Calcolo dell'Indice di Regolarità del Sonno (SRI, 0-100) su coppie di giorni consecutivi
    sri_scores: list[float] = []
    for i in range(1, len(valid_nights)):
        prev_n = valid_nights[i - 1]
        cur_n = valid_nights[i]
        if (cur_n["date_obj"] - prev_n["date_obj"]).days != 1:
            continue
        overlap = max(0.0, min(prev_n["wake_m"], cur_n["wake_m"]) - max(prev_n["bed_m"], cur_n["bed_m"]))
        len_prev = prev_n["wake_m"] - prev_n["bed_m"]
        len_cur = cur_n["wake_m"] - cur_n["bed_m"]
        sym_diff = (len_prev - overlap) + (len_cur - overlap)
        # Frazione delle 24h (1440 min) in cui lo stato sonno/veglia coincide tra t-1 e t
        match_frac = max(0.0, (1440.0 - sym_diff) / 1440.0)
        sri_scores.append(match_frac * 100.0)

    sri_val = round(float(np.mean(sri_scores)), 1) if sri_scores else None

    return make_result(
        value={
            "bedtime_sd_min": bed_sd,
            "waketime_sd_min": wake_sd,
            "midsleep_sd_min": mid_sd,
            "midsleep_mean_hhmm": mean_mid_hhmm,
            "social_jetlag_min": social_jetlag_min,
            "sri_score": sri_val,
        },
        unit="min",
        n_samples=len(valid_nights),
        quality_flag="good",
        assumptions=[f"Calcolato su {len(valid_nights)} notti valide negli ultimi {window_days} giorni."],
        how_it_works=hw,
    )


def compute_nocturnal_physiology_and_debt(
    daily_rows: list[dict[str, Any]],
    target_day: str,
    sleep_payload: dict[str, Any] | None = None,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Calcola la fisiologia notturna e il debito di sonno per `target_day`:
    - FC minima notturna (bpm) e orario in cui viene raggiunta (nadir cardiaco)
    - HRV notturna (media e SD intra-notte) e frequenza respiratoria media notturna
    - Minuti trascorsi sotto la soglia SpO2 configurabile (`config.spo2_threshold_pct`, default 92%)
    - Debito di sonno cumulato negli ultimi 7 e 14 giorni rispetto a `config.sleep_need_min` (default 480 min)

    Formula Debito di Sonno (su finestra di N giorni):
      Debito_N = max(0, sum_{i=1..N} (Fabbisogno - Sonno_i))   [minuti o ore]
    """
    hw = {
        "title": "Fisiologia Notturna (FC Minima, HRV, Respirazione, SpO2) e Debito di Sonno",
        "formula_latex": (
            r"\text{Debito}_{7\text{g}} = \max\!\left(0, \sum_{t-6}^{t} (T_{\text{fabbisogno}} - T_{\text{sonno},i})\right), \quad "
            r"T_{\text{SpO2} < \theta} = \sum_k \Delta t_k \cdot \mathbb{I}(\text{SpO2}_k < \theta)"
        ),
        "parameters": {
            "Fabbisogno giornaliero": f"{config.sleep_need_min:.0f} min ({config.sleep_need_min / 60.0:.1f} h)",
            "Soglia SpO2": f"{config.spo2_threshold_pct:.1f}%",
        },
        "limits": "Il pulsossimetro da polso è sensibile alla postura del braccio e alla perfusione periferica.",
    }

    row = next((r for r in daily_rows if r.get("day") == target_day), None)
    if row is None:
        return make_not_calculable(unit="min", reason="Giorno selezionato non presente in archivio.", how_it_works=hw)

    # 1. FC minima notturna e suo orario da `sleepHeartRate` (se presente nel payload grezzo)
    min_hr_bpm: float | None = None
    min_hr_time_hhmm: str | None = None
    if isinstance(sleep_payload, dict) and isinstance(sleep_payload.get("sleepHeartRate"), list):
        hr_pts = [
            pt
            for pt in sleep_payload["sleepHeartRate"]
            if isinstance(pt, dict) and pt.get("value") is not None and float(pt["value"]) > 30
        ]
        if hr_pts:
            best_pt = min(hr_pts, key=lambda p: float(p["value"]))
            min_hr_bpm = round(float(best_pt["value"]), 1)
            dt_min = _parse_iso_or_ms(best_pt.get("startGMT"))
            if dt_min is not None:
                min_hr_time_hhmm = dt_min.strftime("%H:%M")

    # 2. Tempo sotto soglia SpO2 da `wellnessEpochSPO2DataDTOList`
    spo2_below_min: float | None = None
    if isinstance(sleep_payload, dict) and isinstance(sleep_payload.get("wellnessEpochSPO2DataDTOList"), list):
        epochs = [
            ep
            for ep in sleep_payload["wellnessEpochSPO2DataDTOList"]
            if isinstance(ep, dict) and ep.get("spo2Reading") is not None
        ]
        if epochs:
            # Risoluzione tipica epoca SpO2 Garmin nel sonno: 1 o 5 minuti (stimiamo dal passo mediano)
            ts_list = [float(ep["epochTimestamp"]) for ep in epochs if ep.get("epochTimestamp") is not None]
            step_min = 5.0
            if len(ts_list) >= 2:
                diffs = [
                    (ts_list[i + 1] - ts_list[i]) / 60000.0
                    for i in range(len(ts_list) - 1)
                    if ts_list[i + 1] > ts_list[i]
                ]
                if diffs:
                    step_min = float(np.median(diffs))
            below_count = sum(1 for ep in epochs if float(ep["spo2Reading"]) < config.spo2_threshold_pct)
            spo2_below_min = round(below_count * step_min, 1)

    # 3. Debito di sonno a 7 e 14 giorni
    target_dt = date.fromisoformat(target_day)
    start_7 = (target_dt - timedelta(days=6)).isoformat()
    start_14 = (target_dt - timedelta(days=13)).isoformat()

    rows_7 = [r for r in daily_rows if start_7 <= str(r.get("day") or "") <= target_day and r.get("sleep_total_min") is not None]
    rows_14 = [r for r in daily_rows if start_14 <= str(r.get("day") or "") <= target_day and r.get("sleep_total_min") is not None]

    debt_7_min = (
        round(max(0.0, sum(config.sleep_need_min - float(r["sleep_total_min"]) for r in rows_7)), 1)
        if len(rows_7) >= 4
        else None
    )
    debt_14_min = (
        round(max(0.0, sum(config.sleep_need_min - float(r["sleep_total_min"]) for r in rows_14)), 1)
        if len(rows_14) >= 7
        else None
    )

    if min_hr_bpm is None and debt_7_min is None and row.get("hrv_night_ms") is None:
        return make_not_calculable(
            unit="min",
            reason="Fisiologia notturna e storico del sonno insufficienti per il giorno selezionato.",
            how_it_works=hw,
        )

    return make_result(
        value={
            "min_hr_bpm": min_hr_bpm,
            "min_hr_time_hhmm": min_hr_time_hhmm,
            "hrv_night_ms": row.get("hrv_night_ms"),
            "hrv_night_sd_ms": row.get("hrv_night_sd_ms"),
            "respiration_sleep_brpm": row.get("respiration_sleep_brpm"),
            "spo2_sleep_avg_pct": row.get("spo2_sleep_avg_pct"),
            "spo2_sleep_min_pct": row.get("spo2_sleep_min_pct"),
            "spo2_below_threshold_min": spo2_below_min,
            "spo2_threshold_pct": config.spo2_threshold_pct,
            "sleep_debt_7d_min": debt_7_min,
            "sleep_debt_7d_hours": round(debt_7_min / 60.0, 2) if debt_7_min is not None else None,
            "sleep_debt_14d_min": debt_14_min,
            "sleep_debt_14d_hours": round(debt_14_min / 60.0, 2) if debt_14_min is not None else None,
        },
        unit="min",
        n_samples=len(rows_14),
        quality_flag="good" if min_hr_bpm is not None else "partial",
        assumptions=[
            f"Debito calcolato su {len(rows_7)}/7 notti e {len(rows_14)}/14 notti rispetto a {config.sleep_need_min / 60.0:.1f} h/notte."
        ],
        how_it_works=hw,
    )
