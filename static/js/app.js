/* AP Operations — Dashboard Charts & Global Utilities */
document.addEventListener("DOMContentLoaded", function () {
  "use strict";

  /* ------------------------------------------------------------------ */
  /* Helpers                                                             */
  /* ------------------------------------------------------------------ */
  function fetchJSON(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    });
  }

  /* Design-system-aligned colors */
  var COLORS = {
    MATCHED: "#10b981",
    PARTIAL_MATCH: "#f59e0b",
    UNMATCHED: "#ef4444",
    REQUIRES_REVIEW: "#64748b",
    ERROR: "#3b82f6",
  };

  var CHART_DEFAULTS = {
    font: { family: "'Inter', system-ui, sans-serif", size: 11 },
  };

  /* Apply global Chart.js defaults */
  if (typeof Chart !== "undefined") {
    Chart.defaults.font.family = CHART_DEFAULTS.font.family;
    Chart.defaults.font.size = CHART_DEFAULTS.font.size;
    Chart.defaults.color = "#64748b";
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.legend.labels.pointStyle = "circle";
    Chart.defaults.plugins.legend.labels.padding = 16;
  }

  /* ------------------------------------------------------------------ */
  /* Match-status donut                                                  */
  /* ------------------------------------------------------------------ */
  var donutEl = document.getElementById("matchStatusChart");
  if (donutEl) {
    fetchJSON("/api/v1/dashboard/match-status/").then(function (data) {
      new Chart(donutEl, {
        type: "doughnut",
        data: {
          labels: data.map(function (d) { return d.match_status; }),
          datasets: [{
            data: data.map(function (d) { return d.count; }),
            backgroundColor: data.map(function (d) { return COLORS[d.match_status] || "#cbd5e1"; }),
            borderWidth: 0,
            hoverOffset: 4,
          }],
        },
        options: {
          responsive: true,
          cutout: "65%",
          plugins: { legend: { position: "bottom" } },
        },
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* Exception-type bar                                                  */
  /* ------------------------------------------------------------------ */
  var excEl = document.getElementById("exceptionChart");
  if (excEl) {
    fetchJSON("/api/v1/dashboard/exceptions/").then(function (data) {
      new Chart(excEl, {
        type: "bar",
        data: {
          labels: data.map(function (d) { return d.exception_type; }),
          datasets: [{
            label: "Open Exceptions",
            data: data.map(function (d) { return d.count; }),
            backgroundColor: "#f97316",
            borderRadius: 4,
            barThickness: 18,
          }],
        },
        options: {
          responsive: true,
          indexAxis: "y",
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { display: false }, ticks: { precision: 0 } },
            y: { grid: { display: false } },
          },
        },
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* Daily volume line                                                   */
  /* ------------------------------------------------------------------ */
  var volEl = document.getElementById("dailyVolumeChart");
  if (volEl) {
    fetchJSON("/api/v1/dashboard/daily-volume/?days=30").then(function (data) {
      new Chart(volEl, {
        type: "line",
        data: {
          labels: data.map(function (d) { return d.date; }),
          datasets: [
            { label: "Invoices", data: data.map(function (d) { return d.invoices; }), borderColor: "#3b82f6", backgroundColor: "rgba(59,130,246,.08)", fill: true, tension: 0.4, pointRadius: 0, borderWidth: 2 },
            { label: "Reconciled", data: data.map(function (d) { return d.reconciled; }), borderColor: "#10b981", backgroundColor: "rgba(16,185,129,.08)", fill: true, tension: 0.4, pointRadius: 0, borderWidth: 2 },
            { label: "Exceptions", data: data.map(function (d) { return d.exceptions; }), borderColor: "#ef4444", backgroundColor: "rgba(239,68,68,.08)", fill: true, tension: 0.4, pointRadius: 0, borderWidth: 2 },
          ],
        },
        options: {
          responsive: true,
          interaction: { mode: "index", intersect: false },
          plugins: { legend: { position: "bottom" } },
          scales: {
            x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } },
            y: { grid: { color: "rgba(0,0,0,.04)" }, ticks: { precision: 0 } },
          },
        },
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* Agent-performance bar                                               */
  /* ------------------------------------------------------------------ */
  var agentEl = document.getElementById("agentPerfChart");
  if (agentEl) {
    fetchJSON("/api/v1/dashboard/agent-performance/").then(function (data) {
      new Chart(agentEl, {
        type: "bar",
        data: {
          labels: data.map(function (d) { return d.agent_type; }),
          datasets: [
            { label: "Total Runs", data: data.map(function (d) { return d.total_runs; }), backgroundColor: "#3b82f6", borderRadius: 4, barThickness: 16 },
            { label: "Successes", data: data.map(function (d) { return d.success_count; }), backgroundColor: "#10b981", borderRadius: 4, barThickness: 16 },
          ],
        },
        options: {
          responsive: true,
          plugins: { legend: { position: "bottom" } },
          scales: {
            x: { grid: { display: false } },
            y: { grid: { color: "rgba(0,0,0,.04)" }, ticks: { precision: 0 } },
          },
        },
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* Global: Bootstrap tooltip init on every page                        */
  /* ------------------------------------------------------------------ */
  var tooltipEls = document.querySelectorAll('[data-bs-toggle="tooltip"]');
  tooltipEls.forEach(function (el) {
    new bootstrap.Tooltip(el);
  });
});
