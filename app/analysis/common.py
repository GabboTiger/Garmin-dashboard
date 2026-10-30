"""Costruttori standard e regole di qualità per le analisi algoritmiche (Step 2).

Ogni funzione di analisi restituisce un dizionario strutturato con:
- value: valore numerico o dizionario dei risultati (SEMPRE None se non calcolabile, mai 0 inventato)
- unit: unità di misura esplicita
- calculable: booleano
- label: "non calcolabile" quando calculable=False, altrimenti stringa descrittiva
- reason: motivo esatto per cui il dato non è calcolabile (o None)
- n_samples: numero di campioni o giorni usati
- quality_flag: "good" | "low_resolution" | "partial" | "cooldown_detected" | "insufficient_data"
- assumptions: lista delle assunzioni matematiche/fisiologiche usate
- how_it_works: dizionario per il 3° strato del pannello UI (title, formula_latex, parameters, limits, disclaimer)
"""

from __future__ import annotations

from typing import Any

WRIST_SENSOR_DISCLAIMER = "Stime derivate da sensore ottico da polso: non costituiscono indicazioni o diagnosi mediche."


def make_result(
    *,
    value: Any,
    unit: str,
    n_samples: int,
    quality_flag: str = "good",
    assumptions: list[str] | None = None,
    how_it_works: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Costruisce un risultato valido e calcolabile conforme alle Regole di Qualità."""
    hw = dict(how_it_works or {})
    hw.setdefault("disclaimer", WRIST_SENSOR_DISCLAIMER)
    res: dict[str, Any] = {
        "value": value,
        "unit": unit,
        "calculable": True,
        "label": "calcolabile",
        "reason": None,
        "n_samples": int(n_samples),
        "quality_flag": quality_flag,
        "assumptions": list(assumptions or []),
        "how_it_works": hw,
    }
    if extra:
        res.update(extra)
    return res


def make_not_calculable(
    *,
    unit: str,
    reason: str,
    n_samples: int = 0,
    quality_flag: str = "insufficient_data",
    assumptions: list[str] | None = None,
    how_it_works: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Costruisce un risultato 'non calcolabile' con motivo esplicito (value=None)."""
    hw = dict(how_it_works or {})
    hw.setdefault("disclaimer", WRIST_SENSOR_DISCLAIMER)
    res: dict[str, Any] = {
        "value": None,
        "unit": unit,
        "calculable": False,
        "label": "non calcolabile",
        "reason": reason,
        "n_samples": int(n_samples),
        "quality_flag": quality_flag,
        "assumptions": list(assumptions or []),
        "how_it_works": hw,
    }
    if extra:
        res.update(extra)
    return res

