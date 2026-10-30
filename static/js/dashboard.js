/* ==========================================================================
   dashboard.js — Componenti interattivi della Garmin Personal Dashboard
   1. Pannello di dettaglio a 3 strati (1: Scheda numero, 2: Scomposizione, 3: Come si calcola)
      con chiusura su Esc, pulsante indietro del browser (history.pushState) e gestione focus.
   2. Helper Plotly che leggono i token CSS (tema chiaro/scuro automatico, niente toolbar).
   ========================================================================== */

(function () {
  "use strict";

  let lastFocusedTrigger = null;

  function getCssToken(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

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
      const v = payload.score !== undefined && payload.score !== null ? payload.score : payload.display_value;
      const u = payload.unit ? " " + payload.unit : "";
      valueEl.textContent = v !== undefined && v !== null ? String(v) + u : "Dato assente";
    }
    if (statusEl) {
      statusEl.textContent = payload.subtitle || payload.status_label || "";
    }

    // Strato 2: Scomposizione (componenti, valori, baseline, pesi o assunzioni)
    if (compListEl) {
      compListEl.innerHTML = "";
      if (Array.isArray(payload.components) && payload.components.length > 0) {
        payload.components.forEach(function (c) {
          const row = document.createElement("div");
          row.style.padding = "10px 0";
          row.style.borderBottom = "1px solid var(--border-card)";
          const weightPct = c.weight !== undefined ? Math.round(c.weight * 100) + "%" : "";
          const subTxt = c.subscore !== undefined ? " · Sotto-punteggio: " + c.subscore + "/100" : "";
          row.innerHTML =
            '<div style="display:flex;justify-content:space-between;font-weight:600;color:var(--text-primary);">' +
            "<span>" + (c.name || c.key) + (weightPct ? " (peso " + weightPct + ")" : "") + "</span>" +
            "<span>" + (c.value !== null && c.value !== undefined ? c.value + " " + (c.unit || "") : "dato assente") + "</span>" +
            "</div>" +
            '<div style="font-size:var(--text-xs);color:var(--text-secondary);margin-top:2px;">' +
            (c.baseline_label || (c.baseline !== null && c.baseline !== undefined ? "Baseline: " + c.baseline : "")) +
            subTxt +
            "</div>";
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

    // Strato 3: Come si calcola (chiuso di default finché l'utente non fa il 2° clic)
    const hw = payload.how_it_works || {};
    if (howContentEl) howContentEl.classList.remove("open");
    if (howToggleBtn) howToggleBtn.setAttribute("aria-expanded", "false");
    if (howTitleEl) howTitleEl.textContent = hw.title || "Come si calcola";
    if (howFormulaEl) howFormulaEl.textContent = hw.formula_latex || "Mediana mobile a 30 giorni ± 1.4826 · MAD";
    if (howParamsEl) {
      howParamsEl.innerHTML = "";
      if (hw.parameters && typeof hw.parameters === "object") {
        Object.keys(hw.parameters).forEach(function (k) {
          const li = document.createElement("div");
          li.style.marginBottom = "4px";
          li.innerHTML = "<strong>" + k + ":</strong> " + hw.parameters[k];
          howParamsEl.appendChild(li);
        });
      }
    }
    if (howLimitsEl) {
      howLimitsEl.textContent =
        (hw.limits ? hw.limits + " " : "") +
        "Stime derivate da sensore ottico da polso: non costituiscono indicazioni o diagnosi mediche.";
    }

    backdrop.classList.add("open");
    panel.classList.add("open");
    panel.setAttribute("aria-hidden", "false");

    if (! history.state || !history.state.drawerOpen) {
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

  function basePlotlyLayout(yTitle) {
    const textSecondary = getCssToken("--text-secondary") || "#71717A";
    const gridLine = getCssToken("--grid-line") || "rgba(128,128,128,0.1)";
    const bgCard = getCssToken("--bg-card") || "#17171A";
    const textPrimary = getCssToken("--text-primary") || "#F4F4F6";
    const fontSans = getCssToken("--font-sans") || "-apple-system, system-ui, sans-serif";

    return {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: fontSans, color: textSecondary, size: 12 },
      margin: { t: 14, r: 14, b: 34, l: 44 },
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
      legend: { orientation: "h", x: 0, y: 1.14, font: { size: 11, color: textSecondary } }
    };
  }

  function renderTrendWithBandChart(containerId, seriesData, opts) {
    const el = document.getElementById(containerId);
    if (!el || typeof Plotly === "undefined" || !Array.isArray(seriesData) || seriesData.length === 0) return;

    const areaColor = getCssToken(opts.colorToken || "--color-recovery") || "#2DD4BF";
    const bandFill = getCssToken("--band-fill") || "rgba(161,161,170,0.15)";
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
      line: { color: areaColor, width: 2 },
      marker: { size: 4, color: areaColor },
      hovertemplate: "%{x}<br><b>%{y} " + unit + "</b><extra></extra>"
    });

    if (opts.overlayKey) {
      traces.push({
        x: x,
        y: seriesData.map(function (d) { return d[opts.overlayKey]; }),
        type: "scatter",
        mode: "lines",
        name: opts.overlayName || "Media mobile",
        line: { color: getCssToken("--text-primary") || "#888", width: 1.75, dash: "dot" },
        hovertemplate: "%{x}<br>%{y} " + unit + "<extra></extra>"
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
    layout.xaxis.title = { text: "Ore dall'inizio del sonno (h)", font: { size: 11 } };
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
          fillcolor: getCssToken("--color-sleep-soft") || "rgba(129,140,248,0.14)",
          hovertemplate: "%{text}<extra></extra>"
        }
      ],
      layout,
      { displayModeBar: false, responsive: true }
    );
  }

  document.addEventListener("DOMContentLoaded", function () {
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
    renderTrendWithBandChart: renderTrendWithBandChart,
    renderHypnogramChart: renderHypnogramChart,
    basePlotlyLayout: basePlotlyLayout
  };
})();
