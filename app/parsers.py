"""Parser da payload grezzi JSON Garmin a righe normalizzate (`daily`, `activities`).

Regola fondamentale (AGENTS.md regola 4):
- Nessun valore mancante viene convertito in 0: se un campo è assente o nullo, resta `None` (`NULL` in SQLite).
- I parser leggono i payload salvati in `raw_garmin` così è sempre possibile rifare il parsing (`reparse`)
  senza contattare nuovamente Garmin.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


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
        "steps": _to_int(payload.get("totalSteps")),
        "calories_active": _to_float(payload.get("activeKilocalories")),
        "intensity_min": intensity_min,
    }


def parse_sleep_payload(day: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Estrae durata, fasi, punteggio e orari del sonno dal payload di `get_sleep_data(day)`."""
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

    # Alcuni dispositivi riportano anche `avgOvernightHrv` direttamente nel payload del sonno
    overnight_hrv = _to_float(payload.get("avgOvernightHrv") or dto.get("avgOvernightHrv"))
    if overnight_hrv is not None and overnight_hrv > 0:
        result["hrv_night_ms"] = overnight_hrv

    return result


def parse_hrv_payload(day: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Estrae l'HRV notturna media (in ms) dal payload di `get_hrv_data(day)`."""
    if not isinstance(payload, dict):
        return {"day": day}

    summary = payload.get("hrvSummary")
    if not isinstance(summary, dict):
        summary = payload

    hrv_val = _to_float(summary.get("lastNightAvg"))
    if hrv_val is not None and hrv_val <= 0:
        hrv_val = None

    return {
        "day": day,
        "hrv_night_ms": hrv_val,
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


def parse_activity_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Converte un elemento di `get_activities_by_date` (eventualmente arricchito con `_hr_zones`)
    nella riga normalizzata per la tabella `activities`.
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

    # Zone di frequenza cardiaca (da `_hr_zones` o campi diretti `hrTimeInZone_1..5`)
    z_map: dict[int, float | None] = {1: None, 2: None, 3: None, 4: None, 5: None}
    zones_list = payload.get("_hr_zones")
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
