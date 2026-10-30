"""Server Flask locale (127.0.0.1) per la dashboard Garmin (Fasi 1b, 4 e 5)."""

from __future__ import annotations

import csv
import io
import os
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request

from app import analysis, baselines, db, scores, sync, synthetic
from app.garmin_client import GarminRateLimiter, SafeGarminClient

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

# Guida e spiegazione in italiano semplice + approfondimento tecnico/formula di tutte le voci (mostrata nei rettangoli, nel drawer e nella pagina Guida)
METRIC_GUIDE: dict[str, dict[str, str]] = {
    "Recupero": {
        "area": "Recupero & Sistema Nervoso",
        "short": "Indica quanto il tuo corpo è pronto ad affrontare uno sforzo oggi (0–100), confrontando HRV, FC a riposo, sonno e stress con la tua norma a 30 giorni.",
        "meaning": (
            "Il punteggio di Recupero (0–100) stima lo stato del sistema nervoso autonomo al risveglio. "
            "Combina 5 segnali pesati rispetto alla tua baseline personale a 30 giorni: HRV notturna (35%), FC a riposo invertita (25%), "
            "durata/qualità del sonno (25%) e Body Battery/Stress (15%). "
            "Valori ≥ 70 indicano ottimo recupero (via libera per sedute intense); 45–69 recupero normale; < 45 fatica o stress fisiologico."
        ),
        "formula_latex": r"R = \frac{\sum_{i} w_i \cdot S_i}{\sum_{i} w_i}, \quad S_{\text{HRV}} = \text{clip}\!\left(50 + 25 \cdot z_{\text{HRV}},\, 0,\, 100\right), \quad S_{\text{RHR}} = \text{clip}\!\left(50 - 25 \cdot z_{\text{RHR}},\, 0,\, 100\right)",
        "tech_note": "Ogni sotto-punteggio S_i mappa lo Z-score robusto a 30 giorni (z = 0 → 50/100; z = +2 → 100/100 per HRV; invertito per RHR). Se una metrica manca in un giorno, i pesi w_i vengono rinormalizzati automaticamente.",
        "reference": "Modello autonomico multiparametrico ispirato a Plews et al. (2013), Eur J Appl Physiol.",
        "page_endpoint": "page_recovery",
        "page_label": "Approfondisci in Recupero →",
    },
    "Sforzo": {
        "area": "Attività & Carico",
        "short": "Misura il carico cardiovascolare e di movimento accumulato nella giornata (0–100) da allenamenti (TRIMP / Zone FC) e passi.",
        "meaning": (
            "Il punteggio di Sforzo (0–100) quantifica lo stress fisico della giornata su una scala saturante esponenziale. "
            "Integra i minuti trascorsi nelle 5 zone di frequenza cardiaca (pesando esponenzialmente le zone alte Z4–Z5 tramite il TRIMP) "
            "e il movimento di base (passi giornalieri). Un valore di 30–50 corrisponde a una giornata attiva o seduta leggera; 60–80 a un allenamento impegnativo; > 80 a un carico molto elevato."
        ),
        "formula_latex": r"\text{Sforzo} = 100 \cdot \left(1 - \exp\!\left(-\frac{L_{\text{TRIMP}} + 18 \cdot (\text{Passi}/10000)}{140}\right)\right), \quad L_{\text{zone}} = \frac{1}{60}\sum_{k=1}^{5} t_{Z_k} \cdot w_k",
        "tech_note": "Pesi delle 5 zone cardiache (modello di Edwards modificato): w = [1.0, 2.0, 3.0, 4.5, 6.5] per minuto trascorso in Z1..Z5, con costante di scala k = 140 TRIMP.",
        "reference": "Edwards (1993) Heart Zone Training & Banister (1991) Impulse-Response Model.",
        "page_endpoint": "page_activities",
        "page_label": "Approfondisci in Attività →",
    },
    "Sonno": {
        "area": "Sonno & Architettura",
        "short": "Valuta il ristoro notturno (0–100) unendo durata rispetto al tuo bisogno (8h), quota di sonno profondo/REM, continuità e regolarità.",
        "meaning": (
            "Il punteggio del Sonno (0–100) misura la qualità complessiva del riposo: 50% durata rispetto al fabbisogno personale (480 min), "
            "25% quota rigenerativa (Profondo N3 + REM, ideale ~40–45% del totale) e 25% regolarità degli orari rispetto agli ultimi 7 giorni."
        ),
        "formula_latex": r"\text{Sonno} = 0.50 \cdot S_{\text{dur}} + 0.25 \cdot S_{\text{fasi}} + 0.25 \cdot S_{\text{reg}}, \quad S_{\text{dur}} = \min\!\left(100,\, \frac{T_{\text{sonno}}}{480}\times 100\right)",
        "tech_note": "S_fasi raggiunge 100/100 quando (Profondo + REM) ≥ 42% del sonno totale. S_reg penalizza linearmente le deviazioni dell'orario di addormentamento superiori a ±15 min rispetto alla mediana a 7 giorni.",
        "reference": "Buysse (2014) Sleep Health Framework (Sleep, 37(1), 9–17).",
        "page_endpoint": "page_sleep",
        "page_label": "Approfondisci in Sonno →",
    },
    "HRV Notturna": {
        "area": "Recupero & Sistema Nervoso",
        "short": "Variabilità tra un battito e l'altro nel sonno (rMSSD in ms): più è alta rispetto alla tua norma, più il sistema parasimpatico è rilassato e recuperato.",
        "meaning": (
            "L'HRV (Heart Rate Variability, misurata come rMSSD in millisecondi) misura le micro-variazioni di tempo tra i battiti cardiaci durante la notte. "
            "Non conta il valore assoluto confrontato con altre persone, ma lo scostamento dalla tua mediana a 30 giorni: "
            "un'HRV stabile o sopra la tua norma indica buon adattamento; un calo netto per più giorni segnala fatica residua, stress, alcol o inizio di malessere."
        ),
        "formula_latex": r"\text{rMSSD} = \sqrt{\frac{1}{N-1}\sum_{i=1}^{N-1}\left(\text{RR}_{i+1} - \text{RR}_i\right)^2}\quad[\text{ms}]",
        "tech_note": "RR_i è l'intervallo temporale in ms tra due battiti consecutivi (onda R). La radice quadrata delle differenze successive (rMSSD) isola selettivamente la modulazione parasimpatica (nervo vago) respiratoria.",
        "reference": "Task Force ESC/NASPE (1996) & Shaffer & Ginsberg (2017), Front Public Health.",
        "page_endpoint": "page_recovery",
        "page_label": "Vedi serie HRV in Recupero →",
    },
    "FC a Riposo": {
        "area": "Recupero & Sistema Nervoso",
        "short": "Frequenza cardiaca minima a riposo (bpm): valori più bassi o in linea con la tua norma indicano cuore efficiente e buon recupero.",
        "meaning": (
            "La Frequenza Cardiaca a Riposo (RHR, in battiti al minuto) viene rilevata nelle fasi di quiete profonda/risveglio. "
            "È inversamente legata allo stato di recupero: un aumento di +3/+6 bpm sopra la tua mediana a 30 giorni accompagna spesso allenamenti pesanti non ancora smaltiti, disidratazione, cena tardiva o febbre."
        ),
        "formula_latex": r"z_{\text{RHR}} = \frac{\text{RHR}_t - m_{30}(\text{RHR})}{1.4826 \cdot \text{MAD}_{30}(\text{RHR})}",
        "tech_note": "Poiché la polarità fisiologica della RHR è inversa (più bassa = miglior efficienza vagale e maggior volume sistolico), uno Z-score negativo (z ≤ −0.75) viene classificato come 'In miglioramento (sotto la tua norma)'.",
        "reference": "Buchheit (2014), Monitoring training status with HR measures, Front Physiol.",
        "page_endpoint": "page_recovery",
        "page_label": "Vedi trend RHR in Recupero →",
    },
    "Sonno Totale": {
        "area": "Sonno & Architettura",
        "short": "Minuti effettivi di sonno (esclusi i risvegli notturni) confrontati con il tuo fabbisogno personale di 480 min (8 ore).",
        "meaning": (
            "Tempo totale effettivamente dormito tra l'addormentamento e il risveglio, sottraendo i periodi di veglia intra-notturna. "
            "Costituisce la base del recupero muscolare, ormonale e cognitivo."
        ),
        "formula_latex": r"T_{\text{sonno}} = T_{\text{profondo}} + T_{\text{leggero}} + T_{\text{REM}} = T_{\text{letto}} - T_{\text{veglia}}",
        "tech_note": "Calcolato in minuti (e convertito in ore decimali nei grafici) dalla somma degli stadi NREM (leggero + profondo) e REM rilevati dall'ipnogramma.",
        "page_endpoint": "page_sleep",
        "page_label": "Vedi dettagli in Sonno →",
    },
    "Body Battery Max": {
        "area": "Recupero & Sistema Nervoso",
        "short": "Livello massimo di riserva energetica (0–100 pt) raggiunto al mattino dopo la ricarica notturna.",
        "meaning": (
            "La Body Battery (0–100) è un indice sintetico Garmin/Firstbeat che sale durante il riposo (quando domina il sistema parasimpatico e l'HRV è alta) "
            "e scende durante lo sforzo fisico e lo stress mentale. Il valore Max indica il picco raggiunto al risveglio."
        ),
        "formula_latex": r"\text{BB}_{\max} = \max_{t \in [00:00,\,24:00]} \text{BB}(t) \in [5,\,100]",
        "tech_note": "Derivato dal modello Firstbeat Analytics che integra continuamente HRV istantanea, frequenza cardiaca e movimento accelerometrico.",
        "page_endpoint": "page_recovery",
        "page_label": "Vedi Body Battery in Recupero →",
    },
    "Stress Medio": {
        "area": "Recupero & Sistema Nervoso",
        "short": "Attivazione fisiologica media nelle 24h (0–100 pt, calcolata da FC e HRV a riposo): più è basso (< 30), più la giornata è stata rilassata.",
        "meaning": (
            "Lo Stress Garmin (0–100) misura l'attivazione del sistema nervoso simpatico fuori dagli allenamenti: "
            "0–25 stato di riposo, 26–50 stress basso, 51–75 stress medio, 76–100 stress elevato. Anche digestione impegnativa, lavoro intenso o recupero post-gara alzano lo stress."
        ),
        "tech_note": "Durante il movimento fisico intenso la misurazione dello stress viene sospesa dall'orologio per non confondere l'attivazione da esercizio con lo stress a riposo; l'effetto post-esercizio (EPOC) rientra invece nel calcolo.",
        "page_endpoint": "index",
        "page_label": "Vedi grafico BB & Stress in Oggi →",
    },
    "Passi Giornalieri": {
        "area": "Attività & Carico",
        "short": "Volume totale di locomozione quotidiana (NEAT + sport): contribuisce al dispendio energetico e alla salute metabolica.",
        "meaning": (
            "Numero totale di passi registrati dall'accelerometro da polso nelle 24 ore (camminata quotidiana + corsa/tennis). "
            "Misura il movimento complessivo fuori e dentro l'allenamento strutturato."
        ),
        "page_endpoint": "page_trends",
        "page_label": "Vedi trend Passi in Tendenze →",
    },
    "Respirazione Notte": {
        "area": "Sonno & Architettura",
        "short": "Atti respiratori al minuto (brpm) durante il sonno: è un parametro molto stabile (12–16 atti/m); aumenti insoliti indicano carico o vie aeree affaticate.",
        "meaning": (
            "Frequenza respiratoria media notturna derivata dalla modulazione dell'onda ottica (PPG) e dell'HRV (aritmia sinusale respiratoria). "
            "Negli adulti sani a riposo varia pochissimo da una notte all'altra; un rialzo di +1.5/+2 atti/min sopra la tua mediana può segnalare affaticamento o infezione respiratoria."
        ),
        "page_endpoint": "page_sleep",
        "page_label": "Vedi fisiologia notturna in Sonno →",
    },
    "SpO2 Notturna": {
        "area": "Sonno & Architettura",
        "short": "Saturazione media di ossigeno nel sangue durante il sonno (%): valori normali da polso sono tipicamente ≥ 94–95%.",
        "meaning": (
            "Stima ottica (Pulse Ox) della percentuale di emoglobina legata all'ossigeno nel sonno. "
            "Da polso può subire cali temporanei se il braccio è compresso; valori medi stabili ≥ 94% con pochi minuti < 92% sono fisiologici a bassa quota."
        ),
        "formula_latex": r"T_{\text{SpO}_2 < 92\%} = \sum_{k:\,\text{SpO}_2(t_k) < 92} \Delta t_k \quad[\text{min}]",
        "tech_note": "La dashboard integra tutti gli intervalli temporali Δt_k della serie `wellnessEpochSPO2DataDTOList` (risoluzione 1 minuto) in cui la lettura scende sotto la soglia configurata (default 92%).",
        "page_endpoint": "page_sleep",
        "page_label": "Vedi SpO2 in Sonno →",
    },
    "ln(rMSSD) 7 giorni": {
        "area": "Recupero & Sistema Nervoso",
        "short": "Logaritmo naturale dell'HRV mediato su 7 giorni: linearizza l'HRV riducendo il peso dei picchi isolati (metodo scientifico di Plews).",
        "meaning": (
            "In fisiologia dello sport l'HRV ha una distribuzione asimmetrica (skewed): si usa il logaritmo naturale ln(rMSSD) mediato su 7 giorni "
            "e lo si confronta con la tua banda individuale a 60 giorni (media ± 0.75·SD). Finché la media a 7g resta dentro la banda, il carico è ben assimilato."
        ),
        "formula_latex": r"y_t = \ln(\text{HRV}_t), \quad \bar{y}_{7,t} = \frac{1}{7}\sum_{i=0}^{6} y_{t-i}, \quad \text{Banda}_{60} = \left[\mu_{60} - 0.75\sigma_{60},\; \mu_{60} + 0.75\sigma_{60}\right]",
        "tech_note": "Il moltiplicatore k = 0.75·SD definisce la Smallest Worthwhile Change (SWC) individuale su 60 giorni. Per riportare i limiti della banda in millisecondi si applica l'esponenziale exp(μ_60 ± 0.75·σ_60).",
        "reference": "Plews et al. (2013), Training adaptation and heart rate variability in elite endurance athletes, Int J Sports Physiol Perform.",
        "page_endpoint": "page_recovery",
        "page_label": "Vedi Banda HRV in Recupero →",
    },
    "CV HRV (7 giorni)": {
        "area": "Recupero & Sistema Nervoso",
        "short": "Coefficiente di Variazione dell'HRV negli ultimi 7 giorni (%): più è basso (es. 5–12%), più il tuo sistema nervoso è stabile giorno dopo giorno.",
        "meaning": (
            "Rapporto tra deviazione standard e media dell'HRV negli ultimi 7 giorni (CV = SD / Media × 100). "
            "Un CV basso indica stabilità autonomica; un CV molto alto (> 15–20%) indica forti oscillazioni giorno-dopo-giorno dovute a carichi pesanti o sonno irregolare."
        ),
        "formula_latex": r"\text{CV}_{7g} = \frac{s_{7g}(\text{HRV})}{\overline{\text{HRV}}_{7g}} \times 100\quad[\%]",
        "tech_note": "Spesso un aumento del CV_7g precede il calo della media dell'HRV, segnalando instabilità nell'adattamento ai carichi.",
        "reference": "Flatt & Esco (2016), Evaluating individual training adaptation using HRV coefficient of variation.",
        "page_endpoint": "page_recovery",
        "page_label": "Vedi CV HRV in Recupero →",
    },
    "Ricarica Body Battery": {
        "area": "Recupero & Sistema Nervoso",
        "short": "Punti di energia guadagnati tra il minimo serale e il massimo del mattino: misura quanto la notte ti ha effettivamente ricaricato.",
        "meaning": (
            "Differenza netta (ΔBB) di Body Battery accumulata durante il sonno. Una ricarica di +35/+60 punti indica una notte altamente rigenerante; "
            "< +25 punti indica che il corpo ha lavorato tutta la notte per smaltire stress, digestione o fatica muscolare."
        ),
        "formula_latex": r"\Delta\text{BB}_{\text{notte}} = \text{BB}_{\text{caricata}} \approx \text{BB}_{\max,\text{mattina}} - \text{BB}_{\min,\text{sera}}\quad[\text{pt}]",
        "page_endpoint": "page_recovery",
        "page_label": "Vedi Ricarica BB in Recupero →",
    },
    "Velocità Ricarica BB": {
        "area": "Recupero & Sistema Nervoso",
        "short": "Punti di Body Battery recuperati per ogni ora di sonno (pt/ora): misura l'efficienza oraria del tuo riposo.",
        "meaning": (
            "Divide i punti di Body Battery ricaricati per le ore effettive di sonno. Permette di confrontare la qualità rigenerativa del sonno "
            "indipendentemente dal fatto che tu abbia dormito 6 ore o 9 ore (valori buoni: ≥ 5–7 pt/ora)."
        ),
        "formula_latex": r"v_{\text{BB}} = \frac{\Delta\text{BB}_{\text{notte}}}{T_{\text{sonno}}\;[\text{ore}]}\quad\left[\frac{\text{pt}}{\text{ora}}\right]",
        "page_endpoint": "page_recovery",
        "page_label": "Vedi Velocità Ricarica in Recupero →",
    },
    "Efficienza Sonno": {
        "area": "Sonno & Architettura",
        "short": "Percentuale del tempo a letto trascorsa effettivamente dormendo: ≥ 85–90% indica sonno continuo e con pochi risvegli.",
        "meaning": (
            "Rapporto tra il sonno totale effettivo e il tempo totale trascorso a letto (Sonno / (Sonno + Veglia) × 100). "
            "Un'efficienza ≥ 88–90% è indice di sonno consolidato; valori < 85% indicano sonno frammentato o lunghi risvegli notturni."
        ),
        "formula_latex": r"\text{Efficienza (\%)} = \frac{T_{\text{sonno}}}{T_{\text{sonno}} + T_{\text{veglia}}} \times 100",
        "reference": "Ohayon et al. (2017), National Sleep Foundation's sleep quality recommendations, Sleep Health.",
        "page_endpoint": "page_sleep",
        "page_label": "Vedi Efficienza in Sonno →",
    },
    "Sonno Profondo": {
        "area": "Sonno & Architettura",
        "short": "Percentuale di sonno ad onde lente (N3): è la fase dedicata alla riparazione muscolare, al rilascio di GH e al recupero fisico.",
        "meaning": (
            "Il sonno profondo si concentra nella prima metà della notte. Negli adulti rappresenta tipicamente il 15–23% del sonno totale (circa 60–110 min). "
            "Aumenta dopo allenamenti muscolari intensi; si riduce con alcol, stress serale o camera troppo calda."
        ),
        "formula_latex": r"\%\text{Profondo} = \frac{T_{\text{profondo}}}{T_{\text{sonno}}} \times 100",
        "page_endpoint": "page_sleep",
        "page_label": "Vedi Fasi del Sonno →",
    },
    "Sonno REM": {
        "area": "Sonno & Architettura",
        "short": "Percentuale di sonno REM (Rapid Eye Movement): fase cruciale per memoria, coordinazione motoria e recupero cognitivo/emotivo.",
        "meaning": (
            "Il sonno REM predomina nella seconda metà della notte e al mattino (tipicamente 20–25% del sonno totale, circa 90–120 min). "
            "Svegliarsi molto presto con la sveglia taglia soprattutto gli ultimi cicli ricchi di REM."
        ),
        "formula_latex": r"\%\text{REM} = \frac{T_{\text{REM}}}{T_{\text{sonno}}} \times 100",
        "page_endpoint": "page_sleep",
        "page_label": "Vedi Fasi del Sonno →",
    },
    "Risvegli (≥2 min)": {
        "area": "Sonno & Architettura",
        "short": "Numero di interruzioni del sonno di durata ≥ 2 minuti rilevate nell'ipnogramma notturno.",
        "meaning": (
            "Conta quanti episodi di veglia di almeno 2 minuti interrompono la continuità del sonno. "
            "Pochi risvegli brevi (1–3 a notte) sono normali tra un ciclo e l'altro; molti risvegli riducono il potere ristoratore del sonno."
        ),
        "formula_latex": r"N_{\text{risvegli}} = \sum_{k \in \text{Veglia}} \mathbb{I}\!\left(\Delta t_k \ge 2\;\text{min}\right)",
        "tech_note": "Estratto dagli intervalli `sleepLevels` con `activityLevel == 3` (veglia) successivi al primo addormentamento.",
        "page_endpoint": "page_sleep",
        "page_label": "Vedi Ipnogramma in Sonno →",
    },
    "Latenza 1° Profondo": {
        "area": "Sonno & Architettura",
        "short": "Minuti trascorsi dall'inizio del sonno all'ingresso nella prima fase di sonno profondo (tipicamente 15–40 min).",
        "meaning": (
            "Misura quanto rapidamente il cervello scende nel sonno ad onde lente (N3) dopo l'addormentamento. "
            "Una latenza breve (15–35 min) riflette una buona pressione omeostatica del sonno."
        ),
        "formula_latex": r"L_{\text{profondo}} = t_{\text{inizio } 1^\circ\text{ N3}} - t_{\text{addormentamento}}\quad[\text{min}]",
        "page_endpoint": "page_sleep",
        "page_label": "Vedi Ipnogramma in Sonno →",
    },
    "Latenza 1° REM": {
        "area": "Sonno & Architettura",
        "short": "Minuti dall'addormentamento al primo episodio REM: chiude il primo ciclo completo del sonno (tipicamente 70–110 min).",
        "meaning": (
            "Tempo impiegato per completare la prima discesa NREM ed entrare nel primo episodio REM della notte (normalmente tra 70 e 110 minuti)."
        ),
        "formula_latex": r"L_{\text{REM}} = t_{\text{inizio } 1^\circ\text{ REM}} - t_{\text{addormentamento}}\quad[\text{min}]",
        "page_endpoint": "page_sleep",
        "page_label": "Vedi Ipnogramma in Sonno →",
    },
    "Cicli NREM→REM": {
        "area": "Sonno & Architettura",
        "short": "Numero di cicli completi del sonno (da ~90 min ciascuno): una notte di 7.5–8 ore ne comprende idealmente 4 o 5.",
        "meaning": (
            "Ogni notte il cervello alterna fasi di sonno leggero/profondo (NREM) e sonno REM in cicli di circa 90–100 minuti. "
            "Completare 4–5 cicli garantisce sia il recupero fisico (primi cicli) sia quello nervoso/cognitivo (ultimi cicli)."
        ),
        "tech_note": "Il parser individua i blocchi REM di durata ≥ 5 minuti separati da almeno 30 minuti di sonno NREM intermedio.",
        "page_endpoint": "page_sleep",
        "page_label": "Vedi Cicli in Sonno →",
    },
    "Indice Regolarità (SRI)": {
        "area": "Sonno & Architettura",
        "short": "Punteggio 0–100 che misura quanto vai a dormire e ti svegli agli stessi orari negli ultimi 30 giorni: più è alto, più il ritmo circadiano è allineato.",
        "meaning": (
            "Lo Sleep Regularity Index (SRI) penalizza la dispersione (deviazione standard circolare) dell'ora di addormentamento e di risveglio. "
            "SRI ≥ 80 indica orari molto regolari; < 65 indica forte variabilità che affatica l'orologio biologico anche se dormi abbastanza ore."
        ),
        "formula_latex": r"\text{SRI} = \text{clip}\!\left(100 - \frac{\sigma_{\text{inizio}} + \sigma_{\text{fine}}}{2} \cdot 0.9,\, 0,\, 100\right)",
        "tech_note": "Gli orari intorno alla mezzanotte vengono linearizzati (es. 23:30 = −30 min, 00:30 = +30 min) per calcolare correttamente la deviazione standard circolare senza discontinuità.",
        "reference": "Phillips et al. (2017), Irregular sleep/wake patterns are associated with poorer academic performance and delayed circadian rhythms, Sci Rep.",
        "page_endpoint": "page_sleep",
        "page_label": "Vedi Regolarità in Sonno →",
    },
    "Metà del Sonno Media": {
        "area": "Sonno & Architettura",
        "short": "Orario centrale tra addormentamento e risveglio (es. dormire 23:30–07:30 ha metà sonno alle 03:30): definisce il tuo cronotipo.",
        "meaning": (
            "Il punto mediano del sonno (Mid-sleep) è il marcatore principale della fase del tuo ritmo circadiano interno."
        ),
        "formula_latex": r"t_{\text{mid}} = t_{\text{inizio}} + \frac{t_{\text{fine}} - t_{\text{inizio}}}{2}",
        "reference": "Roenneberg et al. (2003), Life between clocks: daily temporal patterns of human chronotypes, J Biol Rhythms.",
        "page_endpoint": "page_sleep",
        "page_label": "Vedi Cronotipo in Sonno →",
    },
    "Social Jetlag (WE vs Fer.)": {
        "area": "Sonno & Architettura",
        "short": "Spostamento in minuti della metà del sonno tra weekend e giorni feriali: idealmente entro ±45 min per non sfasare il ritmo circadiano.",
        "meaning": (
            "Differenza tra l'orario mediano del sonno nel fine settimana (venerdì/sabato notte) e nei giorni lavorativi. "
            "Uno sfasamento di +90/+120 min equivale a cambiare fuso orario ogni weekend, causando stanchezza il lunedì mattina."
        ),
        "formula_latex": r"\text{SJL} = \overline{t}_{\text{mid, weekend}} - \overline{t}_{\text{mid, feriali}}\quad[\text{min}]",
        "reference": "Wittmann et al. (2006), Social jetlag: misalignment of biological and social time, Chronobiol Int.",
        "page_endpoint": "page_sleep",
        "page_label": "Vedi Social Jetlag in Sonno →",
    },
    "Variabilità Addormentamento": {
        "area": "Sonno & Architettura",
        "short": "Oscillazione tipica (±SD in minuti) dell'ora in cui ti addormenti: sotto ±30–40 min indica ottima costanza serale.",
        "meaning": (
            "Deviazione standard circolare dell'orario di inizio sonno negli ultimi 30 giorni. Più è contenuta (< 35 min), più rapido e profondo è l'addormentamento."
        ),
        "formula_latex": r"\sigma_{\text{inizio}} = \sqrt{\frac{1}{N-1}\sum_{i=1}^{N}\left(t_{\text{inizio},i} - \bar{t}_{\text{inizio}}\right)^2}\quad[\text{min}]",
        "page_endpoint": "page_sleep",
        "page_label": "Vedi Variabilità in Sonno →",
    },
    "FC Minima Notturna": {
        "area": "Sonno & Architettura",
        "short": "Il battito più basso toccato nel sonno (Nadir FC): se raggiunto presto nella notte indica digestione leggera e rapido rilassamento.",
        "meaning": (
            "Valore minimo assoluto della frequenza cardiaca durante la notte e relativo orario. "
            "Dopo cene abbondanti, alcol o allenamenti serali intensi il nadir resta più alto e viene raggiunto solo verso il mattino."
        ),
        "formula_latex": r"\text{FC}_{\min} = \min_{t \in \text{notte}} \text{FC}(t), \quad t_{\text{nadir}} = \arg\min_{t} \text{FC}(t)",
        "page_endpoint": "page_sleep",
        "page_label": "Vedi Nadir FC in Sonno →",
    },
    "Debito Sonno (7 giorni)": {
        "area": "Sonno & Architettura",
        "short": "Ore di sonno mancanti accumulate negli ultimi 7 giorni rispetto al tuo fabbisogno di 8h/notte (0 h = nessun debito).",
        "meaning": (
            "Somma delle ore di sonno perse negli ultimi 7 giorni rispetto all'obiettivo personale configurato (8.0 ore a notte). "
            "Un debito > 3–4 ore su 7 giorni riduce la reattività e abbassa l'HRV."
        ),
        "formula_latex": r"\text{Debito}_{7g} = \max\!\left(0,\; \sum_{i=0}^{6}\frac{T_{\text{target}} - T_{\text{sonno},t-i}}{60}\right)\quad[\text{ore}]",
        "page_endpoint": "page_sleep",
        "page_label": "Vedi Debito Sonno in Sonno →",
    },
    "Debito Sonno (14 giorni)": {
        "area": "Sonno & Architettura",
        "short": "Ore di sonno mancanti accumulate nelle ultime 2 settimane rispetto al fabbisogno di 8h/notte.",
        "meaning": (
            "Bilancio cumulato del deficit di sonno su 14 giorni per monitorare la carenza cronica di riposo."
        ),
        "formula_latex": r"\text{Debito}_{14g} = \max\!\left(0,\; \sum_{i=0}^{13}\frac{T_{\text{target}} - T_{\text{sonno},t-i}}{60}\right)\quad[\text{ore}]",
        "page_endpoint": "page_sleep",
        "page_label": "Vedi Debito Sonno in Sonno →",
    },
    "Carico Acuto (ATL 7g)": {
        "area": "Attività & Carico",
        "short": "Fatica recente (media esponenziale a 7 giorni del TRIMP giornaliero): mostra quanto ti sei allenato nell'ultima settimana.",
        "meaning": (
            "L'Acute Training Load (ATL) è la media mobile esponenziale a 7 giorni del carico TRIMP giornaliero. "
            "Rappresenta la 'Fatica' a breve termine: sale rapidamente dopo giorni di allenamento intenso e scende nei giorni di riposo."
        ),
        "formula_latex": r"\text{ATL}_t = \alpha_7 \cdot L_t + (1 - \alpha_7)\cdot \text{ATL}_{t-1}, \quad \alpha_7 = \frac{2}{7 + 1} = 0.25",
        "tech_note": "Ogni nuovo giorno pesa per il 25% sull'ATL e il passato decade esponenzialmente per il 75%.",
        "reference": "Banister et al. (1975, 1991), Modeling Elite Athletic Performance.",
        "page_endpoint": "page_activities",
        "page_label": "Vedi grafico ATL/CTL in Attività →",
    },
    "Carico Cronico (CTL 28g)": {
        "area": "Attività & Carico",
        "short": "Fitness di base (media esponenziale a 28 giorni del TRIMP): indica il volume di lavoro a cui il tuo corpo è abituato nell'ultimo mese.",
        "meaning": (
            "Il Chronic Training Load (CTL) è la media mobile esponenziale a 28 giorni del carico TRIMP. "
            "Rappresenta la tua 'Fitness' o cilindrata aerobica storica: costruirla richiede settimane di costanza."
        ),
        "formula_latex": r"\text{CTL}_t = \alpha_{28} \cdot L_t + (1 - \alpha_{28})\cdot \text{CTL}_{t-1}, \quad \alpha_{28} = \frac{2}{28 + 1} \approx 0.069",
        "tech_note": "Essendo mediato su una costante lunga (28 giorni), il CTL è stabile e misura quanto carico quotidiano il tuo corpo sa tollerare.",
        "reference": "Banister et al. (1991) & Coggan (Performance Manager Chart).",
        "page_endpoint": "page_activities",
        "page_label": "Vedi grafico ATL/CTL in Attività →",
    },
    "Bilancio TSB (Fitness-Fatica)": {
        "area": "Attività & Carico",
        "short": "Freschezza atletica (TSB = CTL − ATL): positivo (+5/+15) = fresco e brillante per gareggiare; negativo (−10/−25) = in fase di carico.",
        "meaning": (
            "Il Training Stress Balance (TSB = Fitness CTL − Fatica ATL) misura il bilancio tra condizionamento e stanchezza: "
            "valori leggermente negativi (−5 a −20) sono normali durante i blocchi di allenamento; valori positivi (+5 a +15) indicano gambe fresche (ideale prima di un match o gara)."
        ),
        "formula_latex": r"\text{TSB}_t = \text{CTL}_t - \text{ATL}_t\quad[\text{TRIMP/g}]",
        "page_endpoint": "page_activities",
        "page_label": "Vedi TSB in Attività →",
    },
    "Rapporto ACWR": {
        "area": "Attività & Carico",
        "short": "Rapporto Fatica 7g / Fitness 28g (ATL / CTL): la zona ottimale e sicura è 0.8–1.3; sopra 1.5 indica aumento troppo brusco del carico.",
        "meaning": (
            "L'Acute:Chronic Workload Ratio (ACWR = ATL / CTL, modello di Gabbett) confronta quanto stai facendo questa settimana con ciò a cui sei abituato da un mese: "
            "< 0.80 sotto-allenamento/scarico; 0.80–1.30 'sweet spot' (progressione sicura); > 1.50 picco di carico a maggior rischio di sovraccarico."
        ),
        "formula_latex": r"\text{ACWR}_t = \frac{\text{ATL}_t}{\text{CTL}_t}, \quad \text{Zona ottimale: } 0.80 \le \text{ACWR}_t \le 1.30",
        "reference": "Gabbett (2016), The training-injury prevention paradox: should athletes be training smarter and harder?, Br J Sports Med.",
        "page_endpoint": "page_activities",
        "page_label": "Vedi ACWR in Attività →",
    },
    "Tempo Zone 7g (Totale)": {
        "area": "Attività & Carico",
        "short": "Minuti totali di attività con cardiofrequenzimetro registrati nelle 5 zone cardiache (Z1–Z5) negli ultimi 7 giorni.",
        "meaning": (
            "Somma di tutti i minuti trascorsi nelle zone di frequenza cardiaca Z1–Z5 durante le attività degli ultimi 7 giorni."
        ),
        "formula_latex": r"T_{\text{zone},7g} = \sum_{k=1}^{5} T_{Z_k,7g}\quad[\text{min}]",
        "page_endpoint": "page_activities",
        "page_label": "Vedi Zone in Attività →",
    },
    "Quota Bassa Intensità (Z1+Z2)": {
        "area": "Attività & Carico",
        "short": "Percentuale del tempo settimanale passata in Zona 1 e Zona 2 (recupero e base aerobica): costruisce resistenza senza bruciare il recupero.",
        "meaning": (
            "Frazione del volume settimanale svolta a bassa intensità cardiaca (Z1 + Z2, sotto ~75–80% FCmax). "
            "Negli sport di resistenza una quota Z1+Z2 del 65–80% permette di sviluppare i mitocondri e recuperare rapidamente."
        ),
        "formula_latex": r"\%\text{Bassa} = \frac{T_{Z_1} + T_{Z_2}}{\sum_{k=1}^{5} T_{Z_k}} \times 100",
        "reference": "Seiler (2010), What is best practice for training intensity and duration distribution in endurance athletes?, Int J Sports Physiol Perform.",
        "page_endpoint": "page_activities",
        "page_label": "Vedi Distribuzione Zone in Attività →",
    },
    "Quota Alta Intensità (Z4+Z5)": {
        "area": "Attività & Carico",
        "short": "Percentuale del tempo settimanale in Zona 4 (Soglia) e Zona 5 (VO2max): stimola la potenza aerobica ma richiede 24–48h di recupero.",
        "meaning": (
            "Frazione del tempo settimanale trascorsa ad alta intensità (Z4 Soglia Anaerobica + Z5 Massimale). "
            "Nel tennis e nelle corse veloci questa quota sale: monitorarla aiuta a non accumulare troppi giorni consecutivi in Z4–Z5."
        ),
        "formula_latex": r"\%\text{Alta} = \frac{T_{Z_4} + T_{Z_5}}{\sum_{k=1}^{5} T_{Z_k}} \times 100",
        "page_endpoint": "page_activities",
        "page_label": "Vedi Distribuzione Zone in Attività →",
    },
    "TRIMP": {
        "area": "Attività & Carico",
        "short": "Training Impulse di Banister: misura il carico interno di una seduta combinando durata e intensità cardiaca pesata esponenzialmente.",
        "meaning": (
            "Il TRIMP (Training Impulse) trasforma durata e frequenza cardiaca di un allenamento in un unico punteggio di carico fisiologico. "
            "Poiché il fattore esponenziale cresce con la riserva cardiaca, 40 minuti a battiti alti (es. Corsa in Z3–Z4 o Tennis intenso) generano molto più TRIMP di 60 minuti blandi in Z1 (es. Pesi con ampie pause)."
        ),
        "formula_latex": r"\text{TRIMP} = \sum_{i} \Delta t_i \cdot \Delta\text{FC}_{r,i} \cdot 0.64 \cdot \exp\!\left(1.92 \cdot \Delta\text{FC}_{r,i}\right), \quad \Delta\text{FC}_r = \frac{\text{FC} - \text{FC}_{\text{riposo}}}{\text{FC}_{\max} - \text{FC}_{\text{riposo}}}",
        "tech_note": "Il fattore esponenziale e^(1.92·ΔFC_r) modella la curva esponenziale del lattato ematico al crescere della frazione di riserva cardiaca (formula di Karvonen).",
        "reference": "Banister (1991), Modeling Elite Athletic Performance, in Physiological Testing of Elite Athletes.",
        "page_endpoint": "page_activities",
        "page_label": "Vedi TRIMP in Attività →",
    },
    "HRR (60s / 120s)": {
        "area": "Attività & Carico",
        "short": "Quanti battiti scende il cuore in 60s e 120s dopo uno sforzo: un calo rapido (es. ≥ 20–30 bpm in 60s) indica ottima reattività vagale e forma.",
        "meaning": (
            "L'Heart Rate Recovery (HRR60 e HRR120) misura di quanti bpm scende la frequenza cardiaca a 60 e 120 secondi dalla cessazione di uno sforzo attivo. "
            "È un indicatore diretto della riattivazione del nervo vago (sistema parasimpatico): più battiti perdi in 1–2 minuti, migliore è il tuo stato di forma e freschezza. "
            "Richiede la serie secondo-per-secondo (`activity_details`) con pause di recupero o 1–2 minuti a fine seduta."
        ),
        "formula_latex": r"\text{HRR}_{60} = \text{FC}(t_{\text{picco}}) - \text{FC}(t_{\text{picco}} + 60\,\text{s}), \quad \text{HRR}_{120} = \text{FC}(t_{\text{picco}}) - \text{FC}(t_{\text{picco}} + 120\,\text{s})",
        "tech_note": "Se l'orologio viene fermato subito a fine allenamento ma la seduta è intermittente (es. Tennis), l'algoritmo calcola il 75° percentile del calo FC a 60s durante le pause in piedi/camminata (< 0.8 m/s per ≥ 45s) dopo picchi cardiaci.",
        "reference": "Daanen et al. (2012), A systematic review on heart-rate recovery to monitor changes in training status, Int J Sports Physiol Perform.",
        "page_endpoint": "page_activities",
        "page_label": "Vedi HRR e Tracciato 1s in Attività →",
    },
    "Deriva Cardiaca (Cardiac Drift)": {
        "area": "Attività & Carico",
        "short": "Aumento % della FC tra la 1ª e la 2ª metà della seduta: < 5% a passo costante indica ottima tenuta aerobica e buona idratazione.",
        "meaning": (
            "La Deriva Cardiaca confronta la frequenza cardiaca (e il rapporto velocità/FC, detto disaccoppiamento Pa:Hr) della seconda metà dell'allenamento con la prima metà. "
            "Su una corsa a passo costante ≥ 30 min, una deriva < 3.5–5% indica che la resistenza aerobica regge perfettamente la distanza; > 5% segnala affaticamento muscolare, caldo o disidratazione. "
            "Negli sport intermittenti (es. Tennis) indica la variazione di intensità cardiaca tra seconda e prima metà del match."
        ),
        "formula_latex": r"\text{Deriva FC (\%)} = \left(\frac{\overline{\text{FC}}_2}{\overline{\text{FC}}_1} - 1\right)\times 100, \quad \text{Pa:Hr (\%)} = \left(1 - \frac{\bar{v}_2 / \overline{\text{FC}}_2}{\bar{v}_1 / \overline{\text{FC}}_1}\right)\times 100",
        "tech_note": "Esclude automaticamente i primi 10 minuti di riscaldamento e gli ultimi 5 minuti di defaticamento prima di dividere la seduta in due metà uguali.",
        "reference": "Coyle & González-Alonso (2001), Cardiovascular drift during prolonged exercise, Exerc Sport Sci Rev & Friel (TrainingBible).",
        "page_endpoint": "page_activities",
        "page_label": "Vedi Deriva FC in Attività →",
    },
    "Efficienza Velocità/FC": {
        "area": "Attività & Carico",
        "short": "Metri percorsi per ogni singolo battito cardiaco (m/battito): più questo numero sale nel tempo a parità di sport, più sei diventato economico.",
        "meaning": (
            "L'Efficiency Factor (EF = velocità in m/min ÷ FC media in bpm) misura quanta strada percorri per ogni battito del cuore. "
            "Ad esempio, correre a 5:00/km (200 m/min) a 156 bpm dà 1.28 m/battito; se dopo un mese corri allo stesso passo a 148 bpm, l'efficienza sale a 1.35 m/battito."
        ),
        "formula_latex": r"\text{EF} = \frac{\bar{v}\;[\text{m/min}]}{\overline{\text{FC}}\;[\text{bpm}]} = \frac{\text{Distanza}\;[\text{m}]}{\text{Durata}\;[\text{min}] \times \overline{\text{FC}}}\quad\left[\frac{\text{m}}{\text{battito}}\right]",
        "page_endpoint": "page_activities",
        "page_label": "Vedi Efficienza in Attività →",
    },
    "Training Effect (AE / AnE)": {
        "area": "Attività & Carico",
        "short": "Stima Garmin (da 0.0 a 5.0) dello stimolo allenante Aerobico (AE, resistenza) e Anaerobico (AnE, scatti e picchi) della seduta.",
        "meaning": (
            "Il Training Effect Garmin valuta da 0.0 a 5.0 quanto la seduta migliora la capacità Aerobica (AE) e Anaerobica (AnE): "
            "1.0–1.9 Recupero/Beneficio minimo; 2.0–2.9 Mantenimento; 3.0–3.9 Miglioramento; 4.0–4.9 Miglioramento elevato; 5.0 Sovraccarico."
        ),
        "tech_note": "Calcolato da Firstbeat sul picco di EPOC (Excess Post-exercise Oxygen Consumption) per la componente aerobica e sui picchi ad alta intensità per la componente anaerobica.",
        "page_endpoint": "page_activities",
        "page_label": "Vedi Training Effect in Attività →",
    },
    "Monotonia di Foster (7g)": {
        "area": "Attività & Carico",
        "short": "Rapporto tra carico medio e deviazione standard sui 7 giorni: < 1.5 indica ottima alternanza duro/facile; > 2.0 carico troppo uniforme.",
        "meaning": (
            "L'Indice di Monotonia di Carl Foster (Media TRIMP 7g ÷ SD TRIMP 7g) misura quanto variano i tuoi carichi da un giorno all'altro. "
            "Fisiologicamente l'adattamento richiede polarizzazione (giorni di spinta alternati a giorni di riposo o carico leggero): "
            "Monotonia < 1.5 = ottima modulazione; 1.5–2.0 = moderata; > 2.0 = carico piatto senza veri giorni di scarico."
        ),
        "formula_latex": r"\text{Monotonia}_{7g} = \frac{\overline{L}_{7g}}{s_{L,7g}}, \quad \overline{L}_{7g} = \frac{1}{7}\sum_{i=1}^{7} L_i, \quad s_{L,7g} = \sqrt{\frac{1}{6}\sum_{i=1}^{7}\left(L_i - \overline{L}_{7g}\right)^2}",
        "reference": "Foster (1998), Monitoring training in athletes with reference to overtraining syndrome, Med Sci Sports Exerc.",
        "page_endpoint": "page_activities",
        "page_label": "Vedi Monotonia in Attività →",
    },
    "Strain di Foster (7g)": {
        "area": "Attività & Carico",
        "short": "Prodotto tra Carico Settimanale (TRIMP 7g) e Monotonia: penalizza le settimane ad alto volume prive di giorni di recupero.",
        "meaning": (
            "Lo Strain Settimanale di Foster (Load 7g × Monotonia) amplifica il carico settimanale quando manca l'alternanza tra giorni duri e giorni di recupero. "
            "È uno degli indicatori più sensibili in letteratura per prevenire l'affaticamento del sistema nervoso autonomo e i cali prolungati di HRV."
        ),
        "formula_latex": r"\text{Strain}_{7g} = \left(\sum_{i=1}^{7} L_i\right) \times \text{Monotonia}_{7g}",
        "reference": "Foster (1998), Med Sci Sports Exerc, 30(7), 1164–1168.",
        "page_endpoint": "page_activities",
        "page_label": "Vedi Strain di Foster in Attività →",
    },
    "Profilo Multi-Sport (Tennis vs Pesi vs Corsa)": {
        "area": "Attività & Carico",
        "short": "Confronta il costo cardiovascolare al minuto (TRIMP/min) e la risposta di HRV e FC a riposo la mattina dopo ciascuno dei tuoi sport.",
        "meaning": (
            "Ogni disciplina sollecita il corpo in modo diverso: i Pesi hanno bassa densità cardiovascolare (TRIMP/min ~0.4–0.6, 99% in Z1–Z2) ma carico neuromuscolare; "
            "il Tennis ha densità ~3 volte superiore (~1.5–1.7 TRIMP/min con ~25% in Z4–Z5 per via degli scatti); la Corsa ha la massima densità aerobica continua (~1.9–2.1 TRIMP/min). "
            "Confrontare ΔHRV e ΔRHR a t+1 mostra come il tuo sistema nervoso recupera dopo ciascuno sport."
        ),
        "formula_latex": r"\text{Densità}_S = \frac{\sum_{i \in S}\text{TRIMP}_i}{\sum_{i \in S} T_i\;[\text{min}]}, \quad \Delta\text{HRV}_{S,t+1} = \overline{\text{HRV}}_{t+1 \mid S} - \overline{\text{HRV}}_{t+1 \mid \text{riposo}}",
        "page_endpoint": "page_activities",
        "page_label": "Vedi Confronto Multi-Sport in Attività →",
    },
    "Simulatore What-If (7g)": {
        "area": "Attività & Carico",
        "short": "Proiezione deterministica a 7 giorni di Fatica (ATL), Fitness (CTL), Forma (TSB) e ACWR in base agli allenamenti che pianifichi.",
        "meaning": (
            "Usa le equazioni ricorsive esponenziali di Banister partendo dal tuo stato attuale (ATL_0, CTL_0) per mostrare in anticipo "
            "come evolveranno la freschezza atletica (TSB) e il rapporto ACWR nei prossimi 7 giorni scegliendo tra Riposo, Pesi, Tennis o Corsa."
        ),
        "formula_latex": r"\hat{\text{ATL}}_k = \alpha_7 \hat{L}_k + (1-\alpha_7)\hat{\text{ATL}}_{k-1}, \quad \hat{\text{CTL}}_k = \alpha_{28} \hat{L}_k + (1-\alpha_{28})\hat{\text{CTL}}_{k-1}, \quad \hat{\text{TSB}}_k = \hat{\text{CTL}}_k - \hat{\text{ATL}}_k",
        "page_endpoint": "page_activities",
        "page_label": "Prova il Simulatore in Attività →",
    },
    "Baseline Mediana 30g & Z-score Robusto": {
        "area": "Statistica & Trend",
        "short": "Confronta il dato di oggi con la tua mediana degli ultimi 30 giorni usando lo scostamento mediano assoluto (MAD), immune ai valori anomali.",
        "meaning": (
            "Invece di usare soglie fisse uguali per tutti o la media aritmetica (che viene falsata da una singola notte insonne), "
            "la dashboard calcola la tua Mediana mobile a 30 giorni e la dispersione robusta MAD. Lo Z-score robusto z = (Valore − Mediana) / (1.4826 · MAD) "
            "indica di quante tue 'deviazioni standard' il valore di oggi si discosta dalla tua abitudine: tra −0.75 e +0.75 sei 'in linea con la tua norma'."
        ),
        "formula_latex": r"m_{30} = \text{mediana}(x_{t-29..t}), \quad \text{MAD}_{30} = \text{mediana}(|x - m_{30}|), \quad z_{\text{rob}} = \frac{x_t - m_{30}}{1.4826 \cdot \text{MAD}_{30}}",
        "tech_note": "Il fattore costante 1.4826 = 1 / Φ⁻¹(0.75) rende il MAD un estimatore consistente della deviazione standard σ per distribuzioni normali, ma con punto di rottura del 50% contro gli outlier.",
        "reference": "Leys et al. (2013), Detecting outliers: Do not use standard deviation around the mean, use absolute deviation around the median, J Exp Soc Psychol.",
        "page_endpoint": "index",
        "page_label": "Vedi Metriche vs Baseline in Oggi →",
    },
    "Trend Theil-Sen, Mann-Kendall & CUSUM": {
        "area": "Statistica & Trend",
        "short": "Metodi statistici robusti per capire se una metrica sta davvero salendo/scendendo (Theil-Sen, p-value) o se ha cambiato regime (CUSUM).",
        "meaning": (
            "La pendenza di Theil-Sen calcola la mediana di tutte le pendenze tra coppie di giorni (es. +0.8 ms/settimana), mentre il test di Mann-Kendall "
            "verifica se il trend è statisticamente reale (p < 0.05) o semplice oscillazione casuale. Il CUSUM rileva invece i punti di svolta in cui la media si sposta stabilmente verso l'alto o verso il basso."
        ),
        "formula_latex": r"\hat{\beta}_{\text{TS}} = \text{mediana}_{i < j}\!\left(\frac{y_j - y_i}{t_j - t_i}\right), \quad S_{\text{MK}} = \sum_{i < j} \text{sgn}(y_j - y_i), \quad C_t^+ = \max\!\left(0,\, C_{t-1}^+ + z_t - 0.5\right)",
        "tech_note": "Quando la somma cumulata CUSUM (C_t⁺ o C_t⁻) supera la soglia h = 4.0 deviazioni standard robuste, viene segnalato un cambio di regime (changepoint) e l'accumulatore si azzera.",
        "reference": "Sen (1968), JASA; Mann (1945), Econometrica; Page (1954), Biometrika (CUSUM).",
        "page_endpoint": "page_trends",
        "page_label": "Esplora in Tendenze →",
    },
    "Correlazione di Spearman (ρ) & Lag": {
        "area": "Statistica & Trend",
        "short": "Misura da −1 a +1 se due metriche si muovono insieme (Lag 0) o se una metrica di oggi influenza quella di domani/dopo-domani (Lag 1/2).",
        "meaning": (
            "Il coefficiente per ranghi di Spearman (ρ tra −1 e +1) valuta relazioni monotone senza assumere linearità: "
            "ρ > 0 indica che all'aumentare di X aumenta anche Y; ρ < 0 indica che all'aumentare di X diminuisce Y (es. più carico oggi → HRV più bassa domani a Lag +1). "
            "Ricorda sempre: correlazione esplorativa non dimostra da sola un rapporto di causa-effetto."
        ),
        "formula_latex": r"\rho_s(\tau) = \frac{\text{cov}\!\left(\text{rg}(X_t),\, \text{rg}(Y_{t+\tau})\right)}{\sigma_{\text{rg}(X)} \cdot \sigma_{\text{rg}(Y)}} \in [-1,\, +1], \quad \tau \in \{0, 1, 2\}\;\text{giorni}",
        "tech_note": "Per evitare accoppiamenti spuri attraverso buchi temporali, il lag τ viene costruito tramite join esatto sulla data calendario (t + τ giorni) e richiede n ≥ 10 coppie valide.",
        "reference": "Spearman (1904), The proof and measurement of association between two things.",
        "page_endpoint": "page_correlations",
        "page_label": "Esplora Matrice e Scatter in Correlazioni →",
    },
    "Diario Contestuale (Tag Giornalieri)": {
        "area": "Statistica & Trend",
        "short": "Etichette rapide (Studio/Esame, Cena Tardi, Alcol, DOMS, Caffeina, Malessere) per misurare l'effetto delle abitudini extra-sportive su HRV, RHR e Sonno.",
        "meaning": (
            "Permette di annotare con 1 clic i fattori di contesto non misurati dall'orologio e confrontare nella pagina Correlazioni "
            "la differenza mediana di HRV notturna, FC a riposo, Punteggio Sonno e Stress tra i giorni con il tag attivo e quelli senza tag."
        ),
        "formula_latex": r"\Delta M(\text{tag}) = \text{Mediana}\!\left(M_{d \in \text{tag}}\right) - \text{Mediana}\!\left(M_{d \notin \text{tag}}\right)",
        "page_endpoint": "page_correlations",
        "page_label": "Vedi Impatto Tag in Correlazioni →",
    },
}


