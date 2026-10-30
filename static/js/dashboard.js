/* ==========================================================================
   dashboard.js — Componenti interattivi, Mini-Calendario, Motore Matematico
   LaTeX->HTML offline e Grafici Plotly della Garmin Personal Dashboard
   ========================================================================== */

(function () {
  "use strict";

  let lastFocusedTrigger = null;

  function getCssToken(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  /* ========================================================================
     1. MOTORE DI RENDERING MATEMATICO OFFLINE (LaTeX -> HTML tipografico)
     ======================================================================== */

  function extractBracedGroup(str, startIdx) {
    if (startIdx >= str.length || str[startIdx] !== "{") {
      return { content: "", nextIdx: startIdx };
    }
    let depth = 0;
    let i = startIdx;
    for (; i < str.length; i++) {
      if (str[i] === "{" && (i === 0 || str[i - 1] !== "\\")) {
        depth++;
      } else if (str[i] === "}" && (i === 0 || str[i - 1] !== "\\")) {
        depth--;
        if (depth === 0) {
          return { content: str.slice(startIdx + 1, i), nextIdx: i + 1 };
        }
      }
    }
    return { content: str.slice(startIdx + 1), nextIdx: str.length };
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  const LATEX_SYMBOLS = {
    "\\cdot": " · ",
    "\\times": " × ",
    "\\pm": " ± ",
    "\\mp": " ∓ ",
    "\\le": " ≤ ",
    "\\leq": " ≤ ",
    "\\ge": " ≥ ",
    "\\geq": " ≥ ",
    "\\neq": " ≠ ",
    "\\approx": " ≈ ",
    "\\mid": " | ",
    "\\in": " ∈ ",
    "\\{": "{",
    "\\}": "}",
    "\\to": " → ",
    "\\rightarrow": " → ",
    "\\Delta": "Δ",
    "\\alpha": "α",
    "\\beta": "β",
    "\\gamma": "γ",
    "\\theta": "θ",
    "\\lambda": "λ",
    "\\mu": "μ",
    "\\rho": "ρ",
    "\\sigma": "σ",
    "\\tau": "τ",
    "\\circ": "°",
    "\\arg": "<span class=\"math-text\">arg</span>",
    "\\sgn": "<span class=\"math-text\">sgn</span>",
    "\\cov": "<span class=\"math-text\">cov</span>",
    "\\ln": "<span class=\"math-text\">ln</span>",
    "\\exp": "<span class=\"math-text\">exp</span>",
    "\\max": "<span class=\"math-text\">max</span>",
    "\\min": "<span class=\"math-text\">min</span>",
    "\\left(": "(",
    "\\right)": ")",
    "\\left[": "[",
    "\\right]": "]",
    "\\!": "",
    "\\,": "&thinsp;",
    "\\;": "&ensp;",
    "\\%": "%"
  };

  function parseLatexFragment(expr) {
    if (!expr) return "";
    let out = "";
    let i = 0;
    const n = expr.length;

    while (i < n) {
      const ch = expr[i];

      if (expr.startsWith("\\frac", i)) {
        i += 5;
        while (i < n && expr[i] === " ") i++;
        const num = extractBracedGroup(expr, i);
        i = num.nextIdx;
        while (i < n && expr[i] === " ") i++;
        const den = extractBracedGroup(expr, i);
        i = den.nextIdx;
        out +=
          '<span class="math-frac">' +
          '<span class="math-num">' + parseLatexFragment(num.content) + "</span>" +
          '<span class="math-den">' + parseLatexFragment(den.content) + "</span>" +
          "</span>";
        continue;
      }

      if (expr.startsWith("\\text", i)) {
        i += 5;
        while (i < n && expr[i] === " ") i++;
        const grp = extractBracedGroup(expr, i);
        i = grp.nextIdx;
        out += '<span class="math-text">' + escapeHtml(grp.content.replace(/\\%/g, "%")) + "</span>";
        continue;
      }

      if (expr.startsWith("\\overline", i) || expr.startsWith("\\bar", i)) {
        i += expr.startsWith("\\overline", i) ? 9 : 4;
        while (i < n && expr[i] === " ") i++;
        const grp = extractBracedGroup(expr, i);
        i = grp.nextIdx;
        out += '<span class="math-overline">' + parseLatexFragment(grp.content) + "</span>";
        continue;
      }

      if (expr.startsWith("\\sqrt", i)) {
        i += 5;
        while (i < n && expr[i] === " ") i++;
        const grp = extractBracedGroup(expr, i);
        i = grp.nextIdx;
        out +=
          '<span class="math-sqrt"><span class="math-sqrt-sym">√</span><span class="math-sqrt-body">' +
          parseLatexFragment(grp.content) +
          "</span></span>";
        continue;
      }

      if (expr.startsWith("\\hat", i)) {
        i += 4;
        while (i < n && expr[i] === " ") i++;
        const grp = extractBracedGroup(expr, i);
        i = grp.nextIdx;
        out += '<span class="math-hat">' + parseLatexFragment(grp.content) + "̂</span>";
        continue;
      }

      if (expr.startsWith("\\mathbb", i)) {
        i += 7;
        const grp = extractBracedGroup(expr, i);
        i = grp.nextIdx;
        out += "<strong>" + escapeHtml(grp.content) + "</strong>";
        continue;
      }

      if (expr.startsWith("\\sum", i)) {
        i += 4;
        let subHtml = "";
        let supHtml = "";
        while (i < n && (expr[i] === " " || expr[i] === "_" || expr[i] === "^")) {
          if (expr[i] === " ") {
            i++;
            continue;
          }
          const isSub = expr[i] === "_";
          i++;
          let part = "";
          if (expr[i] === "{") {
            const g = extractBracedGroup(expr, i);
            part = parseLatexFragment(g.content);
            i = g.nextIdx;
          } else if (i < n) {
            part = escapeHtml(expr[i]);
            i++;
          }
          if (isSub) subHtml = part;
          else supHtml = part;
        }
        out +=
          '<span class="math-sum">' +
          (supHtml ? '<span class="math-sum-sup">' + supHtml + "</span>" : "") +
          '<span class="math-sum-sym">∑</span>' +
          (subHtml ? '<span class="math-sum-sub">' + subHtml + "</span>" : "") +
          "</span>";
        continue;
      }

      if (ch === "_" || ch === "^") {
        const tag = ch === "_" ? "sub" : "sup";
        i++;
        if (i < n && expr[i] === "{") {
          const grp = extractBracedGroup(expr, i);
          i = grp.nextIdx;
          out += "<" + tag + ">" + parseLatexFragment(grp.content) + "</" + tag + ">";
        } else if (i < n) {
          out += "<" + tag + ">" + escapeHtml(expr[i]) + "</" + tag + ">";
          i++;
        }
        continue;
      }

      if (ch === "\\") {
        let matched = false;
        const symKeys = Object.keys(LATEX_SYMBOLS).sort(function (a, b) {
          return b.length - a.length;
        });
        for (let k = 0; k < symKeys.length; k++) {
          const key = symKeys[k];
          if (expr.startsWith(key, i)) {
            out += LATEX_SYMBOLS[key];
            i += key.length;
            matched = true;
            break;
          }
        }
        if (matched) continue;
        i++;
        continue;
      }

      if (ch === "{" || ch === "}") {
        i++;
        continue;
      }

      out += escapeHtml(ch);
      i++;
    }
    return out;
  }

  function renderLatexToHtml(latexStr) {
    if (!latexStr) return "";
    const parts = String(latexStr)
      .split(/,\s*\\quad|\\quad/g)
      .map(function (s) { return s.trim(); })
      .filter(Boolean);

    return parts
      .map(function (part) {
        return '<div class="math-eq-line">' + parseLatexFragment(part) + "</div>";
      })
      .join("");
  }

  /* ========================================================================
     2. PANNELLO DI DETTAGLIO STRUTTURATO E DISTRIBUITO IN GRIGLIE
     ======================================================================== */

  const KPI_FIELD_LABELS = {
    efficiency_pct: { label: "Efficienza del Sonno", unit: "%" },
    deep_pct: { label: "Quota Sonno Profondo", unit: "%" },
    light_pct: { label: "Quota Sonno Leggero", unit: "%" },
    rem_pct: { label: "Quota Sonno REM", unit: "%" },
    sleep_onset_latency_min: { label: "Latenza Inizio Sonno", unit: "min" },
    awakenings_count: { label: "Risvegli (≥ 2 min)", unit: "eventi" },
    latency_to_deep_min: { label: "Latenza 1° Profondo", unit: "min" },
    latency_to_rem_min: { label: "Latenza 1° REM", unit: "min" },
    estimated_cycles: { label: "Cicli NREM→REM Stimati", unit: "cicli" },
    bedtime_sd_min: { label: "Variabilità Addormentamento", unit: "min SD" },
    waketime_sd_min: { label: "Variabilità Risveglio", unit: "min SD" },
    midsleep_sd_min: { label: "Variabilità Metà Sonno", unit: "min SD" },
    midsleep_mean_hhmm: { label: "Orario Medio Metà Sonno", unit: "" },
    social_jetlag_min: { label: "Social Jetlag (WE vs Feriali)", unit: "min" },
    sri_score: { label: "Indice Regolarità (SRI)", unit: "/ 100" },
    min_hr_bpm: { label: "FC Minima Notturna", unit: "bpm" },
    min_hr_time_hhmm: { label: "Orario Nadir Cardiaco", unit: "" },
    hrv_night_ms: { label: "HRV Media Notturna", unit: "ms" },
    hrv_night_sd_ms: { label: "SD Intra-Notte HRV", unit: "ms" },
    respiration_sleep_brpm: { label: "Respirazione Notturna", unit: "atti/m" },
    spo2_sleep_avg_pct: { label: "SpO2 Media Notturna", unit: "%" },
    spo2_sleep_min_pct: { label: "SpO2 Minima Notturna", unit: "%" },
    spo2_below_threshold_min: { label: "Tempo SpO2 sotto soglia", unit: "min" },
    sleep_debt_7d_hours: { label: "Debito di Sonno (7g)", unit: "h" },
    sleep_debt_14d_hours: { label: "Debito di Sonno (14g)", unit: "h" },
    bb_charged: { label: "Body Battery Caricata (+)", unit: "pt" },
    bb_drained: { label: "Body Battery Scaricata (-)", unit: "pt" },
    bb_max: { label: "Body Battery Massima", unit: "pt" },
    bb_min: { label: "Body Battery Minima", unit: "pt" },
    recharge_rate_pt_per_hour: { label: "Velocità Ricarica Notturna", unit: "pt/h" },
    atl_7d: { label: "Carico Acuto (ATL 7g)", unit: "TRIMP/g" },
    ctl_28d: { label: "Carico Cronico (CTL 28g)", unit: "TRIMP/g" },
    tsb_form: { label: "Bilancio Fitness-Fatica (TSB)", unit: "pt" },
    acwr: { label: "Rapporto Acuto/Cronico (ACWR)", unit: "ratio" },
    monotony_7d: { label: "Monotonia di Foster (7g)", unit: "indice" },
    foster_strain_7d: { label: "Strain di Foster (7g)", unit: "pt" },
    weekly_load_7d: { label: "Carico Totale 7 giorni", unit: "TRIMP" },
    mean_daily_load_7d: { label: "Carico Medio Giornaliero (7g)", unit: "TRIMP/g" },
    sd_daily_load_7d: { label: "Deviazione Standard Carico (7g)", unit: "TRIMP/g" },
    active_days_7d: { label: "Giorni Attivi su 7", unit: "giorni" },
    total_zone_minutes: { label: "Tempo Totale in Zona (7g)", unit: "min" },
    low_intensity_z1_z2_pct: { label: "Quota Bassa Intensità (Z1+Z2)", unit: "%" },
    moderate_z3_pct: { label: "Quota Intensità Media (Z3)", unit: "%" },
    high_intensity_z4_z5_pct: { label: "Quota Alta Intensità (Z4+Z5)", unit: "%" },
    ln_rmssd: { label: "ln(rMSSD) Notte", unit: "ln(ms)" },
    ln_rmssd_7d: { label: "Media 7g ln(rMSSD)", unit: "ln(ms)" },
    cv_7d_pct: { label: "Coefficiente Variazione (CV 7g)", unit: "%" },
    band_status: { label: "Stato rispetto alla Banda 60g", unit: "" }
  };

  function openDetailDrawer(payload, triggerEl) {
    const backdrop = document.getElementById("detail-backdrop");
    const panel = document.getElementById("detail-panel");
    if (!backdrop || !panel || !payload) return;

    lastFocusedTrigger = triggerEl || document.activeElement;

    const cardLabel = triggerEl ? triggerEl.getAttribute("data-card-label") : null;
    const cardValue = triggerEl ? triggerEl.getAttribute("data-card-value") : null;
    const cardUnit = triggerEl ? triggerEl.getAttribute("data-card-unit") : null;
    let baselineObj = null;
    if (triggerEl && triggerEl.getAttribute("data-baseline-json")) {
      try {
        baselineObj = JSON.parse(triggerEl.getAttribute("data-baseline-json"));
      } catch (e) {
        baselineObj = null;
      }
    }

    const titleEl = document.getElementById("drawer-title");
    const valueEl = document.getElementById("drawer-value");
    const statusEl = document.getElementById("drawer-status");
    const expSecEl = document.getElementById("drawer-explanation-section");
    const expTextEl = document.getElementById("drawer-explanation-text");
    const kpiSecEl = document.getElementById("drawer-kpi-section");
    const kpiGridEl = document.getElementById("drawer-kpi-grid");
    const zonesSecEl = document.getElementById("drawer-zones-section");
    const zonesBarsEl = document.getElementById("drawer-zones-bars");
    const compSecEl = document.getElementById("drawer-comp-section");
    const compListEl = document.getElementById("drawer-components");
    const howTitleEl = document.getElementById("drawer-how-title");
    const howFormulaEl = document.getElementById("drawer-how-formula");
    const howParamsEl = document.getElementById("drawer-how-params");
    const howLimitsEl = document.getElementById("drawer-how-limits");
    const howContentEl = document.getElementById("drawer-how-content");
    const howToggleBtn = document.getElementById("drawer-how-toggle");

    // 1. Titolo e Valore protagonista
    if (titleEl) {
      titleEl.textContent = cardLabel || payload.title || payload.name || "Dettaglio metrica";
    }
    if (valueEl) {
      if (cardValue !== null && cardValue !== "") {
        valueEl.textContent = cardValue + (cardUnit ? " " + cardUnit : "");
      } else {
        let v = payload.score !== undefined && payload.score !== null ? payload.score : payload.display_value;
        if (v === undefined && payload.provisional_score !== undefined && payload.provisional_score !== null) {
          v = payload.provisional_score + "*";
        }
        if (v === undefined && payload.value !== undefined && typeof payload.value !== "object") {
          v = payload.value;
        }
        const u = payload.unit && typeof v !== "object" ? " " + payload.unit : (payload.score !== undefined ? " / 100" : "");
        valueEl.textContent = v !== undefined && v !== null ? String(v) + u : "Dato assente";
      }
    }

    if (statusEl) {
      statusEl.className = "drawer-status-badge";
      if (baselineObj && baselineObj.status === "ok") {
        statusEl.textContent = baselineObj.direction_arrow + " " + (baselineObj.semantic_label || baselineObj.direction_text);
        if (baselineObj.semantic_state) {
          statusEl.classList.add("status-" + baselineObj.semantic_state);
        }
      } else if (payload.subtitle || payload.status_label) {
        statusEl.textContent = payload.subtitle || payload.status_label;
      } else if (payload.quality_flag) {
        statusEl.textContent = "Qualità: " + payload.quality_flag + " (n=" + (payload.n_samples || 0) + ")";
      } else {
        statusEl.textContent = "Analisi deterministica";
      }
    }

    // 1B. Spiegazione del significato in italiano semplice (formattata per leggibilità)
    const expStr = payload.explanation || (triggerEl ? triggerEl.getAttribute("data-explanation") : "");
    if (expSecEl && expTextEl) {
      if (expStr && expStr.trim() !== "") {
        expSecEl.style.display = "block";
        const lines = expStr
          .split(/\n+/)
          .map(function (l) { return l.trim(); })
          .filter(Boolean);
        expTextEl.innerHTML = lines
          .map(function (line) {
            const clean = escapeHtml(line.replace(/^[•\-]\s*/, ""));
            const formatted = clean.replace(/^([^:]{2,42}):/, "<strong>$1:</strong>");
            return '<div class="exp-paragraph">' + (line.startsWith("•") ? "• " : "") + formatted + "</div>";
          })
          .join("");
      } else {
        expSecEl.style.display = "none";
        expTextEl.innerHTML = "";
      }
    }

    // 2A. Costruzione Griglia KPI a 2 colonne (ben distribuita)
    const kpiItems = [];
    if (Array.isArray(payload.kpis) && payload.kpis.length > 0) {
      payload.kpis.forEach(function (k) {
        kpiItems.push(k);
      });
    } else {
      if (baselineObj) {
        const uStr = cardUnit ? " " + cardUnit : "";
        kpiItems.push({
          label: "Valore del Giorno",
          value: baselineObj.value !== null && baselineObj.value !== undefined ? baselineObj.value + uStr : "Dato assente"
        });
        kpiItems.push({
          label: "Norma Personale (Mediana 30g)",
          value: baselineObj.median !== null && baselineObj.median !== undefined ? baselineObj.median + uStr : "In calibrazione"
        });
        kpiItems.push({
          label: "Dispersione Robusta (1.48·MAD)",
          value: baselineObj.robust_sd !== null && baselineObj.robust_sd !== undefined ? "±" + baselineObj.robust_sd + uStr : "—"
        });
        kpiItems.push({
          label: "Scostamento Z-Score Robusto",
          value: baselineObj.z_robust !== null && baselineObj.z_robust !== undefined ? (baselineObj.z_robust > 0 ? "+" : "") + baselineObj.z_robust + " σ" : "—"
        });
        kpiItems.push({
          label: "Stato rispetto alla Baseline",
          value: (baselineObj.direction_arrow || "") + " " + (baselineObj.semantic_label || baselineObj.direction_text || "—"),
          semanticClass: baselineObj.semantic_state ? "status-" + baselineObj.semantic_state : ""
        });
        kpiItems.push({
          label: "Copertura Storica (30 giorni)",
          value: (baselineObj.n_days || 0) + " / 30 giorni validi"
        });
      }

      // Se payload.value è un dizionario (es. sleep_arch, sleep_reg, sleep_phys, hrv_band, load_stats), estraiamo i campi scalari
      const valObj =
        payload.value && typeof payload.value === "object"
          ? payload.value.latest && typeof payload.value.latest === "object"
            ? payload.value.latest
            : payload.value
          : null;

      if (valObj) {
        Object.keys(valObj).forEach(function (k) {
          const v = valObj[k];
          if (v === null || v === undefined || typeof v === "object") return;
          const meta = KPI_FIELD_LABELS[k];
          if (meta) {
            kpiItems.push({
              label: meta.label,
              value: v + (meta.unit ? " " + meta.unit : "")
            });
          }
        });

        // Se è un oggetto di trend con theil_sen_mann_kendall
        if (valObj.theil_sen_mann_kendall && typeof valObj.theil_sen_mann_kendall === "object") {
          const mk = valObj.theil_sen_mann_kendall;
          kpiItems.push({ label: "Pendenza Theil-Sen (settimanale)", value: (mk.slope_per_week >= 0 ? "+" : "") + mk.slope_per_week });
          kpiItems.push({ label: "Direzione Trend (Mann-Kendall)", value: mk.direction || "stabile" });
          kpiItems.push({ label: "p-value Mann-Kendall", value: String(mk.p_value) });
          kpiItems.push({ label: "Statistica Z Mann-Kendall", value: String(mk.z_stat) });
        }
      }
    }

    if (kpiSecEl && kpiGridEl) {
      kpiGridEl.innerHTML = "";
      if (kpiItems.length > 0) {
        kpiSecEl.style.display = "block";
        kpiItems.forEach(function (item) {
          const box = document.createElement("div");
          box.className = "drawer-kpi-box" + (item.semanticClass ? " " + item.semanticClass : "");
          box.innerHTML =
            '<span class="drawer-kpi-label">' + escapeHtml(item.label) + "</span>" +
            '<span class="drawer-kpi-val">' + escapeHtml(item.value) + "</span>";
          kpiGridEl.appendChild(box);
        });
      } else {
        kpiSecEl.style.display = "none";
      }
    }

    // 2A-bis. Tracciato 1-secondo FC & Velocità nel Drawer per le Attività
    const tsSecEl = document.getElementById("drawer-timeseries-section");
    const tsChartEl = document.getElementById("drawer-activity-chart");
    const tsBadgeEl = document.getElementById("drawer-timeseries-badge");
    const tsNoteEl = document.getElementById("drawer-timeseries-note");
    if (tsSecEl && tsChartEl) {
      if (payload.activity_id) {
        tsSecEl.style.display = "block";
        tsChartEl.innerHTML = '<div class="state-box" style="padding:18px;">Caricamento tracciato ad alta risoluzione...</div>';
        if (tsBadgeEl) tsBadgeEl.textContent = "Caricamento...";
        if (tsNoteEl) tsNoteEl.textContent = "";

        fetch("/api/activity/" + encodeURIComponent(payload.activity_id) + "/timeseries")
          .then(function (r) { return r.json(); })
          .then(function (tsData) {
            if (!tsData || !tsData.available) {
              tsChartEl.innerHTML =
                '<div class="state-box" style="padding:18px;">' +
                "Serie secondo-per-secondo non ancora presente in cache per questa attività. " +
                'Clicca <strong>🔄 Sincronizza Ora</strong> in alto per scaricare i tracciati mancanti.' +
                "</div>";
              if (tsBadgeEl) tsBadgeEl.textContent = "Assente";
              return;
            }
            tsChartEl.innerHTML = "";
            if (tsBadgeEl) tsBadgeEl.textContent = tsData.n_raw_samples + " campioni";
            if (tsNoteEl) {
              let noteParts = [];
              if (tsData.half1_hr_mean !== null && tsData.half2_hr_mean !== null) {
                noteParts.push("FC media 1ª metà: " + tsData.half1_hr_mean + " bpm → 2ª metà: " + tsData.half2_hr_mean + " bpm");
              }
              if (Array.isArray(tsData.hrr_markers) && tsData.hrr_markers.length > 0) {
                noteParts.push("Evidenziati " + tsData.hrr_markers.length + " picchi con recupero HRR a 60s");
              }
              tsNoteEl.textContent = noteParts.join(" · ");
            }
            renderActivityTimeseriesChart("drawer-activity-chart", tsData);
          })
          .catch(function () {
            tsChartEl.innerHTML = '<div class="state-box" style="padding:18px;">Impossibile caricare il tracciato.</div>';
          });
      } else {
        tsSecEl.style.display = "none";
        tsChartEl.innerHTML = "";
      }
    }

    // 2B. Barre delle 5 Zone Cardiache (per le attività o per la distribuzione settimanale)
    let zonesList = Array.isArray(payload.zones_bars) ? payload.zones_bars : [];
    if (zonesList.length === 0 && payload.value && Array.isArray(payload.value.zones)) {
      const zColors = ["#71717A", "#2DD4BF", "#818CF8", "#F59E0B", "#FB923C"];
      zonesList = payload.value.zones.map(function (z, idx) {
        return {
          zone: "Z" + z.zone,
          name: "Zona " + z.zone,
          min: z.minutes,
          pct: Math.round(z.pct),
          color: zColors[idx] || "#2DD4BF"
        };
      });
    }

    if (zonesSecEl && zonesBarsEl) {
      zonesBarsEl.innerHTML = "";
      if (zonesList.length > 0) {
        zonesSecEl.style.display = "block";
        zonesList.forEach(function (zb) {
          const row = document.createElement("div");
          row.className = "drawer-zone-row";
          row.innerHTML =
            '<div class="drawer-zone-head">' +
            "<span>" + escapeHtml(zb.name || zb.zone) + "</span>" +
            "<span>" + escapeHtml(zb.min) + " min (" + escapeHtml(zb.pct) + "%)</span>" +
            "</div>" +
            '<div class="subscore-bar-track" style="margin-top:2px;">' +
            '<div class="subscore-bar-fill" style="width:' + Math.min(100, Math.max(0, zb.pct)) + "%;background:" + zb.color + ';"></div>' +
            "</div>";
          zonesBarsEl.appendChild(row);
        });
      } else {
        zonesSecEl.style.display = "none";
      }
    }

    // 2C. Scomposizione Componenti / Assunzioni / Note
    if (compListEl && compSecEl) {
      compListEl.innerHTML = "";
      let hasCompContent = false;

      if (Array.isArray(payload.components) && payload.components.length > 0) {
        hasCompContent = true;
        payload.components.forEach(function (c) {
          const card = document.createElement("div");
          card.className = "drawer-comp-card";
          const weightPct = c.weight !== undefined ? Math.round(c.weight * 100) + "%" : "";
          const subVal = c.subscore !== undefined ? Math.round(c.subscore) : null;
          const barHtml =
            subVal !== null
              ? '<div class="subscore-bar-track"><div class="subscore-bar-fill" style="width:' +
                Math.min(100, Math.max(0, subVal)) +
                '%;"></div></div>'
              : "";
          card.innerHTML =
            '<div style="display:flex;justify-content:space-between;align-items:center;font-weight:700;color:var(--text-primary);">' +
            "<span>" + escapeHtml(c.name || c.key) + "</span>" +
            '<span class="quality-pill">' +
            (c.value !== null && c.value !== undefined ? escapeHtml(c.value + " " + (c.unit || "")) : "dato assente") +
            "</span>" +
            "</div>" +
            '<div style="display:flex;justify-content:space-between;font-size:var(--text-xs);color:var(--text-secondary);margin-top:6px;">' +
            "<span>" + escapeHtml(c.baseline_label || (c.baseline !== null && c.baseline !== undefined ? "Riferimento: " + c.baseline : "")) + "</span>" +
            "<span>" + (weightPct ? "Peso " + weightPct : "") + (subVal !== null ? " · Punteggio: <strong>" + subVal + "/100</strong>" : "") + "</span>" +
            "</div>" +
            barHtml;
          compListEl.appendChild(card);
        });
      }

      if (Array.isArray(payload.assumptions) && payload.assumptions.length > 0) {
        hasCompContent = true;
        const noteCard = document.createElement("div");
        noteCard.className = "drawer-comp-card";
        noteCard.innerHTML =
          '<div class="drawer-kpi-label" style="margin-bottom:6px;">Note di calcolo e qualità del dato</div>' +
          payload.assumptions
            .map(function (a) {
              return '<div style="font-size:var(--text-xs);color:var(--text-secondary);margin-bottom:4px;">• ' + escapeHtml(a) + "</div>";
            })
            .join("");
        compListEl.appendChild(noteCard);
      } else if (payload.reason) {
        hasCompContent = true;
        const p = document.createElement("div");
        p.className = "drawer-comp-card";
        p.textContent = payload.reason;
        compListEl.appendChild(p);
      }

      compSecEl.style.display = hasCompContent ? "block" : "none";
    }

    // 3. Strato "Come si calcola" (Formula matematica, Parametri in griglia a 2 colonne, Limiti)
    const hw = payload.how_it_works || {};
    if (howTitleEl) howTitleEl.textContent = hw.title || "Come si calcola (formula, parametri e limiti)";
    if (howFormulaEl) {
      const rawLatex =
        hw.formula_latex ||
        "z_{\\text{rob}} = \\frac{x_t - m_{30}}{1.4826 \\cdot \\text{MAD}_{30}}, \\quad \\text{MAD}_{30} = \\text{mediana}(|x - m_{30}|)";
      howFormulaEl.innerHTML = renderLatexToHtml(rawLatex);
    }
    if (howParamsEl) {
      howParamsEl.innerHTML = "";
      const paramsObj = hw.parameters || {
        "Finestra mobile": "30 giorni precedenti al giorno selezionato",
        "Giorni minimi richiesti": "14 giorni validi (altrimenti 'calibrazione in corso')",
        "Soglia in linea": "|z| ≤ 0.75 deviazioni standard robuste"
      };
      Object.keys(paramsObj).forEach(function (k) {
        const item = document.createElement("div");
        item.className = "drawer-param-item";
        item.innerHTML =
          '<div class="drawer-kpi-label">' + escapeHtml(k) + "</div>" +
          '<div style="color:var(--text-primary);font-weight:600;margin-top:2px;">' + escapeHtml(paramsObj[k]) + "</div>";
        howParamsEl.appendChild(item);
      });
    }
    if (howLimitsEl) {
      howLimitsEl.textContent =
        (hw.limits ? hw.limits + " " : "") +
        "Stime derivate da sensore ottico da polso: non costituiscono indicazioni o diagnosi mediche.";
    }

    if (howContentEl) howContentEl.classList.add("open");
    if (howToggleBtn) howToggleBtn.setAttribute("aria-expanded", "true");

    backdrop.classList.add("open");
    panel.classList.add("open");
    panel.setAttribute("aria-hidden", "false");

    if (!history.state || !history.state.drawerOpen) {
      history.pushState({ drawerOpen: true }, "");
    }

    const closeBtn = document.getElementById("drawer-close-btn");
    if (closeBtn) closeBtn.focus();
  }

  function closeDetailDrawer(fromPopState) {
    const backdrop = document.getElementById("detail-backdrop");
    const panel = document.getElementById("detail-panel");
    if (!backdrop || !panel || !panel.classList.contains("open")) return;

    backdrop.classList.remove("open");
    panel.classList.remove("open");
    panel.setAttribute("aria-hidden", "true");

    if (!fromPopState && history.state && history.state.drawerOpen) {
      history.back();
    }
    if (lastFocusedTrigger && typeof lastFocusedTrigger.focus === "function") {
      lastFocusedTrigger.focus();
    }
  }

  /* ========================================================================
     3. MINI-CALENDARIO INTERATTIVO CON I GIORNI DISPONIBILI
     ======================================================================== */

  const ITALIAN_MONTHS = [
    "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"
  ];

  function initMiniCalendar() {
    const wrap = document.getElementById("date-selector-wrap");
    const toggleBtn = document.getElementById("calendar-toggle-btn");
    const popover = document.getElementById("calendar-popover");
    const prevBtn = document.getElementById("cal-prev-month");
    const nextBtn = document.getElementById("cal-next-month");
    const monthLabel = document.getElementById("cal-month-label");
    const daysGrid = document.getElementById("cal-days-grid");

    if (!wrap || !toggleBtn || !popover || !daysGrid) return;

    let availList = [];
    try {
      availList = JSON.parse(wrap.getAttribute("data-available-days") || "[]");
    } catch (e) {
      availList = [];
    }
    const availSet = new Set(availList);
    const currentDay = wrap.getAttribute("data-current-day") || (availList[availList.length - 1] || "2026-10-30");
    const baseUrl = wrap.getAttribute("data-base-url") || "/";

    const parts = currentDay.split("-");
    let viewYear = parseInt(parts[0], 10) || 2026;
    let viewMonth = (parseInt(parts[1], 10) || 10) - 1; // 0-indexed

    function renderMonth() {
      if (monthLabel) {
        monthLabel.textContent = ITALIAN_MONTHS[viewMonth] + " " + viewYear;
      }
      daysGrid.innerHTML = "";

      const firstDayObj = new Date(Date.UTC(viewYear, viewMonth, 1));
      // Lunedì = 0 ... Domenica = 6
      const startWeekday = (firstDayObj.getUTCDay() + 6) % 7;
      const daysInMonth = new Date(Date.UTC(viewYear, viewMonth + 1, 0)).getUTCDate();

      for (let e = 0; e < startWeekday; e++) {
        const emptyCell = document.createElement("div");
        emptyCell.className = "cal-day-cell cal-day-empty";
        daysGrid.appendChild(emptyCell);
      }

      for (let d = 1; d <= daysInMonth; d++) {
        const mm = String(viewMonth + 1).padStart(2, "0");
        const dd = String(d).padStart(2, "0");
        const isoStr = viewYear + "-" + mm + "-" + dd;
        const isAvail = availSet.has(isoStr);
        const isActive = isoStr === currentDay;

        if (isAvail) {
          const link = document.createElement("a");
          link.href = baseUrl + "?day=" + isoStr;
          link.className = "cal-day-cell cal-day-avail" + (isActive ? " cal-day-active" : "");
          link.textContent = String(d);
          link.title = "Vai a " + isoStr;
          daysGrid.appendChild(link);
        } else {
          const span = document.createElement("span");
          span.className = "cal-day-cell cal-day-disabled";
          span.textContent = String(d);
          daysGrid.appendChild(span);
        }
      }
    }

    toggleBtn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      const isOpen = popover.classList.toggle("open");
      toggleBtn.setAttribute("aria-expanded", isOpen ? "true" : "false");
      popover.setAttribute("aria-hidden", isOpen ? "false" : "true");
      if (isOpen) {
        renderMonth();
      }
    });

    if (prevBtn) {
      prevBtn.addEventListener("click", function (ev) {
        ev.stopPropagation();
        viewMonth--;
        if (viewMonth < 0) {
          viewMonth = 11;
          viewYear--;
        }
        renderMonth();
      });
    }

    if (nextBtn) {
      nextBtn.addEventListener("click", function (ev) {
        ev.stopPropagation();
        viewMonth++;
        if (viewMonth > 11) {
          viewMonth = 0;
          viewYear++;
        }
        renderMonth();
      });
    }

    popover.addEventListener("click", function (ev) {
      ev.stopPropagation();
    });

    document.addEventListener("click", function () {
      if (popover.classList.contains("open")) {
        popover.classList.remove("open");
        toggleBtn.setAttribute("aria-expanded", "false");
        popover.setAttribute("aria-hidden", "true");
      }
    });
  }

  /* ========================================================================
     4. HELPER PLOTLY (Tema Scuro)
     ======================================================================== */

  function basePlotlyLayout(yTitle) {
    const textSecondary = getCssToken("--text-secondary") || "#A1A8B8";
    const gridLine = getCssToken("--grid-line") || "rgba(255,255,255,0.07)";
    const bgCard = getCssToken("--bg-card") || "#151821";
    const textPrimary = getCssToken("--text-primary") || "#F3F4F6";
    const fontSans = getCssToken("--font-sans") || "-apple-system, system-ui, sans-serif";

    return {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: fontSans, color: textSecondary, size: 12 },
      margin: { t: 22, r: 16, b: 36, l: 46 },
      xaxis: {
        showgrid: false,
        zeroline: false,
        tickfont: { size: 11, color: textSecondary }
      },
      yaxis: {
        title: { text: yTitle || "", font: { size: 11, color: textSecondary } },
        gridcolor: gridLine,
        zeroline: false,
        tickfont: { size: 11, color: textSecondary }
      },
      hoverlabel: {
        bgcolor: bgCard,
        bordercolor: textSecondary,
        font: { family: fontSans, color: textPrimary, size: 12 }
      },
      showlegend: true,
      legend: { orientation: "h", x: 0, y: 1.15, font: { size: 11, color: textSecondary } }
    };
  }

  function renderTrendWithBandChart(containerId, seriesData, opts) {
    const el = document.getElementById(containerId);
    if (!el || typeof Plotly === "undefined" || !Array.isArray(seriesData) || seriesData.length === 0) return;

    const areaColor = getCssToken(opts.colorToken || "--color-recovery") || "#2DD4BF";
    const bandFill = getCssToken("--band-fill") || "rgba(161,168,184,0.15)";
    const unit = opts.unit || "";

    const x = seriesData.map(function (d) { return d.day; });
    const y = seriesData.map(function (d) { return d[opts.valueKey || "value"]; });
    const low = seriesData.map(function (d) { return d[opts.bandLowKey || "band_low"]; });
    const high = seriesData.map(function (d) { return d[opts.bandHighKey || "band_high"]; });

    const traces = [];
    if (low.some(function (v) { return v !== null && v !== undefined; })) {
      traces.push({
        x: x,
        y: high,
        type: "scatter",
        mode: "lines",
        line: { width: 0 },
        hoverinfo: "skip",
        showlegend: false,
        name: "Banda sup."
      });
      traces.push({
        x: x,
        y: low,
        type: "scatter",
        mode: "lines",
        fill: "tonexty",
        fillcolor: bandFill,
        line: { width: 0 },
        hoverinfo: "skip",
        name: opts.bandLabel || "Banda baseline"
      });
    }

    traces.push({
      x: x,
      y: y,
      type: "scatter",
      mode: "lines+markers",
      name: opts.seriesName || "Valore",
      line: { color: areaColor, width: 2.2 },
      marker: { size: 4.5, color: areaColor },
      hovertemplate: "%{x}<br><b>%{y} " + unit + "</b><extra></extra>"
    });

    if (opts.overlayKey) {
      traces.push({
        x: x,
        y: seriesData.map(function (d) { return d[opts.overlayKey]; }),
        type: "scatter",
        mode: "lines",
        name: opts.overlayName || "Media mobile",
        line: { color: getCssToken("--text-primary") || "#F3F4F6", width: 1.8, dash: "dot" },
        hovertemplate: "%{x}<br>%{y} " + unit + "<extra></extra>"
      });
    }

    if (opts.targetLine !== undefined && opts.targetLine !== null) {
      traces.push({
        x: x,
        y: x.map(function () { return opts.targetLine; }),
        type: "scatter",
        mode: "lines",
        name: opts.targetLabel || "Fabbisogno",
        line: { color: getCssToken("--text-muted") || "#6E7687", width: 1.4, dash: "dash" },
        hoverinfo: "skip"
      });
    }

    Plotly.newPlot(el, traces, basePlotlyLayout(unit), { displayModeBar: false, responsive: true });
  }

  function renderHypnogramChart(containerId, intervals) {
    const el = document.getElementById(containerId);
    if (!el || typeof Plotly === "undefined" || !Array.isArray(intervals) || intervals.length === 0) return;

    const stageLevelMap = { deep: 1, light: 2, rem: 3, awake: 4 };
    const x = [];
    const y = [];
    const texts = [];

    intervals.forEach(function (iv) {
      const lvl = stageLevelMap[iv.stage_name] || 2;
      x.push(iv.start_offset_min / 60.0);
      y.push(lvl);
      texts.push(iv.start_iso + " – " + iv.end_iso + " (" + iv.stage_name.toUpperCase() + ", " + iv.duration_min + " min)");
      x.push(iv.end_offset_min / 60.0);
      y.push(lvl);
      texts.push(iv.start_iso + " – " + iv.end_iso + " (" + iv.stage_name.toUpperCase() + ", " + iv.duration_min + " min)");
    });

    const sleepColor = getCssToken("--color-sleep") || "#818CF8";
    const layout = basePlotlyLayout("Stadio");
    layout.xaxis.title = { text: "Ore dall'addormentamento (h)", font: { size: 11 } };
    layout.yaxis.tickvals = [1, 2, 3, 4];
    layout.yaxis.ticktext = ["Profondo", "Leggero", "REM", "Veglia"];
    layout.showlegend = false;

    Plotly.newPlot(
      el,
      [
        {
          x: x,
          y: y,
          text: texts,
          type: "scatter",
          mode: "lines",
          line: { shape: "hv", color: sleepColor, width: 2.5 },
          fill: "tozeroy",
          fillcolor: getCssToken("--color-sleep-soft") || "rgba(129,140,248,0.16)",
          hovertemplate: "%{text}<extra></extra>"
        }
      ],
      layout,
      { displayModeBar: false, responsive: true }
    );
  }

  function renderScoresHistoryChart(containerId, historyData) {
    const el = document.getElementById(containerId);
    if (!el || typeof Plotly === "undefined" || !Array.isArray(historyData) || historyData.length === 0) return;

    const x = historyData.map(function (d) { return d.day; });
    const cRec = getCssToken("--color-recovery") || "#2DD4BF";
    const cStr = getCssToken("--color-strain") || "#FB923C";
    const cSlp = getCssToken("--color-sleep") || "#818CF8";

    const layout = basePlotlyLayout("Punti (0-100)");
    layout.yaxis.range = [0, 105];

    Plotly.newPlot(
      el,
      [
        {
          x: x,
          y: historyData.map(function (d) { return d.recovery_score; }),
          type: "scatter",
          mode: "lines+markers",
          name: "Recupero",
          line: { color: cRec, width: 2.2 },
          marker: { size: 4, color: cRec }
        },
        {
          x: x,
          y: historyData.map(function (d) { return d.sleep_score; }),
          type: "scatter",
          mode: "lines+markers",
          name: "Sonno",
          line: { color: cSlp, width: 2.2 },
          marker: { size: 4, color: cSlp }
        },
        {
          x: x,
          y: historyData.map(function (d) { return d.strain_score; }),
          type: "scatter",
          mode: "lines+markers",
          name: "Sforzo",
          line: { color: cStr, width: 2, dash: "dot" },
          marker: { size: 4, color: cStr }
        }
      ],
      layout,
      { displayModeBar: false, responsive: true }
    );
  }

  function renderSleepStagesStackedChart(containerId, historyData) {
    const el = document.getElementById(containerId);
    if (!el || typeof Plotly === "undefined" || !Array.isArray(historyData) || historyData.length === 0) return;

    const x = historyData.map(function (d) { return d.day; });
    const layout = basePlotlyLayout("Ore (h)");
    layout.barmode = "stack";

    Plotly.newPlot(
      el,
      [
        {
          x: x,
          y: historyData.map(function (d) { return d.sleep_deep_h; }),
          type: "bar",
          name: "Profondo (h)",
          marker: { color: getCssToken("--hypno-deep") || "#4F46E5" }
        },
        {
          x: x,
          y: historyData.map(function (d) { return d.sleep_rem_h; }),
          type: "bar",
          name: "REM (h)",
          marker: { color: getCssToken("--hypno-rem") || "#A5B4FC" }
        },
        {
          x: x,
          y: historyData.map(function (d) { return d.sleep_light_h; }),
          type: "bar",
          name: "Leggero (h)",
          marker: { color: getCssToken("--hypno-light") || "#818CF8" }
        },
        {
          x: x,
          y: historyData.map(function (d) { return d.sleep_awake_h; }),
          type: "bar",
          name: "Veglia (h)",
          marker: { color: getCssToken("--hypno-awake") || "#71717A" }
        }
      ],
      layout,
      { displayModeBar: false, responsive: true }
    );
  }

  function renderBodyBatteryStressChart(containerId, historyData) {
    const el = document.getElementById(containerId);
    if (!el || typeof Plotly === "undefined" || !Array.isArray(historyData) || historyData.length === 0) return;

    const x = historyData.map(function (d) { return d.day; });
    const cRec = getCssToken("--color-recovery") || "#2DD4BF";
    const cStr = getCssToken("--color-strain") || "#FB923C";
    const bandFill = getCssToken("--color-recovery-soft") || "rgba(45,212,191,0.16)";

    const layout = basePlotlyLayout("Punti (0-100)");
    layout.yaxis.range = [0, 105];

    Plotly.newPlot(
      el,
      [
        {
          x: x,
          y: historyData.map(function (d) { return d.bb_max; }),
          type: "scatter",
          mode: "lines",
          name: "Body Battery Max",
          line: { color: cRec, width: 2 }
        },
        {
          x: x,
          y: historyData.map(function (d) { return d.bb_min; }),
          type: "scatter",
          mode: "lines",
          fill: "tonexty",
          fillcolor: bandFill,
          name: "Body Battery Min",
          line: { color: cRec, width: 1, dash: "dot" }
        },
        {
          x: x,
          y: historyData.map(function (d) { return d.stress_avg; }),
          type: "scatter",
          mode: "lines+markers",
          name: "Stress Medio",
          line: { color: cStr, width: 2 },
          marker: { size: 4, color: cStr }
        }
      ],
      layout,
      { displayModeBar: false, responsive: true }
    );
  }

  function renderDualMetricOverlayChart(containerId, historyData, m1Key, m2Key, metaMap) {
    const el = document.getElementById(containerId);
    if (!el || typeof Plotly === "undefined" || !Array.isArray(historyData) || historyData.length === 0) return;

    const mapKey = {
      sleep_total_min: function (d) { return d.sleep_total_h !== null ? Math.round(d.sleep_total_h * 60) : null; }
    };
    const getVal = function (d, k) {
      return mapKey[k] ? mapKey[k](d) : d[k];
    };

    const meta1 = (metaMap && metaMap[m1Key]) || { label: m1Key, unit: "", color_var: "--color-recovery" };
    const meta2 = (metaMap && metaMap[m2Key]) || { label: m2Key, unit: "", color_var: "--color-sleep" };

    const c1 = getCssToken(meta1.color_var || "--color-recovery") || "#2DD4BF";
    const c2 = getCssToken(meta2.color_var || "--color-sleep") || "#818CF8";
    const color2 = c1 === c2 ? (getCssToken("--color-strain") || "#FB923C") : c2;

    const x = historyData.map(function (d) { return d.day; });
    const y1 = historyData.map(function (d) { return getVal(d, m1Key); });
    const y2 = historyData.map(function (d) { return getVal(d, m2Key); });

    const layout = basePlotlyLayout(meta1.label + " (" + meta1.unit + ")");
    layout.margin.r = 52;
    layout.yaxis2 = {
      title: { text: meta2.label + " (" + meta2.unit + ")", font: { size: 11, color: color2 } },
      overlaying: "y",
      side: "right",
      showgrid: false,
      zeroline: false,
      tickfont: { size: 11, color: color2 }
    };

    Plotly.newPlot(
      el,
      [
        {
          x: x,
          y: y1,
          type: "scatter",
          mode: "lines+markers",
          name: meta1.label + " (" + meta1.unit + ")",
          line: { color: c1, width: 2.3 },
          marker: { size: 4.5, color: c1 }
        },
        {
          x: x,
          y: y2,
          yaxis: "y2",
          type: "scatter",
          mode: "lines+markers",
          name: meta2.label + " (" + meta2.unit + ")",
          line: { color: color2, width: 2.1, dash: "dot" },
          marker: { size: 4.5, color: color2 }
        }
      ],
      layout,
      { displayModeBar: false, responsive: true }
    );
  }

  function renderCusumAnomaliesChart(containerId, trendObj, label, unit, colorToken) {
    const el = document.getElementById(containerId);
    if (!el || typeof Plotly === "undefined" || !trendObj || !trendObj.calculable) return;

    const series = trendObj.value.series || [];
    const anomalies = trendObj.value.anomalies || [];
    const changepoints = trendObj.value.cusum_changepoints || [];
    const mk = trendObj.value.theil_sen_mann_kendall || {};

    const cMain = getCssToken(colorToken || "--color-recovery") || "#2DD4BF";
    const cAlert = getCssToken("--color-strain") || "#FB923C";
    const x = series.map(function (d) { return d.day; });
    const y = series.map(function (d) { return d.value; });
    const ewma = series.map(function (d) { return d.ewma_7d; });

    const validPts = series.filter(function (d) { return d.value !== null && d.value !== undefined; });
    const tsLine = [];
    if (validPts.length >= 2 && mk.slope_per_day !== undefined) {
      const midIdx = (series.length - 1) / 2.0;
      const vals = validPts.map(function (d) { return d.value; }).sort(function (a, b) { return a - b; });
      const medVal = vals[Math.floor(vals.length / 2)];
      for (let i = 0; i < series.length; i++) {
        tsLine.push(Math.round((medVal + (i - midIdx) * mk.slope_per_day) * 100) / 100);
      }
    }

    const traces = [
      {
        x: x,
        y: y,
        type: "scatter",
        mode: "lines+markers",
        name: label,
        line: { color: cMain, width: 2 },
        marker: { size: 4.5, color: cMain }
      },
      {
        x: x,
        y: ewma,
        type: "scatter",
        mode: "lines",
        name: "EWMA 7g",
        line: { color: getCssToken("--text-primary") || "#F3F4F6", width: 1.8, dash: "dot" }
      }
    ];

    if (tsLine.length === x.length) {
      traces.push({
        x: x,
        y: tsLine,
        type: "scatter",
        mode: "lines",
        name: "Pendenza Theil-Sen (" + (mk.slope_per_week >= 0 ? "+" : "") + mk.slope_per_week + " " + unit + "/sett)",
        line: { color: getCssToken("--text-muted") || "#6E7687", width: 1.6, dash: "dash" }
      });
    }

    if (anomalies.length > 0) {
      traces.push({
        x: anomalies.map(function (a) { return a.day; }),
        y: anomalies.map(function (a) { return a.value; }),
        type: "scatter",
        mode: "markers",
        name: "Anomalia (|z| > 2.5)",
        marker: { size: 10, color: cAlert, symbol: "circle-open", line: { width: 2.5, color: cAlert } },
        hovertemplate: "Anomalia %{x}<br><b>%{y} " + unit + "</b><extra></extra>"
      });
    }

    const layout = basePlotlyLayout(unit);
    layout.shapes = changepoints.map(function (cp) {
      return {
        type: "line",
        x0: cp.day,
        x1: cp.day,
        y0: 0,
        y1: 1,
        yref: "paper",
        line: { color: cAlert, width: 1.5, dash: "dashdot" }
      };
    });

    Plotly.newPlot(el, traces, layout, { displayModeBar: false, responsive: true });
  }

  function renderWeeklyCycleChart(containerId, trendObj, label, unit, colorToken) {
    const el = document.getElementById(containerId);
    if (!el || typeof Plotly === "undefined" || !trendObj || !trendObj.calculable) return;

    const cycle = trendObj.value.weekly_cycle || [];
    const color = getCssToken(colorToken || "--color-recovery") || "#2DD4BF";
    const layout = basePlotlyLayout(unit);
    layout.showlegend = false;

    Plotly.newPlot(
      el,
      [
        {
          x: cycle.map(function (c) { return c.weekday_label; }),
          y: cycle.map(function (c) { return c.mean; }),
          type: "bar",
          name: label,
          marker: { color: color, opacity: 0.88 },
          hovertemplate: "%{x}<br>Media: <b>%{y} " + unit + "</b><extra></extra>"
        }
      ],
      layout,
      { displayModeBar: false, responsive: true }
    );
  }

  function renderSpearmanHeatmap(containerId, matrix, metricMeta) {
    const el = document.getElementById(containerId);
    if (!el || typeof Plotly === "undefined" || !matrix || !Array.isArray(matrix.metrics)) return;

    const metrics = matrix.metrics;
    const labels = metrics.map(function (m) {
      return (metricMeta && metricMeta[m] && metricMeta[m].label) || m;
    });

    const z = [];
    const text = [];
    metrics.forEach(function (my) {
      const rowZ = [];
      const rowT = [];
      metrics.forEach(function (mx) {
        const cell = (matrix.cells || []).find(function (c) {
          return c.metric_x === mx && c.metric_y === my;
        });
        const r = cell && cell.rho !== null && cell.rho !== undefined ? cell.rho : null;
        rowZ.push(r);
        rowT.push(r !== null ? r.toFixed(2) + " (n=" + cell.n_days + ")" : "n/d");
      });
      z.push(rowZ);
      text.push(rowT);
    });

    const layout = basePlotlyLayout("");
    layout.margin = { t: 20, r: 20, b: 60, l: 110 };

    Plotly.newPlot(
      el,
      [
        {
          z: z,
          x: labels,
          y: labels,
          text: text,
          type: "heatmap",
          zmin: -1,
          zmax: 1,
          colorscale: [
            [0.0, "#FB923C"],
            [0.5, "#1E222E"],
            [1.0, "#2DD4BF"]
          ],
          hovertemplate: "X: %{x}<br>Y: %{y}<br><b>ρ = %{text}</b><extra></extra>",
          showscale: true
        }
      ],
      layout,
      { displayModeBar: false, responsive: true }
    );
  }

  function renderCorrelationScatter(containerId, records, mx, my, lagDays, metricMeta) {
    const el = document.getElementById(containerId);
    if (!el || typeof Plotly === "undefined" || !Array.isArray(records)) return;

    const lag = Number(lagDays) || 0;
    const xs = [];
    const ys = [];
    const days = [];

    for (let i = 0; i + lag < records.length; i++) {
      const vx = records[i][mx];
      const vy = records[i + lag][my];
      if (vx !== null && vx !== undefined && vy !== null && vy !== undefined) {
        xs.push(vx);
        ys.push(vy);
        days.push(records[i].day + (lag > 0 ? " → +" + lag + "g" : ""));
      }
    }

    const metaX = (metricMeta && metricMeta[mx]) || { label: mx, unit: "" };
    const metaY = (metricMeta && metricMeta[my]) || { label: my, unit: "" };
    const cSleep = getCssToken("--color-sleep") || "#818CF8";
    const cRec = getCssToken("--color-recovery") || "#2DD4BF";

    const traces = [
      {
        x: xs,
        y: ys,
        text: days,
        type: "scatter",
        mode: "markers",
        name: "Giorni osservati (n=" + xs.length + ")",
        marker: { size: 8, color: cSleep, opacity: 0.82 },
        hovertemplate: "%{text}<br>" + metaX.label + ": <b>%{x}</b><br>" + metaY.label + ": <b>%{y}</b><extra></extra>"
      }
    ];

    if (xs.length >= 3) {
      const n = xs.length;
      const meanX = xs.reduce(function (a, b) { return a + b; }, 0) / n;
      const meanY = ys.reduce(function (a, b) { return a + b; }, 0) / n;
      let num = 0;
      let den = 0;
      for (let j = 0; j < n; j++) {
        num += (xs[j] - meanX) * (ys[j] - meanY);
        den += (xs[j] - meanX) * (xs[j] - meanX);
      }
      if (den > 1e-9) {
        const slope = num / den;
        const intercept = meanY - slope * meanX;
        const minX = Math.min.apply(null, xs);
        const maxX = Math.max.apply(null, xs);
        traces.push({
          x: [minX, maxX],
          y: [intercept + slope * minX, intercept + slope * maxX],
          type: "scatter",
          mode: "lines",
          name: "Tendenza lineare",
          line: { color: cRec, width: 2, dash: "dash" },
          hoverinfo: "skip"
        });
      }
    }

    const layout = basePlotlyLayout(metaY.label + (lag > 0 ? " (t+" + lag + ")" : " (t)"));
    layout.xaxis.title = { text: metaX.label + " (giorno t) [" + (metaX.unit || "") + "]", font: { size: 11 } };

    Plotly.newPlot(el, traces, layout, { displayModeBar: false, responsive: true });
  }

  function renderActivityTimeseriesChart(containerId, tsData) {
    const el = document.getElementById(containerId);
    if (!el || typeof Plotly === "undefined" || !tsData || !tsData.available) return;

    const x = tsData.elapsed_min || [];
    const hr = tsData.hr_bpm || [];
    const spd = tsData.speed_kmh || [];
    const hasSpeed = spd.some(function (v) { return v > 0.5; });

    const cHr = getCssToken("--color-strain") || "#FB923C";
    const cSpd = getCssToken("--color-recovery") || "#2DD4BF";
    const cSleep = getCssToken("--color-sleep") || "#818CF8";

    const traces = [
      {
        x: x,
        y: hr,
        type: "scatter",
        mode: "lines",
        name: "FC (bpm)",
        line: { color: cHr, width: 2 },
        hovertemplate: "Min %{x}m<br><b>%{y} bpm</b><extra></extra>"
      }
    ];

    if (hasSpeed) {
      traces.push({
        x: x,
        y: spd,
        yaxis: "y2",
        type: "scatter",
        mode: "lines",
        name: "Velocità (km/h)",
        line: { color: cSpd, width: 1.5, dash: "dot" },
        opacity: 0.72,
        hovertemplate: "Min %{x}m<br><b>%{y} km/h</b><extra></extra>"
      });
    }

    if (Array.isArray(tsData.hrr_markers) && tsData.hrr_markers.length > 0) {
      traces.push({
        x: tsData.hrr_markers.map(function (m) { return m.rec_min; }),
        y: tsData.hrr_markers.map(function (m) { return m.rec_hr; }),
        text: tsData.hrr_markers.map(function (m) { return m.label + " (da " + m.peak_hr + " a " + m.rec_hr + " bpm)"; }),
        type: "scatter",
        mode: "markers",
        name: "Recupero HRR 60s",
        marker: { size: 9, color: cSpd, symbol: "diamond", line: { width: 2, color: "#0B0D12" } },
        hovertemplate: "%{text}<extra></extra>"
      });
    }

    const layout = basePlotlyLayout("FC (bpm)");
    layout.xaxis.title = { text: "Tempo trascorso (min)", font: { size: 11 } };
    layout.margin = { t: 26, r: hasSpeed ? 48 : 18, b: 38, l: 46 };
    if (hasSpeed) {
      layout.yaxis2 = {
        title: { text: "km/h", font: { size: 11, color: cSpd } },
        overlaying: "y",
        side: "right",
        showgrid: false,
        zeroline: false,
        tickfont: { size: 10, color: cSpd }
      };
    }

    const shapes = [];
    if (
      tsData.half1_hr_mean !== null &&
      tsData.half2_hr_mean !== null &&
      tsData.mid_elapsed_min !== null &&
      x.length > 2
    ) {
      shapes.push({
        type: "line",
        x0: x[0],
        x1: tsData.mid_elapsed_min,
        y0: tsData.half1_hr_mean,
        y1: tsData.half1_hr_mean,
        line: { color: cSleep, width: 1.5, dash: "dash" }
      });
      shapes.push({
        type: "line",
        x0: tsData.mid_elapsed_min,
        x1: x[x.length - 1],
        y0: tsData.half2_hr_mean,
        y1: tsData.half2_hr_mean,
        line: { color: cSleep, width: 1.5, dash: "dash" }
      });
    }
    layout.shapes = shapes;

    Plotly.newPlot(el, traces, layout, { displayModeBar: false, responsive: true });
  }

  function renderTagImpactsChart(containerId, tagImpacts) {
    const el = document.getElementById(containerId);
    if (!el || typeof Plotly === "undefined" || !Array.isArray(tagImpacts) || tagImpacts.length === 0) return;

    const labels = tagImpacts.map(function (t) { return t.label; });
    const hrvD = tagImpacts.map(function (t) { return t.hrv_delta_ms !== null ? t.hrv_delta_ms : 0; });
    const rhrD = tagImpacts.map(function (t) { return t.rhr_delta_bpm !== null ? t.rhr_delta_bpm : 0; });

    const layout = basePlotlyLayout("Scostamento Mediano (Δ)");
    layout.barmode = "group";

    Plotly.newPlot(
      el,
      [
        {
          x: labels,
          y: hrvD,
          type: "bar",
          name: "Δ HRV Notte (ms)",
          marker: { color: "#2DD4BF" },
          hovertemplate: "%{x}<br>Δ HRV: <b>%{y} ms</b><extra></extra>"
        },
        {
          x: labels,
          y: rhrD,
          type: "bar",
          name: "Δ FC Riposo (bpm)",
          marker: { color: "#FB923C" },
          hovertemplate: "%{x}<br>Δ FC Riposo: <b>%{y} bpm</b><extra></extra>"
        }
      ],
      layout,
      { displayModeBar: false, responsive: true }
    );
  }

  function initSyncNowButton() {
    const btn = document.getElementById("sync-now-btn");
    const badge = document.getElementById("sync-status-badge");
    if (!btn || !badge) return;

    let pollTimer = null;

    function checkStatus() {
      fetch("/api/sync/status")
        .then(function (r) { return r.json(); })
        .then(function (st) {
          if (!st) return;
          badge.style.display = "inline-flex";
          badge.textContent = st.message || "";
          if (st.running) {
            btn.disabled = true;
            btn.textContent = "⏳ Sync in corso...";
            badge.className = "sync-status-badge status-in_line";
          } else {
            if (pollTimer) {
              clearInterval(pollTimer);
              pollTimer = null;
            }
            btn.disabled = false;
            btn.textContent = "🔄 Sincronizza Ora";
            if (st.status === "ok" || st.status === "partial") {
              badge.className = "sync-status-badge status-improving";
              setTimeout(function () {
                window.location.reload();
              }, 900);
            } else if (st.status === "error") {
              badge.className = "sync-status-badge status-worsening";
            }
          }
        })
        .catch(function () {});
    }

    btn.addEventListener("click", function () {
      btn.disabled = true;
      btn.textContent = "⏳ Avvio...";
      badge.style.display = "inline-flex";
      badge.className = "sync-status-badge status-in_line";
      badge.textContent = "Avvio sincronizzazione...";

      fetch("/api/sync/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ days: 30 })
      })
        .then(function (r) { return r.json(); })
        .then(function (st) {
          badge.textContent = (st && st.message) || "Sincronizzazione in corso...";
          if (!pollTimer) {
            pollTimer = setInterval(checkStatus, 1200);
          }
        })
        .catch(function () {
          btn.disabled = false;
          btn.textContent = "🔄 Sincronizza Ora";
          badge.className = "sync-status-badge status-worsening";
          badge.textContent = "Errore di rete locale";
        });
    });
  }

  function initDailyTagsBar() {
    const card = document.getElementById("daily-tags-card");
    const pillsWrap = document.getElementById("daily-tags-pills");
    const form = document.getElementById("custom-tag-form");
    const input = document.getElementById("custom-tag-input");
    if (!card || !pillsWrap) return;

    const dayStr = card.getAttribute("data-day");
    if (!dayStr) return;

    function toggleTagOnServer(tagKey, btnEl) {
      fetch("/api/day/" + encodeURIComponent(dayStr) + "/tags", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tag: tagKey })
      })
        .then(function (r) { return r.json(); })
        .then(function (res) {
          if (!res || !Array.isArray(res.tags)) return;
          const activeSet = new Set(res.tags);
          if (btnEl) {
            const isNowOn = activeSet.has(tagKey);
            btnEl.classList.toggle("active", isNowOn);
            btnEl.setAttribute("aria-pressed", isNowOn ? "true" : "false");
          } else {
            window.location.reload();
          }
        })
        .catch(function () {});
    }

    pillsWrap.querySelectorAll(".js-tag-toggle").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const tKey = btn.getAttribute("data-tag");
        if (tKey) toggleTagOnServer(tKey, btn);
      });
    });

    if (form && input) {
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        const rawVal = (input.value || "").trim().toLowerCase().replace(/\s+/g, "_");
        if (!rawVal) return;
        input.value = "";
        toggleTagOnServer(rawVal, null);
      });
    }
  }

  /* ========================================================================
     5. INIZIALIZZAZIONE DOM (Tema Scuro, Mini-Calendario, Formule, Drawer)
     ======================================================================== */

  document.addEventListener("DOMContentLoaded", function () {
    // Forza sempre il tema scuro
    document.documentElement.setAttribute("data-theme", "dark");
    try {
      localStorage.removeItem("garmin_dashboard_theme");
    } catch (e) {}

    // Inizializza il mini-calendario dei giorni disponibili
    initMiniCalendar();
    initSyncNowButton();
    initDailyTagsBar();

    // Renderizza tutte le formule LaTeX inline presenti nella pagina (.js-render-latex)
    document.querySelectorAll(".js-render-latex").forEach(function (el) {
      const raw = el.getAttribute("data-latex") || el.textContent;
      el.innerHTML = renderLatexToHtml(raw);
    });

    // Apertura pannello dai pulsanti/schede/righe con attributo data-detail-json
    document.querySelectorAll("[data-detail-json]").forEach(function (el) {
      el.addEventListener("click", function () {
        try {
          const parsed = JSON.parse(el.getAttribute("data-detail-json") || "{}");
          openDetailDrawer(parsed, el);
        } catch (err) {
          console.error("Impossibile leggere data-detail-json", err);
        }
      });
    });

    const closeBtn = document.getElementById("drawer-close-btn");
    const backdrop = document.getElementById("detail-backdrop");
    const howToggleBtn = document.getElementById("drawer-how-toggle");
    const howContentEl = document.getElementById("drawer-how-content");

    if (closeBtn) {
      closeBtn.addEventListener("click", function () {
        closeDetailDrawer(false);
      });
    }
    if (backdrop) {
      backdrop.addEventListener("click", function () {
        closeDetailDrawer(false);
      });
    }
    if (howToggleBtn && howContentEl) {
      howToggleBtn.addEventListener("click", function () {
        const isOpen = howContentEl.classList.toggle("open");
        howToggleBtn.setAttribute("aria-expanded", isOpen ? "true" : "false");
      });
    }

    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") {
        closeDetailDrawer(false);
        const pop = document.getElementById("calendar-popover");
        if (pop && pop.classList.contains("open")) {
          pop.classList.remove("open");
        }
      }
    });

    window.addEventListener("popstate", function () {
      closeDetailDrawer(true);
    });
  });

  window.GarminDashboardUI = {
    openDetailDrawer: openDetailDrawer,
    closeDetailDrawer: closeDetailDrawer,
    renderLatexToHtml: renderLatexToHtml,
    renderTrendWithBandChart: renderTrendWithBandChart,
    renderHypnogramChart: renderHypnogramChart,
    renderScoresHistoryChart: renderScoresHistoryChart,
    renderSleepStagesStackedChart: renderSleepStagesStackedChart,
    renderBodyBatteryStressChart: renderBodyBatteryStressChart,
    renderDualMetricOverlayChart: renderDualMetricOverlayChart,
    renderCusumAnomaliesChart: renderCusumAnomaliesChart,
    renderWeeklyCycleChart: renderWeeklyCycleChart,
    renderSpearmanHeatmap: renderSpearmanHeatmap,
    renderCorrelationScatter: renderCorrelationScatter,
    renderActivityTimeseriesChart: renderActivityTimeseriesChart,
    renderTagImpactsChart: renderTagImpactsChart,
    basePlotlyLayout: basePlotlyLayout
  };
})();
