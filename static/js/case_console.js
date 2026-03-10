/**
 * Case Console — Investigation Dashboard JS
 * Handles DataTables init, section navigation, timeline filtering,
 * mismatch toggle, and smooth scroll.
 */
document.addEventListener('DOMContentLoaded', function () {

  // ---------------------------------------------------------------
  // 1. DataTables initialization for reconciliation table
  // ---------------------------------------------------------------
  if (typeof jQuery !== 'undefined' && jQuery.fn.DataTable) {
    jQuery('#reconTable').DataTable({
      paging: true,
      pageLength: 25,
      lengthMenu: [10, 25, 50, 100],
      ordering: true,
      searching: true,
      info: true,
      autoWidth: false,
      order: [[10, 'asc']], // Sort by Status column
      language: {
        search: '<i class="bi bi-search me-1"></i>',
        searchPlaceholder: 'Filter lines...',
        lengthMenu: 'Show _MENU_ lines',
        info: 'Showing _START_ to _END_ of _TOTAL_ lines',
        emptyTable: 'No line-level results available',
      },
      columnDefs: [
        { orderable: false, targets: [1] }, // Item description
      ],
      dom: '<"d-flex justify-content-between align-items-center px-3 py-2"lf>t<"d-flex justify-content-between align-items-center px-3 py-2"ip>',
    });
  }

  // ---------------------------------------------------------------
  // 2. Mismatch-only toggle
  // ---------------------------------------------------------------
  var toggleMismatch = document.getElementById('toggleMismatchOnly');
  if (toggleMismatch) {
    toggleMismatch.addEventListener('change', function () {
      var rows = document.querySelectorAll('#reconTable tbody tr[data-match-status]');
      rows.forEach(function (row) {
        if (toggleMismatch.checked) {
          row.style.display = row.dataset.matchStatus === 'MATCHED' ? 'none' : '';
        } else {
          row.style.display = '';
        }
      });
    });
  }

  // ---------------------------------------------------------------
  // 3. Smooth scroll section navigation
  // ---------------------------------------------------------------
  var navLinks = document.querySelectorAll('.case-section-nav a[href^="#"]');
  navLinks.forEach(function (link) {
    link.addEventListener('click', function (e) {
      e.preventDefault();
      var targetId = this.getAttribute('href').substring(1);
      var target = document.getElementById(targetId);
      if (target) {
        var offset = 170; // sticky header + nav height
        var top = target.getBoundingClientRect().top + window.pageYOffset - offset;
        window.scrollTo({ top: top, behavior: 'smooth' });
      }
      // Update active state
      navLinks.forEach(function (l) { l.classList.remove('active'); });
      this.classList.add('active');
    });
  });

  // Scroll spy: highlight active section nav item
  var sections = [];
  navLinks.forEach(function (link) {
    var id = link.getAttribute('href').substring(1);
    var el = document.getElementById(id);
    if (el) sections.push({ id: id, el: el, link: link });
  });

  function updateActiveSection() {
    var scrollPos = window.scrollY + 200;
    var current = null;
    sections.forEach(function (sec) {
      if (sec.el.offsetTop <= scrollPos) {
        current = sec;
      }
    });
    if (current) {
      navLinks.forEach(function (l) { l.classList.remove('active'); });
      current.link.classList.add('active');
    }
  }
  window.addEventListener('scroll', updateActiveSection);

  // ---------------------------------------------------------------
  // 4. Timeline category filter
  // ---------------------------------------------------------------
  var timelineFilters = document.querySelectorAll('.cc-timeline-filter');
  timelineFilters.forEach(function (btn) {
    btn.addEventListener('click', function () {
      var filter = this.dataset.filter;
      // Update active state
      timelineFilters.forEach(function (b) { b.classList.remove('active'); });
      this.classList.add('active');

      var items = document.querySelectorAll('.cc-timeline-item');
      items.forEach(function (item) {
        if (filter === 'all') {
          item.classList.remove('cc-hidden');
        } else {
          // Match on category - some categories share prefix
          var cat = item.dataset.category || '';
          if (filter === 'review') {
            item.classList.toggle('cc-hidden', !cat.startsWith('review'));
          } else {
            item.classList.toggle('cc-hidden', cat !== filter);
          }
        }
      });
    });
  });

  // ---------------------------------------------------------------
  // 5. Action button placeholders (Approve/Reject/Escalate)
  // ---------------------------------------------------------------
  var actionBtns = document.querySelectorAll('.cc-action-btn');
  actionBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      var action = this.dataset.action;
      if (!action) return;

      var actionLabels = {
        'approve': 'Approve this case',
        'reject': 'Reject this case',
        'escalate': 'Escalate this case',
        'accept-recommendation': 'Accept the agent recommendation',
        'override-recommendation': 'Override the agent recommendation',
        'request-info': 'Request vendor clarification',
      };

      var label = actionLabels[action] || action;
      // In production, this would be an API call
      if (confirm('Are you sure you want to: ' + label + '?')) {
        // Show feedback
        var alert = document.createElement('div');
        alert.className = 'alert alert-info alert-dismissible fade show mt-2';
        alert.innerHTML = '<i class="bi bi-info-circle me-1"></i>Action "<strong>' +
          label + '</strong>" would be submitted via API in production.' +
          '<button type="button" class="btn-close" data-bs-dismiss="alert"></button>';
        this.closest('.card, .d-grid, .d-flex').appendChild(alert);
      }
    });
  });

});
