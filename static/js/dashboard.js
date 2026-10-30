/* ==========================================================================
   dashboard.js — Componenti interattivi, Motore Matematico LaTeX->HTML offline
   e Grafici Plotly della Garmin Personal Dashboard
   ========================================================================== */

(function () {
  "use strict";

  let lastFocusedTrigger = null;

  function getCssToken(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  /* ========================================================================
     1. MOTORE DI RENDERING MATEMATICO OFFLINE (LaTeX -> HTML tipografico)
     Converte espressioni LaTeX (\frac{a}{b}, \sum_{i}^{n}, \overline{x}, _{sub},
     ^{sup}, \text{...}, lettere greche e operatori) in HTML semantico con
     frazioni verticali impilate, sommatorie e pedici/apici.
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

      // Comandi con argomento tra graffe: \frac{a}{b}, \text{...}, \overline{...}, \bar{...}, \mathbb{...}
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

      // Pedici e apici: _{...} oppure _x, ^{...} oppure ^x
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

      // Simboli LaTeX noti
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
        // Comando generico non mappato: rimuove la backslash
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
    // Separa equazioni multiple divise da ", \quad" o "\quad" su righe distinte
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
     2. PANNELLO DI DETTAGLIO A SCOMPARSA (3 STRATI + FORMULE SEMPRE VISIBILI)
     ======================================================================== */

  function openDetailDrawer(payload, triggerEl) {
    const backdrop = document.getElementById("detail-backdrop");
    const panel = document.getElementById("detail-panel");
    if (!backdrop || !panel || !payload) return;

    lastFocusedTrigger = triggerEl || document.activeElement;

    const titleEl = document.getElementById("drawer-title");
    const valueEl = document.getElementById("drawer-value");
    const statusEl = document.getElementById("drawer-status");
    const compListEl = document.getElementById("drawer-components");
    const howTitleEl = document.getElementById("drawer-how-title");
    const howFormulaEl = document.getElementById("drawer-how-formula");
    const howParamsEl = document.getElementById("drawer-how-params");
    const howLimitsEl = document.getElementById("drawer-how-limits");
    const howContentEl = document.getElementById("drawer-how-content");
    const howToggleBtn = document.getElementById("drawer-how-toggle");

    if (titleEl) titleEl.textContent = payload.title || payload.name || "Dettaglio metrica";
    if (valueEl) {
      let v = payload.score !== undefined && payload.score !== null ? payload.score : payload.display_value;
      if (v === undefined && payload.value !== undefined && typeof payload.value !== "object") {
        v = payload.value;
      }
      const u = payload.unit && typeof v !== "object" ? " " + payload.unit : "";
      valueEl.textContent = v !== undefined && v !== null ? String(v) + u : "Dettaglio analisi";
    }
    if (statusEl) {
      const qTxt = payload.quality_flag ? "Qualità: " + payload.quality_flag + " (n=" + (payload.n_samples || 0) + ")" : "";
      statusEl.textContent = payload.subtitle || payload.status_label || qTxt || "";
    }

    // Strato 2: Scomposizione (componenti con barra visiva, valori, baseline, pesi o assunzioni)
    if (compListEl) {
      compListEl.innerHTML = "";
      if (Array.isArray(payload.components) && payload.components.length > 0) {
        payload.components.forEach(function (c) {
          const row = document.createElement("div");
          row.style.padding = "10px 0";
          row.style.borderBottom = "1px solid var(--border-card)";
          const weightPct = c.weight !== undefined ? Math.round(c.weight * 100) + "%" : "";
          const subVal = c.subscore !== undefined ? Math.round(c.subscore) : null;
          const subTxt = subVal !== null ? " · Punteggio componente: <strong>" + subVal + "/100</strong>" : "";
          const barHtml =
            subVal !== null
              ? '<div class="subscore-bar-track"><div class="subscore-bar-fill" style="width:' +
                Math.min(100, Math.max(0, subVal)) +
                '%;"></div></div>'
              : "";
          row.innerHTML =
            '<div style="display:flex;justify-content:space-between;font-weight:600;color:var(--text-primary);">' +
            "<span>" + (c.name || c.key) + (weightPct ? " (peso " + weightPct + ")" : "") + "</span>" +
            "<span>" + (c.value !== null && c.value !== undefined ? c.value + " " + (c.unit || "") : "dato assente") + "</span>" +
            "</div>" +
            '<div style="font-size:var(--text-xs);color:var(--text-secondary);margin-top:3px;">' +
            (c.baseline_label || (c.baseline !== null && c.baseline !== undefined ? "Baseline: " + c.baseline : "")) +
            subTxt +
            "</div>" +
            barHtml;
          compListEl.appendChild(row);
        });
      } else if (Array.isArray(payload.assumptions) && payload.assumptions.length > 0) {
        payload.assumptions.forEach(function (a) {
          const p = document.createElement("p");
          p.style.fontSize = "var(--text-sm)";
          p.style.color = "var(--text-secondary)";
          p.style.marginBottom = "6px";
          p.textContent = "• " + a;
          compListEl.appendChild(p);
        });
      } else if (payload.reason) {
        const p = document.createElement("p");
        p.style.fontSize = "var(--text-sm)";
        p.style.color = "var(--text-secondary)";
        p.textContent = payload.reason;
        compListEl.appendChild(p);
      }
    }

    // Strato 3: Come si calcola (Formula renderizzata in HTML matematico, Parametri, Limiti)
    const hw = payload.how_it_works || {};
    if (howTitleEl) howTitleEl.textContent = hw.title || "Come si calcola (formula, parametri e limiti)";
    if (howFormulaEl) {
      const rawLatex = hw.formula_latex || r"z = \frac{x - m_{30}}{1.4826 \cdot \text{MAD}_{30}}";
      howFormulaEl.innerHTML = renderLatexToHtml(rawLatex);
    }
    if (howParamsEl) {
      howParamsEl.innerHTML = "";
      if (hw.parameters && typeof hw.parameters === "object") {
        Object.keys(hw.parameters).forEach(function (k) {
          const li = document.createElement("div");
          li.style.marginBottom = "4px";
          li.innerHTML = "<strong>" + escapeHtml(k) + ":</strong> " + escapeHtml(hw.parameters[k]);
          howParamsEl.appendChild(li);
        });
      }
    }
    if (howLimitsEl) {
      howLimitsEl.textContent =
        (hw.limits ? hw.limits + " " : "") +
        "Stime derivate da sensore ottico da polso: non costituiscono indicazioni o diagnosi mediche.";
    }

    // Mostriamo subito la sezione "Come si calcola" aperta così la formula matematica è immediatamente visibile
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
     3. HELPER PLOTLY (Seguono i token CSS Chiaro/Scuro, niente CDN)
     ======================================================================== */

  function basePlotlyLayout(yTitle) {
    const textSecondary = getCssToken("--text-secondary") || "#4B5563";
    const gridLine = getCssToken("--grid-line") || "rgba(15,23,42,0.06)";
    const bgCard = getCssToken("--bg-card") || "#FFFFFF";
    const textPrimary = getCssToken("--text-primary") || "#111827";
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

    const areaColor = getCssToken(opts.colorToken || "--color-recovery") || "#0D9488";
    const bandFill = getCssToken("--band-fill") || "rgba(100,116,139,0.15)";
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
        line: { color: getCssToken("--text-primary") || "#374151", width: 1.8, dash: "dot" },
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
        line: { color: getCssToken("--text-muted") || "#6B7280", width: 1.4, dash: "dash" },
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

    const sleepColor = getCssToken("--color-sleep") || "#4F46E5";
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
          fillcolor: getCssToken("--color-sleep-soft") || "rgba(79,70,229,0.13)",
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
    const cRec = getCssToken("--color-recovery") || "#0D9488";
    const cStr = getCssToken("--color-strain") || "#EA580C";
    const cSlp = getCssToken("--color-sleep") || "#4F46E5";

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
          marker: { color: getCssToken("--hypno-deep") || "#3730A3" }
        },
        {
          x: x,
          y: historyData.map(function (d) { return d.sleep_rem_h; }),
          type: "bar",
          name: "REM (h)",
          marker: { color: getCssToken("--hypno-rem") || "#818CF8" }
        },
        {
          x: x,
          y: historyData.map(function (d) { return d.sleep_light_h; }),
          type: "bar",
          name: "Leggero (h)",
          marker: { color: getCssToken("--hypno-light") || "#6366F1" }
        },
        {
          x: x,
          y: historyData.map(function (d) { return d.sleep_awake_h; }),
          type: "bar",
          name: "Veglia (h)",
          marker: { color: getCssToken("--hypno-awake") || "#9CA3AF" }
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
    const cRec = getCssToken("--color-recovery") || "#0D9488";
    const cStr = getCssToken("--color-strain") || "#EA580C";
    const bandFill = getCssToken("--color-recovery-soft") || "rgba(13,148,136,0.13)";

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

    const c1 = getCssToken(meta1.color_var || "--color-recovery") || "#0D9488";
    const c2 = getCssToken(meta2.color_var || "--color-sleep") || "#4F46E5";
    const color2 = c1 === c2 ? (getCssToken("--color-strain") || "#EA580C") : c2;

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

    const cMain = getCssToken(colorToken || "--color-recovery") || "#0D9488";
    const cAlert = getCssToken("--color-strain") || "#EA580C";
    const x = series.map(function (d) { return d.day; });
    const y = series.map(function (d) { return d.value; });
    const ewma = series.map(function (d) { return d.ewma_7d; });

    // Retta robusta di Theil-Sen
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
        line: { color: getCssToken("--text-primary") || "#111827", width: 1.8, dash: "dot" }
      }
    ];

    if (tsLine.length === x.length) {
      traces.push({
        x: x,
        y: tsLine,
        type: "scatter",
        mode: "lines",
        name: "Pendenza Theil-Sen (" + (mk.slope_per_week >= 0 ? "+" : "") + mk.slope_per_week + " " + unit + "/sett)",
        line: { color: getCssToken("--text-muted") || "#6B7280", width: 1.6, dash: "dash" }
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
    const color = getCssToken(colorToken || "--color-recovery") || "#0D9488";
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
          marker: { color: color, opacity: 0.85 },
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
            [0.0, "#EA580C"],
            [0.5, "#E5E7EB"],
            [1.0, "#0D9488"]
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
    const cSleep = getCssToken("--color-sleep") || "#4F46E5";
    const cRec = getCssToken("--color-recovery") || "#0D9488";

    const traces = [
      {
        x: xs,
        y: ys,
        text: days,
        type: "scatter",
        mode: "markers",
        name: "Giorni osservati (n=" + xs.length + ")",
        marker: { size: 8, color: cSleep, opacity: 0.78 },
        hovertemplate: "%{text}<br>" + metaX.label + ": <b>%{x}</b><br>" + metaY.label + ": <b>%{y}</b><extra></extra>"
      }
    ];

    // Retta di tendenza lineare semplice (OLS) a solo scopo visivo
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
          y: [ intercept + slope * minX, intercept + slope * maxX ],
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

  /* ========================================================================
     4. INIZIALIZZAZIONE DOM E SELETTORE TEMA CHIARO/SCURO
     ======================================================================== */

  function applyTheme(theme) {
    if (theme === "dark") {
      document.documentElement.setAttribute("data-theme", "dark");
    } else {
      document.documentElement.setAttribute("data-theme", "light");
    }
    const btn = document.getElementById("theme-toggle-btn");
    if (btn) {
      btn.textContent = theme === "dark" ? "☀ Tema Chiaro" : "☾ Tema Scuro";
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    // Imposta tema iniziale (default: light per massima chiarezza, salvo scelta salvata o ?theme=...)
    const forced = document.documentElement.getAttribute("data-theme");
    if (!forced) {
      const saved = localStorage.getItem("garmin_dashboard_theme") || "light";
      applyTheme(saved);
    } else {
      applyTheme(forced);
    }

    const themeBtn = document.getElementById("theme-toggle-btn");
    if (themeBtn) {
      themeBtn.addEventListener("click", function () {
        const current = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
        const next = current === "dark" ? "light" : "dark";
        localStorage.setItem("garmin_dashboard_theme", next);
        applyTheme(next);
        window.dispatchEvent(new Event("resize"));
      });
    }

    // Renderizza tutte le formule LaTeX inline presenti nella pagina (.js-render-latex)
    document.querySelectorAll(".js-render-latex").forEach(function (el) {
      const raw = el.getAttribute("data-latex") || el.textContent;
      el.innerHTML = renderLatexToHtml(raw);
    });

    // Apertura pannello dai pulsanti/schede con attributo data-detail-json
    document.querySelectorAll("[data-detail-json]").forEach(function (el) {
      el.addEventListener("click", function () {
        try {
          const parsed = JSON.parse(el.getAttribute("data-detail-json"));
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
    basePlotlyLayout: basePlotlyLayout
  };
})();
