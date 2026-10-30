RUOLO E MODALITÀ DI LAVORO
Sei un ingegnere software senior che mi aiuta a costruire, passo per passo, una
dashboard personale per i miei dati Garmin. Sono uno studente di fisica/ingegneria
e voglio capire ciò che costruisci: spiega le scelte in modo breve e chiaro.

Regole:
1. Prima di scrivere codice produci un piano di implementazione e le domande
   aperte. Aspetta la mia approvazione.
2. Lavora una fase alla volta. A fine fase: esegui i test, riassumi cosa hai fatto,
   dimmi come verificarlo e FERMATI in attesa del mio ok.
3. Chiedi conferma prima di installare dipendenze, fare chiamate di rete o
   modificare file fuori dalla cartella del progetto.
4. Non inventare mai nomi di campi o formati delle risposte Garmin: ispeziona
   risposte reali e scrivi i parser su quelle. Se non puoi, chiedimi un campione
   (con i dati personali rimossi).
5. Non chiedermi le credenziali Garmin in chat e non scriverle nel codice: le
   inserisco io nel terminale o nel file .env (ignorato da git).
6. Commit git piccoli, almeno uno per fase.

OBIETTIVO
Dashboard web locale, uso personale, stile Bevel (punteggi di recupero, sforzo e
sonno più dettagli), ma più trasparente: ogni punteggio deve poter essere "aperto"
per mostrare da quali valori e baseline deriva. Per ora NIENTE LLM: solo raccolta,
organizzazione, calcolo e visualizzazione dei dati.

STACK
- Python 3.12+, Flask, SQLite (modulo sqlite3, niente ORM pesanti), pandas/numpy,
  pytest.
- python-garminconnect (libreria non ufficiale) per leggere i dati.
- Frontend: HTML + JavaScript semplice + Plotly.js salvato in static/ (nessun CDN,
  deve funzionare offline). Nessun framework JS pesante.
- Server solo su 127.0.0.1. Nessuna telemetria, nessuna chiamata esterna oltre a
  Garmin.

ARCHITETTURA (separa i livelli)
  app/garmin_client.py  login, token, chiamate alla libreria
  app/sync.py           sincronizzazione incrementale e idempotente
  app/db.py             schema e accesso a SQLite
  app/parsers.py        da risposta grezza a righe normalizzate
  app/baselines.py      baseline personali
  app/scores.py         punteggi (Recupero, Sforzo, Sonno)
  app/analysis.py       tendenze e correlazioni
  app/synthetic.py      generatore di dati sintetici
  app/web.py + templates/ + static/   API Flask e pagine
  tests/

DATI
- Tabella raw_garmin(day, endpoint, fetched_at, payload_json): salva le risposte
  grezze, così posso rifare il parsing senza riscaricare.
- Tabella daily (una riga al giorno): sonno totale e fasi, punteggio sonno se c'è,
  FC a riposo, HRV notturna, stress medio, body battery, passi e altre metriche
  che trovi nelle risposte reali.
- Tabella activities (una riga per attività): tipo, durata, distanza, FC media e
  massima, carico se disponibile.
- I valori mancanti restano NULL e la UI li mostra come "dato assente", mai come 0.
- Sync con limite di richieste e pause tra le chiamate, log chiari, gestione di
  errori di login e MFA. Rilanciarlo non deve duplicare dati.

PUNTEGGI (tutto in scores.py, costanti documentate in cima, formule leggibili)
- Baseline personale: mediana mobile a 30 giorni e scostamento robusto (MAD),
  non soglie assolute.
- Recupero (0-100): HRV notturna e FC a riposo rispetto alla baseline, durata e
  qualità del sonno, stress/body battery se disponibili.
- Sforzo (0-100): dai minuti nelle zone di FC o dal carico delle attività.
- Sonno (0-100): durata rispetto al mio bisogno personale, regolarità, fasi.
- Ogni punteggio restituisce anche la scomposizione (componenti, valori, baseline,
  pesi). Con meno di 14 giorni di storia mostra "calibrazione in corso".
- Sono euristiche personali, non indicazioni mediche: scrivilo nell'interfaccia.

PAGINE
1. Oggi: tre schede-punteggio cliccabili (scomposizione), metriche chiave con
   scostamento dalla baseline, ultime attività.
2. Sonno: durata, fasi, regolarità degli orari, trend a 30 giorni.
3. Recupero: HRV e FC a riposo con banda di baseline, body battery.
4. Attività: elenco, carico settimanale, confronto con le settimane precedenti.
5. Tendenze: intervalli 7/30/90 giorni, sovrapposizione di due metriche.
6. Correlazioni: matrice tra metriche con ritardo selezionabile (0/1/2 giorni),
   Spearman, sempre con il numero di giorni usati e l'etichetta "esplorativa,
   correlazione non è causalità".
