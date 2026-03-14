/* =====================================================================
   Agent Performance Command Center — JavaScript
   Fetches data from APIs + renders charts, tables, feeds
   ===================================================================== */
(function () {
  "use strict";

  const BASE = "/api/v1/dashboard/agent-performance";
  const CTX = window.__AP_CTX || {};
  let _charts = {};
  let _feedInterval = null;
  let _filters = {};

  // ── Agent type labels & colors ──
  const AGENT_LABELS = {
    INVOICE_EXTRACTION: "Extraction",
    INVOICE_UNDERSTANDING: "Understanding",
    PO_RETRIEVAL: "PO Retrieval",
    GRN_RETRIEVAL: "GRN Retrieval",
    RECONCILIATION_ASSIST: "Recon Assist",
    EXCEPTION_ANALYSIS: "Exception",
    REVIEW_ROUTING: "Review Routing",
    CASE_SUMMARY: "Case Summary",
  };
  const AGENT_COLORS = [
    "#0d6efd", "#6610f2", "#6f42c1", "#d63384",
    "#fd7e14", "#198754", "#20c997", "#0dcaf0",
  ];

  // ── Helpers ──
  function qs(params) {
    const p = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v) p.set(k, v); });
    const s = p.toString();
    return s ? "?" + s : "";
  }

  async function apiFetch(path) {
    try {
      const resp = await fetch(path, { credentials: "same-origin" });
      if (!resp.ok) return null;
      return await resp.json();
    } catch { return null; }
  }

  function fmt(n) {
    if (n == null) return "—";
    if (typeof n === "number") return n.toLocaleString();
    return n;
  }

  function fmtMs(ms) {
    if (ms == null) return "—";
    if (ms < 1000) return ms + " ms";
    return (ms / 1000).toFixed(1) + " s";
  }

  function fmtCost(c) {
    if (c == null || c === 0) return "$0.00";
    return "$" + Number(c).toFixed(4);
  }

  function fmtTokens(t) {
    if (!t) return "0";
    if (t >= 1000000) return (t / 1000000).toFixed(1) + "M";
    if (t >= 1000) return (t / 1000).toFixed(1) + "K";
    return t.toString();
  }

  function shortLabel(agentType) {
    return AGENT_LABELS[agentType] || agentType;
  }

  function statusBadge(status) {
    return `<span class="ap-status-badge ap-status-${status}">${status}</span>`;
  }

  function sevBadge(sev) {
    return `<span class="ap-status-badge ap-sev-${sev}">${sev}</span>`;
  }

  function healthBadge(successPct) {
    if (successPct >= 90) return '<span class="ap-health-badge ap-health-healthy">Healthy</span>';
    if (successPct >= 70) return '<span class="ap-health-badge ap-health-warning">Warning</span>';
    return '<span class="ap-health-badge ap-health-critical">Critical</span>';
  }

  function timeAgo(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return "just now";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
    return d.toLocaleDateString();
  }

  function destroyChart(key) {
    if (_charts[key]) { _charts[key].destroy(); delete _charts[key]; }
  }

  // ── Filter management ──
  function readFilters() {
    _filters = {
      date_from: document.getElementById("filterDateFrom")?.value || "",
      date_to: document.getElementById("filterDateTo")?.value || "",
      agent_type: document.getElementById("filterAgentType")?.value || "",
      status: document.getElementById("filterStatus")?.value || "",
      trace_id: document.getElementById("filterTraceId")?.value || "",
    };
  }

  function clearFilters() {
    ["filterDateFrom", "filterDateTo", "filterAgentType", "filterStatus", "filterTraceId"]
      .forEach(id => { const el = document.getElementById(id); if (el) el.value = ""; });
    _filters = {};
    refreshAll();
  }

  function applyFilters() {
    readFilters();
    refreshAll();
  }

  // ── 1. Summary KPIs ──
  async function loadSummary() {
    const d = await apiFetch(BASE + "/summary/" + qs(_filters));
    if (!d) return;
    document.getElementById("kpiTotalRuns").textContent = fmt(d.total_runs_today);
    document.getElementById("kpiActiveAgents").textContent = fmt(d.active_agents);
    document.getElementById("kpiSuccessRate").textContent = d.success_rate + "%";
    document.getElementById("kpiEscalationRate").textContent = d.escalation_rate + "%";
    document.getElementById("kpiAvgRuntime").textContent = fmtMs(d.avg_runtime_ms);
    document.getElementById("kpiCostToday").textContent = fmtCost(d.estimated_cost_today);
    document.getElementById("kpiDenied").textContent = fmt(d.access_denied_today);
    document.getElementById("kpiGoverned").textContent = d.governed_pct + "%";
  }

  // ── 2. Utilization charts ──
  async function loadUtilizationChart() {
    const d = await apiFetch(BASE + "/utilization/" + qs(_filters));
    if (!d) return;

    // By-type bar chart
    const types = (d.by_type || []);
    destroyChart("utilization");
    const ctx1 = document.getElementById("chartUtilization")?.getContext("2d");
    if (ctx1) {
      _charts.utilization = new Chart(ctx1, {
        type: "bar",
        data: {
          labels: types.map(r => shortLabel(r.agent_type)),
          datasets: [{
            label: "Runs",
            data: types.map(r => r.count),
            backgroundColor: AGENT_COLORS.slice(0, types.length),
            borderRadius: 4,
            maxBarThickness: 38,
          }],
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
      });
    }

    // Hourly line chart
    const hours = (d.by_hour || []);
    destroyChart("hourly");
    const ctx2 = document.getElementById("chartHourly")?.getContext("2d");
    if (ctx2) {
      _charts.hourly = new Chart(ctx2, {
        type: "line",
        data: {
          labels: hours.map(r => { const d2 = new Date(r.hour); return d2.getHours() + ":00"; }),
          datasets: [{
            label: "Runs",
            data: hours.map(r => r.count),
            borderColor: "#0d6efd",
            backgroundColor: "rgba(13,110,253,.1)",
            fill: true, tension: .3, pointRadius: 3,
          }],
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
      });
    }
  }

  // ── 3. Success table ──
  async function loadSuccessTable() {
    const d = await apiFetch(BASE + "/success/" + qs(_filters));
    if (!d) return;
    const tbody = document.getElementById("successTableBody");
    if (!tbody) return;
    if (!d.length) { tbody.innerHTML = '<tr><td colspan="10" class="text-center text-muted py-3">No agent runs found</td></tr>'; return; }
    tbody.innerHTML = d.map(r => `<tr>
      <td class="fw-semibold">${shortLabel(r.agent_type)}</td>
      <td class="text-center">${fmt(r.total_runs)}</td>
      <td class="text-center">${r.success_pct}%</td>
      <td class="text-center">${r.failed_pct}%</td>
      <td class="text-center">${fmt(r.escalations)}</td>
      <td class="text-center">${r.avg_confidence}%</td>
      <td class="text-center">${fmtMs(r.avg_duration_ms)}</td>
      <td class="text-center">${r.governed_pct}%</td>
      <td class="text-center">${r.trace_coverage_pct}%</td>
      <td class="text-center">${healthBadge(r.success_pct)}</td>
    </tr>`).join("");
  }

  // ── 4. Latency chart ──
  async function loadLatencyWidgets() {
    const d = await apiFetch(BASE + "/latency/" + qs(_filters));
    if (!d) return;

    const agents = (d.per_agent || []);
    destroyChart("latency");
    const ctx = document.getElementById("chartLatency")?.getContext("2d");
    if (ctx) {
      _charts.latency = new Chart(ctx, {
        type: "bar",
        data: {
          labels: agents.map(r => shortLabel(r.agent_type)),
          datasets: [
            { label: "Avg (ms)", data: agents.map(r => Math.round(r.avg_duration || 0)), backgroundColor: "rgba(13,110,253,.6)", borderRadius: 4, maxBarThickness: 30 },
            { label: "Max (ms)", data: agents.map(r => r.max_duration || 0), backgroundColor: "rgba(220,53,69,.4)", borderRadius: 4, maxBarThickness: 30 },
          ],
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } } }, scales: { y: { beginAtZero: true } } },
      });
    }
  }

  // ── 5. Token & cost charts ──
  async function loadTokenCharts() {
    const d = await apiFetch(BASE + "/tokens/" + qs(_filters));
    if (!d) return;

    document.getElementById("tokPrompt").textContent = fmtTokens(d.total_prompt_tokens);
    document.getElementById("tokCompletion").textContent = fmtTokens(d.total_completion_tokens);
    document.getElementById("tokTotal").textContent = fmtTokens(d.total_tokens);
    document.getElementById("tokCost").textContent = fmtCost(d.total_cost);

    const agents = (d.by_agent || []).filter(r => r.cost > 0);
    destroyChart("costShare");
    const ctx = document.getElementById("chartCostShare")?.getContext("2d");
    if (ctx && agents.length) {
      _charts.costShare = new Chart(ctx, {
        type: "doughnut",
        data: {
          labels: agents.map(r => shortLabel(r.agent_type)),
          datasets: [{ data: agents.map(r => r.cost), backgroundColor: AGENT_COLORS.slice(0, agents.length) }],
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "right", labels: { boxWidth: 10, font: { size: 11 } } } } },
      });
    }
  }

  // ── 6. Tool metrics ──
  async function loadToolCharts() {
    const d = await apiFetch(BASE + "/tools/" + qs(_filters));
    if (!d) return;

    document.getElementById("toolMostUsed").textContent = d.most_used || "—";
    document.getElementById("toolSlowest").textContent = d.slowest_tool || "—";
    document.getElementById("toolMostFailed").textContent = d.most_failed || "—";

    const tbody = document.getElementById("toolTableBody");
    if (!tbody) return;
    const tools = d.by_tool || [];
    if (!tools.length) { tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3">No tool calls</td></tr>'; return; }
    tbody.innerHTML = tools.map(r => `<tr>
      <td class="fw-semibold">${r.tool_name}</td>
      <td class="text-center">${fmt(r.total)}</td>
      <td class="text-center text-success">${r.success_pct}%</td>
      <td class="text-center text-danger">${r.failed_pct}%</td>
      <td class="text-center">${fmtMs(r.avg_duration)}</td>
    </tr>`).join("");
  }

  // ── 7. Recommendation charts ──
  async function loadRecommendationCharts() {
    const d = await apiFetch(BASE + "/recommendations/" + qs(_filters));
    if (!d) return;

    const recs = d.by_type || [];
    destroyChart("recs");
    const ctx = document.getElementById("chartRecommendations")?.getContext("2d");
    if (ctx && recs.length) {
      _charts.recs = new Chart(ctx, {
        type: "bar",
        data: {
          labels: recs.map(r => r.recommendation_type.replace(/_/g, " ")),
          datasets: [
            { label: "Accepted", data: recs.map(r => r.accepted), backgroundColor: "rgba(25,135,84,.6)", borderRadius: 4, maxBarThickness: 28 },
            { label: "Rejected", data: recs.map(r => r.rejected), backgroundColor: "rgba(220,53,69,.5)", borderRadius: 4, maxBarThickness: 28 },
            { label: "Pending", data: recs.map(r => r.pending), backgroundColor: "rgba(108,117,125,.3)", borderRadius: 4, maxBarThickness: 28 },
          ],
        },
        options: { responsive: true, maintainAspectRatio: false, indexAxis: "y", plugins: { legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } } }, scales: { x: { stacked: true, ticks: { precision: 0 } }, y: { stacked: true } } },
      });
    }

    // Table
    const tbody = document.getElementById("recTableBody");
    if (!tbody) return;
    if (!recs.length) { tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted py-3">No recommendations</td></tr>'; return; }
    tbody.innerHTML = recs.map(r => `<tr>
      <td style="font-size:.74rem">${r.recommendation_type.replace(/_/g, " ")}</td>
      <td class="text-center">${fmt(r.count)}</td>
      <td class="text-center">${fmt(r.accepted)}</td>
      <td class="text-center">${r.acceptance_rate != null ? r.acceptance_rate + "%" : "—"}</td>
    </tr>`).join("");
  }

  // ── 8. Live feed ──
  async function loadLiveFeed() {
    const d = await apiFetch(BASE + "/live-feed/" + qs(_filters));
    const el = document.getElementById("liveFeed");
    if (!el) return;
    if (!d || !d.length) { el.innerHTML = '<div class="text-center text-muted py-4">No recent agent runs</div>'; return; }

    el.innerHTML = d.map(r => {
      const roleInfo = r.actor_role ? `<span class="badge bg-secondary bg-opacity-25 text-secondary" style="font-size:.6rem">${r.actor_role}</span>` : "";
      const traceBadge = r.has_trace ? '<i class="bi bi-diagram-3 text-primary" title="Traced"></i>' : '<i class="bi bi-diagram-3 text-muted opacity-25" title="No trace"></i>';
      return `<div class="ap-feed-item" onclick="window.APDashboard.loadTraceDetail(${r.id})">
        <div class="ap-feed-icon"><i class="bi bi-robot"></i></div>
        <div class="ap-feed-body">
          <div class="ap-feed-title">${shortLabel(r.agent_type)} ${r.invoice_number ? "· " + r.invoice_number : ""}</div>
          <div class="ap-feed-sub">${r.summary || "—"} ${roleInfo}</div>
        </div>
        <div class="ap-feed-meta">
          <div>${statusBadge(r.status)} ${traceBadge}</div>
          <div class="ap-feed-time">${r.confidence}% · ${fmtMs(r.duration_ms)}</div>
          <div class="ap-feed-time">${timeAgo(r.created_at)}</div>
        </div>
      </div>`;
    }).join("");
  }

  // ── 9. Escalations ──
  async function loadEscalations() {
    const d = await apiFetch(BASE + "/escalations/" + qs(_filters));
    const tbody = document.getElementById("escalationTableBody");
    if (!tbody) return;
    if (!d || !d.length) { tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3">No escalations</td></tr>'; return; }
    tbody.innerHTML = d.map(r => `<tr>
      <td class="fw-semibold">${shortLabel(r.agent_run__agent_type || "")}</td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${r.reason || "—"}</td>
      <td class="text-center">${sevBadge(r.severity || "MEDIUM")}</td>
      <td>${r.suggested_assignee_role || "—"}</td>
      <td class="text-muted">${timeAgo(r.created_at)}</td>
    </tr>`).join("");
  }

  // ── 10. Failures chart ──
  async function loadFailures() {
    const d = await apiFetch(BASE + "/failures/" + qs(_filters));
    if (!d) return;
    const cats = d.categories || {};
    const labels = Object.keys(cats).map(k => k.replace(/_/g, " "));
    const values = Object.values(cats);
    const emptyEl = document.getElementById("failureEmpty");

    if (values.every(v => v === 0)) {
      if (emptyEl) emptyEl.classList.remove("d-none");
      destroyChart("failures");
      const canvas = document.getElementById("chartFailures");
      if (canvas) canvas.style.display = "none";
      return;
    }
    if (emptyEl) emptyEl.classList.add("d-none");
    const canvas = document.getElementById("chartFailures");
    if (canvas) canvas.style.display = "";

    destroyChart("failures");
    const ctx = canvas?.getContext("2d");
    if (ctx) {
      _charts.failures = new Chart(ctx, {
        type: "doughnut",
        data: {
          labels: labels,
          datasets: [{ data: values, backgroundColor: ["#dc3545", "#fd7e14", "#ffc107", "#6c757d", "#0d6efd", "#6f42c1", "#adb5bd"] }],
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "right", labels: { boxWidth: 10, font: { size: 11 } } } } },
      });
    }
  }

  // ── 11. Governance ──
  // (Handled by trace-explorer.js — GovWidgets module)

  // ── 12. Trace detail ──
  // (Handled by trace-explorer.js — TraceExplorer module)
  async function loadTraceDetail(runId) {
    // Delegate to new TraceExplorer if available, else use Phase 1 API
    if (window.TraceExplorer && window.TraceExplorer.selectRun) {
      window.TraceExplorer.selectRun(runId);
      return;
    }
  }

  async function loadTraceRunList(activeId) {
    // Delegated to TraceExplorer module
  }

  // ── Refresh all ──
  async function refreshAll() {
    readFilters();
    document.getElementById("lastRefreshed").textContent = "Updated " + new Date().toLocaleTimeString();

    // Fire all fetches in parallel
    await Promise.all([
      loadSummary(),
      loadUtilizationChart(),
      loadSuccessTable(),
      loadLatencyWidgets(),
      loadTokenCharts(),
      loadToolCharts(),
      loadRecommendationCharts(),
      loadLiveFeed(),
      loadEscalations(),
      loadFailures(),
    ]);
    // Governance + Trace handled by trace-explorer.js
  }

  // ── Init ──
  function init() {
    refreshAll();
    // Auto-refresh live feed every 10 seconds
    _feedInterval = setInterval(loadLiveFeed, 10000);
  }

  // ── Public API ──
  window.APDashboard = {
    refreshAll,
    applyFilters,
    clearFilters,
    loadTraceDetail,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
