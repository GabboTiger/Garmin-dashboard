"""Parser da payload grezzi JSON Garmin a righe normalizzate (`daily`, `activities`)
e ispezione di struttura e risoluzione temporale delle serie grezze.

Regola fondamentale (AGENTS.md regola 4):
- Nessun valore mancante viene convertito in 0: se un campo è assente o nullo, resta `None` (`NULL` in SQLite).
- I parser leggono i payload salvati in `raw_garmin` così è sempre possibile rifare il parsing (`reparse`)
  senza contattare nuovamente Garmin.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np


def _to_float(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    return num


def _to_int(val: Any) -> int | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        return int(round(float(val)))
    except (TypeError, ValueError):
        return None


def _seconds_to_min(val: Any) -> float | None:
    sec = _to_float(val)
    if sec is None:
        return None
    return round(sec / 60.0, 1)


def _ms_local_to_hhmm(ms_val: Any) -> str | None:
    """Converte un timestamp Garmin in millisecondi (già in ora locale) in stringa 'HH:MM'."""
    ms = _to_int(ms_val)
    if ms is None or ms <= 0:
        return None
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%H:%M")


def parse_summary_payload(day: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Estrae metriche giornaliere dal payload di `get_user_summary(day)`."""
    if not isinstance(payload, dict):
        return {"day": day}

    stress_raw = _to_float(payload.get("averageStressLevel"))
    # In Garmin Connect averageStressLevel = -1 o < 0 indica che lo stress non è stato misurato
    stress_avg = stress_raw if (stress_raw is not None and stress_raw >= 0) else None

    mod_min = _to_float(payload.get("moderateIntensityMinutes"))
    vig_min = _to_float(payload.get("vigorousIntensityMinutes"))
    if mod_min is not None or vig_min is not None:
        intensity_min: float | None = (mod_min or 0.0) + 2.0 * (vig_min or 0.0)
    else:
        intensity_min = None

    return {
        "day": day,
        "rhr_bpm": _to_float(payload.get("restingHeartRate")),
        "stress_avg": stress_avg,
        "bb_max": _to_float(payload.get("bodyBatteryHighestValue")),
        "bb_min": _to_float(payload.get("bodyBatteryLowestValue")),
        "bb_charged": _to_float(payload.get("bodyBatteryChargedValue")),
        "bb_drained": _to_float(payload.get("bodyBatteryDrainedValue")),
        "steps": _to_int(payload.get("totalSteps")),
        "calories_active": _to_float(payload.get("activeKilocalories")),
        "intensity_min": intensity_min,
    }