Inoltre: indicatore di completezza dei dati per metrica ed esportazione CSV.
UI: tema scuro di default, responsive, testi in italiano, grafici con unità di
misura, niente decorazioni inutili.

MODALITÀ DEMO
Un flag di configurazione usa un generatore di dati sintetici (90 giorni) con
correlazioni note inserite apposta (es. poco sonno, HRV più bassa il giorno
dopo), per sviluppare e testare senza dati reali. I test verificano che il motore
le ritrovi.

SICUREZZA E PRIVACY
.env e token fuori da git, database locale, nessun dato personale nei log.
README con l'avvertenza che la libreria Garmin non è ufficiale e può smettere
di funzionare.

FASI
Fase 0 - Piano di implementazione e domande aperte (nessun codice).
Fase 1 - Scheletro del progetto, schema DB, generatore sintetico, test.
Fase 2 - Sincronizzazione reale: client Garmin, cache grezza, ispezione di
         risposte reali, parser, comando di sync (es. ultimi 30 giorni),
         riconciliazione dello schema con i campi veri.
Fase 3 - Baseline e punteggi, con test.
Fase 4 - API Flask e pagine Oggi, Sonno, Recupero, Attività.
Fase 5 - Tendenze, Correlazioni, qualità dei dati, export CSV, README.

DESIGN SYSTEM (sostituisce le indicazioni UI generiche)
Stile: moderno, semplice, ordinato, ispirato a Bevel e Apple Health. Prima di
costruire le pagine, definisci tutto in un unico file di token CSS (variabili)
e usa solo quelli.

Struttura
- Schede arrotondate (raggio 20-24 px) su sfondo neutro, molto spazio vuoto, una
  sola idea per scheda. Ombre minime; nel tema scuro bordi sottili al posto delle
  ombre.
- Griglia responsive: 1 colonna su telefono, fino a 12 colonne su desktop,
  larghezza massima circa 1200 px. Scala di spaziatura a multipli di 4 px.
- Navigazione minimale (Oggi, Sonno, Recupero, Attività, Tendenze, Correlazioni)
  e selettore di data in alto (giorno precedente/successivo).

Tipografia
- Solo font di sistema (stack: -apple-system, system-ui, ecc.), niente font
  scaricati. Cifre tabulari (font-variant-numeric: tabular-nums).
- Il numero è il protagonista: valore grande (circa 40-56 px), etichetta piccola
  e discreta, unità sempre presente.

Colori
- Un colore per area: Recupero verde acqua, Sforzo arancio, Sonno indaco. Tutto il
  resto è in scala di grigi, così il colore significa qualcosa.
- Punti di partenza (da verificare e correggere per il contrasto): chiaro verde
  acqua #0D9488, arancio #EA580C, indaco #4F46E5; scuro verde acqua #2DD4BF,
  arancio #FB923C, indaco #818CF8. Sfondi neutri: chiaro #F5F5F7 con schede
  bianche, scuro #0B0B0D con schede #17171A.
- Tema chiaro e scuro automatici in base al sistema (prefers-color-scheme).
  Contrasto del testo almeno AA in entrambi.
- La variazione rispetto alla baseline NON usa verde/rosso: è grigia, con freccia
  (▲▼) e testo ("sopra la tua norma", "sotto la tua norma", "in linea"). Non
  esprimere mai un giudizio di "meglio/peggio" solo col colore.

Componenti
- Anelli di punteggio (SVG) in home: tre anelli affiancati con il numero al
  centro, riempimento con animazione breve (disattivata con prefers-reduced-motion).
- Scheda numero: valore grande, etichetta, variazione dalla baseline.
- Scheda andamento: grafico lineare sottile (1,5-2 px) con banda di baseline
  tenue, poche linee guida, assi minimi, tooltip con data, valore e unità.
- Ipnogramma a gradini per il sonno: veglia in grigio, REM, leggero e profondo in
  tre toni di indaco.
- Pannello di dettaglio (a scomparsa laterale o dal basso su telefono) con i tre
  strati: 1) in superficie il numero; 2) un clic apre scomposizione, grafici e
  informazioni aggiuntive; 3) un secondo clic apre "come si calcola" con formule,
  parametri e limiti. Si chiude con Esc e con il tasto indietro del browser,
  focus gestito, utilizzabile da tastiera.
- Stati vuoti e speciali: "dato assente", "calibrazione in corso", caricamento
  (scheletri), errore di sincronizzazione.
- Configura Plotly in modo che segua i token (colori, font, tema chiaro e scuro,
  niente barra degli strumenti di default).

Regole generali: nessun CDN, nessun font esterno, nessuna decorazione inutile;
ogni dato ha unità, e il colore non è mai l'unico portatore di informazione.

