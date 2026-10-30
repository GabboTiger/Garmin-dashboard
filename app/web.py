"""Server Flask locale (127.0.0.1) per la dashboard Garmin (Fasi 1b, 4 e 5)."""

from __future__ import annotations

import csv
import io
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request

from app import analysis, baselines, db, scores, synthetic

METRIC_META: dict[str, dict[str, str]] = {
    "hrv_night_ms": {"label": "HRV Notturna", "unit": "ms", "color_var": "--color-recovery"},
    "rhr_bpm": {"label": "FC a Riposo", "unit": "bpm", "color_var": "--color-recovery"},
    "sleep_total_min": {"label": "Sonno Totale", "unit": "min", "color_var": "--color-sleep"},
    "stress_avg": {"label": "Stress Medio", "unit": "pt", "color_var": "--color-strain"},
    "bb_max": {"label": "Body Battery Max", "unit": "pt", "color_var": "--color-recovery"},
    "steps": {"label": "Passi Giornalieri", "unit": "passi", "color_var": "--color-strain"},
    "training_load_daily": {"label": "Carico Giornaliero", "unit": "TRIMP", "color_var": "--color-strain"},
    "respiration_sleep_brpm": {"label": "Respirazione Notte", "unit": "atti/m", "color_var": "--color-sleep"},
    "spo2_sleep_avg_pct": {"label": "SpO2 Notturna", "unit": "%", "color_var": "--color-sleep"},
}


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


def _build_window_history(
    daily_rows: list[dict[str, Any]],
    activities: list[dict[str, Any]],
    target_day: str,
    window_days: int = 30,
) -> list[dict[str, Any]]:
    """Costruisce la serie storica degli ultimi `window_days` giorni fino a `target_day`,
    includendo i 3 punteggi giornalieri e le metriche di sonno, recupero ed energia per i grafici.
    """
    if not daily_rows or not target_day:
        return []
    target_dt = date.fromisoformat(target_day)
    start_str = (target_dt - timedelta(days=max(1, window_days) - 1)).isoformat()
    sub_rows = [r for r in daily_rows if start_str <= str(r.get("day") or "") <= target_day]

    out: list[dict[str, Any]] = []
    for r in sub_rows:
        d_str = str(r["day"])
        sc = scores.compute_all_scores_for_day(daily_rows, activities, d_str)
        rec_s = sc["recovery"].get("score") if sc["recovery"].get("score") is not None else sc["recovery"].get("provisional_score")
        str_s = sc["strain"].get("score") if sc["strain"].get("score") is not None else sc["strain"].get("provisional_score")
        slp_s = sc["sleep"].get("score") if sc["sleep"].get("score") is not None else sc["sleep"].get("provisional_score")

        tot_m = r.get("sleep_total_min")
        deep_m = r.get("sleep_deep_min")
        light_m = r.get("sleep_light_min")
        rem_m = r.get("sleep_rem_min")
        awake_m = r.get("sleep_awake_min")

        out.append(
            {
                "day": d_str,
                "recovery_score": rec_s,
                "strain_score": str_s,
                "sleep_score": slp_s,
                "hrv_night_ms": r.get("hrv_night_ms"),
                "rhr_bpm": r.get("rhr_bpm"),
                "stress_avg": r.get("stress_avg"),
                "bb_max": r.get("bb_max"),
                "bb_min": r.get("bb_min"),
                "bb_charged": r.get("bb_charged"),
                "steps": r.get("steps"),
                "respiration_sleep_brpm": r.get("respiration_sleep_brpm"),
                "spo2_sleep_avg_pct": r.get("spo2_sleep_avg_pct"),
                "sleep_total_h": round(float(tot_m) / 60.0, 2) if tot_m is not None else None,
                "sleep_deep_h": round(float(deep_m) / 60.0, 2) if deep_m is not None else None,
                "sleep_light_h": round(float(light_m) / 60.0, 2) if light_m is not None else None,
                "sleep_rem_h": round(float(rem_m) / 60.0, 2) if rem_m is not None else None,
                "sleep_awake_h": round(float(awake_m) / 60.0, 2) if awake_m is not None else None,
                "sleep_start_local": r.get("sleep_start_local"),
                "sleep_end_local": r.get("sleep_end_local"),
            }
        )
    return out


