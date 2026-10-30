"""Interfaccia a riga di comando (CLI) per sincronizzazione, ispezione, ricalcolo e modalità demo."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app import db, parsers, sync, synthetic
from app.garmin_client import GarminRateLimiter, SafeGarminClient


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_seed_demo(args: argparse.Namespace) -> int:
    conn = db.connect(db.get_db_path(demo_mode=True))
    db.init_db(conn)
    res = synthetic.seed_synthetic_db(conn, days=args.days, seed=args.seed)
    conn.close()
    print(f"Database demo popolato con successo: {res['daily_days']} giorni, {res['activities']} attività.")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    conn = db.connect(db.get_db_path(demo_mode=False))
    db.init_db(conn)

    limiter = GarminRateLimiter(min_delay_s=args.delay, max_requests=args.max_requests)
    client = SafeGarminClient(rate_limiter=limiter)

    if args.reset_login:
        client.reset_tokens()

    try:
        client.login(interactive=True)
    except Exception as exc:
        print(f"Errore di autenticazione Garmin: {exc}", file=sys.stderr)
        conn.close()
        return 1

    result = sync.sync_garmin_data(
        conn,
        client,
        days=args.days,
        force_all=args.force,
    )
    conn.close()
    print(
        f"Sync completato ({result['status']}): "
        f"{result['days_synced']} giorni aggiornati, "
        f"{result['activities_synced']} attività ({result.get('activity_details_fetched', 0)} dettagli nuovi), "
        f"{result['requests_made']} richieste HTTP, "
        f"{result['errors']} avvisi/errori."
    )
    return 0 if result["status"] in ("ok", "partial") else 1


def cmd_reparse(_args: argparse.Namespace) -> int:
    conn = db.connect(db.get_db_path(demo_mode=False))
    db.init_db(conn)
    res = sync.reparse_all_from_raw(conn)
    conn.close()
    print(
        f"Ricalcolo dal grezzo completato: {res['days_reparsed']} giorni e "
        f"{res['activities_reparsed']} attività ricostruite."
    )
    return 0


def cmd_inspect_raw(args: argparse.Namespace) -> int:
    """Mostra la struttura (solo chiavi, tipi e risoluzione temporale, senza dati personali) delle risposte in raw_garmin."""
    conn = db.connect(db.get_db_path(demo_mode=args.demo))
    db.init_db(conn)

    target_day = args.day
    if not target_day:
        cur = conn.execute("SELECT MAX(day) AS max_day FROM raw_garmin")
        row = cur.fetchone()
        target_day = row["max_day"] if row and row["max_day"] else date.today().isoformat()

    cur = conn.execute(
        "SELECT endpoint, payload_json FROM raw_garmin WHERE day = ? ORDER BY endpoint ASC",
        (target_day,),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print(f"Nessun payload grezzo trovato per il giorno {target_day}.")
        return 0

    print(f"=== Report tecnico risposte grezze per {target_day} (nessun dato personale mostrato) ===")
    for r in rows:
        endpoint = r["endpoint"]
        payload = json.loads(r["payload_json"])
        res_info = parsers.inspect_timeseries_resolution(endpoint, payload)
        shape = parsers.summarize_payload_structure(payload, max_depth=args.depth)
        print(f"\n--- Endpoint: {endpoint} ---")
        print("Risoluzione e serie:", json.dumps(res_info, ensure_ascii=False))
        print("Struttura chiavi:", json.dumps(shape, indent=2, ensure_ascii=False))
    return 0


def cmd_probe_account(args: argparse.Namespace) -> int:
    """Scarica 1 giorno completo (ieri o --day) + 1 attività dal tuo account Garmin, salva in raw_garmin
    e stampa disponibilità, campi e risoluzione temporale di ogni endpoint (senza dati personali).
    """
    conn = db.connect(db.get_db_path(demo_mode=False))
    db.init_db(conn)

    limiter = GarminRateLimiter(min_delay_s=args.delay, max_requests=30)
    client = SafeGarminClient(rate_limiter=limiter)
    try:
        client.login(interactive=True)
    except Exception as exc:
        print(f"Errore di autenticazione Garmin: {exc}", file=sys.stderr)
        conn.close()
        return 1

    target_day = args.day or (date.today() - timedelta(days=1)).isoformat()
    print(f"Scarico tutti gli endpoint giornalieri per {target_day}...")
    bundle = client.fetch_day_bundle(target_day, include_extended=True)
    for ep_name, payload in bundle.items():
        if payload is not None:
            db.upsert_raw(conn, target_day, ep_name, payload)
    sync.reparse_day_from_raw(conn, target_day)
    conn.commit()

    # Cerca l'ultima attività negli ultimi 14 giorni per testare get_activity_details e get_activity_hr_in_timezones
    start_search = (date.fromisoformat(target_day) - timedelta(days=14)).isoformat()
    acts = client.fetch_activities_list(start_search, target_day)
    if acts:
        sample_act = acts[0]
        act_id = sample_act.get("activityId")
        act_start = str(sample_act.get("startTimeLocal") or target_day)[:10]
        if act_id is not None:
            zones = client.fetch_activity_hr_zones(act_id)
            if zones is not None:
                db.upsert_raw(conn, target_day, f"activity_hr_zones:{act_id}", zones)
            details = client.fetch_activity_details(act_id)
            if details is not None:
                db.upsert_raw(conn, target_day, f"activity_details:{act_id}", details)
            parsed_act = parsers.parse_activity_payload(sample_act, hr_zones_payload=zones)
            if parsed_act is not None:
                db.upsert_activity(conn, parsed_act)
            conn.commit()
            print(f"Campione attività salvato (activity_id={act_id} del {act_start}).")

    conn.close()
    args_inspect = argparse.Namespace(day=target_day, depth=2, demo=False)
    return cmd_inspect_raw(args_inspect)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Strumenti CLI per la dashboard Garmin locale.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_demo = sub.add_parser("seed-demo", help="Popola il database demo con 90 giorni di dati sintetici.")
    p_demo.add_argument("--days", type=int, default=90, help="Numero di giorni sintetici (default: 90).")
    p_demo.add_argument("--seed", type=int, default=42, help="Seme del generatore (default: 42).")
    p_demo.set_defaults(func=cmd_seed_demo)

    p_sync = sub.add_parser("sync", help="Scarica in modo incrementale i dati reali da Garmin Connect.")
    p_sync.add_argument("--days", type=int, default=30, help="Numero di giorni da controllare (default: 30).")
    p_sync.add_argument("--delay", type=float, default=1.5, help="Pausa minima in secondi tra le richieste (default: 1.5).")
    p_sync.add_argument("--max-requests", type=int, default=350, help="Massimo numero di richieste per sessione (default: 350).")
    p_sync.add_argument("--force", action="store_true", help="Riscarica anche i giorni già presenti in raw_garmin.")
    p_sync.add_argument("--reset-login", action="store_true", help="Cancella i token salvati e forza un nuovo login.")
    p_sync.set_defaults(func=cmd_sync)

    p_probe = sub.add_parser(
        "probe-account",
        help="Scarica 1 giorno + 1 attività e mostra disponibilità e risoluzione temporale degli endpoint.",
    )
    p_probe.add_argument("--day", type=str, default=None, help="Data YYYY-MM-DD da testare (default: ieri).")
    p_probe.add_argument("--delay", type=float, default=1.5, help="Pausa minima in secondi tra le richieste.")
    p_probe.set_defaults(func=cmd_probe_account)

    p_rep = sub.add_parser("reparse", help="Ricostruisce le tabelle daily e activities a partire da raw_garmin.")
    p_rep.set_defaults(func=cmd_reparse)

    p_ins = sub.add_parser("inspect-raw", help="Mostra le chiavi e la risoluzione temporale (senza valori personali) dei JSON in raw_garmin.")
    p_ins.add_argument("--day", type=str, default=None, help="Data YYYY-MM-DD da ispezionare (default: l'ultima disponibile).")
    p_ins.add_argument("--depth", type=int, default=2, help="Profondità massima dell'albero JSON (default: 2).")
    p_ins.add_argument("--demo", action="store_true", help="Ispeziona il database demo invece di quello reale.")
    p_ins.set_defaults(func=cmd_inspect_raw)

    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    _setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
