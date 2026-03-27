/* ═══════════════════════════════════════════════════════════════
   Extraction Control Center — JavaScript
   ═══════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  // Auto-dismiss success messages after 4 seconds
  document.querySelectorAll(".alert-success.alert-dismissible").forEach(function (el) {
    setTimeout(function () {
      var bsAlert = bootstrap.Alert.getOrCreateInstance(el);
      if (bsAlert) bsAlert.close();
    }, 4000);
  });

  // Copy-to-clipboard for JSON preview blocks
  document.querySelectorAll("[data-cc-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var target = document.getElementById(btn.getAttribute("data-cc-copy"));
      if (target) {
        navigator.clipboard.writeText(target.textContent).then(function () {
          var orig = btn.innerHTML;
          btn.innerHTML = '<i class="bi bi-check2"></i> Copied';
          setTimeout(function () { btn.innerHTML = orig; }, 1500);
        });
      }
    });
  });

  // Confirm-action forms (data-cc-confirm attribute)
  document.querySelectorAll("[data-cc-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!confirm(form.getAttribute("data-cc-confirm"))) {
        e.preventDefault();
      }
    });
  });
})();
