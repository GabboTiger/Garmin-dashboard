"""Server Flask locale (127.0.0.1) per la dashboard Garmin (Fasi 1b e 4)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from app import analysis, baselines, db, scores, synthetic


def _is_demo_mode() -> bool:
    return os.getenv("DEMO_MODE", "1").strip() in ("1", "true", "True", "yes")


def _resolve_day_navigation(
    daily_rows: list[dict[str, Any]],
    selected_day: str | None,
) -> tuple[dict[str, Any] | None, str, str | None, str | None]:
    """Individua la riga del giorno richiesto (o l'ultimo disponibile) e calcola prev_day / next_day."""
    if not daily_rows:
        return None, "", None, None
    days_list = [str(r["day"]) for r in daily_rows]
    if selected_day and selected_day in days_list:
        idx = days_list.index(selected_day)
    else:
        idx = len(days_list) - 1

    latest = daily_rows[idx]
    target_day = str(latest["day"])
    prev_day = days_list[idx - 1] if idx > 0 else None
    next_day = days_list[idx + 1] if idx < len(days_list) - 1 else None
    return latest, target_day, prev_day, next_day


def _enrich_activities_with_analysis(
    conn: Any,
    activities: list[dict[str, Any]],
    rhr_by_day: dict[str, float | None],
    limit: int = 20,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for act in reversed(activities[-limit:]):
        act_item = dict(act)
        act_id = act.get("activity_id")
        details_raw = db.get_raw_by_endpoint(conn, f"activity_details:{act_id}") if act_id else None
        act_item["hrr"] = analysis.compute_heart_rate_recovery(details_raw)
        act_item["drift"] = analysis.compute_cardiac_drift(details_raw)
        act_item["trimp"] = analysis.compute_trimp(act, details_raw, hr_rest=rhr_by_day.get(str(act.get("day"))))
        act_item["efficiency"] = analysis.compute_speed_hr_efficiency(act)
        enriched.append(act_item)
    return enriched


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

        latest, target_day, prev_day, next_day = _resolve_day_navigation(daily_rows, request.args.get("day"))
        rhr_by_day = {str(r["day"]): r.get("rhr_bpm") for r in daily_rows}
        sleep_raw = db.get_raw(conn, target_day, "sleep") if target_day else None
        enriched_acts = _enrich_activities_with_analysis(conn, activities, rhr_by_day, limit=12)
        conn.close()

        day_scores = scores.compute_all_scores_for_day(daily_rows, activities, target_day) if latest else {}
        day_baselines = baselines.compute_all_baselines_for_day(daily_rows, target_day) if latest else {}
        hrv_band = analysis.compute_hrv_band(daily_rows, target_day=target_day) if latest else None
        sleep_arch = analysis.compute_sleep_architecture(latest, sleep_raw) if latest else None
        sleep_phys = analysis.compute_nocturnal_physiology_and_debt(daily_rows, target_day, sleep_raw) if latest else None
        bb_recharge = analysis.compute_body_battery_recharge(latest) if latest else None

        return render_template(
            "index.html",
            active_page="today",
            demo_mode=app.config["DEMO_MODE"],
            day=target_day,
            prev_day=prev_day,
            next_day=next_day,
            total_days=len(daily_rows),
            latest=latest,
            scores=day_scores,
            baselines=day_baselines,
            hrv_band=hrv_band,
            sleep_arch=sleep_arch,
            sleep_phys=sleep_phys,
            bb_recharge=bb_recharge,
            activities=enriched_acts,
        )

    @app.get("/sleep")
    def page_sleep() -> str:
        conn = db.connect(app.config["DB_PATH"])
        daily_rows = db.fetch_daily_range(conn)
        activities = db.fetch_activities_range(conn)
        latest, target_day, prev_day, next_day = _resolve_day_navigation(daily_rows, request.args.get("day"))
        sleep_raw = db.get_raw(conn, target_day, "sleep") if target_day else None
        conn.close()

        day_scores = scores.compute_all_scores_for_day(daily_rows, activities, target_day) if latest else {}
        sleep_arch = analysis.compute_sleep_architecture(latest, sleep_raw) if latest else None
        sleep_reg = analysis.compute_sleep_regularity(daily_rows, target_day, window_days=30) if latest else None
        sleep_phys = analysis.compute_nocturnal_physiology_and_debt(daily_rows, target_day, sleep_raw) if latest else None
        sleep_trend = analysis.compute_metric_trend_analysis(
            daily_rows,
            metric="sleep_total_min",
            unit="min",
            window_days=30,
            target_day=target_day,
        ) if latest else None

        return render_template(
            "sleep.html",
            active_page="sleep",
            demo_mode=app.config["DEMO_MODE"],
            day=target_day,
            prev_day=prev_day,
            next_day=next_day,
            total_days=len(daily_rows),
            latest=latest,
            scores=day_scores,
            sleep_arch=sleep_arch,
            sleep_reg=sleep_reg,
            sleep_phys=sleep_phys,
            sleep_trend=sleep_trend,
        )

    @app.get("/recovery")
    def page_recovery() -> str:
        conn = db.connect(app.config["DB_PATH"])
        daily_rows = db.fetch_daily_range(conn)
        activities = db.fetch_activities_range(conn)
        conn.close()

        latest, target_day, prev_day, next_day = _resolve_day_navigation(daily_rows, request.args.get("day"))
        day_scores = scores.compute_all_scores_for_day(daily_rows, activities, target_day) if latest else {}
        day_baselines = baselines.compute_all_baselines_for_day(daily_rows, target_day) if latest else {}
        hrv_band = analysis.compute_hrv_band(daily_rows, target_day=target_day) if latest else None
        bb_recharge = analysis.compute_body_battery_recharge(latest) if latest else None
        rhr_trend = analysis.compute_metric_trend_analysis(
            daily_rows,
            metric="rhr_bpm",
            unit="bpm",
            window_days=30,
            target_day=target_day,
        ) if latest else None
        train_effect = analysis.compute_training_to_recovery_effect(daily_rows, activities) if latest else None

        return render_template(
            "recovery.html",
            active_page="recovery",
            demo_mode=app.config["DEMO_MODE"],
            day=target_day,
            prev_day=prev_day,
            next_day=next_day,
            total_days=len(daily_rows),
            latest=latest,
            scores=day_scores,
            baselines=day_baselines,
            hrv_band=hrv_band,
            bb_recharge=bb_recharge,
            rhr_trend=rhr_trend,
            train_effect=train_effect,
        )

    @app.get("/activities")
    def page_activities() -> str:
        conn = db.connect(app.config["DB_PATH"])
        daily_rows = db.fetch_daily_range(conn)
        activities = db.fetch_activities_range(conn)
        latest, target_day, prev_day, next_day = _resolve_day_navigation(daily_rows, request.args.get("day"))
        rhr_by_day = {str(r["day"]): r.get("rhr_bpm") for r in daily_rows}
        enriched_acts = _enrich_activities_with_analysis(conn, activities, rhr_by_day, limit=30)
        conn.close()

        day_scores = scores.compute_all_scores_for_day(daily_rows, activities, target_day) if latest else {}
        load_stats = analysis.compute_acute_chronic_load(daily_rows, activities, target_day=target_day) if latest else None
        weekly_zones = analysis.compute_weekly_zone_distribution(activities, end_day=target_day, window_days=7) if latest else None

        return render_template(
            "activities.html",
            active_page="activities",
            demo_mode=app.config["DEMO_MODE"],
            day=target_day,
            prev_day=prev_day,
            next_day=next_day,
            total_days=len(daily_rows),
            latest=latest,
            scores=day_scores,
            load_stats=load_stats,
            weekly_zones=weekly_zones,
            activities=enriched_acts,
        )

    @app.get("/trends")
    def page_trends() -> str:
        conn = db.connect(app.config["DB_PATH"])
        daily_rows = db.fetch_daily_range(conn)
        conn.close()

        latest, target_day, prev_day, next_day = _resolve_day_navigation(daily_rows, request.args.get("day"))
        hrv_trend = analysis.compute_metric_trend_analysis(daily_rows, "hrv_night_ms", "ms", 30, target_day) if latest else None
        rhr_trend = analysis.compute_metric_trend_analysis(daily_rows, "rhr_bpm", "bpm", 30, target_day) if latest else None
        sleep_trend = analysis.compute_metric_trend_analysis(daily_rows, "sleep_total_min", "min", 30, target_day) if latest else None

        trend_items = [
            {"label": "HRV Notturna", "unit": "ms", "color_var": "--color-recovery", "trend": hrv_trend},
            {"label": "FC a Riposo", "unit": "bpm", "color_var": "--color-recovery", "trend": rhr_trend},
            {"label": "Sonno Totale", "unit": "min", "color_var": "--color-sleep", "trend": sleep_trend},
        ]
        return render_template(
            "trends.html",
            active_page="trends",
            demo_mode=app.config["DEMO_MODE"],
            day=target_day,
            prev_day=prev_day,
            next_day=next_day,
            total_days=len(daily_rows),
            trend_items=trend_items,
            hrv_trend=hrv_trend,
        )

    @app.get("/correlations")
    def page_correlations() -> str:
        conn = db.connect(app.config["DB_PATH"])
        daily_rows = db.fetch_daily_range(conn)
        activities = db.fetch_activities_range(conn)
        conn.close()

        _, target_day, prev_day, next_day = _resolve_day_navigation(daily_rows, request.args.get("day"))
        lag_days = int(request.args.get("lag", "1"))
        lag_days = max(0, min(2, lag_days))
        df = analysis.build_daily_dataframe(daily_rows, activities)
        matrix = analysis.compute_correlation_matrix(
            df,
            metrics=("sleep_total_min", "hrv_night_ms", "rhr_bpm", "stress_avg", "bb_max", "training_load_daily"),
            lag_days=lag_days,
        )
        return render_template(
            "correlations.html",
            active_page="correlations",
            demo_mode=app.config["DEMO_MODE"],
            day=target_day,
            prev_day=prev_day,
            next_day=next_day,
            total_days=len(daily_rows),
            lag_days=lag_days,
            matrix=matrix,
        )

    @app.get("/design")
    def page_design() -> str:
        conn = db.connect(app.config["DB_PATH"])
        daily_rows = db.fetch_daily_range(conn)
        activities = db.fetch_activities_range(conn)
        latest, target_day, prev_day, next_day = _resolve_day_navigation(daily_rows, request.args.get("day"))
        sleep_raw = db.get_raw(conn, target_day, "sleep") if target_day else None
        conn.close()

        force_theme = request.args.get("theme")
        if force_theme not in ("light", "dark"):
            force_theme = None

        day_scores = scores.compute_all_scores_for_day(daily_rows, activities, target_day) if latest else {}
        day_baselines = baselines.compute_all_baselines_for_day(daily_rows, target_day) if latest else {}
        hrv_band = analysis.compute_hrv_band(daily_rows, target_day=target_day) if latest else None
        sleep_arch = analysis.compute_sleep_architecture(latest, sleep_raw) if latest else None

        return render_template(
            "design.html",
            active_page="design",
            force_theme=force_theme,
            demo_mode=app.config["DEMO_MODE"],
            day=target_day,
            prev_day=prev_day,
            next_day=next_day,
            total_days=len(daily_rows),
            latest=latest,
            scores=day_scores,
            baselines=day_baselines,
            hrv_band=hrv_band,
            sleep_arch=sleep_arch,
        )

    @app.get("/api/day/<day_str>")
    def api_day(day_str: str) -> Any:
        conn = db.connect(app.config["DB_PATH"])
        daily_rows = db.fetch_daily_range(conn)
        activities = db.fetch_activities_range(conn)
        sleep_raw = db.get_raw(conn, day_str, "sleep")
        conn.close()

        latest, target_day, _, _ = _resolve_day_navigation(daily_rows, day_str)
        if not latest:
            return jsonify({"error": "not_found"}), 404

        return jsonify(
            {
                "day": target_day,
                "daily": latest,
                "scores": scores.compute_all_scores_for_day(daily_rows, activities, target_day),
                "baselines": baselines.compute_all_baselines_for_day(daily_rows, target_day),
                "sleep_architecture": analysis.compute_sleep_architecture(latest, sleep_raw),
                "hrv_band": analysis.compute_hrv_band(daily_rows, target_day=target_day),
            }
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
