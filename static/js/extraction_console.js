/* ============================================================
   Extraction Review Console — JavaScript
   ============================================================ */
document.addEventListener('DOMContentLoaded', function () {

  // ── Tab / pill helpers ──
  const tabLinks = document.querySelectorAll('[data-bs-toggle="pill"]');
  tabLinks.forEach(function (link) {
    link.addEventListener('shown.bs.tab', function () {
      const tabId = link.getAttribute('data-bs-target');
      sessionStorage.setItem('exc-active-tab', tabId);
    });
  });

  // Restore last active tab
  const savedTab = sessionStorage.getItem('exc-active-tab');
  if (savedTab) {
    const savedLink = document.querySelector('[data-bs-target="' + savedTab + '"]');
    if (savedLink) {
      var tab = new bootstrap.Tab(savedLink);
      tab.show();
    }
  }

  // ── Field filter buttons ──
  document.querySelectorAll('[data-filter]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('[data-filter]').forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      applyFieldFilter(btn.dataset.filter);
    });
  });

  function applyFieldFilter(filter) {
    var rows = document.querySelectorAll('.exc-field-row, .exc-line-row');
    rows.forEach(function (row) {
      if (filter === 'all') {
        row.classList.remove('d-none');
      } else if (filter === 'flagged') {
        var hasFlagOrWarning = row.classList.contains('exc-low-confidence') ||
                               row.classList.contains('exc-med-confidence') ||
                               row.classList.contains('exc-flagged');
        row.classList.toggle('d-none', !hasFlagOrWarning);
      } else if (filter === 'low-confidence') {
        row.classList.toggle('d-none', !row.classList.contains('exc-low-confidence'));
      }
    });
  }

  // ── Edit mode toggle ──
  var editToggle = document.getElementById('toggleEditMode');
  if (editToggle) {
    editToggle.addEventListener('change', function () {
      var rows = document.querySelectorAll('.exc-field-row');
      rows.forEach(function (row) {
        row.classList.toggle('exc-editing', editToggle.checked);
      });
    });
  }

  // Track field edits
  document.querySelectorAll('.exc-field-edit').forEach(function (input) {
    input.addEventListener('input', function () {
      var original = input.dataset.original || '';
      input.classList.toggle('exc-modified', input.value !== original);
    });
  });

  // ── Go-to-field navigation ──
  document.querySelectorAll('.exc-goto-field').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var field = btn.dataset.field;
      var targetTab = btn.dataset.tab || 'extracted-data';

      // Switch to target tab
      var tabTrigger = document.querySelector('[data-bs-target="#tab-' + targetTab + '"]');
      if (tabTrigger) {
        var tab = new bootstrap.Tab(tabTrigger);
        tab.show();
      }

      // Highlight the field row
      setTimeout(function () {
        var row = document.querySelector('[data-field-key="' + field + '"]');
        if (row) {
          row.scrollIntoView({ behavior: 'smooth', block: 'center' });
          row.classList.add('exc-active-highlight');
          setTimeout(function () { row.classList.remove('exc-active-highlight'); }, 2000);
        }
      }, 200);
    });
  });

  // ── Evidence field → viewer highlight ──
  document.querySelectorAll('.exc-evidence-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var field = btn.dataset.field;

      // Switch to evidence tab
      var evidenceTab = document.querySelector('[data-bs-target="#tab-evidence"]');
      if (evidenceTab) {
        var tab = new bootstrap.Tab(evidenceTab);
        tab.show();
      }

      // Highlight matching evidence card
      setTimeout(function () {
        var card = document.querySelector('[data-evidence-field="' + field + '"]');
        if (card) {
          card.scrollIntoView({ behavior: 'smooth', block: 'center' });
          card.classList.add('border-primary');
          setTimeout(function () { card.classList.remove('border-primary'); }, 2000);
        }
      }, 200);
    });
  });

  // ── Evidence filter by field ──
  var evidenceFilter = document.getElementById('evidenceFieldFilter');
  if (evidenceFilter) {
    evidenceFilter.addEventListener('change', function () {
      var selectedField = evidenceFilter.value;
      document.querySelectorAll('.exc-evidence-card').forEach(function (card) {
        if (!selectedField || card.dataset.evidenceField === selectedField) {
          card.classList.remove('d-none');
        } else {
          card.classList.add('d-none');
        }
      });
    });
  }

  // ── Line item expand/collapse ──
  document.querySelectorAll('.exc-line-expand').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var lineIdx = btn.dataset.line;
      var detail = document.getElementById('line-detail-' + lineIdx);
      if (detail) {
        detail.classList.toggle('d-none');
        var icon = btn.querySelector('i');
        icon.classList.toggle('bi-chevron-down');
        icon.classList.toggle('bi-chevron-up');
      }
    });
  });

  // ── Line item flag ──
  document.querySelectorAll('.exc-line-flag').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var row = btn.closest('.exc-line-row');
      if (row) {
        row.classList.toggle('exc-flagged');
        var icon = btn.querySelector('i');
        icon.classList.toggle('bi-flag');
        icon.classList.toggle('bi-flag-fill');
      }
    });
  });

  // ── Highlight in document viewer ──
  document.querySelectorAll('.exc-highlight-evidence').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var page = parseInt(btn.dataset.page, 10);
      var bbox = btn.dataset.bbox;
      navigateToPage(page);
      if (bbox) {
        highlightRegion(bbox);
      }
    });
  });

  document.querySelectorAll('.exc-goto-page').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var page = parseInt(btn.dataset.page, 10);
      navigateToPage(page);
    });
  });

  function navigateToPage(pageNum) {
    var pageDisplay = document.getElementById('currentPage');
    if (pageDisplay) {
      pageDisplay.textContent = pageNum;
    }
    // Placeholder: real viewer would render the page via PDF.js
  }

  function highlightRegion(bboxStr) {
    var layer = document.getElementById('highlightLayer');
    if (!layer) return;

    // Clear existing highlights
    layer.innerHTML = '';

    // bbox expected as "x1,y1,x2,y2" normalized 0-1
    try {
      var parts = bboxStr.split(',').map(Number);
      if (parts.length === 4) {
        var el = document.createElement('div');
        el.className = 'exc-field-highlight';
        el.style.left = (parts[0] * 100) + '%';
        el.style.top = (parts[1] * 100) + '%';
        el.style.width = ((parts[2] - parts[0]) * 100) + '%';
        el.style.height = ((parts[3] - parts[1]) * 100) + '%';
        layer.appendChild(el);

        // Auto-clear after 3 seconds
        setTimeout(function () { layer.innerHTML = ''; }, 3000);
      }
    } catch (e) {
      // Ignore malformed bbox
    }
  }

  // ── Approve modal ──
  var confirmApproveBtn = document.getElementById('confirmApproveBtn');
  var confirmReviewedCheck = document.getElementById('confirmReviewedCheck');
  if (confirmReviewedCheck && confirmApproveBtn) {
    confirmReviewedCheck.addEventListener('change', function () {
      confirmApproveBtn.disabled = !confirmReviewedCheck.checked;
    });
  }

  if (confirmApproveBtn) {
    confirmApproveBtn.addEventListener('click', function () {
      var form = document.getElementById('approveForm');
      if (form) {
        var formData = new FormData(form);
        submitAction('/api/v1/extraction/approve/', formData, 'Approved successfully');
      }
    });
  }

  // ── Reprocess modal ──
  var confirmReprocessBtn = document.getElementById('confirmReprocessBtn');
  if (confirmReprocessBtn) {
    confirmReprocessBtn.addEventListener('click', function () {
      var form = document.getElementById('reprocessForm');
      if (form) {
        var formData = new FormData(form);
        submitAction('/api/v1/extraction/reprocess/', formData, 'Reprocess started');
      }
    });
  }

  // ── Escalate modal ──
  var confirmEscalateBtn = document.getElementById('confirmEscalateBtn');
  if (confirmEscalateBtn) {
    confirmEscalateBtn.addEventListener('click', function () {
      var form = document.getElementById('escalateForm');
      if (form) {
        var formData = new FormData(form);
        submitAction('/api/v1/extraction/escalate/', formData, 'Escalated successfully');
      }
    });
  }

  // ── Comment modal ──
  var confirmCommentBtn = document.getElementById('confirmCommentBtn');
  if (confirmCommentBtn) {
    confirmCommentBtn.addEventListener('click', function () {
      var form = document.getElementById('commentForm');
      if (form) {
        var formData = new FormData(form);
        submitAction('/api/v1/extraction/comment/', formData, 'Comment added');
      }
    });
  }

  // ── AJAX helper ──
  function submitAction(url, formData, successMsg) {
    fetch(url, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCsrfToken()
      },
      body: formData
    })
    .then(function (resp) {
      if (resp.ok) {
        showToast(successMsg, 'success');
        // Close any open modal
        var openModal = document.querySelector('.modal.show');
        if (openModal) {
          var modal = bootstrap.Modal.getInstance(openModal);
          if (modal) modal.hide();
        }
        // Reload after short delay
        setTimeout(function () { window.location.reload(); }, 800);
      } else {
        resp.json().then(function (data) {
          showToast(data.error || 'Action failed', 'danger');
        }).catch(function () {
          showToast('Action failed', 'danger');
        });
      }
    })
    .catch(function () {
      showToast('Network error', 'danger');
    });
  }

  function getCsrfToken() {
    var input = document.querySelector('[name="csrfmiddlewaretoken"]');
    if (input) return input.value;
    var cookie = document.cookie.split(';').find(function (c) { return c.trim().startsWith('csrftoken='); });
    return cookie ? cookie.split('=')[1] : '';
  }

  function showToast(message, type) {
    var container = document.getElementById('toastContainer');
    if (!container) {
      container = document.createElement('div');
      container.id = 'toastContainer';
      container.className = 'toast-container position-fixed top-0 end-0 p-3';
      container.style.zIndex = '1090';
      document.body.appendChild(container);
    }

    var toast = document.createElement('div');
    toast.className = 'toast align-items-center text-bg-' + type + ' border-0';
    toast.setAttribute('role', 'alert');
    toast.innerHTML = '<div class="d-flex"><div class="toast-body">' + message +
                      '</div><button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>';
    container.appendChild(toast);
    var bsToast = new bootstrap.Toast(toast, { delay: 3000 });
    bsToast.show();
    toast.addEventListener('hidden.bs.toast', function () { toast.remove(); });
  }

  // ── Pipeline stage click ──
  document.querySelectorAll('.exc-stage-pill').forEach(function (pill) {
    pill.addEventListener('click', function () {
      var stage = pill.dataset.stage;
      // Switch to audit trail tab and highlight stage
      var auditTab = document.querySelector('[data-bs-target="#tab-audit-trail"]');
      if (auditTab) {
        var tab = new bootstrap.Tab(auditTab);
        tab.show();
      }
    });
  });

  // ── Document viewer zoom controls ──
  var currentZoom = 100;
  var zoomIn = document.getElementById('zoomIn');
  var zoomOut = document.getElementById('zoomOut');
  var zoomFit = document.getElementById('zoomFit');
  var zoomLevel = document.getElementById('zoomLevel');

  if (zoomIn) {
    zoomIn.addEventListener('click', function () {
      currentZoom = Math.min(currentZoom + 25, 300);
      applyZoom();
    });
  }

  if (zoomOut) {
    zoomOut.addEventListener('click', function () {
      currentZoom = Math.max(currentZoom - 25, 50);
      applyZoom();
    });
  }

  if (zoomFit) {
    zoomFit.addEventListener('click', function () {
      currentZoom = 100;
      applyZoom();
    });
  }

  function applyZoom() {
    if (zoomLevel) zoomLevel.textContent = currentZoom + '%';
    var canvas = document.getElementById('pdfCanvas');
    if (canvas) {
      canvas.style.transform = 'scale(' + (currentZoom / 100) + ')';
      canvas.style.transformOrigin = 'top left';
    }
  }

});
