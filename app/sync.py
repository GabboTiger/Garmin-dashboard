"""Sincronizzazione incrementale e idempotente da Garmin Connect a SQLite, e ricalcolo dal grezzo."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app import db, parsers
from app.garmin_client import SafeGarminClient

logger = logging.getLogger(__name__)


def compute_days_to_fetch(
    conn: sqlite3.Connection,
    days: int = 30,
    end_date: date | None = None,
    always_refresh_last_n: int = 2,
    force_all: bool = False,
) -> list[str]:
    """Determina quali giorni nell'intervallo [end_date - days + 1, end_date] vanno scaricati:
    - tutti i giorni che non hanno ancora il payload 'summary' in `raw_garmin`;
    - più gli ultimi `always_refresh_last_n` giorni (perché l'orologio può sincronizzarsi in ritardo).
    """
    if end_date is None:
        end_date = date.today()
    start_date = end_date - timedelta(days=max(1, days) - 1)
    all_days = [(start_date + timedelta(days=i)).isoformat() for i in range(days)]

    if force_all:
        return all_days

    cur = conn.execute(
        """
        SELECT DISTINCT day FROM raw_garmin
        WHERE endpoint = 'summary' AND day >= ? AND day <= ?
        """,
        (all_days[0], all_days[-1]),
    )
    existing = {row["day"] for row in cur.fetchall()}
    recent_cutoff = (end_date - timedelta(days=max(0, always_refresh_last_n - 1))).isoformat()

    return [d for d in all_days if (d not in existing or d >= recent_cutoff)]


def reparse_day_from_raw(conn: sqlite3.Connection, day_str: str) -> dict[str, Any]:
    """Ricostruisce la riga `daily` di `day_str` a partire da tutti i JSON salvati in `raw_garmin`."""
    summary_raw = db.get_raw(conn, day_str, "summary")
    sleep_raw = db.get_raw(conn, day_str, "sleep")
    hrv_raw = db.get_raw(conn, day_str, "hrv")
    resp_raw = db.get_raw(conn, day_str, "respiration")
    spo2_raw = db.get_raw(conn, day_str, "spo2")
    bb_raw = db.get_raw(conn, day_str, "body_battery")
    tr_raw = db.get_raw(conn, day_str, "training_readiness")

    frag_summary = parsers.parse_summary_payload(day_str, summary_raw)
    frag_sleep = parsers.parse_sleep_payload(day_str, sleep_raw)
    frag_hrv = parsers.parse_hrv_payload(day_str, hrv_raw)
    frag_resp = parsers.parse_respiration_payload(day_str, resp_raw)
    frag_spo2 = parsers.parse_spo2_payload(day_str, spo2_raw)
    frag_bb = parsers.parse_body_battery_payload(day_str, bb_raw)
    frag_tr = parsers.parse_training_readiness_payload(day_str, tr_raw)

    # L'ordine fa sì che summary/sleep/hrv e i payload specifici arricchiscano senza mai sovrascrivere con None
    merged = parsers.merge_daily_fragments(
        day_str,
        frag_bb,
        frag_summary,
        frag_sleep,
        frag_hrv,
        frag_resp,
        frag_spo2,
        frag_tr,
    )
    db.replace_daily(conn, merged)
    return merged


def reparse_all_from_raw(conn: sqlite3.Connection) -> dict[str, int]:
    """Riesegue il parsing di tutte le righe in `raw_garmin` senza contattare Garmin."""
    cur_days = conn.execute(
        """
        SELECT DISTINCT day FROM raw_garmin
        WHERE endpoint IN ('summary', 'sleep', 'hrv', 'respiration', 'spo2', 'body_battery', 'training_readiness')
        ORDER BY day ASC
        """
    )
    days = [r["day"] for r in cur_days.fetchall()]
    for d in days:
        reparse_day_from_raw(conn, d)

    cur_acts = conn.execute(
        "SELECT payload_json FROM raw_garmin WHERE endpoint = 'activities_range' ORDER BY day ASC"
    )
    act_count = 0
    for row in cur_acts.fetchall():
        items = json.loads(row["payload_json"])
        if isinstance(items, list):
            for item in items:
                act_id = item.get("activityId") if isinstance(item, dict) else None
                zones_raw = (
                    db.get_raw_by_endpoint(conn, f"activity_hr_zones:{act_id}")
                    if act_id is not None
                    else None
                )
                parsed = parsers.parse_activity_payload(item, hr_zones_payload=zones_raw)
                if parsed is not None:
                    db.upsert_activity(conn, parsed)
                    act_count += 1

    conn.commit()
    return {"days_reparsed": len(days), "activities_reparsed": act_count}


def sync_garmin_data(
    conn: sqlite3.Connection,
    client: SafeGarminClient,
    days: int = 30,
    end_date: date | None = None,
    force_all: bool = False,
    include_extended: bool = True,
    fetch_activity_details: bool = True,
    progress_cb: Any | None = None,
) -> dict[str, Any]:
    """Esegue la sincronizzazione incrementale e idempotente per gli ultimi `days` giorni.
    Scarica i dettagli delle attività (`activity_details:<id>` e `activity_hr_zones:<id>`)
    SOLO per le attività non ancora presenti nella cache `raw_garmin`.
    """
    if end_date is None:
        end_date = date.today()
    start_date = end_date - timedelta(days=max(1, days) - 1)
    start_str = start_date.isoformat()
    end_str = end_date.isoformat()

    to_fetch = compute_days_to_fetch(conn, days=days, end_date=end_date, force_all=force_all)
    logger.info(
        "Avvio sync per %s..%s (%d giorni da aggiornare su %d totali)",
        start_str,
        end_str,
        len(to_fetch),
        days,
    )
    if callable(progress_cb):
        progress_cb(f"Controllo {len(to_fetch)} giorni da aggiornare...")

    errors = 0
    days_synced = 0

    for idx_d, d_str in enumerate(to_fetch, start=1):
        try:
            if callable(progress_cb):
                progress_cb(f"Download giorno {d_str} ({idx_d}/{len(to_fetch)})...")
            bundle = client.fetch_day_bundle(d_str, include_extended=include_extended)
            for ep_name, payload in bundle.items():
                if payload is not None:
                    db.upsert_raw(conn, d_str, ep_name, payload)
            reparse_day_from_raw(conn, d_str)
            conn.commit()
            days_synced += 1
        except RuntimeError as limit_err:
            logger.warning("Stop anticipato durante il sync: %s", limit_err)
            errors += 1
            break
        except Exception as exc:
            logger.error("Errore durante il sync del giorno %s: %s", d_str, type(exc).__name__)
            errors += 1

    # Scarica l'elenco delle attività dell'intervallo; per ciascuna, scarica zone e details solo se assenti in cache
    activities_saved = 0
    details_fetched = 0
    try:
        if callable(progress_cb):
            progress_cb("Download elenco attività e serie 1s mancanti...")
        raw_activities = client.fetch_activities_list(start_str, end_str)
        db.upsert_raw(conn, end_str, "activities_range", raw_activities)

        for idx_a, item in enumerate(raw_activities, start=1):
            act_id = item.get("activityId")
            start_local = str(item.get("startTimeLocal") or "")
            act_day = start_local[:10] if len(start_local) >= 10 else end_str

            zones_payload = item.get("_hr_zones")
            if act_id is not None:
                zones_ep = f"activity_hr_zones:{act_id}"
                if force_all or not db.has_raw(conn, zones_ep):
                    zones_payload = client.fetch_activity_hr_zones(act_id)
                    if zones_payload is not None:
                        db.upsert_raw(conn, act_day, zones_ep, zones_payload)
                else:
                    zones_payload = db.get_raw_by_endpoint(conn, zones_ep)

                if fetch_activity_details:
                    details_ep = f"activity_details:{act_id}"
                    if force_all or not db.has_raw(conn, details_ep):
                        if callable(progress_cb):
                            progress_cb(f"Scarico serie 1s attività {idx_a}/{len(raw_activities)} ({act_day})...")
                        details_payload = client.fetch_activity_details(act_id)
                        if details_payload is not None:
                            db.upsert_raw(conn, act_day, details_ep, details_payload)
                            details_fetched += 1
                            conn.commit()

            parsed_act = parsers.parse_activity_payload(item, hr_zones_payload=zones_payload)
            if parsed_act is not None:
                db.upsert_activity(conn, parsed_act)
                activities_saved += 1
        conn.commit()
    except RuntimeError as limit_err:
        logger.warning("Stop anticipato durante il download dettagli attività: %s", limit_err)
        errors += 1
    except Exception as exc:
        logger.error("Errore durante il download delle attività (%s)", type(exc).__name__)
        errors += 1

    status = "ok" if errors == 0 else ("partial" if days_synced > 0 else "error")
    conn.execute(
        """
        INSERT INTO sync_log (run_at, days_requested, requests_made, errors, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            days,
            client.requests_made,
            errors,
            status,
        ),
    )
    conn.commit()

    return {
        "status": status,
        "days_requested": days,
        "days_synced": days_synced,
        "activities_synced": activities_saved,
        "activity_details_fetched": details_fetched,
        "requests_made": client.requests_made,
        "errors": errors,
    }