NUOVA FASE
Fase 1b - Design system e galleria (dopo la Fase 1): file dei token, componenti
sopra, e una pagina /design che li mostra tutti con dati sintetici, in tema chiaro
e scuro, a larghezza telefono e desktop. Se hai un browser integrato, salva
screenshot di queste quattro combinazioni. FERMATI e aspetta il mio ok prima di
costruire le pagine. Le pagine della Fase 4 usano solo questi componenti.

INIZIA ORA dalla Fase 0: leggi la documentazione di python-garminconnect, poi
scrivi il piano e le domande aperte, e aspetta il mio ok.

STEP 2
AGGIORNAMENTO DEL PROGETTO: ANALISI AVANZATE ALGORITMICHE
Questa è un'estensione del piano esistente. Prima di scrivere codice, aggiorna il
piano con le fasi nuove qui sotto e aspetta il mio ok. Valgono tutte le regole di
lavoro già date (una fase alla volta, test, stop a fine fase, niente campi
inventati). Niente LLM: tutto deterministico.

DATI DA AGGIUNGERE ALLA SINCRONIZZAZIONE
Verifica quali di questi sono davvero disponibili nel mio account e salva le
risposte grezze nella cache raw: serie temporali di ogni attività
(get_activity_details), tempo nelle zone (get_activity_hr_in_timezones), battito
della giornata (get_heart_rates), sonno con le fasi (get_sleep_data), HRV
(get_hrv_data), respirazione, SpO2, body battery, training readiness.
- Ispeziona le risposte reali e dimmi risoluzione temporale e campi disponibili
  per ciascuna. Non assumere né inventare nulla.
- Scarica i dettagli delle attività solo per quelle non già in cache, con pause
  tra le chiamate.

ANALISI DA IMPLEMENTARE (funzioni pure in app/analysis/, ognuna con docstring
che descrive formula, parametri, dati minimi e limiti)
Attività: recupero della FC a 60 s e 120 s dopo la fine (con flag di qualità e
"non calcolabile" se la risoluzione o il defaticamento non lo permettono); tempo
per zona e distribuzione settimanale; TRIMP; carico acuto/cronico con medie
esponenziali a 7 e 28 giorni e modello fitness/fatica; deriva cardiaca su sedute
costanti di almeno 30 minuti; efficienza velocità/FC.
Sonno: latenza, risvegli, efficienza, percentuali per fase, tempo al primo
profondo e REM, cicli stimati, ipnogramma; regolarità (variabilità degli orari,
metà del sonno, differenza feriali/weekend, indice di regolarità); FC minima
notturna e suo orario; HRV e respirazione durante la notte; tempo sotto soglia
SpO2 configurabile; debito di sonno rispetto a un fabbisogno personale
configurabile.
Recupero e tendenze: banda HRV (ln rMSSD, media a 7 giorni contro media ± k·SD a
60 giorni, k configurabile) e coefficiente di variazione; per ogni metrica media
mobile, EWMA, pendenza Theil-Sen con test di Mann-Kendall, punti di cambio con
CUSUM, anomalie con z robusto, ciclo settimanale; ricarica notturna del body
battery; effetto di sedute dure e facili sul recupero della notte successiva con
intervallo bootstrap e numero di eventi.

REGOLE DI QUALITÀ
- Ogni risultato riporta n di campioni o giorni, un flag di qualità e le
  assunzioni usate. Con dati insufficienti restituisci "non calcolabile" e il
  motivo, mai un numero.
- Parametri (zone FC, fabbisogno di sonno, k della banda HRV, soglia SpO2,
  costanti TRIMP) in un file di configurazione unico con valori di default
  commentati.
- Ogni metrica ha nell'interfaccia un pannello "come si calcola" con formula,
  parametri e limiti. Avviso fisso: stime da dispositivo da polso, non indicazioni
  mediche.

ESTENSIONE DEI DATI SINTETICI E DEI TEST
Il generatore sintetico produce anche serie di FC durante e dopo le attività e
ipnogrammi, con segnali noti inseriti apposta (un recupero FC noto, una deriva
cardiaca nota, un calo di HRV dopo sedute dure). I test verificano che le funzioni
li ritrovino entro una tolleranza dichiarata.

FASI NUOVE (dopo la Fase 2 del piano)
Fase 2b - Sincronizzazione dei dati aggiuntivi, ispezione e parser.
Fase 3b - Analisi Attività (HRR, zone, TRIMP, carichi) con test.
Fase 3c - Analisi Sonno (architettura, regolarità, fisiologia notturna) con test.
Fase 3d - Banda HRV, trend, analisi evento allenamento→recupero, con test.
Le pagine Attività, Sonno, Recupero, Tendenze e Correlazioni mostrano poi queste
metriche (grafici, valore, qualità, "come si calcola").
Per ora dammi solo il piano aggiornato e le domande aperte.