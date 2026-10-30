"""Gestione dello schema SQLite e accesso ai dati locali."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path("data/garmin.db")
DEFAULT_DEMO_DB_PATH = Path("data/demo.db")


def get_db_path(demo_mode: bool | None = None) -> Path:
    """Restituisce il percorso del database in base a DEMO_MODE o DB_PATH."""
    explicit = os.getenv("DB_PATH")
    if explicit:
        return Path(explicit)
    if demo_mode is None:
        demo_mode = os.getenv("DEMO_MODE", "1").strip() in ("1", "true", "True", "yes")
    return DEFAULT_DEMO_DB_PATH if demo_mode else DEFAULT_DB_PATH


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS raw_garmin (
    day TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (day, endpoint)
);

CREATE TABLE IF NOT EXISTS daily (
    day TEXT PRIMARY KEY,
    sleep_total_min REAL,
    sleep_deep_min REAL,
    sleep_light_min REAL,
    sleep_rem_min REAL,
    sleep_awake_min REAL,
    sleep_score REAL,
    sleep_start_local TEXT,
    sleep_end_local TEXT,
    rhr_bpm REAL,
    hrv_night_ms REAL,
    stress_avg REAL,
    bb_max REAL,
    bb_min REAL,
    bb_charged REAL,
    steps INTEGER,
    calories_active REAL,
    intensity_min REAL
);

CREATE TABLE IF NOT EXISTS activities (
    activity_id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    start_local TEXT,
    type TEXT NOT NULL,
    name TEXT,
    duration_s REAL,
    distance_m REAL,
    hr_avg REAL,
    hr_max REAL,
    training_load REAL,
    z1_s REAL,
    z2_s REAL,
    z3_s REAL,
    z4_s REAL,
    z5_s REAL
);

CREATE INDEX IF NOT EXISTS idx_activities_day ON activities(day);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    days_requested INTEGER NOT NULL,
    requests_made INTEGER NOT NULL,
    errors INTEGER NOT NULL,
    status TEXT NOT NULL
);
"""

DAILY_COLUMNS = (
    "day",
    "sleep_total_min",
    "sleep_deep_min",
    "sleep_light_min",
    "sleep_rem_min",
    "sleep_awake_min",
    "sleep_score",
    "sleep_start_local",
    "sleep_end_local",
    "rhr_bpm",
    "hrv_night_ms",
    "stress_avg",
    "bb_max",
    "bb_min",
    "bb_charged",
    "steps",
    "calories_active",
    "intensity_min",
)

ACTIVITY_COLUMNS = (
    "activity_id",
    "day",
    "start_local",
    "type",
    "name",
    "duration_s",
    "distance_m",
    "hr_avg",
    "hr_max",
    "training_load",
    "z1_s",
    "z2_s",
    "z3_s",
    "z4_s",
    "z5_s",
)


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Apre una connessione SQLite con row_factory=sqlite3.Row."""
    path = Path(db_path) if db_path is not None else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Crea le tabelle se non esistono."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def upsert_raw(
    conn: sqlite3.Connection,
    day: str,
    endpoint: str,
    payload: Any,
    fetched_at: str | None = None,
) -> None:
    """Salva o aggiorna la risposta grezza JSON per (day, endpoint)."""
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc).isoformat()
    payload_str = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO raw_garmin (day, endpoint, fetched_at, payload_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(day, endpoint) DO UPDATE SET
            fetched_at = excluded.fetched_at,
            payload_json = excluded.payload_json
        """,
        (day, endpoint, fetched_at, payload_str),
    )


