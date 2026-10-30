"""Server Flask locale (127.0.0.1) per la dashboard Garmin."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from app import analysis, db, synthetic


def _is_demo_mode() -> bool:
    return os.getenv("DEMO_MODE", "1").strip() in ("1", "true", "True", "yes")


def create_app(db_path: Path | str | None = None, demo_mode: bool | None = None) -> Flask:
    load_dotenv()
    resolved_demo = _is_demo_mode() if demo_mode is None else demo_mode
    resolved_db_path = Path(db_path) if db_path is not None else db.get_db_path(demo_mode=resolved_demo)

    root_dir = Path(__file__).resolve().parent.parent
    app = Flask(
        __name__,
        template_folder=str(root_dir / "templates"),
        static_folder=str(root_dir / "static"),
    )
    app.config["DB_PATH"] = resolved_db_path
    app.config["DEMO_MODE"] = resolved_demo

    # Inizializza lo schema; se siamo in DEMO_MODE e il DB demo è vuoto, genera i 90 giorni sintetici
    conn = db.connect(resolved_db_path)
    db.init_db(conn)
    if resolved_demo:
        cur = conn.execute("SELECT COUNT(*) AS cnt FROM daily")
        if cur.fetchone()["cnt"] == 0:
            synthetic.seed_synthetic_db(conn, days=90, seed=42)
    conn.close()

    @app.get("/")
    def index() -> str:
        conn = db.connect(app.config["DB_PATH"])
        daily_rows = db.fetch_daily_range(conn)
        activities = db.fetch_activities_range(conn)
        conn.close()

        selected_day = request.args.get("day")
        latest: dict[str, Any] | None = None
        if daily_rows:
            if selected_day:
                matches = [r for r in daily_rows if r["day"] == selected_day]
                latest = matches[-1] if matches else daily_rows[-1]
            else:
                latest = daily_rows[-1]

        recent_activities = list(reversed(activities[-10:]))
        return render_template(
            "index.html",
            demo_mode=app.config["DEMO_MODE"],
            day=latest["day"] if latest else "—",
            total_days=len(daily_rows),
            latest=latest,
            activities=recent_activities,
        )

    @app.get("/api/status")
    def api_status() -> Any:
        conn = db.connect(app.config["DB_PATH"])
        daily_rows = db.fetch_daily_range(conn)
        activities = db.fetch_activities_range(conn)
        conn.close()
        return jsonify(
            {
                "demo_mode": app.config["DEMO_MODE"],
                "days_count": len(daily_rows),
                "activities_count": len(activities),
                "completeness": analysis.compute_completeness(daily_rows),
            }
        )

    return app


def main() -> None:
    app = create_app()
    host = "127.0.0.1"
    port = int(os.getenv("PORT", "5000"))
    print(f"Dashboard Garmin attiva su http://{host}:{port} (DEMO_MODE={app.config['DEMO_MODE']})")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
