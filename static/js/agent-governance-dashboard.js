/* =====================================================================
   Agent Governance Dashboard — JavaScript
   Fetches data from governance APIs + renders charts, tables, trace
   ===================================================================== */
(function () {
  "use strict";

  var BASE = "/api/v1/dashboard/agents/governance";
  var CTX = window.__AG_CTX || {};
  var _charts = {};
  var _activeRunId = null;
  var _filters = {};

  // ── Agent type labels & colors ──
  var AGENT_LABELS = {
    INVOICE_EXTRACTION: "Extraction",
    INVOICE_UNDERSTANDING: "Understanding",
    PO_RETRIEVAL: "PO Retrieval",
    GRN_RETRIEVAL: "GRN Retrieval",
    RECONCILIATION_ASSIST: "Recon Assist",
    EXCEPTION_ANALYSIS: "Exception",
    REVIEW_ROUTING: "Review Routing",
    CASE_SUMMARY: "Case Summary",
  };
  var AGENT_COLORS = [
    "#0d6efd", "#6610f2", "#6f42c1", "#d63384",
    "#fd7e14", "#198754", "#20c997", "#0dcaf0",
  ];
  var ROLE_COLORS = {
    ADMIN: "#0d6efd",
    AP_PROCESSOR: "#198754",
    REVIEWER: "#6f42c1",
    FINANCE_MANAGER: "#fd7e14",
    AUDITOR: "#d63384",
    SYSTEM_AGENT: "#6c757d",
  };

  // ── Helpers ──
  function qs(params) {
    var p = new URLSearchParams();
    Object.entries(params).forEach(function(e) { if (e[1]) p.set(e[0], e[1]); });
    var s = p.toString();
    return s ? "?" + s : "";
  }

  async function apiFetch(path) {
    try {
      var resp = await fetch(path, { credentials: "same-origin" });
      if (!resp.ok) return null;
      return await resp.json();
    } catch(e) { return null; }
  }

  function fmt(n) { return n == null ? "—" : (typeof n === "number" ? n.toLocaleString() : n); }
  function fmtMs(ms) { return ms == null ? "—" : ms < 1000 ? ms + " ms" : (ms / 1000).toFixed(1) + " s"; }
  function fmtCost(c) { return !c ? "$0.00" : "$" + Number(c).toFixed(4); }
  function fmtTokens(t) { return !t ? "0" : t >= 1e6 ? (t / 1e6).toFixed(1) + "M" : t >= 1e3 ? (t / 1e3).toFixed(1) + "K" : String(t); }
  function shortLabel(t) { return AGENT_LABELS[t] || t; }

  function statusBadge(s) { return '<span class="ag-status-badge ag-status-' + s + '">' + s + '</span>'; }
  function accessBadge(granted) {
    return granted
      ? '<span class="ag-access-badge ag-access-granted"><i class="bi bi-check-circle-fill me-1"></i>Granted</span>'
      : '<span class="ag-access-badge ag-access-denied"><i class="bi bi-x-circle-fill me-1"></i>Denied</span>';
  }

  function timeAgo(iso) {
    if (!iso) return "";
    var diff = (Date.now() - new Date(iso).getTime()) / 1000;
    if (diff < 60) return "just now";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
    return new Date(iso).toLocaleDateString();
  }

  function destroyChart(key) {
    if (_charts[key]) { _charts[key].destroy(); delete _charts[key]; }
  }

  // ── Filter management ──
  function readFilters() {
    _filters = {
      date_from: document.getElementById("govFilterDateFrom")?.value || "",
      date_to: document.getElementById("govFilterDateTo")?.value || "",
      agent_type: document.getElementById("govFilterAgentType")?.value || "",
      actor_role: document.getElementById("govFilterRole")?.value || "",
      permission: document.getElementById("govFilterPermission")?.value || "",
      trace_id: document.getElementById("govFilterTraceId")?.value || "",
    };
  }

  function clearFilters() {
    ["govFilterDateFrom", "govFilterDateTo", "govFilterAgentType",
     "govFilterRole", "govFilterPermission", "govFilterTraceId"]
      .forEach(function(id) { var el = document.getElementById(id); if (el) el.value = ""; });
    _filters = {};
    refreshAll();
  }

  function applyFilters() {
    readFilters();
    refreshAll();
  }

  // ── A. Governance Summary KPIs ──
  async function loadSummary() {
    var d = await apiFetch(BASE + "/summary/" + qs(_filters));
    if (!d) return;
    var el = function(id, val) { var e = document.getElementById(id); if (e) e.textContent = val; };
    el("govKpiRbacCoverage", (d.rbac_coverage_pct || 0) + "%");
    el("govKpiGranted", fmt(d.access_granted));
    el("govKpiDenied", fmt(d.access_denied));
    el("govKpiTraceCoverage", (d.trace_coverage_pct || 0) + "%");
    el("govKpiSystemAgent", fmt(d.system_agent_runs));
    el("govKpiPermCompliance", (d.permission_compliance_pct || 0) + "%");
  }

  // ── B. Execution Identity ──
  async function loadIdentity() {
    var d = await apiFetch(BASE + "/identity/" + qs(_filters));
    if (!d) return;

    // Access by role chart
    var byRole = d.by_role || [];
    destroyChart("accessByRole");
    var ctx = document.getElementById("govChartAccessByRole")?.getContext("2d");
    if (ctx && byRole.length) {
      var roles = byRole.map(function(r) { return r.role || "Unknown"; });
      _charts.accessByRole = new Chart(ctx, {
        type: "bar",
        data: {
          labels: roles,
          datasets: [
            { label: "Granted", data: byRole.map(function(r) { return r.granted; }), backgroundColor: "rgba(25,135,84,.6)", borderRadius: 4, maxBarThickness: 30 },
            { label: "Denied", data: byRole.map(function(r) { return r.denied; }), backgroundColor: "rgba(220,53,69,.5)", borderRadius: 4, maxBarThickness: 30 },
          ],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } } },
          scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } } },
        },
      });
    }

    // Identity attribution table
    var tbody = document.getElementById("govIdentityTableBody");
    if (!tbody) return;
    if (!byRole.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3">No identity data</td></tr>';
      return;
    }
    tbody.innerHTML = byRole.map(function(r) {
      var grantPct = r.total ? Math.round(r.granted / r.total * 100) : 0;
      return '<tr>' +
        '<td><span class="badge bg-secondary bg-opacity-25 text-secondary" style="font-size:.65rem">' + (r.role || "—") + '</span></td>' +
        '<td class="text-center">' + fmt(r.total) + '</td>' +
        '<td class="text-center text-success">' + fmt(r.granted) + '</td>' +
        '<td class="text-center text-danger">' + fmt(r.denied) + '</td>' +
        '<td class="text-center">' + grantPct + '%</td>' +
      '</tr>';
    }).join("");
  }

  // ── C. Authorization Matrix ──
  async function loadAuthorization() {
    var d = await apiFetch(BASE + "/authorization/" + qs(_filters));
    if (!d) return;

    var perms = d.permissions || [];
    var totalEl = document.getElementById("govAuthTotal");
    if (totalEl) totalEl.textContent = perms.length + " permissions";

    var tbody = document.getElementById("govAuthMatrixBody");
    if (!tbody) return;
    if (!perms.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-3">No authorization data</td></tr>';
      return;
    }
    tbody.innerHTML = perms.map(function(r) {
      var denialPct = r.checks ? Math.round(r.denied / r.checks * 100) : 0;
      return '<tr>' +
        '<td><code style="font-size:.72rem">' + (r.permission || "—") + '</code></td>' +
        '<td class="text-center">' + fmt(r.checks) + '</td>' +
        '<td class="text-center text-success">' + fmt(r.granted) + '</td>' +
        '<td class="text-center text-danger">' + fmt(r.denied) + '</td>' +
        '<td class="text-center">' + (denialPct > 0 ? '<span class="text-danger">' + denialPct + '%</span>' : '0%') + '</td>' +
        '<td style="font-size:.72rem">' + (r.source || "—") + '</td>' +
      '</tr>';
    }).join("");
  }

  // ── D. Tool Authorization ──
  async function loadToolAuthorization() {
    if (!CTX.isFullGovernance) return;
    var d = await apiFetch(BASE + "/tools/" + qs(_filters));
    if (!d) return;

    var tools = d.tools || [];
    var tbody = document.getElementById("govToolAuthBody");
    if (!tbody) return;
    if (!tools.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-3">No tool authorization data</td></tr>';
      return;
    }
    tbody.innerHTML = tools.map(function(r) {
      var authRate = r.calls ? Math.round(r.authorized / r.calls * 100) : 0;
      return '<tr>' +
        '<td class="fw-semibold">' + (r.tool_name || "—") + '</td>' +
        '<td><code style="font-size:.7rem">' + (r.required_permission || "—") + '</code></td>' +
        '<td class="text-center">' + fmt(r.calls) + '</td>' +
        '<td class="text-center text-success">' + fmt(r.authorized) + '</td>' +
        '<td class="text-center text-danger">' + fmt(r.denied) + '</td>' +
        '<td class="text-center">' + authRate + '%</td>' +
      '</tr>';
    }).join("");
  }

  // ── E. Recommendation Governance ──
  async function loadRecommendationGovernance() {
    if (!CTX.isFullGovernance) return;
    var d = await apiFetch(BASE + "/recommendations/" + qs(_filters));
    if (!d) return;

    var recs = d.recommendations || [];
    var tbody = document.getElementById("govRecGovBody");
    if (!tbody) return;
    if (!recs.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-3">No recommendation governance data</td></tr>';
      return;
    }
    tbody.innerHTML = recs.map(function(r) {
      var authRate = r.total ? Math.round(r.accepted / r.total * 100) : 0;
      return '<tr>' +
        '<td style="font-size:.74rem">' + (r.type || "").replace(/_/g, " ") + '</td>' +
        '<td><code style="font-size:.7rem">' + (r.required_permission || "—") + '</code></td>' +
        '<td class="text-center">' + fmt(r.total) + '</td>' +
        '<td class="text-center text-success">' + fmt(r.accepted) + '</td>' +
        '<td class="text-center text-danger">' + fmt(r.rejected) + '</td>' +
        '<td class="text-center">' + authRate + '%</td>' +
      '</tr>';
    }).join("");
  }

  // ── F. Protected Action Outcomes ──
  async function loadProtectedActions() {
    var d = await apiFetch(BASE + "/protected-actions/" + qs(_filters));
    if (!d) return;

    var actions = d.actions || [];
    destroyChart("protectedActions");
    var ctx = document.getElementById("govChartProtectedActions")?.getContext("2d");
    if (ctx && actions.length) {
      _charts.protectedActions = new Chart(ctx, {
        type: "bar",
        data: {
          labels: actions.map(function(r) { return (r.event_type || "").replace(/_/g, " "); }),
          datasets: [
            { label: "Granted", data: actions.map(function(r) { return r.granted; }), backgroundColor: "rgba(25,135,84,.6)", borderRadius: 4, maxBarThickness: 28 },
            { label: "Denied", data: actions.map(function(r) { return r.denied; }), backgroundColor: "rgba(220,53,69,.5)", borderRadius: 4, maxBarThickness: 28 },
          ],
        },
        options: {
          responsive: true, maintainAspectRatio: false, indexAxis: "y",
          plugins: { legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } } },
          scales: { x: { stacked: true, ticks: { precision: 0 } }, y: { stacked: true } },
        },
      });
    }
  }

  // ── RBAC Coverage Trend ──
  async function loadCoverageTrend() {
    var d = await apiFetch(BASE + "/coverage-trend/" + qs(_filters));
    if (!d) return;

    var daily = d.daily || [];
    destroyChart("coverageTrend");
    var ctx = document.getElementById("govChartCoverageTrend")?.getContext("2d");
    if (ctx && daily.length) {
      _charts.coverageTrend = new Chart(ctx, {
        type: "line",
        data: {
          labels: daily.map(function(r) {
            var dt = new Date(r.date);
            return dt.toLocaleDateString(undefined, { month: "short", day: "numeric" });
          }),
          datasets: [
            {
              label: "RBAC Coverage %",
              data: daily.map(function(r) { return r.rbac_pct; }),
              borderColor: "#0d6efd",
              backgroundColor: "rgba(13,110,253,.1)",
              fill: true, tension: .3, pointRadius: 2,
            },
            {
              label: "Trace Coverage %",
              data: daily.map(function(r) { return r.trace_pct; }),
              borderColor: "#198754",
              backgroundColor: "rgba(25,135,84,.1)",
              fill: true, tension: .3, pointRadius: 2,
            },
          ],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } } },
          scales: { y: { beginAtZero: true, max: 100, ticks: { callback: function(v) { return v + "%"; } } } },
        },
      });
    }
  }

  // ── G. Denials Feed ──
  async function loadDenials() {
    var d = await apiFetch(BASE + "/denials/" + qs(_filters));
    if (!d) return;

    var events = d.events || [];
    var countEl = document.getElementById("govDenialCount");
    if (countEl) countEl.textContent = events.length + " denials";

    var tbody = document.getElementById("govDenialsBody");
    if (!tbody) return;
    if (!events.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-3">No denials recorded</td></tr>';
      return;
    }
    tbody.innerHTML = events.map(function(r) {
      var traceShort = (r.trace_id || "").substring(0, 12);
      return '<tr>' +
        '<td class="text-muted">' + timeAgo(r.created_at) + '</td>' +
        '<td>' + (r.actor_email || "—") + '</td>' +
        '<td><span class="badge bg-secondary bg-opacity-25 text-secondary" style="font-size:.62rem">' + (r.actor_role || "—") + '</span></td>' +
        '<td style="font-size:.72rem">' + (r.event_type || "").replace(/_/g, " ") + '</td>' +
        '<td><code style="font-size:.7rem">' + (r.permission || "—") + '</code></td>' +
        '<td style="font-size:.72rem">' + (r.source || "—") + '</td>' +
        '<td style="font-size:.66rem;font-family:monospace">' + (traceShort ? traceShort + "…" : "—") + '</td>' +
      '</tr>';
    }).join("");
  }

  // ── H. System Agent Oversight ──
  async function loadSystemAgent() {
    if (!CTX.isFullGovernance) return;
    var d = await apiFetch(BASE + "/system-agent/" + qs(_filters));
    if (!d) return;

    var el = function(id, val) { var e = document.getElementById(id); if (e) e.textContent = val; };
    el("govSysTotal", fmt(d.total_runs));
    el("govSysCompleted", fmt(d.completed));
    el("govSysFailed", fmt(d.failed));
    el("govSysAutoClose", fmt(d.auto_close_actions));

    var byType = d.by_type || [];
    var tbody = document.getElementById("govSysAgentBody");
    if (!tbody) return;
    if (!byType.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-3">No system agent data</td></tr>';
      return;
    }
    tbody.innerHTML = byType.map(function(r) {
      return '<tr>' +
        '<td class="fw-semibold">' + shortLabel(r.agent_type) + '</td>' +
        '<td class="text-center">' + fmt(r.runs) + '</td>' +
        '<td class="text-center text-success">' + fmt(r.completed) + '</td>' +
        '<td class="text-center text-danger">' + fmt(r.failed) + '</td>' +
        '<td class="text-center">' + fmt(r.auto_close) + '</td>' +
        '<td class="text-center">' + fmtMs(r.avg_duration_ms) + '</td>' +
      '</tr>';
    }).join("");
  }

  // =====================================================================
  // I. TRACE EXPLORER
  // =====================================================================

  function getTeFilters() {
    var base = Object.assign({}, _filters);
    var searchEl = document.getElementById("govTraceSearch");
    var statusEl = document.getElementById("govTraceStatusFilter");
    if (searchEl?.value) base.trace_id = searchEl.value;
    if (statusEl?.value) base.status = statusEl.value;
    return base;
  }

  async function loadTraceRunList() {
    var runs = await apiFetch(BASE + "/denials/" + qs(Object.assign({}, getTeFilters(), { limit: 50 })));
    // Actually we need a list of agent runs, not denials. Use the live-feed from performance as a fallback
    // We'll use the governance summary to get runs with governance data
    runs = await apiFetch("/api/v1/dashboard/agents/performance/live-feed/?limit=50");

    var el = document.getElementById("govTraceRunList");
    if (!el || !runs) return;

    var total = runs.length;
    var traced = runs.filter(function(r) { return r.has_trace; }).length;
    var setCount = function(id, val) { var e = document.getElementById(id); if (e) e.textContent = val; };
    setCount("govTeRunCount", total);
    setCount("govTeTracedCount", traced);
    setCount("govTeNoTraceCount", total - traced);

    if (!runs.length) {
      el.innerHTML = '<div class="text-center text-muted py-4" style="font-size:.82rem">No runs found</div>';
      return;
    }

    el.innerHTML = runs.map(function(r) {
      var isActive = r.id === _activeRunId;
      var govBadges =
        '<span class="ag-gov-indicator ' + (r.has_trace ? 'ag-gi-ok' : 'ag-gi-warn') + '" title="Trace"><i class="bi bi-diagram-3"></i></span>' +
        '<span class="ag-gov-indicator ' + (r.access_granted !== false ? 'ag-gi-ok' : 'ag-gi-warn') + '" title="Access"><i class="bi bi-shield-check"></i></span>';
      return '<div class="te-run-item ' + (isActive ? 'active' : '') + '" onclick="window.AGDashboard.selectTrace(' + r.id + ')">' +
        '<div class="te-run-main">' +
          '<span class="fw-semibold">#' + r.id + '</span> ' +
          '<span>' + shortLabel(r.agent_type) + '</span>' +
          '<span class="ms-auto d-flex gap-1 align-items-center">' + govBadges + ' ' + statusBadge(r.status) + '</span>' +
        '</div>' +
        '<div class="te-run-sub">' +
          (r.invoice_number ? '<span>' + r.invoice_number + '</span>' : '') +
          '<span>' + (r.confidence || 0) + '%</span>' +
          '<span>' + fmtMs(r.duration_ms) + '</span>' +
          '<span class="text-muted">' + timeAgo(r.created_at) + '</span>' +
        '</div>' +
      '</div>';
    }).join("");
  }

  async function selectTraceRun(runId) {
    _activeRunId = runId;

    var d = await apiFetch(BASE + "/trace/" + runId + "/");
    var el = document.getElementById("govTraceDetail");
    if (!el || !d) {
      if (el) el.innerHTML = '<div class="text-center text-danger py-4">Run not found</div>';
      return;
    }

    // Highlight active in list
    document.querySelectorAll("#govTraceRunList .te-run-item").forEach(function(item) {
      item.classList.toggle("active", item.textContent.includes("#" + runId));
    });

    // Build metadata grid
    var meta = '';
    var addMeta = function(key, val) {
      if (val) meta += '<div class="ag-trace-meta-item"><span class="ag-trace-meta-key">' + key + '</span><span class="ag-trace-meta-val">' + val + '</span></div>';
    };
    addMeta("Agent", shortLabel(d.agent_type));
    addMeta("Status", statusBadge(d.status));
    addMeta("Confidence", (d.confidence || 0) + "%");
    addMeta("Duration", fmtMs(d.duration_ms));
    addMeta("Invoice", d.invoice_number || "—");
    addMeta("Trace ID", d.trace_id);
    addMeta("Span ID", d.span_id);
    addMeta("Actor", d.actor_email || d.actor_user_id || "—");
    addMeta("Role", d.actor_primary_role || "—");
    addMeta("Permission", d.permission_checked || "—");
    addMeta("Source", d.permission_source || "—");
    addMeta("Access", d.access_granted != null ? accessBadge(d.access_granted) : "—");
    addMeta("Cost Est.", d.cost_estimate ? fmtCost(d.cost_estimate) : null);
    addMeta("Tokens", d.total_tokens ? fmtTokens(d.total_tokens) : null);
    addMeta("Model", d.llm_model_used);
    if (d.error_message) addMeta("Error", '<span class="text-danger">' + d.error_message + '</span>');

    // Build execution timeline
    var timeline = (d.timeline || []).map(function(ev) {
      var extra = '';
      if (ev.duration_ms) extra += '<span class="ag-trace-event-meta">' + fmtMs(ev.duration_ms) + '</span> ';
      if (ev.status) extra += statusBadge(ev.status) + ' ';
      if (ev.confidence != null) extra += '<span class="ag-trace-event-meta">' + Math.round((ev.confidence || 0) * 100) + '%</span> ';
      if (ev.accepted != null) extra += '<span class="ag-trace-event-meta">' + (ev.accepted === true ? "Accepted" : ev.accepted === false ? "Rejected" : "Pending") + '</span> ';
      if (ev.rationale) extra += '<div class="ag-event-detail">' + ev.rationale + '</div>';
      if (ev.reasoning) extra += '<div class="ag-event-detail">' + ev.reasoning + '</div>';
      if (ev.input_summary) extra += '<div class="ag-event-io"><b>In:</b> ' + ev.input_summary + '</div>';
      if (ev.output_summary) extra += '<div class="ag-event-io"><b>Out:</b> ' + ev.output_summary + '</div>';
      if (ev.error) extra += '<div class="ag-event-detail text-danger">' + ev.error + '</div>';
      if (ev.reason) extra += '<div class="ag-event-detail">' + ev.reason + '</div>';

      return '<div class="ag-trace-event event-' + ev.event + '">' +
        '<div class="ag-trace-event-time">' + new Date(ev.time).toLocaleTimeString() + '</div>' +
        '<div class="ag-trace-event-label">' + ev.label + '</div>' +
        (extra ? '<div>' + extra + '</div>' : '') +
      '</div>';
    }).join("");

    // Span tree
    var spanHtml = '';
    var spans = d.span_tree || [];
    if (spans.length) {
      spanHtml = '<h6 class="fw-semibold mb-2 mt-3" style="font-size:.8rem"><i class="bi bi-bezier2 me-1"></i>Related Spans</h6>' +
        '<div class="ag-span-tree">' + spans.map(function(s) {
          return '<div class="ag-span-item" onclick="window.AGDashboard.selectTrace(' + s.id + ')">' +
            '<span class="fw-semibold">#' + s.id + '</span> ' +
            shortLabel(s.agent_type) + ' ' +
            statusBadge(s.status) + ' ' +
            '<span class="text-muted">' + fmtMs(s.duration_ms) + '</span>' +
          '</div>';
        }).join("") + '</div>';
    }

    el.innerHTML =
      '<h6 class="fw-bold mb-3"><i class="bi bi-diagram-3 me-1"></i>Run #' + d.id + '</h6>' +
      '<div class="ag-trace-meta">' + meta + '</div>' +
      (d.summarized_reasoning ? '<div class="mb-3 p-2 bg-light rounded" style="font-size:.78rem">' + d.summarized_reasoning + '</div>' : '') +
      '<h6 class="fw-semibold mb-2" style="font-size:.8rem"><i class="bi bi-clock-history me-1"></i>Execution Timeline</h6>' +
      '<div class="ag-trace-timeline">' + (timeline || '<div class="text-muted py-2" style="font-size:.8rem">No timeline events</div>') + '</div>' +
      spanHtml;

    loadTraceRunList();
  }

  // ── Refresh all ──
  async function refreshAll() {
    readFilters();
    var el = document.getElementById("govLastRefreshed");
    if (el) el.textContent = "Updated " + new Date().toLocaleTimeString();

    await Promise.all([
      loadSummary(),
      loadIdentity(),
      loadAuthorization(),
      loadToolAuthorization(),
      loadRecommendationGovernance(),
      loadProtectedActions(),
      loadCoverageTrend(),
      loadDenials(),
      loadSystemAgent(),
      loadTraceRunList(),
    ]);
  }

  function refreshTrace() {
    loadTraceRunList();
  }

  // ── Init ──
  function init() {
    refreshAll();

    // Search/filter event listeners for trace explorer
    var searchEl = document.getElementById("govTraceSearch");
    if (searchEl) {
      var debounce;
      searchEl.addEventListener("input", function() {
        clearTimeout(debounce);
        debounce = setTimeout(loadTraceRunList, 300);
      });
    }
    var statusEl = document.getElementById("govTraceStatusFilter");
    if (statusEl) statusEl.addEventListener("change", loadTraceRunList);
  }

  // ── Public API ──
  window.AGDashboard = {
    refreshAll: refreshAll,
    applyFilters: applyFilters,
    clearFilters: clearFilters,
    selectTrace: selectTraceRun,
    refreshTrace: refreshTrace,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
