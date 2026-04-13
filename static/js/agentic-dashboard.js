/* =================================================================
   Agentic LMG  Command Center — JavaScript
   Real-time dashboard with HTMX + Chart.js integration
   ================================================================= */

(function () {
  "use strict";

  const API = {
    summary:          "/api/v1/dashboard/summary/",
    agentPerformance: "/api/v1/dashboard/agent-performance/",
    matchStatus:      "/api/v1/dashboard/match-status/",
    exceptions:       "/api/v1/dashboard/exceptions/",
    dailyVolume:      "/api/v1/dashboard/daily-volume/",
    recentActivity:   "/api/v1/dashboard/recent-activity/",
    cases:            "/api/v1/cases/",
    agentRuns:        "/api/v1/agents/runs/",
  };

  var CC_ROLE = window.CC_ROLE || "";
  var IS_ORG_WIDE = (CC_ROLE === "ADMIN" || CC_ROLE === "FINANCE_MANAGER" || CC_ROLE === "AUDITOR");

  // ── CSRF helper ──
  function getCookie(name) {
    const v = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return v ? v.pop() : "";
  }
  const csrfToken = getCookie("csrftoken");

  function apiFetch(url) {
    return fetch(url, {
      headers: {
        "X-CSRFToken": csrfToken,
        "Accept": "application/json",
      },
      credentials: "same-origin",
    }).then(function (r) {
      if (!r.ok) throw new Error("API " + r.status);
      return r.json();
    });
  }

  // ── Agent type display mapping ──
  const AGENT_MAP = {
    INVOICE_UNDERSTANDING:  { label: "Extraction Agent",     icon: "bi-file-earmark-text", iconClass: "cc-agent-entry-icon--extraction" },
    PO_RETRIEVAL:           { label: "PO Retrieval Agent",   icon: "bi-search",            iconClass: "cc-agent-entry-icon--po" },
    GRN_RETRIEVAL:          { label: "GRN Specialist Agent", icon: "bi-box-seam",          iconClass: "cc-agent-entry-icon--grn" },
    RECONCILIATION_ASSIST:  { label: "Reconciliation Agent", icon: "bi-arrow-left-right",  iconClass: "cc-agent-entry-icon--recon" },
    EXCEPTION_ANALYSIS:     { label: "Exception Agent",      icon: "bi-exclamation-triangle", iconClass: "cc-agent-entry-icon--exception" },
    REVIEW_ROUTING:         { label: "Review Routing Agent", icon: "bi-signpost-split",    iconClass: "cc-agent-entry-icon--review" },
    CASE_SUMMARY:           { label: "Case Summary Agent",   icon: "bi-journal-text",      iconClass: "cc-agent-entry-icon--summary" },
  };

  function agentInfo(type) {
    return AGENT_MAP[type] || { label: type, icon: "bi-robot", iconClass: "cc-agent-entry-icon--extraction" };
  }

  function statusClass(status) {
    if (!status) return "cc-agent-entry--analysis";
    var s = status.toUpperCase();
    if (s === "COMPLETED" || s === "MATCHED") return "cc-agent-entry--success";
    if (s === "FAILED" || s === "ESCALATED")  return "cc-agent-entry--escalation";
    if (s === "RUNNING")                      return "cc-agent-entry--running";
    return "cc-agent-entry--analysis";
  }

  // ── KPI Rendering (org-wide: ADMIN / FINANCE_MANAGER / AUDITOR) ──
  function renderKPIs(data) {
    setKPI("kpi-total-invoices", data.total_invoices || 0);
    var matchPct = data.matched_pct != null ? data.matched_pct : 0;
    setKPI("kpi-auto-reconciled", matchPct + "%");
    setKPI("kpi-pending-reviews", data.pending_reviews || 0);
    setKPI("kpi-exceptions", data.open_exceptions || 0);
    setKPI("kpi-avg-confidence", (data.avg_confidence || 0).toFixed(1) + "%");
    setKPI("kpi-active-agents", data.active_agents || 7);
  }

  // ── REVIEWER header KPIs: review workload from Cases API + Summary API ──
  function renderReviewerKPIs(summary, casesData) {
    var cases = (casesData.results || casesData) || [];
    var total = cases.length;
    var review = 0, closed = 0, escalated = 0;

    cases.forEach(function (c) {
      var s = (c.status || "").toUpperCase();
      if (s === "CLOSED") { closed++; }
      else if (s === "ESCALATED" || s === "FAILED" || s === "REJECTED") { escalated++; }
      else if (s.indexOf("REVIEW") !== -1 || s.indexOf("APPROVAL") !== -1) { review++; }
    });

    setKPI("kpi-my-cases", total);
    setKPI("kpi-in-review", review);
    setKPI("kpi-pending-reviews", summary.pending_reviews || 0);
    setKPI("kpi-closed-cases", closed);
    setKPI("kpi-avg-confidence", (summary.avg_confidence || 0).toFixed(1) + "%");
    setKPI("kpi-escalated", escalated);
  }

  // ── AP_PROCESSOR header KPIs: personal invoice metrics + case count ──
  function renderProcessorKPIs(summary, casesData) {
    setKPI("kpi-total-invoices", summary.total_invoices || 0);
    var matchPct = summary.matched_pct != null ? summary.matched_pct : 0;
    setKPI("kpi-auto-reconciled", matchPct + "%");
    setKPI("kpi-pending-reviews", summary.pending_reviews || 0);
    setKPI("kpi-exceptions", summary.open_exceptions || 0);
    setKPI("kpi-avg-confidence", (summary.avg_confidence || 0).toFixed(1) + "%");
    var cases = (casesData.results || casesData) || [];
    setKPI("kpi-my-cases", cases.length);
  }

  function setKPI(id, value) {
    var el = document.getElementById(id);
    if (el) {
      // Preserve the <i> icon — only update the text node
      var icon = el.querySelector("i");
      if (icon) {
        // Remove existing text nodes, keep the icon
        Array.from(el.childNodes).forEach(function (node) {
          if (node.nodeType === 3) el.removeChild(node);
        });
        el.appendChild(document.createTextNode(" " + value));
      } else {
        el.textContent = value;
      }
      el.closest(".cc-kpi").classList.add("cc-fade-in");
    }
  }

  // ── Agent Activity Feed ──
  function renderAgentFeed(runs) {
    var container = document.getElementById("agent-feed");
    if (!container) return;

    if (!runs || !runs.length) {
      container.innerHTML = '<div class="text-center py-4 text-muted"><i class="bi bi-robot" style="font-size:2rem"></i><p class="mt-2 mb-0" style="font-size:var(--ap-font-size-sm)">No agent activity yet</p></div>';
      return;
    }

    // Handle both paginated and flat responses
    var items = runs.results || runs;
    var html = "";

    items.slice(0, 30).forEach(function (run) {
      var info = agentInfo(run.agent_type);
      var cls = statusClass(run.status);
      var confidence = run.confidence != null ? (run.confidence * 100).toFixed(0) + "%" : "—";
      var duration = run.duration_ms != null ? (run.duration_ms / 1000).toFixed(1) + "s" : "—";
      var invoiceLabel = run.invoice_number || run.reconciliation_result_id || "—";

      html += '<div class="cc-agent-entry ' + cls + ' cc-fade-in">'
        + '<div class="cc-agent-entry-header">'
        + '  <div class="cc-agent-entry-icon ' + info.iconClass + '"><i class="bi ' + info.icon + '"></i></div>'
        + '  <span class="cc-agent-name">' + info.label + '</span>'
        + '  <span class="cc-agent-time">' + duration + '</span>'
        + '</div>'
        + '<div class="cc-agent-entry-body">'
        + '  <div class="cc-agent-detail">' + escapeHtml(invoiceLabel) + '</div>'
        + '  <div class="cc-agent-metrics">'
        + '    <span class="cc-agent-metric cc-agent-metric--confidence"><i class="bi bi-bullseye"></i> ' + confidence + '</span>'
        + '    <span class="cc-agent-metric cc-agent-metric--duration"><i class="bi bi-clock"></i> ' + duration + '</span>'
        + '    <span class="cc-agent-metric"><i class="bi bi-activity"></i> ' + escapeHtml(run.status || "—") + '</span>'
        + '  </div>'
        + '</div></div>';
    });

    container.innerHTML = html;
  }

  // ── Case Intelligence KPIs ──
  function renderCases(data) {
    var cases = (data.results || data) || [];
    var total = cases.length;
    var inflight = 0, review = 0, closed = 0, escalated = 0, human = 0;

    cases.forEach(function (c) {
      var s = (c.status || "").toUpperCase();
      if (s === "CLOSED") { closed++; }
      else if (s === "ESCALATED" || s === "FAILED" || s === "REJECTED") { escalated++; }
      else if (s.indexOf("REVIEW") !== -1 || s.indexOf("APPROVAL") !== -1) { review++; }
      else { inflight++; }
      if (c.requires_human_review) { human++; }
    });

    setKPI("case-total", total);
    setKPI("case-inflight", inflight);
    setKPI("case-review", review);
    setKPI("case-closed", closed);
    setKPI("case-escalated", escalated);
    setKPI("case-human", human);
  }

  function friendlyStatus(s) {
    if (!s) return "New";
    return s.replace(/_/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  // ── Pipeline Rendering ──
  function renderPipeline(summary, matchData) {
    var total = summary.total_invoices || 0;
    var extracted = summary.extracted_count || 0;
    var reconciled = summary.reconciled_count || 0;
    var posted = summary.posted_count || 0;
    var matchedPct = summary.matched_pct || 0;

    var extractPct = total ? Math.round(extracted / total * 100) : 0;
    var reconPct = total ? Math.round(reconciled / total * 100) : 0;

    setPipelineStage("pipeline-upload", total, total + " invoices");
    setPipelineStage("pipeline-extraction", extracted, extractPct + "% done");
    setPipelineStage("pipeline-recon", reconciled, matchedPct + "% matched");
    setPipelineStage("pipeline-agent", summary.open_exceptions || 0, "exceptions");
    setPipelineStage("pipeline-review", summary.pending_reviews || 0, "pending");
    setPipelineStage("pipeline-posting", posted, posted + " complete");
  }

  function setPipelineStage(id, count, meta) {
    var countEl = document.getElementById(id + "-count");
    var metaEl = document.getElementById(id + "-meta");
    if (countEl) countEl.textContent = count;
    if (metaEl && meta) metaEl.textContent = meta;
  }

  // ── Charts ──
  var exceptionChart = null;
  var reconChart = null;

  function renderExceptionChart(data) {
    var canvas = document.getElementById("exceptionPieChart");
    if (!canvas) return;
    var ctx = canvas.getContext("2d");
    var items = data || [];
    var labels = items.map(function (d) { return friendlyException(d.exception_type); });
    var values = items.map(function (d) { return d.count; });
    var colors = ["#2563eb", "#d97706", "#dc2626", "#059669", "#7c3aed", "#0284c7", "#db2777", "#ea580c"];

    if (exceptionChart) exceptionChart.destroy();
    exceptionChart = new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: labels,
        datasets: [{
          data: values,
          backgroundColor: colors.slice(0, values.length),
          borderWidth: 0,
          hoverOffset: 8,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "65%",
        plugins: {
          legend: { position: "bottom", labels: { padding: 12, usePointStyle: true, pointStyleWidth: 8, font: { size: 11 } } },
        },
      },
    });
  }

  function renderReconChart(matchData) {
    var canvas = document.getElementById("reconBarChart");
    if (!canvas) return;
    var ctx = canvas.getContext("2d");
    var items = matchData || [];
    var labels = items.map(function (d) { return friendlyStatus(d.match_status); });
    var values = items.map(function (d) { return d.count; });
    var bgColors = items.map(function (d) {
      var s = (d.match_status || "").toUpperCase();
      if (s === "MATCHED") return "#059669";
      if (s === "PARTIAL_MATCH") return "#d97706";
      if (s === "UNMATCHED") return "#dc2626";
      if (s === "REQUIRES_REVIEW") return "#7c3aed";
      return "#94a3b8";
    });

    if (reconChart) reconChart.destroy();
    reconChart = new Chart(ctx, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [{
          label: "Count",
          data: values,
          backgroundColor: bgColors,
          borderRadius: 6,
          borderSkipped: false,
          barPercentage: 0.6,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
        },
        scales: {
          y: { beginAtZero: true, grid: { color: "rgba(0,0,0,0.05)" }, ticks: { font: { size: 11 } } },
          x: { grid: { display: false }, ticks: { font: { size: 11 } } },
        },
      },
    });
  }

  function friendlyException(t) {
    if (!t) return "Unknown";
    return t.replace(/_/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  // ── Agent Performance Cards ──
  function renderAgentPerformance(data) {
    var container = document.getElementById("agent-perf-cards");
    if (!container) return;
    var items = data || [];
    if (!items.length) {
      container.innerHTML = '<div class="text-center py-4 text-muted w-100"><i class="bi bi-robot" style="font-size:2rem"></i><p class="mt-2 mb-0" style="font-size:var(--ap-font-size-sm)">No agent data</p></div>';
      return;
    }

    var html = "";
    items.forEach(function (a) {
      var info = agentInfo(a.agent_type);
      var successRate = a.total_runs > 0 ? ((a.success_count / a.total_runs) * 100).toFixed(0) : 0;
      var avgConf = a.avg_confidence != null ? (a.avg_confidence * 100).toFixed(0) + "%" : "—";
      var avgTime = a.avg_duration_ms != null ? (a.avg_duration_ms / 1000).toFixed(1) + "s" : "—";
      var barColor = successRate >= 80 ? "var(--ap-success)" : successRate >= 50 ? "var(--ap-warning)" : "var(--ap-danger)";

      html += '<div class="cc-agent-card cc-fade-in">'
        + '  <div class="cc-agent-card-top">'
        + '    <div class="cc-agent-card-icon ' + info.iconClass + '"><i class="bi ' + info.icon + '"></i></div>'
        + '    <div class="cc-agent-card-head">'
        + '      <div class="cc-agent-card-name">' + info.label + '</div>'
        + '      <div class="cc-agent-card-subtitle">Agentic workflow</div>'
        + '    </div>'
        + '  </div>'
        + '  <div class="cc-agent-card-stats">'
        + '    <div class="cc-agent-card-stat"><span class="cc-agent-card-stat-val">' + (a.total_runs || 0) + '</span><span class="cc-agent-card-stat-lbl">Runs</span></div>'
        + '    <div class="cc-agent-card-stat"><span class="cc-agent-card-stat-val" style="color:' + barColor + '">' + successRate + '%</span><span class="cc-agent-card-stat-lbl">Success</span></div>'
        + '    <div class="cc-agent-card-stat"><span class="cc-agent-card-stat-val">' + avgConf + '</span><span class="cc-agent-card-stat-lbl">Confidence</span></div>'
        + '    <div class="cc-agent-card-stat"><span class="cc-agent-card-stat-val">' + avgTime + '</span><span class="cc-agent-card-stat-lbl">Avg Time</span></div>'
        + '  </div>'
        + '  <div class="cc-perf-bar"><div class="cc-perf-bar-fill" style="width:' + successRate + '%;background:' + barColor + '"></div></div>'
        + '</div>';
    });

    container.innerHTML = html;
  }

  // ── Timeline (sample data — replace with real API when available) ──
  function renderTimeline() {
    // The timeline is static HTML in the template; no dynamic rendering needed
    // unless a case is selected via the case panel
  }

  // ── Data Fetch Orchestration ──
  var refreshInterval = null;

  function loadAll() {
    // Build promise list based on role — skip APIs the role doesn't need
    var promises = [
      apiFetch(API.summary).catch(function () { return {}; }),                                  // [0] summary
      IS_ORG_WIDE ? apiFetch(API.agentPerformance).catch(function () { return []; })            // [1] agentPerf
                  : Promise.resolve([]),
      apiFetch(API.matchStatus).catch(function () { return []; }),                              // [2] matchStatus
      apiFetch(API.exceptions).catch(function () { return []; }),                               // [3] exceptions
      apiFetch(API.cases + "?ordering=-created_at&page_size=100").catch(function () { return []; }), // [4] cases
    ];

    Promise.all(promises).then(function (results) {
      var summary = results[0];
      var agentPerf = results[1];
      var matchStatus = results[2];
      var exceptions = results[3];
      var cases = results[4];

      // ── Header KPIs (role-aware) ──
      if (CC_ROLE === "REVIEWER") {
        renderReviewerKPIs(summary, cases);
      } else if (CC_ROLE === "AP_PROCESSOR") {
        renderProcessorKPIs(summary, cases);
      } else {
        renderKPIs(summary);
      }

      // ── Pipeline (not shown for REVIEWER — hidden in template) ──
      if (CC_ROLE !== "REVIEWER") {
        renderPipeline(summary, matchStatus);
      }

      // ── Case Intelligence (all roles — backend scoped) ──
      renderCases(cases);

      // ── Charts (all roles — backend scoped) ──
      renderExceptionChart(exceptions);
      renderReconChart(matchStatus);

      // ── Agent Performance (org-wide roles only — hidden in template) ──
      if (IS_ORG_WIDE) {
        renderAgentPerformance(agentPerf);
      }

      // Update last-refreshed timestamp
      var ts = document.getElementById("last-refresh");
      if (ts) ts.textContent = new Date().toLocaleTimeString();
    }).catch(function (err) {
      console.error("[Command Center] Data load failed:", err);
    });
  }

  function startAutoRefresh() {
    if (refreshInterval) clearInterval(refreshInterval);
    refreshInterval = setInterval(loadAll, 10000);
  }

  // ── Search / Command Bar ──
  function initCommandBar() {
    var searchInput = document.getElementById("cc-search");
    if (!searchInput) return;

    var debounceTimer;
    searchInput.addEventListener("input", function () {
      clearTimeout(debounceTimer);
      var q = this.value.trim();
      debounceTimer = setTimeout(function () {
        if (q.length < 2) return;
        // Navigate to appropriate search depending on prefix
        if (q.startsWith("INV") || q.startsWith("inv")) {
          window.location.href = "/invoices/?q=" + encodeURIComponent(q);
        } else if (q.startsWith("CASE") || q.startsWith("case") || q.startsWith("#")) {
          window.location.href = "/cases/?q=" + encodeURIComponent(q.replace("#", ""));
        } else {
          window.location.href = "/invoices/?q=" + encodeURIComponent(q);
        }
      }, 600);
    });

    searchInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        var q = this.value.trim();
        if (q) window.location.href = "/invoices/?q=" + encodeURIComponent(q);
      }
    });
  }

  // ── Utility ──
  function escapeHtml(s) {
    var div = document.createElement("div");
    div.appendChild(document.createTextNode(s));
    return div.innerHTML;
  }

  // ── Init ──
  document.addEventListener("DOMContentLoaded", function () {
    loadAll();
    startAutoRefresh();
    initCommandBar();
    renderTimeline();
  });
})();
