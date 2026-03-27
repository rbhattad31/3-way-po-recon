/* =====================================================================
   Trace Explorer & Governance Widgets — JavaScript
   Dedicated governance observability for ADMIN/AUDITOR roles
   ===================================================================== */
(function () {
  "use strict";

  const GOV_BASE = "/api/v1/dashboard/governance";
  const AP_BASE = "/api/v1/dashboard/agent-performance";
  const CTX = window.__AP_CTX || {};
  let _govCharts = {};
  let _activeRunId = null;

  // ── Agent labels (shared) ──
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
  const ROLE_COLORS = {
    ADMIN: "#0d6efd",
    AP_PROCESSOR: "#198754",
    REVIEWER: "#6f42c1",
    FINANCE_MANAGER: "#fd7e14",
    AUDITOR: "#d63384",
  };

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

  function fmt(n) { return n == null ? "—" : (typeof n === "number" ? n.toLocaleString() : n); }
  function fmtMs(ms) { return ms == null ? "—" : ms < 1000 ? ms + " ms" : (ms / 1000).toFixed(1) + " s"; }
  function fmtCost(c) { return !c ? "$0.00" : "$" + Number(c).toFixed(4); }
  function fmtTokens(t) { return !t ? "0" : t >= 1e6 ? (t / 1e6).toFixed(1) + "M" : t >= 1e3 ? (t / 1e3).toFixed(1) + "K" : String(t); }
  function shortLabel(t) { return AGENT_LABELS[t] || t; }

  function statusBadge(s) { return '<span class="ap-status-badge ap-status-' + s + '">' + s + '</span>'; }
  function sevBadge(s) { return '<span class="ap-status-badge ap-sev-' + s + '">' + s + '</span>'; }
  function accessBadge(granted) {
    return granted
      ? '<span class="te-access-badge te-access-granted"><i class="bi bi-check-circle-fill me-1"></i>Granted</span>'
      : '<span class="te-access-badge te-access-denied"><i class="bi bi-x-circle-fill me-1"></i>Denied</span>';
  }

  function healthBadge(tracePct, decisionPct) {
    const avg = (tracePct + decisionPct) / 2;
    if (avg >= 80) return '<span class="ap-health-badge ap-health-healthy">Healthy</span>';
    if (avg >= 50) return '<span class="ap-health-badge ap-health-warning">Warning</span>';
    return '<span class="ap-health-badge ap-health-critical">Critical</span>';
  }

  function timeAgo(iso) {
    if (!iso) return "";
    const diff = (Date.now() - new Date(iso).getTime()) / 1000;
    if (diff < 60) return "just now";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
    return new Date(iso).toLocaleDateString();
  }

  function destroyChart(key) {
    if (_govCharts[key]) { _govCharts[key].destroy(); delete _govCharts[key]; }
  }

  // Filter readers
  function getGovFilters() {
    // Re-use main filter bar for date/agent/status, plus governance-specific filters
    return {
      date_from: document.getElementById("filterDateFrom")?.value || "",
      date_to: document.getElementById("filterDateTo")?.value || "",
      agent_type: document.getElementById("filterAgentType")?.value || "",
      status: document.getElementById("filterStatus")?.value || "",
      trace_id: document.getElementById("filterTraceId")?.value || "",
      actor_role: document.getElementById("govRoleFilter")?.value || "",
      permission: document.getElementById("govPermFilter")?.value || "",
    };
  }

  function getTeFilters() {
    const base = getGovFilters();
    const searchEl = document.getElementById("traceExplorerSearch");
    const statusEl = document.getElementById("traceExplorerStatusFilter");
    if (searchEl?.value) base.trace_id = searchEl.value;
    if (statusEl?.value) base.status = statusEl.value;
    return base;
  }


  // =====================================================================
  // GOVERNANCE WIDGETS
  // =====================================================================

  async function loadGovSummary() {
    if (!CTX.isGovernance) return;
    const d = await apiFetch(GOV_BASE + "/summary/" + qs(getGovFilters()));
    if (!d) return;

    const el = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };
    el("govTraceCoverage", d.trace_coverage_pct + "%");
    el("govPermCompliance", d.permission_compliance_pct + "%");
    el("govAccessGranted", fmt(d.access_granted));
    el("govAccessDenied", fmt(d.access_denied));
    el("govDecisionCov", d.decision_coverage_pct + "%");
    el("govRecCov", d.recommendation_coverage_pct + "%");
  }

  async function loadPermissionActivity() {
    if (!CTX.isGovernance) return;
    const d = await apiFetch(GOV_BASE + "/permission-activity/" + qs(getGovFilters()));
    if (!d) return;

    // Daily grant/deny chart
    const daily = d.daily || [];
    destroyChart("permActivity");
    const ctx = document.getElementById("chartPermActivity")?.getContext("2d");
    if (ctx && daily.length) {
      _govCharts.permActivity = new Chart(ctx, {
        type: "line",
        data: {
          labels: daily.map(r => {
            const dt = new Date(r.date);
            return dt.toLocaleDateString(undefined, { month: "short", day: "numeric" });
          }),
          datasets: [
            {
              label: "Granted",
              data: daily.map(r => r.granted),
              borderColor: "#198754",
              backgroundColor: "rgba(25,135,84,.1)",
              fill: true, tension: .3, pointRadius: 2,
            },
            {
              label: "Denied",
              data: daily.map(r => r.denied),
              borderColor: "#dc3545",
              backgroundColor: "rgba(220,53,69,.1)",
              fill: true, tension: .3, pointRadius: 2,
            },
          ],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } } },
          scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
        },
      });
    }

    // Top permissions table
    const tbody = document.getElementById("govTopPermsBody");
    const perms = d.top_permissions || [];
    if (tbody) {
      if (!perms.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted py-3">No permission data</td></tr>';
      } else {
        tbody.innerHTML = perms.map(r => {
          const denialPct = r.count ? Math.round(r.denied / r.count * 100) : 0;
          return '<tr>' +
            '<td><code style="font-size:.72rem">' + (r.permission_checked || "—") + '</code></td>' +
            '<td class="text-center">' + fmt(r.count) + '</td>' +
            '<td class="text-center text-danger">' + fmt(r.denied) + '</td>' +
            '<td class="text-center">' + (denialPct > 0 ? '<span class="text-danger">' + denialPct + '%</span>' : '0%') + '</td>' +
          '</tr>';
        }).join("");
      }
    }

    // Permission source doughnut
    const sources = d.by_source || [];
    destroyChart("permSource");
    const ctx2 = document.getElementById("chartPermSource")?.getContext("2d");
    if (ctx2 && sources.length) {
      _govCharts.permSource = new Chart(ctx2, {
        type: "doughnut",
        data: {
          labels: sources.map(r => r.permission_source || "Unknown"),
          datasets: [{ data: sources.map(r => r.count), backgroundColor: ["#0d6efd", "#6f42c1", "#198754", "#fd7e14", "#d63384", "#6c757d"] }],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { position: "right", labels: { boxWidth: 10, font: { size: 11 } } } },
        },
      });
    }
  }

  async function loadAccessEvents() {
    if (!CTX.isGovernance) return;
    const d = await apiFetch(GOV_BASE + "/access-events/" + qs(getGovFilters()));
    if (!d) return;

    // Access by role chart
    const byRole = d.by_role || [];
    destroyChart("accessByRole");
    const ctx = document.getElementById("chartAccessByRole")?.getContext("2d");
    if (ctx && byRole.length) {
      const roles = byRole.map(r => r.actor_primary_role || "Unknown");
      _govCharts.accessByRole = new Chart(ctx, {
        type: "bar",
        data: {
          labels: roles,
          datasets: [
            { label: "Granted", data: byRole.map(r => r.granted), backgroundColor: "rgba(25,135,84,.6)", borderRadius: 4, maxBarThickness: 30 },
            { label: "Denied", data: byRole.map(r => r.denied), backgroundColor: "rgba(220,53,69,.5)", borderRadius: 4, maxBarThickness: 30 },
          ],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } } },
          scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } } },
        },
      });
    }

    // Access events table
    const events = d.events || [];
    const totalEl = document.getElementById("govAccessTotal");
    if (totalEl) totalEl.textContent = events.length + " events";

    const tbody = document.getElementById("govAccessEventsBody");
    if (!tbody) return;
    if (!events.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-3">No access events</td></tr>';
      return;
    }
    tbody.innerHTML = events.map(r => {
      const traceShort = (r.trace_id || "").substring(0, 12);
      return '<tr>' +
        '<td class="text-muted">' + timeAgo(r.created_at) + '</td>' +
        '<td>' + (r.actor_email || "—") + '</td>' +
        '<td><span class="badge bg-secondary bg-opacity-25 text-secondary" style="font-size:.62rem">' + (r.actor_primary_role || "—") + '</span></td>' +
        '<td><code style="font-size:.7rem">' + (r.permission_checked || "—") + '</code></td>' +
        '<td style="font-size:.72rem">' + (r.permission_source || "—") + '</td>' +
        '<td class="text-center">' + accessBadge(r.access_granted) + '</td>' +
        '<td style="font-size:.66rem;font-family:monospace">' + (traceShort ? traceShort + "…" : "—") + '</td>' +
        '<td style="font-size:.72rem">' + (r.entity_type || "") + (r.entity_id ? "#" + r.entity_id : "") + '</td>' +
      '</tr>';
    }).join("");
  }

  async function loadGovHealth() {
    if (!CTX.isGovernance) return;
    const d = await apiFetch(GOV_BASE + "/health/" + qs(getGovFilters()));
    if (!d) return;

    const rows = d.per_agent || [];
    const tbody = document.getElementById("govHealthBody");
    if (!tbody) return;
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="11" class="text-center text-muted py-3">No agent data</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(r => {
      return '<tr>' +
        '<td class="fw-semibold">' + shortLabel(r.agent_type) + '</td>' +
        '<td class="text-center">' + fmt(r.total) + '</td>' +
        '<td class="text-center">' + r.trace_pct + '%</td>' +
        '<td class="text-center">' + r.decision_pct + '%</td>' +
        '<td class="text-center">' + r.recommendation_pct + '%</td>' +
        '<td class="text-center">' + r.permission_pct + '%</td>' +
        '<td class="text-center">' + (r.missing_trace > 0 ? '<span class="text-warning fw-bold">' + r.missing_trace + '</span>' : '0') + '</td>' +
        '<td class="text-center">' + (r.missing_decision > 0 ? '<span class="text-warning fw-bold">' + r.missing_decision + '</span>' : '0') + '</td>' +
        '<td class="text-center">' + (r.failed > 0 ? '<span class="text-danger fw-bold">' + r.failed + '</span>' : '0') + '</td>' +
        '<td class="text-center">' + (r.escalated > 0 ? '<span class="text-danger fw-bold">' + r.escalated + '</span>' : '0') + '</td>' +
        '<td class="text-center">' + healthBadge(r.trace_pct, r.decision_pct) + '</td>' +
      '</tr>';
    }).join("");
  }

  async function refreshGovernance() {
    if (!CTX.isGovernance) return;
    await Promise.all([
      loadGovSummary(),
      loadPermissionActivity(),
      loadAccessEvents(),
      loadGovHealth(),
    ]);
  }


  // =====================================================================
  // TRACE EXPLORER
  // =====================================================================

  async function loadTraceRunList() {
    if (!CTX.isExtended) return;

    // Use dedicated governance trace-runs API if governance user,
    // otherwise fall back to the Phase 1 live-feed
    const isGov = CTX.isGovernance;
    let runs;
    if (isGov) {
      runs = await apiFetch(GOV_BASE + "/trace-runs/" + qs(getTeFilters()));
    } else {
      runs = await apiFetch(AP_BASE + "/live-feed/?limit=50");
    }

    const el = document.getElementById("traceExplorerRunList");
    if (!el || !runs) return;

    // Update count badges
    const total = runs.length;
    const traced = runs.filter(r => r.has_trace).length;
    const setCount = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };
    setCount("teRunCount", total);
    setCount("teTracedCount", traced);
    setCount("teNoTraceCount", total - traced);

    if (!runs.length) {
      el.innerHTML = '<div class="text-center text-muted py-4" style="font-size:.82rem">No runs found</div>';
      return;
    }

    el.innerHTML = runs.map(r => {
      const isActive = r.id === _activeRunId;
      const govBadges = isGov
        ? ('<span class="te-gov-indicator ' + (r.has_trace ? 'te-gi-ok' : 'te-gi-warn') + '" title="Trace"><i class="bi bi-diagram-3"></i></span>' +
           '<span class="te-gov-indicator ' + (r.has_decisions ? 'te-gi-ok' : 'te-gi-warn') + '" title="Decision"><i class="bi bi-check-square"></i></span>' +
           '<span class="te-gov-indicator ' + (r.has_recommendations ? 'te-gi-ok' : 'te-gi-warn') + '" title="Rec"><i class="bi bi-lightbulb"></i></span>')
        : (r.has_trace ? '<i class="bi bi-diagram-3 text-primary" style="font-size:.7rem"></i>' : '');
      return '<div class="te-run-item ' + (isActive ? 'active' : '') + '" onclick="window.TraceExplorer.selectRun(' + r.id + ')">' +
        '<div class="te-run-main">' +
          '<span class="fw-semibold">#' + r.id + '</span> ' +
          '<span>' + shortLabel(r.agent_type) + '</span>' +
          '<span class="ms-auto d-flex gap-1 align-items-center">' + govBadges + ' ' + statusBadge(r.status) + '</span>' +
        '</div>' +
        '<div class="te-run-sub">' +
          (r.invoice_number ? '<span>' + r.invoice_number + '</span>' : '') +
          '<span>' + r.confidence + '%</span>' +
          '<span>' + fmtMs(r.duration_ms) + '</span>' +
          '<span class="text-muted">' + timeAgo(r.created_at) + '</span>' +
        '</div>' +
      '</div>';
    }).join("");
  }

  async function selectTraceRun(runId) {
    if (!CTX.isExtended) return;
    _activeRunId = runId;

    // Use dedicated governance API for full detail if available
    const isGov = CTX.isGovernance;
    const d = isGov
      ? await apiFetch(GOV_BASE + "/trace/" + runId + "/")
      : await apiFetch(AP_BASE + "/trace/" + runId + "/");

    const el = document.getElementById("traceExplorerDetail");
    if (!el || !d) { if (el) el.innerHTML = '<div class="text-center text-danger py-4">Run not found</div>'; return; }

    // Highlight active in list
    document.querySelectorAll(".te-run-item").forEach(item => {
      item.classList.toggle("active", item.textContent.includes("#" + runId));
    });

    // Build metadata grid
    let meta = '';
    const addMeta = (key, val) => { if (val) meta += '<div class="ap-trace-meta-item"><span class="ap-trace-meta-key">' + key + '</span><span class="ap-trace-meta-val">' + val + '</span></div>'; };
    addMeta("Agent", shortLabel(d.agent_type));
    addMeta("Status", statusBadge(d.status));
    addMeta("Confidence", d.confidence + "%");
    addMeta("Duration", fmtMs(d.duration_ms));
    addMeta("Invoice", d.invoice_number || "—");
    addMeta("Reason", d.invocation_reason || "—");
    addMeta("Trace ID", d.trace_id);
    addMeta("Span ID", d.span_id);
    addMeta("Prompt Ver.", d.prompt_version);
    addMeta("Actor ID", d.actor_user_id);
    addMeta("Permission", d.permission_checked);
    addMeta("Cost Est.", d.cost_estimate ? fmtCost(d.cost_estimate) : null);
    addMeta("Tokens", d.total_tokens ? fmtTokens(d.total_tokens) : null);
    addMeta("Model", d.llm_model_used);
    if (d.error_message) addMeta("Error", '<span class="text-danger">' + d.error_message + '</span>');

    // Build execution timeline
    const timeline = (d.timeline || []).map(ev => {
      let extra = '';
      if (ev.duration_ms) extra += '<span class="ap-trace-event-meta">' + fmtMs(ev.duration_ms) + '</span> ';
      if (ev.status) extra += statusBadge(ev.status) + ' ';
      if (ev.confidence != null) extra += '<span class="ap-trace-event-meta">' + Math.round((ev.confidence || 0) * 100) + '%</span> ';
      if (ev.accepted != null) extra += '<span class="ap-trace-event-meta">' + (ev.accepted === true ? "✓ Accepted" : ev.accepted === false ? "✗ Rejected" : "Pending") + '</span> ';
      if (ev.rationale) extra += '<div class="te-event-detail">' + ev.rationale + '</div>';
      if (ev.reasoning) extra += '<div class="te-event-detail">' + ev.reasoning + '</div>';
      if (ev.input_summary) extra += '<div class="te-event-io"><b>In:</b> ' + ev.input_summary + '</div>';
      if (ev.output_summary) extra += '<div class="te-event-io"><b>Out:</b> ' + ev.output_summary + '</div>';
      if (ev.error) extra += '<div class="te-event-detail text-danger">' + ev.error + '</div>';
      if (ev.reason) extra += '<div class="te-event-detail">' + ev.reason + '</div>';
      if (ev.deterministic != null) extra += '<span class="te-det-badge">' + (ev.deterministic ? "Deterministic" : "LLM") + '</span> ';

      return '<div class="ap-trace-event event-' + ev.event + '">' +
        '<div class="ap-trace-event-time">' + new Date(ev.time).toLocaleTimeString() + '</div>' +
        '<div class="ap-trace-event-label">' + ev.label + '</div>' +
        (extra ? '<div>' + extra + '</div>' : '') +
      '</div>';
    }).join("");

    // Span tree
    let spanHtml = '';
    const spans = d.span_tree || [];
    if (spans.length) {
      spanHtml = '<h6 class="fw-semibold mb-2 mt-3" style="font-size:.8rem"><i class="bi bi-bezier2 me-1"></i>Related Spans (same trace)</h6>' +
        '<div class="te-span-tree">' + spans.map(s =>
          '<div class="te-span-item" onclick="window.TraceExplorer.selectRun(' + s.id + ')">' +
            '<span class="fw-semibold">#' + s.id + '</span> ' +
            shortLabel(s.agent_type) + ' ' +
            statusBadge(s.status) + ' ' +
            '<span class="text-muted">' + fmtMs(s.duration_ms) + '</span>' +
          '</div>'
        ).join("") + '</div>';
    }

    el.innerHTML =
      '<h6 class="fw-bold mb-3"><i class="bi bi-diagram-3 me-1"></i>Run #' + d.id + '</h6>' +
      '<div class="ap-trace-meta">' + meta + '</div>' +
      (d.summarized_reasoning ? '<div class="mb-3 p-2 bg-light rounded" style="font-size:.78rem">' + d.summarized_reasoning + '</div>' : '') +
      '<h6 class="fw-semibold mb-2" style="font-size:.8rem"><i class="bi bi-clock-history me-1"></i>Execution Timeline</h6>' +
      '<div class="ap-trace-timeline">' + (timeline || '<div class="text-muted py-2" style="font-size:.8rem">No timeline events</div>') + '</div>' +
      spanHtml;

    // Also update left panel to highlight
    loadTraceRunList();
  }

  async function refreshTraceExplorer() {
    if (!CTX.isExtended) return;
    await loadTraceRunList();
  }


  // =====================================================================
  // INIT & PUBLIC API
  // =====================================================================

  function init() {
    // Only load if the sections exist
    if (CTX.isGovernance) refreshGovernance();
    if (CTX.isExtended) refreshTraceExplorer();

    // Search/filter event listeners for trace explorer
    const searchEl = document.getElementById("traceExplorerSearch");
    if (searchEl) {
      let debounce;
      searchEl.addEventListener("input", () => {
        clearTimeout(debounce);
        debounce = setTimeout(loadTraceRunList, 300);
      });
    }
    const statusEl = document.getElementById("traceExplorerStatusFilter");
    if (statusEl) statusEl.addEventListener("change", loadTraceRunList);
  }

  // Public namespaces
  window.TraceExplorer = {
    refresh: refreshTraceExplorer,
    selectRun: selectTraceRun,
  };

  window.GovWidgets = {
    refresh: refreshGovernance,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