def get_raw(conn: sqlite3.Connection, day: str, endpoint: str) -> Any | None:
    """Restituisce il payload JSON decodificato per (day, endpoint), o None se assente."""
    cur = conn.execute(
        "SELECT payload_json FROM raw_garmin WHERE day = ? AND endpoint = ?",
        (day, endpoint),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return json.loads(row["payload_json"])


def upsert_daily(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Inserisce o aggiorna una riga nella tabella daily. I campi non forniti restano NULL (o invariati se presenti)."""
    if "day" not in row or not row["day"]:
        raise ValueError("Il campo 'day' è obbligatorio per upsert_daily.")

    record = {col: row.get(col, None) for col in DAILY_COLUMNS}
    placeholders = ", ".join("?" for _ in DAILY_COLUMNS)
    col_names = ", ".join(DAILY_COLUMNS)
    update_clause = ", ".join(
        f"{col} = COALESCE(excluded.{col}, daily.{col})"
        for col in DAILY_COLUMNS
        if col != "day"
    )
    conn.execute(
        f"""
        INSERT INTO daily ({col_names})
        VALUES ({placeholders})
        ON CONFLICT(day) DO UPDATE SET
            {update_clause}
        """,
        tuple(record[col] for col in DAILY_COLUMNS),
    )


def replace_daily(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Sostituisce interamente la riga daily per quel giorno (preservando i NULL espliciti)."""
    if "day" not in row or not row["day"]:
        raise ValueError("Il campo 'day' è obbligatorio per replace_daily.")

    record = {col: row.get(col, None) for col in DAILY_COLUMNS}
    placeholders = ", ".join("?" for _ in DAILY_COLUMNS)
    col_names = ", ".join(DAILY_COLUMNS)
    update_clause = ", ".join(
        f"{col} = excluded.{col}" for col in DAILY_COLUMNS if col != "day"
    )
    conn.execute(
        f"""
        INSERT INTO daily ({col_names})
        VALUES ({placeholders})
        ON CONFLICT(day) DO UPDATE SET
            {update_clause}
        """,
        tuple(record[col] for col in DAILY_COLUMNS),
    )


def upsert_activity(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Inserisce o aggiorna un'attività per activity_id."""
    if "activity_id" not in row or row["activity_id"] is None:
        raise ValueError("Il campo 'activity_id' è obbligatorio per upsert_activity.")
    if "day" not in row or not row["day"]:
        raise ValueError("Il campo 'day' è obbligatorio per upsert_activity.")

    record = {col: row.get(col, None) for col in ACTIVITY_COLUMNS}
    if record["type"] is None:
        record["type"] = "other"

    placeholders = ", ".join("?" for _ in ACTIVITY_COLUMNS)
    col_names = ", ".join(ACTIVITY_COLUMNS)
    update_clause = ", ".join(
        f"{col} = excluded.{col}" for col in ACTIVITY_COLUMNS if col != "activity_id"
    )
    conn.execute(
        f"""
        INSERT INTO activities ({col_names})
        VALUES ({placeholders})
        ON CONFLICT(activity_id) DO UPDATE SET
            {update_clause}
        """,
        tuple(record[col] for col in ACTIVITY_COLUMNS),
    )


def fetch_daily_range(
    conn: sqlite3.Connection,
    start_day: str | None = None,
    end_day: str | None = None,
) -> list[dict[str, Any]]:
    """Restituisce le righe di daily ordinate per data crescente."""
    clauses: list[str] = []
    params: list[Any] = []
    if start_day is not None:
        clauses.append("day >= ?")
        params.append(start_day)
    if end_day is not None:
        clauses.append("day <= ?")
        params.append(end_day)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    cur = conn.execute(f"SELECT * FROM daily {where} ORDER BY day ASC", params)
    return [dict(r) for r in cur.fetchall()]


def fetch_activities_range(
    conn: sqlite3.Connection,
    start_day: str | None = None,
    end_day: str | None = None,
) -> list[dict[str, Any]]:
    """Restituisce le attività ordinate per data e ora di inizio."""
    clauses: list[str] = []
    params: list[Any] = []
    if start_day is not None:
        clauses.append("day >= ?")
        params.append(start_day)
    if end_day is not None:
        clauses.append("day <= ?")
        params.append(end_day)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    cur = conn.execute(
        f"SELECT * FROM activities {where} ORDER BY day ASC, start_local ASC, activity_id ASC",
        params,
    )
    return [dict(r) for r in cur.fetchall()]
