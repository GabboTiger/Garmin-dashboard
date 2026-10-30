"""Client per Garmin Connect: login, gestione token e chiamate di sola lettura.

Nessun dato personale o token viene mai scritto nei log.
"""

from __future__ import annotations

import logging
import os
import random
import time
from getpass import getpass
from pathlib import Path
from typing import Any, Callable

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_DIR = Path("data/tokens")
DEFAULT_DELAY_SECONDS = 1.5
DEFAULT_MAX_REQUESTS = 180


def get_token_dir() -> Path:
    """Restituisce la cartella locale in cui salvare i token OAuth (esclusa da git)."""
    raw = os.getenv("TOKEN_DIR", str(DEFAULT_TOKEN_DIR))
    path = Path(raw).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


class GarminRateLimiter:
    """Limitatore di frequenza con pausa + jitter e tetto massimo di richieste per esecuzione."""

    def __init__(
        self,
        min_delay_s: float = DEFAULT_DELAY_SECONDS,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_delay_s = max(0.0, float(min_delay_s))
        self.max_requests = max(1, int(max_requests))
        self.sleep_fn = sleep_fn
        self.requests_made = 0
        self._last_call_ts: float | None = None

    def wait_before_request(self) -> None:
        """Applica la pausa minima tra richieste e verifica il limite massimo."""
        if self.requests_made >= self.max_requests:
            raise RuntimeError(
                f"Raggiunto il limite massimo di richieste per questa sessione ({self.max_requests}). "
                "Rilancia il comando sync per proseguire in modo incrementale."
            )
        if self._last_call_ts is not None and self.min_delay_s > 0:
            jitter = random.uniform(0.0, 0.35 * self.min_delay_s)
            target_wait = self.min_delay_s + jitter
            elapsed = time.monotonic() - self._last_call_ts
            if elapsed < target_wait:
                self.sleep_fn(target_wait - elapsed)

        self.requests_made += 1
        self._last_call_ts = time.monotonic()


class SafeGarminClient:
    """Wrapper di sola lettura attorno a `garminconnect.Garmin` con rate limiting e gestione MFA."""

    def __init__(
        self,
        token_dir: Path | str | None = None,
        rate_limiter: GarminRateLimiter | None = None,
        client_factory: Callable[..., Any] = Garmin,
    ) -> None:
        self.token_dir = Path(token_dir) if token_dir is not None else get_token_dir()
        self.rate_limiter = rate_limiter or GarminRateLimiter()
        self._client_factory = client_factory
        self._api: Any | None = None

    @property
    def requests_made(self) -> int:
        return self.rate_limiter.requests_made

    def login(
        self,
        email: str | None = None,
        password: str | None = None,
        interactive: bool = True,
    ) -> None:
        """Effettua il login usando prima i token salvati; se assenti o scaduti, chiede le credenziali."""
        token_file = self.token_dir / "garmin_tokens.json"
        self.token_dir.mkdir(parents=True, exist_ok=True)

        # 1. Tentativo con i soli token salvati (senza chiedere password)
        if token_file.exists():
            try:
                logger.info("Tentativo di accesso con i token salvati in %s", self.token_dir)
                api = self._client_factory()
                api.login(str(self.token_dir))
                self._api = api
                logger.info("Accesso con token salvati riuscito.")
                return
            except (GarminConnectAuthenticationError, GarminConnectConnectionError, Exception) as exc:
                logger.warning(
                    "Token salvati non validi o scaduti (%s). Passo al login con credenziali.",
                    type(exc).__name__,
                )

        # 2. Login con credenziali (da argomenti, .env o prompt interattivo)
        resolved_email = (email or os.getenv("GARMIN_EMAIL") or "").strip()
        if not resolved_email or resolved_email == "la-tua-email@esempio.com":
            if not interactive:
                raise GarminConnectAuthenticationError(
                    "GARMIN_EMAIL non configurata nel file .env e modalità non interattiva."
                )
            resolved_email = input("Email Garmin Connect: ").strip()

        resolved_password = password or os.getenv("GARMIN_PASSWORD") or ""
        if not resolved_password:
            if not interactive:
                raise GarminConnectAuthenticationError(
                    "Token non presenti e password non fornita in modalità non interattiva."
                )
            resolved_password = getpass("Password Garmin Connect (non verrà mostrata): ")

        prompt_mfa_fn = (
            (lambda: input("Codice MFA Garmin (inviato via SMS/email/app): ").strip())
            if interactive
            else None
        )

        api = self._client_factory(
            resolved_email,
            resolved_password,
            prompt_mfa=prompt_mfa_fn,
        )
        api.login(str(self.token_dir))
        self._api = api
        logger.info("Login completato e token salvati in %s", self.token_dir)

    def reset_tokens(self) -> None:
        """Rimuove i token salvati localmente."""
        token_file = self.token_dir / "garmin_tokens.json"
        if token_file.exists():
            token_file.unlink()
            logger.info("File token locale rimosso: %s", token_file)

    def _call(self, endpoint_label: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Esegue una chiamata API con pausa, backoff su 429 e log privi di dati personali."""
        if self._api is None:
            raise RuntimeError("Client non autenticato: chiama prima login().")

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            self.rate_limiter.wait_before_request()
            try:
                return fn(*args, **kwargs)
            except GarminConnectTooManyRequestsError:
                if attempt == max_attempts:
                    logger.error("Errore 429 (Troppe richieste) su %s dopo %d tentativi.", endpoint_label, attempt)
                    raise
                backoff = 10.0 * (2 ** (attempt - 1))
                logger.warning("Rate limit (429) su %s: attendo %.1f s (tentativo %d/%d)", endpoint_label, backoff, attempt, max_attempts)
                self.rate_limiter.sleep_fn(backoff)
            except GarminConnectConnectionError as exc:
                if attempt == max_attempts:
                    logger.error("Errore di connessione su %s: %s", endpoint_label, type(exc).__name__)
                    raise
                backoff = 3.0 * attempt
                logger.warning("Errore di rete temporaneo su %s: riprovo tra %.1f s", endpoint_label, backoff)
                self.rate_limiter.sleep_fn(backoff)

    def fetch_day_bundle(self, day_str: str) -> dict[str, Any]:
        """Scarica i 3 payload giornalieri principali per `day_str` ('YYYY-MM-DD'):
        - summary: riepilogo giornaliero (passi, RHR, stress, Body Battery, calorie, minuti intensità)
        - sleep: sonno dettagliato (durata, fasi, punteggio, orari, più eventuale ipnogramma)
        - hrv: variabilità cardiaca notturna
        """
        if self._api is None:
            raise RuntimeError("Client non autenticato: chiama prima login().")

        bundle: dict[str, Any] = {}
        endpoints = (
            ("summary", self._api.get_user_summary),
            ("sleep", self._api.get_sleep_data),
            ("hrv", self._api.get_hrv_data),
        )
        for name, method in endpoints:
            try:
                data = self._call(f"{name}:{day_str}", method, day_str)
                bundle[name] = data
            except GarminConnectAuthenticationError:
                raise
            except Exception as exc:
                logger.warning("Impossibile scaricare %s per %s (%s)", name, day_str, type(exc).__name__)
                bundle[name] = None
        return bundle

    def fetch_activities_range(
        self,
        start_day: str,
        end_day: str,
        fetch_hr_zones: bool = True,
    ) -> list[dict[str, Any]]:
        """Scarica l'elenco delle attività nell'intervallo [start_day, end_day] e, se richiesto, le zone FC."""
        if self._api is None:
            raise RuntimeError("Client non autenticato: chiama prima login().")

        raw_list = self._call(
            f"activities:{start_day}..{end_day}",
            self._api.get_activities_by_date,
            start_day,
            end_day,
        )
        if not isinstance(raw_list, list):
            return []

        enriched: list[dict[str, Any]] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            act_copy = dict(item)
            act_id = act_copy.get("activityId")
            if fetch_hr_zones and act_id is not None and "_hr_zones" not in act_copy:
                try:
                    zones = self._call(
                        f"activity_hr_zones:{act_id}",
                        self._api.get_activity_hr_in_timezones,
                        str(act_id),
                    )
                    act_copy["_hr_zones"] = zones
                except Exception as exc:
                    logger.warning(
                        "Zone FC non disponibili per activity_id=%s (%s)",
                        act_id,
                        type(exc).__name__,
                    )
                    act_copy["_hr_zones"] = None
            enriched.append(act_copy)
        return enriched
