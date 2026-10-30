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

# Guida e spiegazione in italiano semplice di tutte le voci tecniche (mostrata nei rettangoli, nel drawer e nella pagina Guida)
METRIC_GUIDE: dict[str, dict[str, str]] = {
    "Recupero": {
        "area": "Recupero & Sistema Nervoso",
        "short": "Indica quanto il tuo corpo è pronto ad affrontare uno sforzo oggi (0–100), confrontando HRV, FC a riposo, sonno e stress con la tua norma a 30 giorni.",
        "meaning": (
            "Il punteggio di Recupero (0–100) stima lo stato del sistema nervoso autonomo al risveglio. "
            "Combina 5 segnali pesati rispetto alla tua baseline personale a 30 giorni: HRV notturna (35%), FC a riposo invertita (25%), "
            "durata/qualità del sonno (20%), stress del giorno precedente (10%) e Body Battery al mattino (10%). "
            "Valori ≥ 70 indicano ottimo recupero (via libera per sedute intense); 45–69 recupero normale; < 45 fatica o stress fisiologico."
        ),
    },
    "Sforzo": {
        "area": "Attività & Carico",
        "short": "Misura il carico cardiovascolare e di movimento accumulato nella giornata (0–100) da allenamenti (TRIMP / Zone FC) e passi.",
        "meaning": (
            "Il punteggio di Sforzo (0–100) quantifica lo stress fisico della giornata su una scala saturante esponenziale. "
            "Integra i minuti trascorsi nelle 5 zone di frequenza cardiaca (pesando esponenzialmente le zone alte Z4–Z5 tramite il TRIMP) "
            "e il movimento di base (passi giornalieri). Un valore di 30–50 corrisponde a una giornata attiva o seduta leggera; 60–80 a un allenamento impegnativo; > 80 a un carico molto elevato."
        ),
    },
    "Sonno": {
        "area": "Sonno & Architettura",
        "short": "Valuta il ristoro notturno (0–100) unendo durata rispetto al tuo bisogno (8h), quota di sonno profondo/REM, continuità e regolarità.",
        "meaning": (
            "Il punteggio del Sonno (0–100) misura la qualità complessiva del riposo: 40% durata rispetto al fabbisogno personale (480 min), "
            "25% quota rigenerativa (Profondo N3 + REM, ideale ~40–45% del totale), 20% continuità (pochi minuti di veglia infra-sonno) "
            "e 15% regolarità dell'orario di metà sonno rispetto alla tua abitudine."
        ),
    },
    "HRV Notturna": {
        "area": "Recupero & Sistema Nervoso",
        "short": "Variabilità tra un battito e l'altro nel sonno (rMSSD in ms): più è alta rispetto alla tua norma, più il sistema parasimpatico è rilassato e recuperato.",
        "meaning": (
            "L'HRV (Heart Rate Variability, misurata come rMSSD in millisecondi) misura le micro-variazioni di tempo tra i battiti cardiaci durante la notte. "
            "Non conta il valore assoluto confrontato con altre persone, ma lo scostamento dalla tua mediana a 30 giorni: "
            "un'HRV stabile o sopra la tua norma indica buon adattamento; un calo netto per più giorni segnala fatica residua, stress, alcol o inizio di malessere."
        ),
    },
    "FC a Riposo": {
        "area": "Recupero & Sistema Nervoso",
        "short": "Frequenza cardiaca minima a riposo (bpm): valori più bassi o in linea con la tua norma indicano cuore efficiente e buon recupero.",
        "meaning": (
            "La Frequenza Cardiaca a Riposo (RHR, in battiti al minuto) viene rilevata nelle fasi di quiete profonda/risveglio. "
            "È inversamente legata allo stato di recupero: un aumento di +3/+6 bpm sopra la tua mediana a 30 giorni accompagna spesso allenamenti pesanti non ancora smaltiti, disidratazione, cena tardiva o febbre."
        ),
    },
    "Sonno Totale": {
        "area": "Sonno & Architettura",
        "short": "Minuti effettivi di sonno (esclusi i risvegli notturni) confrontati con il tuo fabbisogno personale di 480 min (8 ore).",
        "meaning": (
            "Tempo totale effettivamente dormito tra l'addormentamento e il risveglio, sottraendo i periodi di veglia intra-notturna. "
            "Costituisce la base del recupero muscolare, ormonale e cognitivo."
        ),
    },
    "Body Battery Max": {
        "area": "Recupero & Sistema Nervoso",
        "short": "Livello massimo di riserva energetica (0–100 pt) raggiunto al mattino dopo la ricarica notturna.",
        "meaning": (
            "La Body Battery (0–100) è un indice sintetico Garmin che sale durante il riposo (quando domina il sistema parasimpatico e l'HRV è alta) "
            "e scende durante lo sforzo fisico e lo stress mentale. Il valore Max indica il picco raggiunto al risveglio."
        ),
    },
    "Stress Medio": {
        "area": "Recupero & Sistema Nervoso",
        "short": "Attivazione fisiologica media nelle 24h (0–100 pt, calcolata da FC e HRV a riposo): più è basso (< 30), più la giornata è stata rilassata.",
        "meaning": (
            "Lo Stress Garmin (0–100) misura l'attivazione del sistema nervoso simpatico fuori dagli allenamenti: "
            "0–25 stato di riposo, 26–50 stress basso, 51–75 stress medio, 76–100 stress elevato. Anche digestione impegnativa, lavoro intenso o recupero post-gara alzano lo stress."
        ),
    },
    "Passi Giornalieri": {
        "area": "Attività & Carico",
        "short": "Volume totale di locomozione quotidiana (NEAT + sport): contribuisce al dispendio energetico e alla salute metabolica.",
        "meaning": (
            "Numero totale di passi registrati dall'accelerometro da polso nelle 24 ore (camminata quotidiana + corsa/tennis). "
            "Misura il movimento complessivo fuori e dentro l'allenamento strutturato."
        ),
    },
    "Respirazione Notte": {
        "area": "Sonno & Architettura",
        "short": "Atti respiratori al minuto (brpm) durante il sonno: è un parametro molto stabile (12–16 atti/m); aumenti insoliti indicano carico o vie aeree affaticate.",
        "meaning": (
            "Frequenza respiratoria media notturna derivata dalla modulazione dell'onda ottica (PPG) e dell'HRV. "
            "Negli adulti sani a riposo varia pochissimo da una notte all'altra; un rialzo di +1.5/+2 atti/min sopra la tua mediana può segnalare affaticamento o infezione respiratoria."
        ),
    },
    "SpO2 Notturna": {
        "area": "Sonno & Architettura",
        "short": "Saturazione media di ossigeno nel sangue durante il sonno (%): valori normali da polso sono tipicamente ≥ 94–95%.",
        "meaning": (
            "Stima ottica (Pulse Ox) della percentuale di emoglobina legata all'ossigeno nel sonno. "
            "Da polso può subire cali temporanei se il braccio è compresso; valori medi stabili ≥ 94% con pochi minuti < 92% sono fisiologici a bassa quota."
        ),
    },
    "ln(rMSSD) 7 giorni": {
        "area": "Recupero & Sistema Nervoso",
        "short": "Logaritmo naturale dell'HRV mediato su 7 giorni: linearizza l'HRV riducendo il peso dei picchi isolati (metodo scientifico di Plews).",
        "meaning": (
            "In fisiologia dello sport l'HRV ha una distribuzione asimmetrica: si usa il logaritmo naturale ln(rMSSD) mediato su 7 giorni "
            "e lo si confronta con la tua banda individuale a 60 giorni (media ± 0.75·SD). Finché la media a 7g resta dentro la banda, il carico è ben assimilato."
        ),
    },
    "CV HRV (7 giorni)": {
        "area": "Recupero & Sistema Nervoso",
        "short": "Coefficiente di Variazione dell'HRV negli ultimi 7 giorni (%): più è basso (es. 5–12%), più il tuo sistema nervoso è stabile giorno dopo giorno.",
        "meaning": (
            "Rapporto tra deviazione standard e media dell'HRV negli ultimi 7 giorni (CV = SD / Media × 100). "
            "Un CV basso indica stabilità autonomica; un CV molto alto (> 15–20%) indica forti oscillazioni giorno-dopo-giorno dovute a carichi pesanti o sonno irregolare."
        ),
    },
    "Ricarica Body Battery": {
        "area": "Recupero & Sistema Nervoso",
        "short": "Punti di energia guadagnati tra il minimo serale e il massimo del mattino: misura quanto la notte ti ha effettivamente ricaricato.",
        "meaning": (
            "Differenza netta (ΔBB) di Body Battery accumulata durante il sonno. Una ricarica di +35/+60 punti indica una notte altamente rigenerante; "
            "< +25 punti indica che il corpo ha lavorato tutta la notte per smaltire stress, digestione o fatica muscolare."
        ),
    },
    "Velocità Ricarica BB": {
        "area": "Recupero & Sistema Nervoso",
        "short": "Punti di Body Battery recuperati per ogni ora di sonno (pt/ora): misura l'efficienza oraria del tuo riposo.",
        "meaning": (
            "Divide i punti di Body Battery ricaricati per le ore effettive di sonno. Permette di confrontare la qualità rigenerativa del sonno "
            "indipendentemente dal fatto che tu abbia dormito 6 ore o 9 ore (valori buoni: ≥ 5–7 pt/ora)."
        ),
    },
    "Efficienza Sonno": {
        "area": "Sonno & Architettura",
        "short": "Percentuale del tempo a letto trascorsa effettivamente dormendo: ≥ 85–90% indica sonno continuo e con pochi risvegli.",
        "meaning": (
            "Rapporto tra il sonno totale effettivo e il tempo totale trascorso a letto (Sonno / (Sonno + Veglia) × 100). "
            "Un'efficienza ≥ 88–90% è indice di sonno consolidato; valori < 85% indicano sonno frammentato o lunghi risvegli notturni."
        ),
    },
    "Sonno Profondo": {
        "area": "Sonno & Architettura",
        "short": "Percentuale di sonno ad onde lente (N3): è la fase dedicata alla riparazione muscolare, al rilascio di GH e al recupero fisico.",
        "meaning": (
            "Il sonno profondo si concentra nella prima metà della notte. Negli adulti rappresenta tipicamente il 15–23% del sonno totale (circa 60–110 min). "
            "Aumenta dopo allenamenti muscolari intensi; si riduce con alcol, stress serale o camera troppo calda."
        ),
    },
    "Sonno REM": {
        "area": "Sonno & Architettura",
        "short": "Percentuale di sonno REM (Rapid Eye Movement): fase cruciale per memoria, coordinazione motoria e recupero cognitivo/emotivo.",
        "meaning": (
            "Il sonno REM predomina nella seconda metà della notte e al mattino (tipicamente 20–25% del sonno totale, circa 90–120 min). "
            "Svegliarsi molto presto con la sveglia taglia soprattutto gli ultimi cicli ricchi di REM."
        ),
    },
    "Risvegli (≥2 min)": {
        "area": "Sonno & Architettura",
        "short": "Numero di interruzioni del sonno di durata ≥ 2 minuti rilevate nell'ipnogramma notturno.",
        "meaning": (
            "Conta quanti episodi di veglia di almeno 2 minuti interrompono la continuità del sonno. "
            "Pochi risvegli brevi (1–3 a notte) sono normali tra un ciclo e l'altro; molti risvegli riducono il potere ristoratore del sonno."
        ),
    },
    "Latenza 1° Profondo": {
        "area": "Sonno & Architettura",
        "short": "Minuti trascorsi dall'inizio del sonno all'ingresso nella prima fase di sonno profondo (tipicamente 15–40 min).",
        "meaning": (
            "Misura quanto rapidamente il cervello scende nel sonno ad onde lente (N3) dopo l'addormentamento. "
            "Una latenza breve (15–35 min) riflette una buona pressione omeostatica del sonno."
        ),
    },
    "Latenza 1° REM": {
        "area": "Sonno & Architettura",
        "short": "Minuti dall'addormentamento al primo episodio REM: chiude il primo ciclo completo del sonno (tipicamente 70–110 min).",
        "meaning": (
            "Tempo impiegato per completare la prima discesa NREM ed entrare nel primo episodio REM della notte (normalmente tra 70 e 110 minuti)."
        ),
    },
    "Cicli NREM→REM": {
        "area": "Sonno & Architettura",
        "short": "Numero di cicli completi del sonno (da ~90 min ciascuno): una notte di 7.5–8 ore ne comprende idealmente 4 o 5.",
        "meaning": (
            "Ogni notte il cervello alterna fasi di sonno leggero/profondo (NREM) e sonno REM in cicli di circa 90–100 minuti. "
            "Completare 4–5 cicli garantisce sia il recupero fisico (primi cicli) sia quello nervoso/cognitivo (ultimi cicli)."
        ),
    },
    "Indice Regolarità (SRI)": {
        "area": "Sonno & Architettura",
        "short": "Punteggio 0–100 che misura quanto vai a dormire e ti svegli agli stessi orari negli ultimi 30 giorni: più è alto, più il ritmo circadiano è allineato.",
        "meaning": (
            "Lo Sleep Regularity Index (SRI) penalizza la dispersione (deviazione standard circolare) dell'ora di addormentamento e di risveglio. "
            "SRI ≥ 80 indica orari molto regolari; < 65 indica forte variabilità che affatica l'orologio biologico anche se dormi abbastanza ore."
        ),
    },
    "Metà del Sonno Media": {
        "area": "Sonno & Architettura",
        "short": "Orario centrale tra addormentamento e risveglio (es. dormire 23:30–07:30 ha metà sonno alle 03:30): definisce il tuo cronotipo.",
        "meaning": (
            "Il punto mediano del sonno (Mid-sleep) è il marcatore principale della fase del tuo ritmo circadiano interno."
        ),
    },
    "Social Jetlag (WE vs Fer.)": {
        "area": "Sonno & Architettura",
        "short": "Spostamento in minuti della metà del sonno tra weekend e giorni feriali: idealmente entro ±45 min per non sfasare il ritmo circadiano.",
        "meaning": (
            "Differenza tra l'orario mediano del sonno nel fine settimana (venerdì/sabato notte) e nei giorni lavorativi. "
            "Uno sfasamento di +90/+120 min equivale a cambiare fuso orario ogni weekend, causando stanchezza il lunedì mattina."
        ),
    },
    "Variabilità Addormentamento": {
        "area": "Sonno & Architettura",
        "short": "Oscillazione tipica (±SD in minuti) dell'ora in cui ti addormenti: sotto ±30–40 min indica ottima costanza serale.",
        "meaning": (
            "Deviazione standard circolare dell'orario di inizio sonno negli ultimi 30 giorni. Più è contenuta (< 35 min), più rapido e profondo è l'addormentamento."
        ),
    },
    "FC Minima Notturna": {
        "area": "Sonno & Architettura",
        "short": "Il battito più basso toccato nel sonno (Nadir FC): se raggiunto presto nella notte indica digestione leggera e rapido rilassamento.",
        "meaning": (
            "Valore minimo assoluto della frequenza cardiaca durante la notte e relativo orario. "
            "Dopo cene abbondanti, alcol o allenamenti serali intensi il nadir resta più alto e viene raggiunto solo verso il mattino."
        ),
    },
    "Debito Sonno (7 giorni)": {
        "area": "Sonno & Architettura",
        "short": "Ore di sonno mancanti accumulate negli ultimi 7 giorni rispetto al tuo fabbisogno di 8h/notte (0 h = nessun debito).",
        "meaning": (
            "Somma delle ore di sonno perse negli ultimi 7 giorni rispetto all'obiettivo personale configurato (8.0 ore a notte). "
            "Un debito > 3–4 ore su 7 giorni riduce la reattività e abbassa l'HRV."
        ),
    },
    "Debito Sonno (14 giorni)": {
        "area": "Sonno & Architettura",
        "short": "Ore di sonno mancanti accumulate nelle ultime 2 settimane rispetto al fabbisogno di 8h/notte.",
        "meaning": (
            "Bilancio cumulato del deficit di sonno su 14 giorni per monitorare la carenza cronica di riposo."
        ),
    },
    "Carico Acuto (ATL 7g)": {
        "area": "Attività & Carico",
        "short": "Fatica recente (media esponenziale a 7 giorni del TRIMP giornaliero): mostra quanto ti sei allenato nell'ultima settimana.",
        "meaning": (
            "L'Acute Training Load (ATL) è la media mobile esponenziale a 7 giorni del carico TRIMP giornaliero. "
            "Rappresenta la 'Fatica' a breve termine: sale rapidamente dopo giorni di allenamento intenso e scende nei giorni di riposo."
        ),
    },
    "Carico Cronico (CTL 28g)": {
        "area": "Attività & Carico",
        "short": "Fitness di base (media esponenziale a 28 giorni del TRIMP): indica il volume di lavoro a cui il tuo corpo è abituato nell'ultimo mese.",
        "meaning": (
            "Il Chronic Training Load (CTL) è la media mobile esponenziale a 28 giorni del carico TRIMP. "
            "Rappresenta la tua 'Fitness' o cilindrata aerobica storica: costruirla richiede settimane di costanza."
        ),
    },
    "Bilancio TSB (Fitness-Fatica)": {
        "area": "Attività & Carico",
        "short": "Freschezza atletica (TSB = CTL − ATL): positivo (+5/+15) = fresco e brillante per gareggiare; negativo (−10/−25) = in fase di carico.",
        "meaning": (
            "Il Training Stress Balance (TSB = Fitness CTL − Fatica ATL) misura il bilancio tra condizionamento e stanchezza: "
            "valori leggermente negativi (−5 a −20) sono normali durante i blocchi di allenamento; valori positivi (+5 a +15) indicano gambe fresche (ideale prima di un match o gara)."
        ),
    },
    "Rapporto ACWR": {
        "area": "Attività & Carico",
        "short": "Rapporto Fatica 7g / Fitness 28g (ATL / CTL): la zona ottimale e sicura è 0.8–1.3; sopra 1.5 indica aumento troppo brusco del carico.",
        "meaning": (
            "L'Acute:Chronic Workload Ratio (ACWR = ATL / CTL, modello di Gabbett) confronta quanto stai facendo questa settimana con ciò a cui sei abituato da un mese: "
            "< 0.80 sotto-allenamento/scarico; 0.80–1.30 'sweet spot' (progressione sicura); > 1.50 picco di carico a maggior rischio di sovraccarico."
        ),
    },
    "Tempo Zone 7g (Totale)": {
        "area": "Attività & Carico",
        "short": "Minuti totali di attività con cardiofrequenzimetro registrati nelle 5 zone cardiache (Z1–Z5) negli ultimi 7 giorni.",
        "meaning": (
            "Somma di tutti i minuti trascorsi nelle zone di frequenza cardiaca Z1–Z5 durante le attività degli ultimi 7 giorni."
        ),
    },
    "Quota Bassa Intensità (Z1+Z2)": {
        "area": "Attività & Carico",
        "short": "Percentuale del tempo settimanale passata in Zona 1 e Zona 2 (recupero e base aerobica): costruisce resistenza senza bruciare il recupero.",
        "meaning": (
            "Frazione del volume settimanale svolta a bassa intensità cardiaca (Z1 + Z2, sotto ~75–80% FCmax). "
            "Negli sport di resistenza una quota Z1+Z2 del 65–80% permette di sviluppare i mitocondri e recuperare rapidamente."
        ),
    },
    "Quota Alta Intensità (Z4+Z5)": {
        "area": "Attività & Carico",
        "short": "Percentuale del tempo settimanale in Zona 4 (Soglia) e Zona 5 (VO2max): stimola la potenza aerobica ma richiede 24–48h di recupero.",
        "meaning": (
            "Frazione del tempo settimanale trascorsa ad alta intensità (Z4 Soglia Anaerobica + Z5 Massimale). "
            "Nel tennis e nelle corse veloci questa quota sale: monitorarla aiuta a non accumulare troppi giorni consecutivi in Z4–Z5."
        ),
    },
    "TRIMP": {
        "area": "Attività & Carico",
        "short": "Training Impulse di Banister: misura il carico interno di una seduta combinando durata e intensità cardiaca pesata esponenzialmente.",
        "meaning": (
            "Il TRIMP (Training Impulse) trasforma durata e frequenza cardiaca di un allenamento in un unico punteggio di carico fisiologico. "
            "Poiché il fattore esponenziale cresce con la riserva cardiaca, 40 minuti a battiti alti (es. Corsa in Z3–Z4 o Tennis intenso) generano molto più TRIMP di 60 minuti blandi in Z1 (es. Pesi con ampie pause)."
        ),
    },
    "HRR (60s / 120s)": {
        "area": "Attività & Carico",
        "short": "Quanti battiti scende il cuore in 60s e 120s dopo uno sforzo: un calo rapido (es. ≥ 20–30 bpm in 60s) indica ottima reattività vagale e forma.",
        "meaning": (
            "L'Heart Rate Recovery (HRR60 e HRR120) misura di quanti bpm scende la frequenza cardiaca a 60 e 120 secondi dalla cessazione di uno sforzo attivo. "
            "È un indicatore diretto della riattivazione del nervo vago (sistema parasimpatico): più battiti perdi in 1–2 minuti, migliore è il tuo stato di forma e freschezza. "
            "Richiede la serie secondo-per-secondo (`activity_details`) con pause di recupero o 1–2 minuti a fine seduta."
        ),
    },
    "Deriva Cardiaca (Cardiac Drift)": {
        "area": "Attività & Carico",
        "short": "Aumento % della FC tra la 1ª e la 2ª metà della seduta: < 5% a passo costante indica ottima tenuta aerobica e buona idratazione.",
        "meaning": (
            "La Deriva Cardiaca confronta la frequenza cardiaca (e il rapporto velocità/FC, detto disaccoppiamento Pa:Hr) della seconda metà dell'allenamento con la prima metà. "
            "Su una corsa a passo costante ≥ 30 min, una deriva < 3.5–5% indica che la resistenza aerobica regge perfettamente la distanza; > 5% segnala affaticamento muscolare, caldo o disidratazione. "
            "Negli sport intermittenti (es. Tennis) indica la variazione di intensità cardiaca tra seconda e prima metà del match."
        ),
    },
    "Efficienza Velocità/FC": {
        "area": "Attività & Carico",
        "short": "Metri percorsi per ogni singolo battito cardiaco (m/battito): più questo numero sale nel tempo a parità di sport, più sei diventato economico.",
        "meaning": (
            "L'Efficiency Factor (EF = velocità in m/min ÷ FC media in bpm) misura quanta strada percorri per ogni battito del cuore. "
            "Ad esempio, correre a 5:00/km (200 m/min) a 156 bpm dà 1.28 m/battito; se dopo un mese corri allo stesso passo a 148 bpm, l'efficienza sale a 1.35 m/battito."
        ),
    },
    "Training Effect (AE / AnE)": {
        "area": "Attività & Carico",
        "short": "Stima Garmin (da 0.0 a 5.0) dello stimolo allenante Aerobico (AE, resistenza) e Anaerobico (AnE, scatti e picchi) della seduta.",
        "meaning": (
            "Il Training Effect Garmin valuta da 0.0 a 5.0 quanto la seduta migliora la capacità Aerobica (AE) e Anaerobica (AnE): "
            "1.0–1.9 Recupero/Beneficio minimo; 2.0–2.9 Mantenimento; 3.0–3.9 Miglioramento; 4.0–4.9 Miglioramento elevato; 5.0 Sovraccarico."
        ),
    },
    "Baseline Mediana 30g & Z-score Robusto": {
        "area": "Statistica & Trend",
        "short": "Confronta il dato di oggi con la tua mediana degli ultimi 30 giorni usando lo scostamento mediano assoluto (MAD), immune ai valori anomali.",
        "meaning": (
            "Invece di usare soglie fisse uguali per tutti o la media aritmetica (che viene falsata da una singola notte insonne), "
            "la dashboard calcola la tua Mediana mobile a 30 giorni e la dispersione robusta MAD. Lo Z-score robusto z = (Valore − Mediana) / (1.4826 · MAD) "
            "indica di quante tue 'deviazioni standard' il valore di oggi si discosta dalla tua abitudine: tra −1 e +1 sei 'in linea con la tua norma'."
        ),
    },
    "Trend Theil-Sen, Mann-Kendall & CUSUM": {
        "area": "Statistica & Trend",
        "short": "Metodi statistici robusti per capire se una metrica sta davvero salendo/scendendo (Theil-Sen, p-value) o se ha cambiato regime (CUSUM).",
        "meaning": (
            "La pendenza di Theil-Sen calcola la mediana di tutte le pendenze tra coppie di giorni (es. +0.8 ms/settimana), mentre il test di Mann-Kendall "
            "verifica se il trend è statisticamente reale (p < 0.05) o semplice oscillazione casuale. Il CUSUM rileva invece i punti di svolta in cui la media si sposta stabilmente verso l'alto o verso il basso."
        ),
    },
    "Correlazione di Spearman (ρ) & Lag": {
        "area": "Statistica & Trend",
        "short": "Misura da −1 a +1 se due metriche si muovono insieme (Lag 0) o se una metrica di oggi influenza quella di domani/dopo-domani (Lag 1/2).",
        "meaning": (
            "Il coefficiente per ranghi di Spearman (ρ tra −1 e +1) valuta relazioni monotone senza assumere linearità: "
            "ρ > 0 indica che all'aumentare di X aumenta anche Y; ρ < 0 indica che all'aumentare di X diminuisce Y (es. più carico oggi → HRV più bassa domani a Lag +1). "
            "Ricorda sempre: correlazione esplorativa non dimostra da sola un rapporto di causa-effetto."
        ),
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
    app.run(host=host, port=port, debug=False, use_reloader=True)


if __name__ == "__main__":
    main()