def _build_weekly_activity_summary(
    activities: list[dict[str, Any]],
    target_day: str,
    n_weeks: int = 8,
) -> list[dict[str, Any]]:
    """Raggruppa le attività nelle ultime `n_weeks` finestre di 7 giorni fino a `target_day`
    per il confronto settimanale di carico, volume e zone FC.
    """
    if not target_day:
        return []
    end_dt = date.fromisoformat(target_day)
    weeks: list[dict[str, Any]] = []

    for w in range(n_weeks - 1, -1, -1):
        w_end = end_dt - timedelta(days=w * 7)
        w_start = w_end - timedelta(days=6)
        s_str, e_str = w_start.isoformat(), w_end.isoformat()
        w_acts = [a for a in activities if s_str <= str(a.get("day") or "") <= e_str]

        dur_min = sum(float(a.get("duration_s") or 0.0) / 60.0 for a in w_acts)
        dist_km = sum(float(a.get("distance_m") or 0.0) / 1000.0 for a in w_acts)
        load_sum = 0.0
        z_mins = [0.0, 0.0, 0.0, 0.0, 0.0]

        for a in w_acts:
            t_res = analysis.compute_trimp(a)
            if t_res["calculable"] and t_res["value"] is not None:
                load_sum += float(t_res["value"])
            elif a.get("training_load") is not None:
                load_sum += float(a["training_load"])
            for k in range(5):
                z_mins[k] += float(a.get(f"z{k + 1}_s") or 0.0) / 60.0

        weeks.append(
            {
                "label": f"{w_start.strftime('%d/%m')}–{w_end.strftime('%d/%m')}",
                "start_day": s_str,
                "end_day": e_str,
                "activities_count": len(w_acts),
                "duration_min": round(dur_min, 0),
                "distance_km": round(dist_km, 1),
                "trimp_load": round(load_sum, 1),
                "z1_min": round(z_mins[0], 1),
                "z2_min": round(z_mins[1], 1),
                "z3_min": round(z_mins[2], 1),
                "z4_min": round(z_mins[3], 1),
                "z5_min": round(z_mins[4], 1),
            }
        )
    return weeks


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
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    # Inizializza lo schema; se siamo in DEMO_MODE e il DB demo è vuoto (o privo di serie raw Step 2), genera i 90 giorni sintetici
    conn = db.connect(resolved_db_path)
    db.init_db(conn)
    if resolved_demo:
        cnt_daily = conn.execute("SELECT COUNT(*) AS cnt FROM daily").fetchone()["cnt"]
        cnt_raw = conn.execute("SELECT COUNT(*) AS cnt FROM raw_garmin").fetchone()["cnt"]
        if cnt_daily == 0 or cnt_raw == 0:
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
        history_30d = _build_window_history(daily_rows, activities, target_day, window_days=30) if latest else []

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
            history_30d=history_30d,
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
        history_30d = _build_window_history(daily_rows, activities, target_day, window_days=30) if latest else []

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
            history_30d=history_30d,
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
        history_30d = _build_window_history(daily_rows, activities, target_day, window_days=30) if latest else []

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
            history_30d=history_30d,
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
        weekly_summary = _build_weekly_activity_summary(activities, target_day, n_weeks=8) if latest else []

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
            weekly_summary=weekly_summary,
            activities=enriched_acts,
        )

    @app.get("/trends")
    def page_trends() -> str:
        conn = db.connect(app.config["DB_PATH"])
        daily_rows = db.fetch_daily_range(conn)
        activities = db.fetch_activities_range(conn)
        conn.close()

        latest, target_day, prev_day, next_day = _resolve_day_navigation(daily_rows, request.args.get("day"))
        raw_win = request.args.get("window", "30")
        window_days = int(raw_win) if raw_win in ("7", "30", "90") else 30

        m1 = request.args.get("m1", "hrv_night_ms")
        m2 = request.args.get("m2", "rhr_bpm")
        if m1 not in METRIC_META:
            m1 = "hrv_night_ms"
        if m2 not in METRIC_META:
            m2 = "rhr_bpm"

        trend_keys = ("hrv_night_ms", "rhr_bpm", "sleep_total_min", "stress_avg", "bb_max", "steps")
        trends_by_key: dict[str, Any] = {}
        trend_items: list[dict[str, Any]] = []

        for k in trend_keys:
            meta = METRIC_META[k]
            t_res = (
                analysis.compute_metric_trend_analysis(daily_rows, k, meta["unit"], window_days, target_day)
                if latest
                else None
            )
            trends_by_key[k] = t_res
            trend_items.append(
                {
                    "key": k,
                    "label": meta["label"],
                    "unit": meta["unit"],
                    "color_var": meta["color_var"],
                    "trend": t_res,
                }
            )

        history_win = _build_window_history(daily_rows, activities, target_day, window_days=window_days) if latest else []

        return render_template(
            "trends.html",
            active_page="trends",
            demo_mode=app.config["DEMO_MODE"],
            day=target_day,
            prev_day=prev_day,
            next_day=next_day,
            total_days=len(daily_rows),
            window_days=window_days,
            m1=m1,
            m2=m2,
            metric_meta=METRIC_META,
            trend_items=trend_items,
            trends_by_key=trends_by_key,
            hrv_trend=trends_by_key.get("hrv_night_ms"),
            history_win=history_win,
        )

    @app.get("/correlations")
    def page_correlations() -> str:
        conn = db.connect(app.config["DB_PATH"])
        daily_rows = db.fetch_daily_range(conn)
        activities = db.fetch_activities_range(conn)
        conn.close()

        _, target_day, prev_day, next_day = _resolve_day_navigation(daily_rows, request.args.get("day"))
        try:
            lag_days = int(request.args.get("lag", "1"))
        except ValueError:
            lag_days = 1
        lag_days = max(0, min(2, lag_days))

        corr_metrics = ("sleep_total_min", "hrv_night_ms", "rhr_bpm", "stress_avg", "bb_max", "training_load_daily")
        df = analysis.build_daily_dataframe(daily_rows, activities)
        matrix = analysis.compute_correlation_matrix(
            df,
            metrics=corr_metrics,
            lag_days=lag_days,
        )
        raw_comp = analysis.compute_completeness(
            daily_rows,
            metrics=("hrv_night_ms", "rhr_bpm", "sleep_total_min", "stress_avg", "bb_max", "steps", "respiration_sleep_brpm", "spo2_sleep_avg_pct"),
        )
        completeness = [
            {
                "metric": m,
                "present_days": info["present_days"],
                "total_days": info["total_days"],
                "missing_days": int(info["total_days"]) - int(info["present_days"]),
                "completeness_pct": info["completeness_pct"],
            }
            for m, info in raw_comp.items()
        ]

        # Estrae le serie giornaliere pulite per lo scatter plot interattivo (X_t vs Y_{t+lag})
        records_for_scatter: list[dict[str, Any]] = []
        if not df.empty:
            for _, row in df.iterrows():
                item: dict[str, Any] = {"day": str(row.get("day"))}
                for m in corr_metrics:
                    val = row.get(m)
                    item[m] = round(float(val), 2) if val is not None and str(val) != "nan" else None
                records_for_scatter.append(item)

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
            completeness=completeness,
            metric_meta=METRIC_META,
            scatter_records=records_for_scatter,
        )

    @app.get("/export/daily.csv")
    def export_daily_csv() -> Response:
        """Esporta la tabella `daily` in formato CSV, mantenendo i NULL come celle vuote (mai 0)."""
        conn = db.connect(app.config["DB_PATH"])
        daily_rows = db.fetch_daily_range(conn)
        conn.close()

        buf = io.StringIO()
        fieldnames = list(db.DAILY_COLUMNS)
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in daily_rows:
            clean_row = {k: ("" if r.get(k) is None else r.get(k)) for k in fieldnames}
            writer.writerow(clean_row)

        return Response(
            buf.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=garmin_daily.csv"},
        )

    @app.get("/export/activities.csv")
    def export_activities_csv() -> Response:
        """Esporta la tabella `activities` in formato CSV, mantenendo i NULL come celle vuote."""
        conn = db.connect(app.config["DB_PATH"])
        activities = db.fetch_activities_range(conn)
        conn.close()

        buf = io.StringIO()
        fieldnames = list(db.ACTIVITY_COLUMNS)
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for a in activities:
            clean_row = {k: ("" if a.get(k) is None else a.get(k)) for k in fieldnames}
            writer.writerow(clean_row)

        return Response(
            buf.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=garmin_activities.csv"},
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
