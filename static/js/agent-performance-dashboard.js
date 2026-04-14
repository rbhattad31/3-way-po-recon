/* =====================================================================
   Agent Performance Dashboard — JavaScript
   Fetches data from performance APIs + renders charts, tables, feeds
   ===================================================================== */
(function () {
  "use strict";

  const BASE = "/api/v1/dashboard/agents/performance";
  let _charts = {};
  let _feedInterval = null;
  let _filters = {};
  let _cacheCounter = 0;

  // ── Agent type labels & colors ──
    // ── Agent labels are sourced dynamically from the server-rendered filter options ──
    const AGENT_LABELS = {};
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
     return AGENT_LABELS[agentType] || _humanizeAgentType(agentType) || agentType;
  }
  function _humanizeAgentType(agentType) {
    if (!agentType) return "";
    return String(agentType)
      .toLowerCase()
      .split("_")
      .map(function(part) { return part ? part.charAt(0).toUpperCase() + part.slice(1) : ""; })
      .join(" ");
  }

  function _hydrateAgentLabelsFromFilter() {
    var select = document.getElementById("filterAgentType");
    if (!select) return;
    Array.from(select.options || []).forEach(function(opt) {
      if (!opt || !opt.value) return;
      AGENT_LABELS[opt.value] = (opt.textContent || "").trim() || opt.value;
    });
  }

  function statusBadge(status) {
    return '<span class="ap-status-badge ap-status-' + status + '">' + status + '</span>';
  }

  function healthBadge(successPct) {
    if (successPct >= 90) return '<span class="ap-health-badge ap-health-healthy">Healthy</span>';
    if (successPct >= 70) return '<span class="ap-health-badge ap-health-warning">Warning</span>';
    return '<span class="ap-health-badge ap-health-critical">Critical</span>';
  }

  function timeAgo(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    var diff = (Date.now() - d.getTime()) / 1000;
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
    };
  }

  function clearFilters() {
    ["filterDateFrom", "filterDateTo", "filterAgentType", "filterStatus"]
      .forEach(function(id) { var el = document.getElementById(id); if (el) el.value = ""; });
    _filters = {};
    refreshAll();
  }

  function applyFilters() {
    readFilters();
    refreshAll();
  }

  // ── 1. Summary KPIs ──
  async function loadSummary() {
    var d = await apiFetch(BASE + "/summary/" + qs(_filters));
    if (!d) return;
    document.getElementById("kpiTotalRuns").textContent = fmt(d.total_runs_today);
    document.getElementById("kpiActiveAgents").textContent = fmt(d.active_agents);
    document.getElementById("kpiSuccessRate").textContent = d.success_rate + "%";
    document.getElementById("kpiEscalationRate").textContent = d.escalation_rate + "%";
    document.getElementById("kpiAvgRuntime").textContent = fmtMs(d.avg_runtime_ms);
    document.getElementById("kpiCostToday").textContent = fmtCost(d.estimated_cost_today);
  }

  // ── 2. Utilization charts ──
  async function loadUtilizationChart() {
    var d = await apiFetch(BASE + "/utilization/" + qs(_filters));
    if (!d) return;

    var types = (d.by_type || []);
    destroyChart("utilization");
    var ctx1 = document.getElementById("chartUtilization")?.getContext("2d");
    if (ctx1) {
      _charts.utilization = new Chart(ctx1, {
        type: "bar",
        data: {
          labels: types.map(function(r) { return shortLabel(r.agent_type); }),
          datasets: [{
            label: "Runs",
            data: types.map(function(r) { return r.count; }),
            backgroundColor: AGENT_COLORS.slice(0, types.length),
            borderRadius: 4,
            maxBarThickness: 38,
          }],
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
      });
    }

    var hours = (d.by_hour || []);
    destroyChart("hourly");
    var ctx2 = document.getElementById("chartHourly")?.getContext("2d");
    if (ctx2) {
      _charts.hourly = new Chart(ctx2, {
        type: "line",
        data: {
          labels: hours.map(function(r) { var d2 = new Date(r.hour); return d2.getHours() + ":00"; }),
          datasets: [{
            label: "Runs",
            data: hours.map(function(r) { return r.count; }),
            borderColor: "#0d6efd",
            backgroundColor: "rgba(13,110,253,.1)",
            fill: true, tension: .3, pointRadius: 3,
          }],
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
      });
    }
  }

  // ── 3. Reliability table ──
  async function loadReliabilityTable() {
    var d = await apiFetch(BASE + "/reliability/" + qs(_filters));
    if (!d) return;
    var tbody = document.getElementById("reliabilityTableBody");
    if (!tbody) return;
    if (!d.length) { tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-3">No agent runs found</td></tr>'; return; }
    tbody.innerHTML = d.map(function(r) { return '<tr>' +
      '<td class="fw-semibold">' + shortLabel(r.agent_type) + '</td>' +
      '<td class="text-center">' + fmt(r.total_runs) + '</td>' +
      '<td class="text-center">' + r.success_pct + '%</td>' +
      '<td class="text-center">' + r.failed_pct + '%</td>' +
      '<td class="text-center">' + fmt(r.escalations) + '</td>' +
      '<td class="text-center">' + r.avg_confidence + '%</td>' +
      '<td class="text-center">' + fmtMs(r.avg_duration_ms) + '</td>' +
      '<td class="text-center">' + healthBadge(r.success_pct) + '</td>' +
    '</tr>'; }).join("");
  }

  // ── 4. Latency chart ──
  async function loadLatencyWidgets() {
    _cacheCounter++;
    var filterStr = qs(_filters);
    var sep = filterStr ? "&" : "?";
    var d = await apiFetch(BASE + "/latency/" + filterStr + sep + "cb=" + _cacheCounter);
    if (!d) return;

    var tbody = document.getElementById("slowestRunsBody");
    if (tbody) {
      var runs = d.slowest_runs || [];
      if (!runs.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3">No data in selected period.</td></tr>';
      } else {
        tbody.innerHTML = runs.map(function(r) {
          var invoiceCell = r.invoice_id
            ? '<a href="/governance/invoices/' + r.invoice_id + '/">' + (r.invoice_number || r.id) + '</a>'
            : (r.invoice_number || "-");
          return '<tr>' +
            '<td>' + shortLabel(r.agent_type) + '</td>' +
            '<td>' + invoiceCell + '</td>' +
            '<td class="text-end">' + fmtMs(r.duration_ms) + '</td>' +
            '<td class="text-center">' + statusBadge(r.status) + '</td>' +
            '<td>' + timeAgo(r.started_at) + '</td>' +
          '</tr>';
        }).join("");
      }
    }
  }

  // ── 5. Token & cost charts ──
  async function loadTokenCharts() {
    var d = await apiFetch(BASE + "/tokens/" + qs(_filters));
    if (!d) return;

    document.getElementById("tokPrompt").textContent = fmtTokens(d.total_prompt_tokens);
    document.getElementById("tokCompletion").textContent = fmtTokens(d.total_completion_tokens);
    document.getElementById("tokTotal").textContent = fmtTokens(d.total_tokens);
    document.getElementById("tokCost").textContent = fmtCost(d.total_cost);

    var agents = (d.by_agent || []).filter(function(r) { return r.cost > 0; });
    destroyChart("costShare");
    var ctx = document.getElementById("chartCostShare")?.getContext("2d");
    if (ctx && agents.length) {
      _charts.costShare = new Chart(ctx, {
        type: "doughnut",
        data: {
          labels: agents.map(function(r) { return shortLabel(r.agent_type); }),
          datasets: [{ data: agents.map(function(r) { return r.cost; }), backgroundColor: AGENT_COLORS.slice(0, agents.length) }],
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "right", labels: { boxWidth: 10, font: { size: 11 } } } } },
      });
    }
  }

  // ── 6. Tool metrics ──
  async function loadToolCharts() {
    var d = await apiFetch(BASE + "/tools/" + qs(_filters));
    if (!d) return;

    document.getElementById("toolMostUsed").textContent = d.most_used || "—";
    document.getElementById("toolSlowest").textContent = d.slowest_tool || "—";
    document.getElementById("toolMostFailed").textContent = d.most_failed || "—";

    var tbody = document.getElementById("toolTableBody");
    if (!tbody) return;
    var tools = d.by_tool || [];
    if (!tools.length) { tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3">No tool calls</td></tr>'; return; }
    tbody.innerHTML = tools.map(function(r) { return '<tr>' +
      '<td class="fw-semibold">' + r.tool_name + '</td>' +
      '<td class="text-center">' + fmt(r.total) + '</td>' +
      '<td class="text-center text-success">' + r.success_pct + '%</td>' +
      '<td class="text-center text-danger">' + r.failed_pct + '%</td>' +
      '<td class="text-center">' + fmtMs(r.avg_duration) + '</td>' +
    '</tr>'; }).join("");
  }

  // ── 7. Recommendation charts ──
  async function loadRecommendationCharts() {
    var d = await apiFetch(BASE + "/recommendations/" + qs(_filters));
    if (!d) return;

    var recs = d.by_type || [];
    destroyChart("recs");
    var ctx = document.getElementById("chartRecommendations")?.getContext("2d");
    if (ctx && recs.length) {
      _charts.recs = new Chart(ctx, {
        type: "bar",
        data: {
          labels: recs.map(function(r) { return r.recommendation_type.replace(/_/g, " "); }),
          datasets: [
            { label: "Accepted", data: recs.map(function(r) { return r.accepted; }), backgroundColor: "rgba(25,135,84,.6)", borderRadius: 4, maxBarThickness: 28 },
            { label: "Rejected", data: recs.map(function(r) { return r.rejected; }), backgroundColor: "rgba(220,53,69,.5)", borderRadius: 4, maxBarThickness: 28 },
            { label: "Pending", data: recs.map(function(r) { return r.pending; }), backgroundColor: "rgba(108,117,125,.3)", borderRadius: 4, maxBarThickness: 28 },
          ],
        },
        options: { responsive: true, maintainAspectRatio: false, indexAxis: "y", plugins: { legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } } }, scales: { x: { stacked: true, ticks: { precision: 0 } }, y: { stacked: true } } },
      });
    }

    var tbody = document.getElementById("recTableBody");
    if (!tbody) return;
    if (!recs.length) { tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted py-3">No recommendations</td></tr>'; return; }
    tbody.innerHTML = recs.map(function(r) { return '<tr>' +
      '<td style="font-size:.74rem">' + r.recommendation_type.replace(/_/g, " ") + '</td>' +
      '<td class="text-center">' + fmt(r.count) + '</td>' +
      '<td class="text-center">' + fmt(r.accepted) + '</td>' +
      '<td class="text-center">' + (r.acceptance_rate != null ? r.acceptance_rate + "%" : "—") + '</td>' +
    '</tr>'; }).join("");
  }

  // -- 9. Plan Comparison --
  async function loadPlanComparison() {
    var d = await apiFetch(BASE + "/plan-comparison/" + qs(_filters));
    if (!d) return;

    var el = document.getElementById("planTotal");
    if (el) el.textContent = fmt(d.total_compared);
    el = document.getElementById("planMatched");
    if (el) el.textContent = fmt(d.plans_matched);
    el = document.getElementById("planRate");
    if (el) el.textContent = (d.match_rate != null ? d.match_rate + "%" : "--");

    var tbody = document.getElementById("planCompTableBody");
    if (!tbody) return;

    var rows = d.rows || [];
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-3">No data in selected period.</td></tr>';
      return;
    }

    tbody.innerHTML = rows.map(function(r) {
      var invoiceCell;
      if (r.invoice_id) {
        invoiceCell = '<a href="/governance/invoices/' + r.invoice_id + '/">' +
          (r.invoice_number || r.result_id) + '</a>';
      } else {
        invoiceCell = String(r.result_id);
      }

      var msClass = "text-muted";
      if (r.match_status === "MATCHED") { msClass = "text-success"; }
      else if (r.match_status === "PARTIAL_MATCH") { msClass = "text-warning"; }
      else if (r.match_status === "UNMATCHED") { msClass = "text-danger"; }
      var statusCell = '<small class="' + msClass + ' fw-semibold">' + (r.match_status || "") + '</small>';

      var policyCell = (r.policy_plan || []).map(function(a) { return shortLabel(a); }).join(" -> ");
      var actualCell = (r.actual_plan || []).map(function(a) { return shortLabel(a); }).join(" -> ");

      var matchCell = r.plans_match
        ? '<i class="bi bi-check-circle-fill text-success"></i>'
        : '<i class="bi bi-exclamation-triangle-fill text-warning"></i>';

      var changes = [];
      if (r.added_by_actual && r.added_by_actual.length) {
        changes.push('<span class="text-success">+' +
          r.added_by_actual.map(function(a) { return shortLabel(a); }).join(", +") +
        '</span>');
      }
      if (r.removed_by_actual && r.removed_by_actual.length) {
        changes.push('<span class="text-danger">-' +
          r.removed_by_actual.map(function(a) { return shortLabel(a); }).join(", -") +
        '</span>');
      }
      var changesCell = changes.length ? changes.join(" ") : "-";

      return '<tr>' +
        '<td>' + invoiceCell + '</td>' +
        '<td>' + statusCell + '</td>' +
        '<td style="font-size:.73rem">' + (policyCell || "-") + '</td>' +
        '<td style="font-size:.73rem">' + (actualCell || "-") + '</td>' +
        '<td class="text-center">' + matchCell + '</td>' +
        '<td style="font-size:.73rem">' + changesCell + '</td>' +
      '</tr>';
    }).join("");
  }

  // -- 8. Recent Runs --
  async function loadLiveFeed() {
    _cacheCounter++;
    var filterStr2 = qs(_filters);
    var sep2 = filterStr2 ? "&" : "?";
    var d = await apiFetch(BASE + "/live-feed/" + filterStr2 + sep2 + "cb=" + _cacheCounter);
    var tbody = document.getElementById("recentRunsBody");
    if (!tbody) return;
    if (!d || !d.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-3">No recent agent runs</td></tr>';
      return;
    }
    tbody.innerHTML = d.map(function(r) {
      var invoiceCell = r.invoice_id
        ? '<a href="/governance/invoices/' + r.invoice_id + '/">' + (r.invoice_number || r.invoice_id) + '</a>'
        : (r.invoice_number || "-");
      return '<tr>' +
        '<td>' + shortLabel(r.agent_type) + '</td>' +
        '<td>' + invoiceCell + '</td>' +
        '<td class="text-end">' + r.confidence + '%</td>' +
        '<td class="text-end">' + fmtMs(r.duration_ms) + '</td>' +
        '<td class="text-center">' + statusBadge(r.status) + '</td>' +
        '<td>' + timeAgo(r.created_at) + '</td>' +
      '</tr>';
    }).join("");
  }

  // ── 10. Planner Comparison: Deterministic vs LLM ──
  async function loadPlannerComparison() {
    var spinEl = document.getElementById("plannerCompRefreshing");
    if (spinEl) spinEl.style.display = "";
    var d = await apiFetch(BASE + "/planner-comparison/" + qs(_filters));
    if (spinEl) spinEl.style.display = "none";
    if (!d) return;

    // KPI strip
    var _set = function(id, val) { var el = document.getElementById(id); if (el) el.textContent = val; };
    _set("pcTotalRuns", fmt(d.total_runs));
    _set("pcLlmRuns", fmt(d.llm_runs));
    _set("pcDetRuns", fmt(d.deterministic_runs));
    _set("pcLlmRate", d.llm_rate != null ? d.llm_rate + "%" : "--");
    _set("pcDivRate", d.divergence_rate != null ? d.divergence_rate + "%" : "--");

    // Grouped bar chart
    destroyChart("plannerComp");
    var ctx = document.getElementById("chartPlannerComparison");
    if (ctx && d.chart_labels && d.chart_labels.length) {
      var CHART_COLORS = ["rgba(25,135,84,.75)", "rgba(13,110,253,.75)", "rgba(253,126,20,.75)"];
      _charts.plannerComp = new Chart(ctx.getContext("2d"), {
        type: "bar",
        data: {
          labels: d.chart_labels,
          datasets: (d.chart_datasets || []).map(function(ds, i) {
            return {
              label: ds.label,
              data: ds.data,
              backgroundColor: CHART_COLORS[i % CHART_COLORS.length],
              borderRadius: 4,
              maxBarThickness: 32,
            };
          }),
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } },
            tooltip: { callbacks: { label: function(ctx2) { return ctx2.dataset.label + ": " + ctx2.parsed.y + "%"; } } },
          },
          scales: {
            y: { beginAtZero: true, max: 100, ticks: { callback: function(v) { return v + "%"; } } },
          },
        },
      });
    }

    // Per-source breakdown table
    var srcBody = document.getElementById("plannerSourceTableBody");
    if (srcBody) {
      var srcs = d.by_source || [];
      if (!srcs.length) {
        srcBody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-3">No orchestration runs in period.</td></tr>';
      } else {
        srcBody.innerHTML = srcs.map(function(r) {
          var srcBadge = r.plan_source === "llm"
            ? '<span class="badge bg-primary">LLM</span>'
            : r.plan_source === "deterministic"
              ? '<span class="badge bg-success">Deterministic</span>'
              : '<span class="badge bg-secondary">' + r.plan_source + '</span>';
          var divClass = r.divergence_rate > 20 ? "text-danger" : (r.divergence_rate > 5 ? "text-warning" : "text-success");
          return '<tr>' +
            '<td>' + srcBadge + ' <small class="text-muted">' + r.label + '</small></td>' +
            '<td class="text-center">' + fmt(r.run_count) + '</td>' +
            '<td class="text-center">' + r.success_rate + '%</td>' +
            '<td class="text-center">' + r.avg_confidence + '%</td>' +
            '<td class="text-center ' + divClass + '">' + r.divergence_rate + '%</td>' +
            '<td class="text-center">' + fmtMs(r.avg_duration_ms) + '</td>' +
          '</tr>';
        }).join("");
      }
    }

    // Recent orchestration runs table
    var recBody = document.getElementById("plannerRecentBody");
    if (recBody) {
      var runs = d.recent_runs || [];
      if (!runs.length) {
        recBody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-3">No orchestration runs in period.</td></tr>';
      } else {
        recBody.innerHTML = runs.map(function(r) {
          var invCell = r.invoice_id
            ? '<a href="/governance/invoices/' + r.invoice_id + '/">' + (r.invoice_number || r.result_id) + '</a>'
            : (r.invoice_number || String(r.result_id || "-"));

          var plannerBadge = r.plan_source === "llm"
            ? '<span class="badge bg-primary" style="font-size:.68rem">LLM</span>'
            : r.plan_source === "deterministic"
              ? '<span class="badge bg-success" style="font-size:.68rem">Det.</span>'
              : '<span class="badge bg-secondary" style="font-size:.68rem">' + r.plan_source + '</span>';

          var _fmtAgents = function(arr) {
            if (!arr || !arr.length) return '<span class="text-muted">—</span>';
            return arr.map(function(a) { return '<span class="badge bg-light text-dark border me-1" style="font-size:.63rem">' + _humanizeAgentType(a) + '</span>'; }).join("");
          };

          var divergedCell = r.diverged
            ? '<i class="bi bi-exclamation-triangle-fill text-warning" title="Planned != Executed"></i>'
            : '<i class="bi bi-check-circle-fill text-success" title="No divergence"></i>';

          var statusBadgeCell = statusBadge(r.status);

          return '<tr>' +
            '<td>' + invCell + '</td>' +
            '<td class="text-center">' + plannerBadge + '</td>' +
            '<td style="font-size:.7rem">' + _fmtAgents(r.planned_agents) + '</td>' +
            '<td style="font-size:.7rem">' + _fmtAgents(r.executed_agents) + '</td>' +
            '<td class="text-center">' + divergedCell + '</td>' +
            '<td class="text-center">' + statusBadgeCell + '</td>' +
            '<td class="text-end">' + (r.final_confidence ? r.final_confidence + '%' : '--') + '</td>' +
            '<td class="text-center">' + timeAgo(r.started_at) + '</td>' +
          '</tr>';
        }).join("");
      }
    }
  }

  // ── Refresh all ──
  async function refreshAll() {
    readFilters();
    document.getElementById("lastRefreshed").textContent = "Updated " + new Date().toLocaleTimeString();

    await Promise.all([
      loadSummary(),
      loadUtilizationChart(),
      loadReliabilityTable(),
      loadLatencyWidgets(),
      loadTokenCharts(),
      loadToolCharts(),
      loadRecommendationCharts(),
      loadLiveFeed(),
      loadPlanComparison(),
      loadPlannerComparison(),
    ]);
  }

  // ── Init ──
  function init() {
    _hydrateAgentLabelsFromFilter();
    refreshAll();
    _feedInterval = setInterval(loadLiveFeed, 10000);
  }

  // ── Public API ──
  window.APDashboard = {
    refreshAll: refreshAll,
    applyFilters: applyFilters,
    clearFilters: clearFilters,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
