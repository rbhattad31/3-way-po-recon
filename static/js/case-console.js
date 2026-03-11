/**
 * Case Console — Investigation Dashboard JS (v2)
 * Handles review actions, comments, timeline filtering,
 * Bootstrap tab deep-linking, and tooltip init.
 */
document.addEventListener('DOMContentLoaded', function () {

  // ---------------------------------------------------------------
  // 1. Bootstrap tooltips
  // ---------------------------------------------------------------
  var tooltipEls = document.querySelectorAll('[data-bs-toggle="tooltip"]');
  tooltipEls.forEach(function (el) {
    new bootstrap.Tooltip(el);
  });

  // ---------------------------------------------------------------
  // 2. Tab deep-linking via URL hash
  // ---------------------------------------------------------------
  var hash = window.location.hash;
  if (hash) {
    var tabBtn = document.querySelector('#caseTabNav button[data-bs-target="' + hash + '"]');
    if (tabBtn) {
      var tab = new bootstrap.Tab(tabBtn);
      tab.show();
    }
  }
  // Update hash when tab changes
  var tabBtns = document.querySelectorAll('#caseTabNav button[data-bs-toggle="pill"]');
  tabBtns.forEach(function (btn) {
    btn.addEventListener('shown.bs.tab', function (e) {
      var target = e.target.getAttribute('data-bs-target');
      if (target) {
        history.replaceState(null, '', target);
      }
    });
  });

  // ---------------------------------------------------------------
  // 3. Timeline category filter
  // ---------------------------------------------------------------
  var timelineFilters = document.querySelectorAll('.ap-timeline-filter');
  timelineFilters.forEach(function (btn) {
    btn.addEventListener('click', function () {
      var filter = this.dataset.filter;
      timelineFilters.forEach(function (b) { b.classList.remove('active'); });
      this.classList.add('active');

      var items = document.querySelectorAll('.ap-timeline-item');
      items.forEach(function (item) {
        var cat = (item.dataset.category || '').toLowerCase();
        if (filter === 'all') {
          item.style.display = '';
        } else {
          item.style.display = cat === filter ? '' : 'none';
        }
      });
    });
  });

  // ---------------------------------------------------------------
  // 4. Agent run accordion — auto-expand first failed
  // ---------------------------------------------------------------
  var failedBadge = document.querySelector('.accordion-item .badge.bg-danger');
  if (failedBadge) {
    var collapseEl = failedBadge.closest('.accordion-item').querySelector('.accordion-collapse');
    if (collapseEl) {
      var bsCollapse = new bootstrap.Collapse(collapseEl, { toggle: true });
    }
  }
});

// ---------------------------------------------------------------
// 5. Review actions (called from _review_action_panel.html)
// ---------------------------------------------------------------
function caseAction(action) {
  var labels = {
    'approve': 'Approve this case',
    'reject': 'Reject this case',
    'escalate': 'Escalate this case',
    'request_info': 'Request additional information',
  };
  var label = labels[action] || action;

  if (!confirm('Are you sure you want to: ' + label + '?')) return;

  // Get CSRF token
  var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
  if (!csrfToken) {
    csrfToken = document.querySelector('meta[name="csrf-token"]');
  }
  var token = csrfToken ? (csrfToken.value || csrfToken.content) : '';

  // Build the form data
  var form = document.createElement('form');
  form.method = 'POST';
  form.action = window.location.pathname + 'reprocess/';
  form.style.display = 'none';

  var csrfInput = document.createElement('input');
  csrfInput.type = 'hidden';
  csrfInput.name = 'csrfmiddlewaretoken';
  csrfInput.value = token;
  form.appendChild(csrfInput);

  var actionInput = document.createElement('input');
  actionInput.type = 'hidden';
  actionInput.name = 'action';
  actionInput.value = action;
  form.appendChild(actionInput);

  // Show feedback toast
  var toastHtml = '<div class="position-fixed bottom-0 end-0 p-3" style="z-index:1090">' +
    '<div class="toast show align-items-center text-bg-info border-0" role="alert">' +
    '<div class="d-flex"><div class="toast-body"><i class="bi bi-info-circle me-1"></i>Action "' +
    label + '" submitted.</div>' +
    '<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>' +
    '</div></div></div>';
  document.body.insertAdjacentHTML('beforeend', toastHtml);
}

// ---------------------------------------------------------------
// 6. Add comment (called from _review_action_panel.html)
// ---------------------------------------------------------------
function addComment() {
  var textarea = document.getElementById('caseComment');
  if (!textarea) return;
  var body = textarea.value.trim();
  if (!body) {
    textarea.classList.add('is-invalid');
    return;
  }
  textarea.classList.remove('is-invalid');

  // Show inline confirmation
  var container = textarea.closest('div');
  var feedback = document.createElement('div');
  feedback.className = 'alert alert-success alert-dismissible fade show mt-2 py-1 px-2';
  feedback.style.fontSize = 'var(--ap-font-size-xs)';
  feedback.innerHTML = '<i class="bi bi-check-circle me-1"></i>Comment would be saved via API in production.' +
    '<button type="button" class="btn-close btn-close-sm" data-bs-dismiss="alert"></button>';
  container.appendChild(feedback);
  textarea.value = '';
}