def parse_sleep_payload(day: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Estrae durata, fasi, punteggio, orari del sonno ed eventuali medie notturne da `get_sleep_data(day)`."""
    if not isinstance(payload, dict):
        return {"day": day}

    dto = payload.get("dailySleepDTO")
    if not isinstance(dto, dict):
        dto = payload

    sleep_total_min = _seconds_to_min(dto.get("sleepTimeSeconds"))
    # Se sleepTimeSeconds è None o <= 0, il sonno non è stato registrato per quella notte
    if sleep_total_min is not None and sleep_total_min <= 0:
        sleep_total_min = None

    # Punteggio del sonno: in Garmin è tipicamente in dailySleepDTO.sleepScores.overall.value
    sleep_score: float | None = None
    scores_obj = dto.get("sleepScores")
    if isinstance(scores_obj, dict):
        overall = scores_obj.get("overall")
        if isinstance(overall, dict):
            sleep_score = _to_float(overall.get("value"))
        elif overall is not None:
            sleep_score = _to_float(overall)

    result: dict[str, Any] = {
        "day": day,
        "sleep_total_min": sleep_total_min,
        "sleep_deep_min": _seconds_to_min(dto.get("deepSleepSeconds")) if sleep_total_min is not None else None,
        "sleep_light_min": _seconds_to_min(dto.get("lightSleepSeconds")) if sleep_total_min is not None else None,
        "sleep_rem_min": _seconds_to_min(dto.get("remSleepSeconds")) if sleep_total_min is not None else None,
        "sleep_awake_min": _seconds_to_min(dto.get("awakeSleepSeconds")) if sleep_total_min is not None else None,
        "sleep_score": sleep_score,
        "sleep_start_local": _ms_local_to_hhmm(dto.get("sleepStartTimestampLocal")),
        "sleep_end_local": _ms_local_to_hhmm(dto.get("sleepEndTimestampLocal")),
    }

    # Alcuni dispositivi riportano anche `avgOvernightHrv`, `averageRespirationValue`, `averageSpO2Value`
    overnight_hrv = _to_float(payload.get("avgOvernightHrv") or dto.get("avgOvernightHrv"))
    if overnight_hrv is not None and overnight_hrv > 0:
        result["hrv_night_ms"] = overnight_hrv

    resp_sleep = _to_float(dto.get("averageRespirationValue") or payload.get("avgSleepRespirationValue"))
    if resp_sleep is not None and resp_sleep > 0:
        result["respiration_sleep_brpm"] = resp_sleep

    spo2_sleep = _to_float(dto.get("averageSpO2Value"))
    if spo2_sleep is not None and spo2_sleep > 0:
        result["spo2_sleep_avg_pct"] = spo2_sleep

    spo2_min = _to_float(dto.get("lowestSpO2Value"))
    if spo2_min is not None and spo2_min > 0:
        result["spo2_sleep_min_pct"] = spo2_min

    return result


def parse_hrv_payload(day: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Estrae l'HRV notturna media (in ms) e la deviazione standard dei campioni notturni da `get_hrv_data(day)`."""
    if not isinstance(payload, dict):
        return {"day": day}

    summary = payload.get("hrvSummary")
    if not isinstance(summary, dict):
        summary = payload

    hrv_val = _to_float(summary.get("lastNightAvg"))
    if hrv_val is not None and hrv_val <= 0:
        hrv_val = None

    # Calcola la SD intra-notte dai campioni `hrvReadings` se disponibili
    readings = payload.get("hrvReadings")
    hrv_sd: float | None = None
    if isinstance(readings, list) and len(readings) >= 3:
        vals = [
            _to_float(r.get("hrvValue"))
            for r in readings
            if isinstance(r, dict) and _to_float(r.get("hrvValue")) is not None and _to_float(r.get("hrvValue")) > 0
        ]
        if len(vals) >= 3:
            hrv_sd = round(float(np.std(vals, ddof=1)), 2)

    return {
        "day": day,
        "hrv_night_ms": hrv_val,
        "hrv_night_sd_ms": hrv_sd,
    }


def parse_respiration_payload(day: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Estrae la frequenza respiratoria media notturna e diurna (atti/min) da `get_respiration_data(day)`."""
    if not isinstance(payload, dict):
        return {"day": day}

    sleep_resp = _to_float(payload.get("avgSleepRespirationValue"))
    if sleep_resp is not None and sleep_resp <= 0:
        sleep_resp = None

    waking_resp = _to_float(payload.get("avgWakingRespirationValue"))
    if waking_resp is not None and waking_resp <= 0:
        waking_resp = None

    return {
        "day": day,
        "respiration_sleep_brpm": sleep_resp,
        "respiration_day_brpm": waking_resp,
    }


def parse_spo2_payload(day: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Estrae SpO2 media e minima notturna (%) da `get_spo2_data(day)`."""
    if not isinstance(payload, dict):
        return {"day": day}

    avg_spo2 = _to_float(payload.get("averageSpO2") or payload.get("sleepSpO2Avg"))
    if avg_spo2 is not None and avg_spo2 <= 0:
        avg_spo2 = None

    min_spo2 = _to_float(payload.get("lowestSpO2"))
    if min_spo2 is not None and min_spo2 <= 0:
        min_spo2 = None

    return {
        "day": day,
        "spo2_sleep_avg_pct": avg_spo2,
        "spo2_sleep_min_pct": min_spo2,
    }


def parse_body_battery_payload(day: str, payload: Any) -> dict[str, Any]:
    """Estrae charged/drained e min/max da `get_body_battery(day, day)` (lista di riepiloghi giornalieri)."""
    item: dict[str, Any] | None = None
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        item = payload[0]
    elif isinstance(payload, dict):
        item = payload

    if not item:
        return {"day": day}

    charged = _to_float(item.get("charged"))
    drained = _to_float(item.get("drained"))

    # Se è presente `bodyBatteryValuesArray` (coppie [timestamp_ms, level]), ricava min e max
    bb_min: float | None = None
    bb_max: float | None = None
    arr = item.get("bodyBatteryValuesArray")
    if isinstance(arr, list):
        levels = [
            _to_float(pt[1])
            for pt in arr
            if isinstance(pt, (list, tuple)) and len(pt) >= 2 and _to_float(pt[1]) is not None and _to_float(pt[1]) >= 0
        ]
        if levels:
            bb_min = float(min(levels))
            bb_max = float(max(levels))

    return {
        "day": day,
        "bb_charged": charged,
        "bb_drained": drained,
        "bb_min": bb_min,
        "bb_max": bb_max,
    }


def parse_training_readiness_payload(day: str, payload: Any) -> dict[str, Any]:
    """Estrae il punteggio di Training Readiness da `get_training_readiness(day)`."""
    item: dict[str, Any] | None = None
    if isinstance(payload, list):
        # Garmin può restituire più letture nella giornata; cerchiamo quella mattutina o la prima valida
        for entry in payload:
            if isinstance(entry, dict) and entry.get("score") is not None:
                item = entry
                if entry.get("inputContext") == "AFTER_WAKEUP_RESET":
                    break
    elif isinstance(payload, dict):
        item = payload

    score = _to_float(item.get("score")) if item else None
    if score is not None and score < 0:
        score = None

    return {
        "day": day,
        "training_readiness_score": score,
    }


def merge_daily_fragments(day: str, *fragments: dict[str, Any]) -> dict[str, Any]:
    """Unisce più dizionari parziali dello stesso giorno: i valori non-None sovrascrivono i None."""
    merged: dict[str, Any] = {"day": day}
    for frag in fragments:
        if not isinstance(frag, dict):
            continue
        for k, v in frag.items():
            if k == "day":
                continue
            if v is not None or k not in merged:
                merged[k] = v
    return merged


def parse_activity_payload(payload: dict[str, Any], hr_zones_payload: Any | None = None) -> dict[str, Any] | None:
    """Converte un elemento di `get_activities_by_date` (ed eventuali zone FC da `activity_hr_zones:<id>`
    o `_hr_zones`) nella riga normalizzata per la tabella `activities`.
    """
    if not isinstance(payload, dict):
        return None

    act_id = _to_int(payload.get("activityId"))
    if act_id is None:
        return None

    start_local = payload.get("startTimeLocal")
    start_str = str(start_local) if start_local else None
    day_str = start_str[:10] if (start_str and len(start_str) >= 10) else payload.get("day")
    if not day_str:
        return None

    act_type_obj = payload.get("activityType")
    if isinstance(act_type_obj, dict):
        act_type = str(act_type_obj.get("typeKey") or "other")
    else:
        act_type = str(act_type_obj or "other")

    # Zone di frequenza cardiaca (da hr_zones_payload, `_hr_zones` o campi diretti `hrTimeInZone_1..5`)
    z_map: dict[int, float | None] = {1: None, 2: None, 3: None, 4: None, 5: None}
    zones_list = hr_zones_payload if hr_zones_payload is not None else payload.get("_hr_zones")
    if isinstance(zones_list, list):
        for z_item in zones_list:
            if isinstance(z_item, dict):
                z_num = _to_int(z_item.get("zoneNumber"))
                z_secs = _to_float(z_item.get("secsInZone"))
                if z_num in z_map and z_secs is not None:
                    z_map[z_num] = z_secs
    else:
        for z_num in range(1, 6):
            direct = _to_float(payload.get(f"hrTimeInZone_{z_num}"))
            if direct is not None:
                z_map[z_num] = direct

    return {
        "activity_id": act_id,
        "day": str(day_str),
        "start_local": start_str,
        "type": act_type,
        "name": payload.get("activityName"),
        "duration_s": _to_float(payload.get("duration")),
        "distance_m": _to_float(payload.get("distance")),
        "hr_avg": _to_float(payload.get("averageHR")),
        "hr_max": _to_float(payload.get("maxHR")),
        "training_load": _to_float(payload.get("activityTrainingLoad")),
        "z1_s": z_map[1],
        "z2_s": z_map[2],
        "z3_s": z_map[3],
        "z4_s": z_map[4],
        "z5_s": z_map[5],
    }


def summarize_payload_structure(payload: Any, max_depth: int = 2, _depth: int = 0) -> Any:
    """Restituisce la sola struttura (chiavi e tipi di dato, SENZA valori personali)
    di una risposta JSON Garmin per ispezionare i campi veri in sicurezza.
    """
    if payload is None:
        return "None"
    if isinstance(payload, bool):
        return "bool"
    if isinstance(payload, int):
        return "int"
    if isinstance(payload, float):
        return "float"
    if isinstance(payload, str):
        return "str"
    if isinstance(payload, list):
        if not payload:
            return "list[empty]"
        if _depth >= max_depth:
            return f"list[{len(payload)} items]"
        return [summarize_payload_structure(payload[0], max_depth=max_depth, _depth=_depth + 1)]
    if isinstance(payload, dict):
        if _depth >= max_depth:
            return f"dict[{len(payload)} keys]"
        return {
            str(k): summarize_payload_structure(v, max_depth=max_depth, _depth=_depth + 1)
            for k, v in sorted(payload.items(), key=lambda kv: str(kv[0]))
        }
    return type(payload).__name__


def _compute_gaps_stats(timestamps_ms: list[float]) -> dict[str, Any]:
    """Calcola numero di campioni e passo temporale (mediano, min, max in secondi) da una lista di timestamp in ms."""
    if len(timestamps_ms) < 2:
        return {"n_samples": len(timestamps_ms), "median_step_s": None, "min_step_s": None, "max_step_s": None}
    ordered = sorted(timestamps_ms)
    diffs_s = [(ordered[i + 1] - ordered[i]) / 1000.0 for i in range(len(ordered) - 1) if ordered[i + 1] > ordered[i]]
    if not diffs_s:
        return {"n_samples": len(timestamps_ms), "median_step_s": 0.0, "min_step_s": 0.0, "max_step_s": 0.0}
    return {
        "n_samples": len(timestamps_ms),
        "median_step_s": round(float(np.median(diffs_s)), 2),
        "min_step_s": round(float(np.min(diffs_s)), 2),
        "max_step_s": round(float(np.max(diffs_s)), 2),
    }


def inspect_timeseries_resolution(endpoint: str, payload: Any) -> dict[str, Any]:
    """Analizza un payload grezzo Garmin e restituisce disponibilità, campi serie temporali e
    risoluzione temporale (passo mediano in secondi), senza esporre dati personali.
    """
    if payload is None:
        return {"available": False, "reason": "payload None"}
    if isinstance(payload, (dict, list)) and len(payload) == 0:
        return {"available": False, "reason": "payload vuoto"}

    series_info: dict[str, Any] = {}

    if endpoint == "heart_rates" and isinstance(payload, dict):
        hr_arr = payload.get("heartRateValues")
        if isinstance(hr_arr, list):
            ts = [float(x[0]) for x in hr_arr if isinstance(x, (list, tuple)) and len(x) >= 2 and x[0] is not None]
            series_info["heartRateValues"] = _compute_gaps_stats(ts)

    elif endpoint == "sleep" and isinstance(payload, dict):
        for key in ("sleepLevels", "sleepMovement"):
            arr = payload.get(key)
            if isinstance(arr, list) and arr:
                series_info[key] = {"n_intervals": len(arr), "sample_keys": sorted(arr[0].keys()) if isinstance(arr[0], dict) else []}
        for ts_key in ("wellnessEpochSPO2DataDTOList", "wellnessEpochRespirationDataDTOList", "sleepHeartRate", "sleepBodyBattery"):
            arr = payload.get(ts_key)
            if isinstance(arr, list) and arr:
                series_info[ts_key] = {"n_samples": len(arr)}

    elif endpoint == "hrv" and isinstance(payload, dict):
        readings = payload.get("hrvReadings")
        if isinstance(readings, list):
            series_info["hrvReadings"] = {"n_samples": len(readings)}

    elif endpoint == "respiration" and isinstance(payload, dict):
        arr = payload.get("respirationValuesArray")
        if isinstance(arr, list):
            ts = [float(x[0]) for x in arr if isinstance(x, (list, tuple)) and len(x) >= 2 and x[0] is not None]
            series_info["respirationValuesArray"] = _compute_gaps_stats(ts)

    elif endpoint == "spo2" and isinstance(payload, dict):
        for key in ("spO2SingleValues", "spO2HourlyAverages"):
            arr = payload.get(key)
            if isinstance(arr, list):
                ts = [float(x[0]) for x in arr if isinstance(x, (list, tuple)) and len(x) >= 2 and x[0] is not None]
                series_info[key] = _compute_gaps_stats(ts)

    elif endpoint == "body_battery":
        item = payload[0] if (isinstance(payload, list) and payload and isinstance(payload[0], dict)) else (payload if isinstance(payload, dict) else None)
        if isinstance(item, dict):
            arr = item.get("bodyBatteryValuesArray")
            if isinstance(arr, list):
                ts = [float(x[0]) for x in arr if isinstance(x, (list, tuple)) and len(x) >= 2 and x[0] is not None]
                series_info["bodyBatteryValuesArray"] = _compute_gaps_stats(ts)

    elif endpoint.startswith("activity_details") and isinstance(payload, dict):
        descriptors = payload.get("metricDescriptors")
        metrics_list = payload.get("activityDetailMetrics")
        metric_keys = [
            d.get("key") for d in descriptors if isinstance(d, dict) and d.get("key")
        ] if isinstance(descriptors, list) else []
        ts_idx: int | None = None
        if isinstance(descriptors, list):
            for d in descriptors:
                if isinstance(d, dict) and d.get("key") in ("directTimestamp", "sumElapsedDuration"):
                    ts_idx = _to_int(d.get("metricsIndex"))
                    if d.get("key") == "directTimestamp":
                        break
        ts_vals: list[float] = []
        if isinstance(metrics_list, list) and ts_idx is not None:
            for m_item in metrics_list:
                if isinstance(m_item, dict):
                    m_arr = m_item.get("metrics")
                    if isinstance(m_arr, list) and len(m_arr) > ts_idx and m_arr[ts_idx] is not None:
                        val = _to_float(m_arr[ts_idx])
                        if val is not None:
                            ts_vals.append(val if val > 1e11 else val * 1000.0)
        series_info["activityDetailMetrics"] = {
            "available_metrics": metric_keys,
            **_compute_gaps_stats(ts_vals),
        }

    return {
        "available": True,
        "top_keys": sorted(payload.keys()) if isinstance(payload, dict) else f"list[{len(payload)}]",
        "series": series_info,
    }
