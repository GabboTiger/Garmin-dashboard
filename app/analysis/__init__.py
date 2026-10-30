"""Package `app.analysis` — esporta le funzioni di correlazione, completezza e analisi algoritmiche."""

from __future__ import annotations

from app.analysis.activity import (
    compute_acute_chronic_load,
    compute_cardiac_drift,
    compute_heart_rate_recovery,
    compute_speed_hr_efficiency,
    compute_trimp,
    compute_weekly_zone_distribution,
    extract_activity_timeseries,
)
from app.analysis.common import WRIST_SENSOR_DISCLAIMER, make_not_calculable, make_result
from app.analysis.correlations import (
    DISCLAIMER_CORRELATION,
    build_daily_dataframe,
    compute_completeness,
    compute_correlation_matrix,
    spearman_corr_with_lag,
)
from app.analysis.sleep import (
    compute_nocturnal_physiology_and_debt,
    compute_sleep_architecture,
    compute_sleep_regularity,
    extract_hypnogram_intervals,
)

__all__ = [
    "DISCLAIMER_CORRELATION",
    "WRIST_SENSOR_DISCLAIMER",
    "build_daily_dataframe",
    "compute_acute_chronic_load",
    "compute_cardiac_drift",
    "compute_completeness",
    "compute_correlation_matrix",
    "compute_heart_rate_recovery",
    "compute_nocturnal_physiology_and_debt",
    "compute_sleep_architecture",
    "compute_sleep_regularity",
    "compute_speed_hr_efficiency",
    "compute_trimp",
    "compute_weekly_zone_distribution",
    "extract_activity_timeseries",
    "extract_hypnogram_intervals",
    "make_not_calculable",
    "make_result",
    "spearman_corr_with_lag",
]
