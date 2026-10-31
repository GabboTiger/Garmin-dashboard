# Come funzionano i test in Python (`pytest`) e cosa verificano

Questa guida spiega passo per passo i concetti usati in `pytest.ini`, `tests/test_db.py` e `tests/test_synthetic.py`, pensata per chi ha basi di fisica/ingegneria e programmazione ma non ha mai usato un framework di test automatico.

---

## 1. Cos'è `pytest` e perché non usiamo un semplice script con `print()`?

Quando scrivi codice scientifico spesso fai una verifica "a occhio": lanci uno script, stampi due numeri con `print()` e controlli se sembrano sensati. Il problema è che quando il progetto cresce dovresti ricontrollare tutto a mano a ogni modifica.

`pytest` automatizza questo processo:
1. **Scopre i test da solo**: cerca tutti i file che iniziano con `test_` (dentro la cartella `tests/`, come indicato in `pytest.ini`) ed esegue tutte le funzioni che iniziano con `def test_...()`.
2. **Verifica le condizioni con `assert`**: in Python l'istruzione `assert condizione` non fa nulla se la condizione è vera (`True`), ma solleva un errore (`AssertionError`) se è falsa (`False`). Quando `pytest` intercetta quell'errore, segna il test come `FAILED` e ti mostra esattamente i valori che hanno causato il fallimento.
3. **Isola ogni test**: se hai 5 test, ognuno parte da uno stato pulito e non "sporca" i dati degli altri.

### Cosa fa `pytest.ini`?
```ini
[pytest]
pythonpath = .
testpaths = tests
```
- `pythonpath = .` dice a Python di aggiungere la cartella principale del progetto (`.`) ai percorsi in cui cercare i moduli. Senza questa riga, scrivendo `from app import db` dentro `tests/test_db.py`, Python cercherebbe `app` solo dentro `tests/` e darebbe `ModuleNotFoundError`.
- `testpaths = tests` dice a `pytest` dove cercare i test quando lanci `./env/bin/pytest`.

---

## 2. Il "trucco" di `tmp_path`: le *fixture* di `pytest`

In `tests/test_db.py` avrai notato la firma della funzione:
```python
def test_init_and_raw_upsert_idempotent(tmp_path: Path) -> None:
```
Chi passa l'argomento `tmp_path` a questa funzione, visto che noi non la chiamiamo mai direttamente?

Lo fa `pytest` tramite un meccanismo chiamato **fixture** (letteralmente "apparecchiatura di prova", come il banco di misura in laboratorio):
- `pytest` legge il nome del parametro della funzione (`tmp_path`).
- Riconosce che `tmp_path` è una fixture integrata.
- Prima di eseguire il test, crea una **cartella temporanea nuova e vuota** nel sistema operativo (es. `/tmp/pytest-of-user/test_init_...0/`) e ne passa il percorso (`Path`) alla funzione.
- Così `conn = db.connect(tmp_path / "test.db")` crea un file SQLite usa-e-getta solo per quel singolo test, senza mai toccare il tuo database vero in `data/`.

---

## 3. Cosa verifica `tests/test_db.py`

### a) `test_init_and_raw_upsert_idempotent` — L'idempotenza (`UPSERT`)
In matematica e informatica un'operazione è **idempotente** se applicarla una o più volte produce lo stesso stato finale (senza duplicare righe).
Nel database salviamo le risposte grezze di Garmin con chiave primaria composta `(day, endpoint)`:
```sql
PRIMARY KEY (day, endpoint)
```
Nel test chiamiamo due volte `db.upsert_raw` per lo stesso giorno (`2026-10-01`) e lo stesso endpoint (`sleep`), prima con `27000` secondi e poi con `28800`:
```python
db.upsert_raw(conn, "2026-10-01", "sleep", payload1, ...)
db.upsert_raw(conn, "2026-10-01", "sleep", payload2, ...)
```
Dietro le quinte, `app/db.py` esegue un **UPSERT** (`INSERT ... ON CONFLICT(day, endpoint) DO UPDATE SET ...`):
- Se la coppia `('2026-10-01', 'sleep')` non esiste, fa una `INSERT`.
- Se esiste già, **non** crea una seconda riga e non va in errore, ma aggiorna la riga esistente con il nuovo JSON.
Il test verifica con `assert cur.fetchone()["cnt"] == 1` che ci sia una sola riga e con `assert saved == payload2` che contenga il valore più recente.

