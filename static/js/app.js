/* PO Reconciliation – Dashboard JS */
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

  var COLORS = {
    MATCHED: "#198754",
    PARTIAL_MATCH: "#ffc107",
    UNMATCHED: "#dc3545",
    REQUIRES_REVIEW: "#6c757d",
    ERROR: "#0d6efd",
  };

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
            backgroundColor: data.map(function (d) { return COLORS[d.match_status] || "#adb5bd"; }),
          }],
        },
        options: { responsive: true, plugins: { legend: { position: "bottom" } } },
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
            backgroundColor: "#fd7e14",
          }],
        },
        options: {
          responsive: true,
          indexAxis: "y",
          plugins: { legend: { display: false } },
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
            { label: "Invoices", data: data.map(function (d) { return d.invoices; }), borderColor: "#0d6efd", tension: 0.3 },
            { label: "Reconciled", data: data.map(function (d) { return d.reconciled; }), borderColor: "#198754", tension: 0.3 },
            { label: "Exceptions", data: data.map(function (d) { return d.exceptions; }), borderColor: "#dc3545", tension: 0.3 },
          ],
        },
        options: { responsive: true, plugins: { legend: { position: "bottom" } } },
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
            { label: "Total Runs", data: data.map(function (d) { return d.total_runs; }), backgroundColor: "#0d6efd" },
            { label: "Successes", data: data.map(function (d) { return d.success_count; }), backgroundColor: "#198754" },
          ],
        },
        options: { responsive: true, plugins: { legend: { position: "bottom" } } },
      });
    });
  }
});
