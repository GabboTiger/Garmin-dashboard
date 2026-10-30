"""Analisi di completezza dei dati e correlazioni di Spearman con ritardo (lag 0, 1, 2 giorni)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

DISCLAIMER_CORRELATION = "Analisi esplorativa: la correlazione non implica causalità."


def build_daily_dataframe(
    daily_rows: list[dict[str, Any]],
    activities_rows: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """Costruisce un DataFrame giornaliero unendo `daily` con il carico giornaliero delle `activities`."""
    if not daily_rows:
        return pd.DataFrame()

    df = pd.DataFrame(daily_rows).sort_values("day").reset_index(drop=True)
    if activities_rows:
        act_df = pd.DataFrame(activities_rows)
        if not act_df.empty and "day" in act_df.columns:
            agg = (
                act_df.groupby("day", as_index=False)
                .agg(
                    training_load_daily=(
                        "training_load",
                        lambda s: float(s.dropna().sum()) if len(s.dropna()) else 0.0,
                    ),
                    activity_duration_min=(
                        "duration_s",
                        lambda s: float(s.dropna().sum()) / 60.0 if len(s.dropna()) else 0.0,
                    ),
                )
            )
            df = df.merge(agg, on="day", how="left")
            df["training_load_daily"] = df["training_load_daily"].fillna(0.0)
            df["activity_duration_min"] = df["activity_duration_min"].fillna(0.0)
        else:
            df["training_load_daily"] = 0.0
            df["activity_duration_min"] = 0.0
    else:
        df["training_load_daily"] = 0.0
        df["activity_duration_min"] = 0.0

    return df


def spearman_corr_with_lag(
    df: pd.DataFrame,
    metric_x: str,
    metric_y: str,
    lag_days: int = 0,
) -> dict[str, Any]:
    """Calcola la correlazione di Spearman tra `metric_x` al giorno t e `metric_y` al giorno t + lag_days.

    Formula:
      rho_S = Cov(rank(X_t), rank(Y_{t + lag})) / (SD(rank(X_t)) * SD(rank(Y_{t + lag})))

    Parametri:
      - df: DataFrame giornaliero ordinato per data
      - metric_x: colonna predittore al giorno t
      - metric_y: colonna risposta al giorno t + lag_days
      - lag_days: ritardo intero >= 0 (tipicamente 0, 1 o 2 giorni)

    Dati minimi e limiti:
      - Richiede almeno 5 coppie (X_t, Y_{t+lag}) entrambe non-NULL.
      - Cattura relazioni monotone (anche non lineari), ma non dimostra nessi causali.
    """
    if lag_days < 0:
        raise ValueError("lag_days deve essere >= 0.")
    if df.empty or metric_x not in df.columns or metric_y not in df.columns:
        return {
            "metric_x": metric_x,
            "metric_y": metric_y,
            "lag_days": lag_days,
            "rho": None,
            "n_days": 0,
            "label": DISCLAIMER_CORRELATION,
        }

    ordered = df.sort_values("day").reset_index(drop=True)
    x_series = pd.to_numeric(ordered[metric_x], errors="coerce")
    y_series = pd.to_numeric(ordered[metric_y], errors="coerce").shift(-lag_days)

    valid = pd.DataFrame({"x": x_series, "y": y_series}).dropna()
    n_days = len(valid)
    if n_days < 5:
        return {
            "metric_x": metric_x,
            "metric_y": metric_y,
            "lag_days": lag_days,
            "rho": None,
            "n_days": int(n_days),
            "label": DISCLAIMER_CORRELATION,
        }

    rx = valid["x"].rank(method="average").to_numpy(dtype=float)
    ry = valid["y"].rank(method="average").to_numpy(dtype=float)
    std_x = float(np.std(rx))
    std_y = float(np.std(ry))
    if std_x == 0.0 or std_y == 0.0:
        rho = 0.0
    else:
        rho = float(np.corrcoef(rx, ry)[0, 1])

    return {
        "metric_x": metric_x,
        "metric_y": metric_y,
        "lag_days": lag_days,
        "rho": round(rho, 3),
        "n_days": int(n_days),
        "label": DISCLAIMER_CORRELATION,
    }


def compute_correlation_matrix(
    df: pd.DataFrame,
    metrics: list[str] | tuple[str, ...],
    lag_days: int = 0,
) -> dict[str, Any]:
    """Calcola la matrice completa di correlazione di Spearman tra tutte le coppie di `metrics` al ritardo `lag_days`."""
    cells: list[dict[str, Any]] = []
    for mx in metrics:
        for my in metrics:
            cells.append(spearman_corr_with_lag(df, mx, my, lag_days=lag_days))
    return {
        "metrics": list(metrics),
        "lag_days": lag_days,
        "label": DISCLAIMER_CORRELATION,
        "cells": cells,
    }


def compute_completeness(
    daily_rows: list[dict[str, Any]],
    metrics: list[str] | tuple[str, ...] | None = None,
) -> dict[str, dict[str, int | float]]:
    """Calcola la completezza (giorni presenti vs totale giorni) per ogni metrica."""
    if metrics is None:
        metrics = (
            "sleep_total_min",
            "sleep_score",
            "rhr_bpm",
            "hrv_night_ms",
            "stress_avg",
            "bb_max",
            "steps",
            "respiration_sleep_brpm",
            "spo2_sleep_avg_pct",
        )
    total = len(daily_rows)
    result: dict[str, dict[str, int | float]] = {}
    for m in metrics:
        present = sum(1 for r in daily_rows if r.get(m) is not None)
        pct = round((present / total) * 100.0, 1) if total > 0 else 0.0
        result[m] = {
            "present_days": present,
            "total_days": total,
            "completeness_pct": pct,
        }
    return result