### b) `test_daily_preserves_nulls_and_merges_on_upsert` — `NULL` vs `0` e `COALESCE`
In un'analisi fisica/fisiologica un **dato non misurato** (`NULL` in SQL, `None` in Python) è completamente diverso da `0` (avere HRV = 0 ms o FC a riposo = 0 bpm falserebbe tutte le mediane e le correlazioni).

Inoltre, la tabella `daily` viene popolata da chiamate diverse (una scarica il sonno, una l'HRV, una lo stress). Nel test:
1. Prima salviamo solo `sleep_total_min = 450.0` per il `2026-10-10`. Verifichiamo che `hrv_night_ms` sia `None` (e non `0`).
2. Poi facciamo un secondo `upsert_daily` sullo stesso giorno passando solo `hrv_night_ms = 64.5`.
In `app/db.py` usiamo la funzione SQL `COALESCE(excluded.colonna, daily.colonna)`, che restituisce il primo valore non-`NULL`: così il nuovo `hrv_night_ms` viene scritto, ma `sleep_total_min` (che nella seconda chiamata era `None`) **non viene cancellato** e resta `450.0`.

---

## 4. Cosa verifica `tests/test_synthetic.py`

Qui testiamo il motore matematico prima ancora di avere i dati reali di Garmin, usando un **esperimento controllato (Monte Carlo sintetico)** con seme fisso (`seed=42`, così i numeri casuali sono identici a ogni esecuzione).

### Come funziona la correlazione di Spearman con ritardo (`lag_days`)
In `app/analysis.py` vogliamo misurare se una variabile $X$ al giorno $t$ (es. minuti di sonno) è associata a una variabile $Y$ al giorno $t + \text{lag}$ (es. HRV della notte successiva, con $\text{lag} = 1$).

1. **Allineamento temporale (`shift`)**:
   Con `y_series = ordered[metric_y].shift(-lag_days)`, per `lag_days = 1` spostiamo la colonna $Y$ indietro di una riga. In questo modo sulla riga del giorno $t$ abbiamo:
   $$X(t) \quad \text{accoppiato con} \quad Y(t+1)$$
2. **Rimozione dei dati mancanti (`dropna`)**:
   Se in una coppia $(X(t), Y(t+1))$ uno dei due valori è `NULL`, quella coppia viene scartata (senza mai sostituire con `0`), e contiamo quante coppie valide restano in `n_days`.
3. **Coefficiente di Spearman ($\rho$)**:
   Invece della correlazione lineare di Pearson (sensibile agli outlier e valida solo per relazioni rette), trasformiamo i valori $X$ e $Y$ nei loro **ranghi** (posizione in classifica da $1$ a $N$, con `rank(method="average")`) e calcoliamo il coefficiente di Pearson sui ranghi:
   $$\rho_S = \frac{\text{Cov}(\text{rank}(X), \text{rank}(Y))}{\sigma_{\text{rank}(X)} \, \sigma_{\text{rank}(Y)}} \in [-1, +1]$$

### Cosa controlla `test_synthetic_known_correlations_recovered`
Nel generatore `app/synthetic.py` abbiamo cablato nel modello le equazioni:
- $\text{HRV}(t) = 62 + 0.095 \cdot (\text{Sonno}(t-1) - 440) - 0.055 \cdot (\text{Carico}(t-1) - 60) + \varepsilon$
- $\text{RHR}(t) = 52 - 0.015 \cdot (\text{Sonno}(t-1) - 440) + 0.032 \cdot (\text{Carico}(t-1) - 60) + \varepsilon$
- $\text{BodyBattery}_{\max}(t) = 95 - 0.85 \cdot (\text{Stress}(t) - 25) + \dots + \varepsilon$

Il test verifica che `spearman_corr_with_lag` ritrovi esattamente questi legami dai dati generati:
- `sleep_total_min(t)` vs `hrv_night_ms(t+1)` ($\text{lag}=1$) ha $\rho > 0.45$, ed è nettamente più forte rispetto a $\text{lag}=0$ (`assert sleep_to_next_hrv["rho"] > sleep_to_same_hrv["rho"] + 0.25`), perché il sonno della notte $t$ precede l'HRV della notte $t+1$.
- `training_load_daily(t)` vs `rhr_bpm(t+1)` ($\text{lag}=1$) ha $\rho > 0.40$ (uno sforzo elevato oggi alza la FC a riposo domani).
- `stress_avg(t)` vs `bb_max(t)` ($\text{lag}=0$) ha $\rho < -0.55$ (più stress nella giornata corrisponde a una Body Battery massima più bassa).

