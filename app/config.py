"""Configurazione centralizzata dei parametri fisiologici, statistici e di qualità dei dati.

Tutti i parametri usati da `app/baselines.py`, `app/scores.py` e `app/analysis/` sono definiti
qui con commenti espliciti e valori di default documentati.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AnalysisConfig:
    """Parametri configurabili per il motore di analisi e punteggi."""

    # --- Sonno ---
    # Fabbisogno personale di sonno in minuti (default: 480 min = 8h 00m)
    sleep_need_min: float = 480.0
    # Durata minima (in minuti) di un episodio di veglia intra-sonno per contarlo come "risveglio"
    sleep_awakening_min_duration: float = 2.0
    # Soglia SpO2 notturna (%) sotto la quale calcolare i minuti trascorsi in desaturazione
    spo2_threshold_pct: float = 92.0

    # --- Frequenza Cardiaca e Zone ---
    # Frequenza cardiaca massima e a riposo di default (usate per TRIMP se assenti nel profilo/giorno)
    hr_max_default: float = 195.0
    hr_rest_default: float = 52.0
    # Soglie superiori delle 5 zone di FC come frazione di HR_max (fallback se get_heart_rate_zones non è disponibile)
    # Z1: 50-60%, Z2: 60-70%, Z3: 70-80%, Z4: 80-90%, Z5: 90-100%
    hr_zone_fractions: tuple[tuple[float, float], ...] = (
        (0.50, 0.60),
        (0.60, 0.70),
        (0.70, 0.80),
        (0.80, 0.90),
        (0.90, 1.00),
    )

    # --- TRIMP (Training Impulse) ---
    # Costanti del modello esponenziale di Banister: TRIMP = dur_min * HR_r * a * exp(b * HR_r)
    # Default profilo maschile: a = 0.64, b = 1.92 (profilo femminile: a = 0.86, b = 1.67)
    trimp_banister_a: float = 0.64
    trimp_banister_b: float = 1.92
    # Pesi per minuto nelle 5 zone FC (fallback TRIMP a zone di Edwards/Lucia)
    trimp_zone_weights: tuple[float, float, float, float, float] = (1.0, 2.0, 3.0, 4.0, 5.0)

    # --- Carico Acuto / Cronico (EWMA Banister) ---
    # Costanti di tempo in giorni per Acute Training Load (fatica) e Chronic Training Load (fitness)
    atl_tau_days: int = 7
    ctl_tau_days: int = 28

    # --- Analisi intra/post Attività (HRR e Deriva Cardiaca) ---
    # Risoluzione massima accettabile (in secondi tra campioni) per calcolare HRR a 60s e 120s
    hrr_max_sample_gap_s: float = 25.0
    # Durata minima (in minuti) di una seduta costante per calcolare la deriva cardiaca
    cardiac_drift_min_duration_min: float = 30.0
    # Minuti di riscaldamento iniziale e defaticamento finale esclusi dal calcolo della deriva
    cardiac_drift_warmup_min: float = 10.0
    cardiac_drift_cooldown_min: float = 5.0
    # Massimo coefficiente di variazione (CV) della velocità/potenza per ritenere la seduta "costante"
    cardiac_drift_max_speed_cv: float = 0.12

    # --- Recupero, Baseline e Banda HRV ---
    # Giorni minimi di storico per considerare calibrata una baseline a 30 giorni
    baseline_window_days: int = 30
    baseline_min_valid_days: int = 14
    # Banda HRV (ln rMSSD): media mobile a 7 giorni confrontata con media ± k * SD a 60 giorni
    hrv_short_window_days: int = 7
    hrv_long_window_days: int = 60
    hrv_min_long_days: int = 21
    hrv_band_k: float = 0.75

    # --- Tendenze, Anomalie e Punti di Cambio (CUSUM) ---
    # Soglia di |z robusto| (basato su mediana e 1.4826 * MAD) per segnalare un'anomalia giornaliera
    robust_z_anomaly_threshold: float = 2.5
    # Parametri Page's CUSUM standardizzato: slack (drift k) e soglia di allarme (h) in unità di deviazione standard
    cusum_drift_k: float = 0.5
    cusum_threshold_h: float = 4.0

    # --- Analisi Evento Allenamento -> Recupero ---
    # Numero di ricampionamenti bootstrap e livello di confidenza
    bootstrap_iterations: int = 2000
    bootstrap_confidence: float = 0.95
    bootstrap_seed: int = 42


DEFAULT_CONFIG = AnalysisConfig()
