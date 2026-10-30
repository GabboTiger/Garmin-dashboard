# Garmin Personal Dashboard

Dashboard web locale (`127.0.0.1`), trasparente e interamente deterministica per l'analisi dei dati personali Garmin Connect (Recupero, Sforzo, Sonno, fisiologia notturna, architettura del sonno, modelli di carico acuto/cronico, tendenze non parametriche e correlazioni di Spearman).

> **Avvertenze importanti**
> 1. **Libreria non ufficiale**: questo progetto utilizza [`python-garminconnect`](https://github.com/cyberjunky/python-garminconnect), una libreria non ufficiale che interroga le API private di Garmin Connect. Poiché non si tratta di un'API pubblica supportata da Garmin, **può smettere di funzionare in qualsiasi momento** in seguito a modifiche lato server o ai flussi di autenticazione SSO/MFA. I dati già scaricati rimangono comunque sempre consultabili e ricalcolabili localmente grazie alla cache grezza SQLite (`raw_garmin`).
> 2. **Nessuna indicazione medica**: tutti i punteggi (Recupero, Sforzo, Sonno) e le analisi fisiologiche sono euristiche personali basate sulle tue mediane mobili a 30 giorni e su stime da sensore ottico da polso. Non costituiscono in alcun modo pareri o diagnosi mediche.

---

## Caratteristiche principali

- **100% Locale e Offline-First**:
  - Server Flask vincolato esclusivamente a `127.0.0.1`.
  - Nessun CDN esterno, nessun font scaricato dalla rete, nessuna telemetria: `Plotly.js` è salvato localmente in `static/vendor/plotly.min.js`.
  - Database SQLite locale con separazione tra cache grezza (`raw_garmin`) e tabelle normalizzate (`daily`, `activities`).
- **Trasparenza Algoritmica e Formule Matematiche**:
  - Ogni punteggio e ogni metrica avanzata è cliccabile per aprire il pannello a 3 strati (1: Valore, 2: Scomposizione / Baseline / Qualità, 3: Formula matematica renderizzata, parametri attivi e limiti).
  - I valori mancanti restano `NULL` (`"dato assente"` o `"non calcolabile"` con il motivo esplicito, mai `0`).
  - Gli scostamenti dalla baseline usano solo frecce (`▲▼•`) e testo neutro (*"sopra la tua norma"*, *"sotto la tua norma"*, *"in linea"*).
- **Modalità Demo Sintetica (90 giorni)**:
  - Generatore deterministico (`app/synthetic.py`) con segnali noti inseriti (recupero FC post-esercizio, deriva cardiaca, calo di HRV dopo sedute dure, legame sonno → HRV) verificati automaticamente dalla suite `pytest`.

---

## Installazione e Avvio Rapido

### 1. Ambiente virtuale e dipendenze
```bash
python3 -m venv env
./env/bin/pip install -r requirements.txt
```

### 2. Configurazione (`.env`)
Copia il file di esempio `.env.example` in `.env`:
```bash
cp .env.example .env
```
- Per usare i **dati sintetici di prova (90 giorni)**: lascia `DEMO_MODE=1` in `.env`.
- Per usare i **tuoi dati reali Garmin**: imposta `DEMO_MODE=0` e inserisci `GARMIN_EMAIL` e `GARMIN_PASSWORD` in `.env` (il file `.env` e la cartella `.garmin_tokens/` sono ignorati da Git).

### 3. Comandi CLI (`app/cli.py`)
```bash
# Verifica la disponibilità e la risoluzione temporale degli endpoint sul tuo account
./env/bin/python -m app.cli probe-account

# Sincronizza gli ultimi 30 (o 90) giorni da Garmin Connect (incrementale e con rate-limit)
./env/bin/python -m app.cli sync --days 30

# Ricalcola le tabelle daily e activities dalla cache grezza locale senza chiamate di rete
./env/bin/python -m app.cli reparse

# Rigenera il database demo sintetico (data/demo.db)
./env/bin/python -m app.cli seed-demo --days 90
```

### 4. Avvio del Server Web Locale
```bash
./env/bin/python -m app.web
```
Apri nel browser: **`http://127.0.0.1:5000`**

---

## Esecuzione dei Test
```bash
./env/bin/pytest -v
```
Per una roadmap completa di studio del codice, spiegazione dell'architettura, modelli matematici e guida dettagliata a `pytest` (`tmp_path`, `monkeypatch`, test Monte Carlo e test client Flask), consulta **[`GUIDA.md`](GUIDA.md)**.