def get_metric_explanation(label: str) -> dict[str, str]:
    """Restituisce `{short, meaning, area}` per il titolo di una scheda (gestendo anche prefissi dinamici come 'Tempo SpO2 <')."""
    if not label:
        return {"short": "", "meaning": "", "area": ""}
    if label in METRIC_GUIDE:
        return METRIC_GUIDE[label]
    if label.startswith("Tempo SpO2 <"):
        return {
            "area": "Sonno & Architettura",
            "short": "Minuti trascorsi durante la notte con saturazione SpO2 sotto la soglia di attenzione (92%): idealmente vicino a 0 min.",
            "meaning": (
                "Conta i minuti notturni in cui il sensore ottico Pulse Ox ha registrato valori di SpO2 inferiori al 92%. "
                "Pochi minuti isolati possono dipendere dalla posizione del braccio; valori elevati e ripetuti meritano attenzione."
            ),
        }
    return {"short": "", "meaning": "", "area": ""}


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


ACTIVITY_TYPE_LABELS: dict[str, str] = {
    "running": "🏃 Corsa",
    "tennis_v2": "🎾 Tennis",
    "tennis": "🎾 Tennis",
    "strength_training": "🏋️ Pesi / Forza",
    "cycling": "🚴 Ciclismo",
    "swimming": "🏊 Nuoto",
    "walking": "🚶 Camminata",
    "hiking": "🥾 Escursionismo",
}


def _format_pace_mmss(sec_per_km: float) -> str:
    tot_s = int(round(sec_per_km))
    return f"{tot_s // 60}:{tot_s % 60:02d} /km"


def _format_duration_hm(duration_s: float | None) -> str:
    if duration_s is None or duration_s <= 0:
        return "dato assente"
    mins = int(round(float(duration_s) / 60.0))
    if mins >= 60:
        return f"{mins // 60}h {mins % 60:02d}m"
    return f"{mins} min"


def _enrich_activities_with_analysis(
    conn: Any,
    activities: list[dict[str, Any]],
    rhr_by_day: dict[str, float | None],
    limit: int = 20,
) -> list[dict[str, Any]]:
    # Carica la mappa dei payload grezzi `activities_range` se presente per estrarre calorie, TE, serie/rip, potenza, ecc.
    raw_acts_list = db.get_raw_by_endpoint(conn, "activities_range")
    raw_by_id: dict[int, dict[str, Any]] = {}
    if isinstance(raw_acts_list, list):
        for ra in raw_acts_list:
            if isinstance(ra, dict) and ra.get("activityId") is not None:
                try:
                    raw_by_id[int(ra["activityId"])] = ra
                except (ValueError, TypeError):
                    pass

    enriched: list[dict[str, Any]] = []
    for act in reversed(activities[-limit:]):
        act_item = dict(act)
        act_id = act.get("activity_id")
        raw_act = raw_by_id.get(int(act_id)) if act_id is not None else None
        if raw_act is None:
            raw_act = {}

        details_raw = db.get_raw_by_endpoint(conn, f"activity_details:{act_id}") if act_id else None
        hrr = analysis.compute_heart_rate_recovery(details_raw)
        drift = analysis.compute_cardiac_drift(details_raw)
        trimp = analysis.compute_trimp(act, details_raw, hr_rest=rhr_by_day.get(str(act.get("day"))))
        efficiency = analysis.compute_speed_hr_efficiency(act)

        act_item["hrr"] = hrr
        act_item["drift"] = drift
        act_item["trimp"] = trimp
        act_item["efficiency"] = efficiency

        # Orario di inizio HH:MM
        start_local = str(act.get("start_local") or "")
        start_hhmm = start_local[11:16] if len(start_local) >= 16 else ""
        act_item["start_hhmm"] = start_hhmm

        act_type = str(act.get("type") or "other")
        type_badge = ACTIVITY_TYPE_LABELS.get(act_type, f"⚡ {act_type.replace('_', ' ').title()}")
        act_item["type_badge"] = type_badge

        dur_s = float(act["duration_s"]) if act.get("duration_s") is not None else None
        dist_m = float(act["distance_m"]) if act.get("distance_m") is not None else None
        act_item["duration_fmt"] = _format_duration_hm(dur_s)

        moving_s = raw_act.get("movingDuration")
        moving_fmt = _format_duration_hm(float(moving_s)) if moving_s else None

        # Passo / Velocità / Serie & Ripetizioni
        pace_or_detail = "—"
        speed_kmh: float | None = None
        if dist_m and dist_m > 50 and dur_s and dur_s > 0:
            speed_kmh = round((dist_m / 1000.0) / (dur_s / 3600.0), 1)
            if act_type == "running":
                sec_per_km = dur_s / (dist_m / 1000.0)
                pace_or_detail = f"{_format_pace_mmss(sec_per_km)} ({speed_kmh} km/h)"
            elif act_type in ("tennis_v2", "tennis") and raw_act.get("steps"):
                pace_or_detail = f"{int(raw_act['steps'])} passi · max {round(float(raw_act.get('maxSpeed') or 0) * 3.6, 1)} km/h"
            else:
                pace_or_detail = f"{speed_kmh} km/h"
        elif act_type == "strength_training":
            sets = raw_act.get("activeSets") or raw_act.get("totalSets")
            reps = raw_act.get("totalReps")
            if sets and reps:
                pace_or_detail = f"{int(sets)} serie · {int(reps)} rip."
            elif sets:
                pace_or_detail = f"{int(sets)} serie"
            else:
                pace_or_detail = "Sessione forza"
        act_item["pace_or_detail"] = pace_or_detail

        # Calorie, Body Battery, Training Effect
        cal = raw_act.get("calories")
        if cal is None and dur_s and act.get("hr_avg"):
            # Stima per dati demo sintetici
            cal = round((dur_s / 60.0) * max(4.0, (float(act["hr_avg"]) - 65.0) * 0.14), 0)
        act_item["calories_kcal"] = int(round(float(cal))) if cal is not None else None
        act_item["bb_diff"] = int(raw_act["differenceBodyBattery"]) if raw_act.get("differenceBodyBattery") is not None else None

        ae_te = round(float(raw_act["aerobicTrainingEffect"]), 1) if raw_act.get("aerobicTrainingEffect") is not None else None
        ane_te = round(float(raw_act["anaerobicTrainingEffect"]), 1) if raw_act.get("anaerobicTrainingEffect") is not None else None
        te_label = raw_act.get("trainingEffectLabel")
        act_item["ae_te"] = ae_te
        act_item["ane_te"] = ane_te
        act_item["te_label"] = te_label

        # Scomposizione delle 5 Zone Cardiache (Z1..Z5)
        zone_colors = ["#71717A", "#2DD4BF", "#818CF8", "#F59E0B", "#FB923C"]
        zone_names = ["Z1 Recupero", "Z2 Base Aerobica", "Z3 Ritmo / Tempo", "Z4 Soglia Anaerobica", "Z5 VO2max / Massimale"]
        z_secs = [float(act.get(f"z{k}_s") or 0.0) for k in range(1, 6)]
        tot_z_s = sum(z_secs)
        zones_bars: list[dict[str, Any]] = []
        for idx in range(5):
            zm = round(z_secs[idx] / 60.0, 1)
            zpct = round((z_secs[idx] / tot_z_s) * 100.0, 0) if tot_z_s > 0 else 0.0
            zones_bars.append(
                {
                    "zone": f"Z{idx + 1}",
                    "name": zone_names[idx],
                    "min": zm,
                    "pct": int(zpct),
                    "color": zone_colors[idx],
                }
            )
        act_item["zones_bars"] = zones_bars
        act_item["has_zones"] = tot_z_s > 0

        # Costruzione KPI ordinati per il pannello laterale (drawer)
        kpis: list[dict[str, str]] = [
            {"label": "Data e Ora", "value": f"{act.get('day')} {('ore ' + start_hhmm) if start_hhmm else ''}"},
            {"label": "Disciplina", "value": type_badge},
            {"label": "Durata Totale", "value": act_item["duration_fmt"]},
        ]
        if moving_fmt:
            kpis.append({"label": "Tempo in Movimento", "value": moving_fmt})
        if dist_m and dist_m > 0:
            kpis.append({"label": "Distanza Percorsa", "value": f"{dist_m / 1000.0:.2f} km"})
        if pace_or_detail != "—":
            kpis.append({"label": "Passo / Dettaglio Lavoro", "value": pace_or_detail})
        if act.get("hr_avg") is not None:
            kpis.append(
                {
                    "label": "Frequenza Cardiaca (Media / Max)",
                    "value": f"{int(act['hr_avg'])} bpm / {int(act['hr_max']) if act.get('hr_max') else '—'} bpm",
                }
            )
        if act_item["calories_kcal"] is not None:
            kpis.append({"label": "Calorie Bruciate", "value": f"{act_item['calories_kcal']} kcal"})
        if act_item["bb_diff"] is not None:
            kpis.append({"label": "Impatto Body Battery", "value": f"{act_item['bb_diff']} pt"})
        if ae_te is not None:
            te_txt = f"Aerobico {ae_te} / 5.0" + (f" · Anaerobico {ane_te} / 5.0" if ane_te is not None else "")
            if te_label:
                te_txt += f" ({te_label})"
            kpis.append({"label": "Training Effect Garmin", "value": te_txt})
        if raw_act.get("avgPower"):
            p_txt = f"{int(raw_act['avgPower'])} W"
            if raw_act.get("normPower"):
                p_txt += f" (NP {int(raw_act['normPower'])} W / Max {int(raw_act.get('maxPower') or 0)} W)"
            kpis.append({"label": "Potenza di Corsa", "value": p_txt})
        if raw_act.get("averageRunningCadenceInStepsPerMinute") and act_type == "running":
            kpis.append(
                {
                    "label": "Cadenza e Falcata",
                    "value": f"{int(round(float(raw_act['averageRunningCadenceInStepsPerMinute'])))} spm · {round(float(raw_act.get('avgStrideLength') or 0) / 100.0, 2)} m",
                }
            )
        if raw_act.get("elevationGain"):
            kpis.append({"label": "Dislivello Positivo", "value": f"+{int(round(float(raw_act['elevationGain'])))} m"})
        if raw_act.get("vO2MaxValue"):
            kpis.append({"label": "VO2max Stimato", "value": f"{raw_act['vO2MaxValue']} ml/kg/min"})
        if raw_act.get("fastestSplit_1000"):
            kpis.append({"label": "Miglior 1 km (Split)", "value": _format_pace_mmss(float(raw_act["fastestSplit_1000"]))})
        if raw_act.get("waterEstimated"):
            kpis.append({"label": "Sudorazione Stimata", "value": f"{int(round(float(raw_act['waterEstimated'])))} ml"})
        if trimp["calculable"]:
            kpis.append({"label": "Carico TRIMP Calcolato", "value": f"{trimp['value']} TRIMP ({trimp['method']})"})
        if efficiency["calculable"]:
            kpis.append({"label": "Efficienza Velocità/FC", "value": f"{efficiency['value']} m/battito"})
        # Formattazione esplicita HRR (60s / 120s) e Deriva FC per tabella e drawer
        has_details_1s = details_raw is not None
        act_item["has_details_1s"] = has_details_1s

        if hrr["calculable"]:
            hrr60 = hrr["value"]["hrr_60_bpm"]
            hrr120 = hrr["value"].get("hrr_120_bpm")
            hrr_display = f"-{hrr60:.0f}" + (f" / -{hrr120:.0f} bpm" if hrr120 is not None else " bpm")
            hrr_sub = "pause intra-seduta" if hrr.get("quality_flag") == "intra_session" else "post-esercizio"
            hrr_txt = f"-{hrr60} bpm a 60s" + (f" / -{hrr120} bpm a 120s ({hrr_sub})" if hrr120 is not None else f" ({hrr_sub})")
            kpis.append({"label": "Recupero FC (HRR 60s/120s)", "value": hrr_txt})
        elif not has_details_1s:
            hrr_display = "Sync 1s assente"
            hrr_sub = "esegui sync"
            kpis.append(
                {
                    "label": "Recupero FC (HRR 60s/120s)",
                    "value": "Serie 1s non ancora sincronizzata (esegui: python -m app.cli sync --days 30)",
                }
            )
        else:
            hrr_display = "Stop < 60s"
            hrr_sub = "fine immediata"
            kpis.append({"label": "Recupero FC (HRR 60s/120s)", "value": f"Non calcolabile ({hrr.get('reason', '')})"})

        if drift["calculable"]:
            d_val = drift["value"]["hr_drift_pct"]
            drift_display = f"{'+' if d_val > 0 else ''}{d_val}%"
            drift_sub = "2ª vs 1ª metà (var.)" if drift.get("quality_flag") == "variable_pace" else f"Pa:Hr {drift['value']['aerobic_decoupling_pct']}%"
            kpis.append(
                {
                    "label": "Deriva Cardiaca (2ª vs 1ª metà)",
                    "value": f"{drift_display} · FC 1ª metà {drift['value']['hr_first_half_bpm']} → 2ª metà {drift['value']['hr_second_half_bpm']} bpm",
                }
            )
        elif act_type == "strength_training":
            drift_display = "N/A (Forza)"
            drift_sub = "solo sport aerobici"
            kpis.append({"label": "Deriva Cardiaca", "value": "Non applicabile (sessione di forza statica senza locomozione)"})
        elif not has_details_1s:
            drift_display = "Sync 1s assente"
            drift_sub = "esegui sync"
            kpis.append(
                {
                    "label": "Deriva Cardiaca",
                    "value": "Serie 1s non ancora sincronizzata (esegui: python -m app.cli sync --days 30)",
                }
            )
        else:
            drift_display = "Durata < 30m"
            drift_sub = "seduta breve"
            kpis.append({"label": "Deriva Cardiaca", "value": f"Non calcolabile ({drift.get('reason', '')})"})

        act_item["hrr_display"] = hrr_display
        act_item["hrr_sub"] = hrr_sub
        act_item["drift_display"] = drift_display
        act_item["drift_sub"] = drift_sub

        act_item["detail_payload"] = {
            "activity_id": int(act_id) if act_id is not None else None,
            "has_details_1s": has_details_1s,
            "title": f"{act.get('name') or type_badge} ({act.get('day')})",
            "display_value": trimp["value"] if trimp["calculable"] else act_item["duration_fmt"],
            "unit": "TRIMP" if trimp["calculable"] else "",
            "subtitle": f"{type_badge} · {act_item['duration_fmt']}" + (f" · {act_item['calories_kcal']} kcal" if act_item["calories_kcal"] else ""),
            "explanation": (
                "• TRIMP (Training Impulse): misura il carico interno pesando esponenzialmente i minuti passati a battiti alti.\n"
                "• Training Effect (AE / AnE): stima da 0 a 5 quanto la seduta allena la resistenza aerobica (AE) e la capacità anaerobica/scatti (AnE).\n"
                "• HRR (60s / 120s): quanti bpm scende il cuore in 1 e 2 minuti dopo i picchi di sforzo (più scende rapidamente, migliore è il recupero vagale).\n"
                "• Deriva FC: variazione % della FC tra 2ª e 1ª metà della seduta (< 5% a passo costante indica ottima tenuta aerobica).\n"
                "• Efficienza (m/b): metri percorsi per ogni singolo battito cardiaco."
            ),
            "kpis": kpis,
            "zones_bars": zones_bars if tot_z_s > 0 else [],
            "assumptions": trimp["assumptions"] + hrr["assumptions"] + drift["assumptions"],
            "how_it_works": trimp["how_it_works"],
        }
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
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True

    # Inizializza lo schema; se siamo in DEMO_MODE e il DB demo è vuoto (o privo di serie raw Step 2), genera i 90 giorni sintetici
    conn = db.connect(resolved_db_path)
    db.init_db(conn)
    if resolved_demo:
        cnt_daily = conn.execute("SELECT COUNT(*) AS cnt FROM daily").fetchone()["cnt"]
        cnt_raw = conn.execute("SELECT COUNT(*) AS cnt FROM raw_garmin").fetchone()["cnt"]
        if cnt_daily == 0 or cnt_raw == 0:
            synthetic.seed_synthetic_db(conn, days=90, seed=42)
    conn.close()

    @app.after_request
    def add_no_cache_headers(response: Response) -> Response:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @app.context_processor
    def inject_available_days() -> dict[str, Any]:
        c = db.connect(app.config["DB_PATH"])
        rows = c.execute("SELECT day FROM daily ORDER BY day ASC").fetchall()
        c.close()
        return {
            "available_days": [str(r["day"]) for r in rows],
            "metric_guide": METRIC_GUIDE,
            "get_metric_explanation": get_metric_explanation,
        }

    @app.get("/")
    def index() -> str:
        conn = db.connect(app.config["DB_PATH"])
        daily_rows = db.fetch_daily_range(conn)
        activities = db.fetch_activities_range(conn)

        latest, target_day, prev_day, next_day = _resolve_day_navigation(daily_rows, request.args.get("day"))
        rhr_by_day = {str(r["day"]): r.get("rhr_bpm") for r in daily_rows}
        sleep_raw = db.get_raw(conn, target_day, "sleep") if target_day else None
        enriched_acts = _enrich_activities_with_analysis(conn, activities, rhr_by_day, limit=12)
        day_tags = db.get_day_tags(conn, target_day) if target_day else []
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
            day_tags=day_tags,
            preset_tags=analysis.PRESET_TAGS,
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
        multisport_stats = analysis.compute_multisport_comparison(daily_rows, activities) if latest else None
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
            multisport_stats=multisport_stats,
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
        foster_stats = analysis.compute_foster_monotony_strain(daily_rows, activities, target_day=target_day) if latest else None
        multisport_stats = analysis.compute_multisport_comparison(daily_rows, activities) if latest else None
        weekly_zones = analysis.compute_weekly_zone_distribution(activities, end_day=target_day, window_days=7) if latest else None
        weekly_summary = _build_weekly_activity_summary(activities, target_day, n_weeks=8) if latest else []

        # Giorni futuri (d+1 .. d+7) e preset sportivi per il simulatore What-If
        weekday_names_it = ("Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom")
        sim_days: list[dict[str, Any]] = []
        default_loads = [0.0, 35.0, 0.0, 110.0, 35.0, 0.0, 110.0]
        if target_day:
            base_dt = date.fromisoformat(target_day)
            for idx in range(7):
                f_dt = base_dt + timedelta(days=idx + 1)
                sim_days.append(
                    {
                        "offset": idx + 1,
                        "day": f_dt.isoformat(),
                        "label": f"{weekday_names_it[f_dt.weekday()]} {f_dt.strftime('%d/%m')}",
                        "default_load": default_loads[idx],
                    }
                )

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
            foster_stats=foster_stats,
            multisport_stats=multisport_stats,
            sim_days=sim_days,
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
        tags_by_day = db.fetch_all_tags_by_day(conn)
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
        tag_impacts = analysis.compute_tag_impacts(daily_rows, tags_by_day)
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
            tag_impacts=tag_impacts,
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

    @app.get("/api/activity/<int:activity_id>/timeseries")
    def api_activity_timeseries(activity_id: int) -> Any:
        conn = db.connect(app.config["DB_PATH"])
        details_raw = db.get_raw_by_endpoint(conn, f"activity_details:{activity_id}")
        conn.close()
        series = analysis.extract_activity_chart_series(details_raw)
        series["activity_id"] = activity_id
        return jsonify(series)

    @app.post("/api/day/<day_str>/tags")
    def api_toggle_day_tag(day_str: str) -> Any:
        payload = request.get_json(silent=True) or {}
        tag_val = str(payload.get("tag") or "").strip()
        if not tag_val:
            return jsonify({"error": "tag_missing"}), 400
        conn = db.connect(app.config["DB_PATH"])
        active_tags = db.toggle_day_tag(conn, day_str, tag_val)
        conn.commit()
        conn.close()
        return jsonify({"day": day_str, "tags": active_tags})

    sync_lock = threading.Lock()
    sync_state: dict[str, Any] = {
        "running": False,
        "status": "idle",
        "message": "Pronto",
        "last_run_at": None,
        "result": None,
    }

    def _run_background_sync(days_to_sync: int) -> None:
        try:
            if app.config["DEMO_MODE"]:
                with sync_lock:
                    sync_state["message"] = "Rigenerazione dataset demo in corso..."
                c = db.connect(app.config["DB_PATH"])
                res = synthetic.seed_synthetic_db(c, days=90, seed=42)
                c.close()
                with sync_lock:
                    sync_state["running"] = False
                    sync_state["status"] = "ok"
                    sync_state["message"] = f"Demo aggiornata ({res['daily_days']}g, {res['activities']} att.)"
                    sync_state["last_run_at"] = datetime.now(timezone.utc).isoformat()
                    sync_state["result"] = res
                return

            def _update_msg(msg: str) -> None:
                with sync_lock:
                    sync_state["message"] = msg

            _update_msg("Autenticazione token Garmin...")
            limiter = GarminRateLimiter(min_delay_s=1.2, max_requests=140)
            client = SafeGarminClient(rate_limiter=limiter)
            client.login(interactive=False)

            c = db.connect(app.config["DB_PATH"])
            res = sync.sync_garmin_data(
                c,
                client,
                days=days_to_sync,
                progress_cb=_update_msg,
            )
            c.close()
            with sync_lock:
                sync_state["running"] = False
                sync_state["status"] = res.get("status", "ok")
                sync_state["message"] = (
                    f"Completato: {res['days_synced']}g, {res['activities_synced']} att. "
                    f"(+{res.get('activity_details_fetched', 0)} serie 1s)"
                )
                sync_state["last_run_at"] = datetime.now(timezone.utc).isoformat()
                sync_state["result"] = res
        except Exception as exc:
            with sync_lock:
                sync_state["running"] = False
                sync_state["status"] = "error"
                sync_state["message"] = f"Errore sync: {exc}"

    @app.post("/api/sync/start")
    def api_sync_start() -> Any:
        payload = request.get_json(silent=True) or {}
        try:
            days_req = int(payload.get("days", 30))
        except (ValueError, TypeError):
            days_req = 30
        days_req = max(1, min(90, days_req))

        with sync_lock:
            if sync_state["running"]:
                return jsonify(sync_state), 409
            sync_state["running"] = True
            sync_state["status"] = "running"
            sync_state["message"] = "Avvio sincronizzazione..."

        t = threading.Thread(target=_run_background_sync, args=(days_req,), daemon=True)
        t.start()
        with sync_lock:
            return jsonify(dict(sync_state)), 202

    @app.get("/api/sync/status")
    def api_sync_status() -> Any:
        with sync_lock:
            return jsonify(dict(sync_state))

    return app


def main() -> None:
    app = create_app()
    host = "127.0.0.1"
    port = int(os.getenv("PORT", "5000"))
    print(f"Dashboard Garmin attiva su http://{host}:{port} (DEMO_MODE={app.config['DEMO_MODE']})")
    app.run(host=host, port=port, debug=False, use_reloader=True)


if __name__ == "__main__":
    main()
