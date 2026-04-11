/**
 * AP Copilot Case Workspace -- Client-side controller
 * Handles: chat tab, tab data loading, action buttons, supervisor agent trigger
 */
(function () {
  'use strict';

  // ── Configuration ──
  var cfgEl = document.getElementById('caseWorkspaceConfig');
  if (!cfgEl) return;
  var CFG = JSON.parse(cfgEl.textContent);

  // ── State ──
  var sessionId = CFG.sessionId;
  var isSending = false;
  var supervisorRunning = false;

  // ── DOM ──
  var chatMessages = document.getElementById('chatMessages');
  var chatInput    = document.getElementById('chatInput');
  var chatForm     = document.getElementById('chatForm');
  var chatSend     = document.getElementById('chatSend');
  var chatWelcome  = document.getElementById('chatWelcome');

  // Upload (attach button in chat input bar)
  var btnAttachFile   = document.getElementById('btnAttachFile');
  var invoiceFileInput = document.getElementById('invoiceFileInput');

  // ── Init ──
  function init() {
    // Chat form
    if (chatForm) chatForm.addEventListener('submit', onChatSubmit);
    if (chatInput) {
      chatInput.addEventListener('input', onInputChange);
      chatInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          if (chatInput.value.trim()) chatForm.dispatchEvent(new Event('submit'));
        }
      });
    }

    // Lazy-load tab data when tab is shown
    var tabEls = document.querySelectorAll('[data-bs-toggle="tab"]');
    tabEls.forEach(function (tab) {
      tab.addEventListener('shown.bs.tab', onTabShown);
    });

    // Load overview data immediately (for when user clicks to those tabs)
    loadLineItems();

    // Upload attach button
    if (btnAttachFile) btnAttachFile.addEventListener('click', function () { invoiceFileInput.click(); });
    if (invoiceFileInput) invoiceFileInput.addEventListener('change', onFileSelected);

    // Auto-focus chat input
    if (chatInput) chatInput.focus();

    // Scroll to bottom if there are existing messages
    scrollChatToBottom();

    // Auto-run supervisor agent if flagged (e.g. after invoice upload)
    if (CFG.autoRunSupervisor) {
      if (CFG.invoiceId) {
        // Invoice already available -- run immediately
        setTimeout(function () { runSupervisor('Analyze this newly uploaded invoice'); }, 500);
      } else {
        // Invoice not linked yet (extraction still running) -- poll until ready
        waitForInvoiceThenRunSupervisor();
      }
    }
  }

  // ==================================================================
  // CHAT: Input & Submit
  // ==================================================================
  function onInputChange() {
    chatSend.disabled = !chatInput.value.trim();
    chatInput.style.height = 'auto';
    chatInput.style.height = Math.min(chatInput.scrollHeight, 120) + 'px';
  }

  async function onChatSubmit(e) {
    e.preventDefault();
    var text = chatInput.value.trim();
    if (!text || isSending) return;

    var sid = await ensureSession();
    if (!sid) return;

    // Hide welcome
    if (chatWelcome) chatWelcome.style.display = 'none';

    // Render user bubble
    appendChatMessage('user', text);
    chatInput.value = '';
    chatInput.style.height = 'auto';
    chatSend.disabled = true;
    isSending = true;

    // Thinking
    var thinkingEl = appendThinking();

    try {
      var res = await apiFetch(CFG.urls.chat, {
        method: 'POST',
        body: { session_id: sid, message: text, case_id: CFG.caseId },
      });
      removeThinking(thinkingEl);
      if (res && res.response) {
        appendRichResponse(res.response);
      } else {
        appendChatMessage('system', 'No response received.');
      }
    } catch (err) {
      removeThinking(thinkingEl);
      appendChatMessage('system', 'Error: ' + ((err && err.message) || 'Unknown'));
    } finally {
      isSending = false;
    }
  }

  // Quick send from chip buttons
  window.chatSendQuick = function (text) {
    if (chatInput) {
      chatInput.value = text;
      chatForm.dispatchEvent(new Event('submit'));
    }
    // Switch to chat tab if not active
    var chatTab = document.getElementById('tab-chat');
    if (chatTab && !chatTab.classList.contains('active')) {
      new bootstrap.Tab(chatTab).show();
    }
  };

  // ==================================================================
  // SESSION MANAGEMENT
  // ==================================================================
  async function ensureSession() {
    if (sessionId) return sessionId;
    try {
      var res = await apiFetch(CFG.urls.sessionStart, {
        method: 'POST',
        body: { case_id: CFG.caseId },
      });
      if (res && res.id) {
        sessionId = res.id;
        return sessionId;
      }
    } catch (err) {
      console.error('[case-ws] ensureSession failed', err);
    }
    return null;
  }

  // ==================================================================
  // CHAT: Message rendering
  // ==================================================================
  function appendChatMessage(type, text) {
    var div = document.createElement('div');
    div.className = 'chat-msg';
    var bubble = document.createElement('div');
    if (type === 'user') {
      bubble.className = 'chat-msg-bubble chat-msg-user-bubble';
    } else if (type === 'assistant') {
      bubble.className = 'chat-msg-bubble chat-msg-ai-bubble';
    } else {
      bubble.className = 'chat-msg-bubble chat-msg-ai-bubble';
      bubble.style.fontStyle = 'italic';
      bubble.style.color = '#94a3b8';
    }
    bubble.textContent = text;
    div.appendChild(bubble);
    chatMessages.appendChild(div);
    scrollChatToBottom();
  }

  /**
   * Render a rich summary card from structured supervisor output.
   */
  function appendSummaryCard(s, evtConfidence, evtRec) {
    if (chatWelcome) chatWelcome.style.display = 'none';

    var confidence = s.confidence || (evtConfidence ? Math.round(evtConfidence * 100) : 0);
    var rec = s.recommendation || (evtRec || '').replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, function (c) { return c.toUpperCase(); });
    var severity = s.recommendation_severity || 'warning';
    var findings = s.findings || [];
    var issues = s.issues || [];
    var toolsOk = s.tools_ok || 0;
    var toolsFailed = s.tools_failed || 0;
    var analysisText = s.analysis_text || '';

    // Severity -> Bootstrap color map
    var sevColor = { success: '#16a34a', warning: '#d97706', danger: '#dc2626' };
    var sevBg = { success: '#f0fdf4', warning: '#fffbeb', danger: '#fef2f2' };
    var sevBorder = { success: '#bbf7d0', warning: '#fde68a', danger: '#fecaca' };
    var sevIcon = { success: 'check-circle-fill', warning: 'exclamation-triangle-fill', danger: 'x-circle-fill' };

    var color = sevColor[severity] || sevColor.warning;
    var bg = sevBg[severity] || sevBg.warning;
    var border = sevBorder[severity] || sevBorder.warning;
    var icon = sevIcon[severity] || sevIcon.warning;

    // Confidence ring color
    var confColor = '#dc2626';
    if (confidence >= 80) confColor = '#16a34a';
    else if (confidence >= 50) confColor = '#d97706';

    var html = '<div class="sv-summary-card" style="border-color:' + border + '">';

    // Header: recommendation + confidence
    html += '<div class="sv-summary-header" style="background:' + bg + ';border-color:' + border + '">';
    html += '<div class="sv-summary-rec">';
    html += '<i class="bi bi-' + icon + '" style="color:' + color + ';font-size:1.1rem"></i>';
    html += '<div><div class="sv-summary-rec-label">Recommendation</div>';
    html += '<div class="sv-summary-rec-value" style="color:' + color + '">' + esc(rec) + '</div></div>';
    html += '</div>';
    html += '<div class="sv-summary-confidence">';
    html += '<svg class="sv-conf-ring" viewBox="0 0 36 36">';
    html += '<path class="sv-conf-ring-bg" d="M18 2.0845a15.9155 15.9155 0 0 1 0 31.831a15.9155 15.9155 0 0 1 0-31.831" />';
    html += '<path class="sv-conf-ring-fg" stroke="' + confColor + '" stroke-dasharray="' + confidence + ', 100" d="M18 2.0845a15.9155 15.9155 0 0 1 0 31.831a15.9155 15.9155 0 0 1 0-31.831" />';
    html += '</svg>';
    html += '<span class="sv-conf-pct" style="color:' + confColor + '">' + confidence + '%</span>';
    html += '</div>';
    html += '</div>';

    // Findings
    if (findings.length) {
      html += '<div class="sv-summary-section">';
      html += '<div class="sv-summary-section-title"><i class="bi bi-search"></i> Findings</div>';
      html += '<div class="sv-summary-findings">';
      for (var i = 0; i < findings.length; i++) {
        var f = findings[i];
        var fSev = f.severity || '';
        var fBadge = '';
        if (fSev === 'success') fBadge = ' sv-finding-success';
        else if (fSev === 'danger') fBadge = ' sv-finding-danger';
        html += '<div class="sv-finding' + fBadge + '">';
        html += '<span class="sv-finding-label">' + esc(f.label) + '</span>';
        html += '<span class="sv-finding-value">' + esc(f.value) + '</span>';
        html += '</div>';
      }
      html += '</div></div>';
    }

    // Issues
    if (issues.length) {
      html += '<div class="sv-summary-section">';
      html += '<div class="sv-summary-section-title sv-issues-title"><i class="bi bi-exclamation-circle"></i> Issues</div>';
      html += '<ul class="sv-summary-issues">';
      for (var j = 0; j < issues.length; j++) {
        html += '<li>' + esc(issues[j]) + '</li>';
      }
      html += '</ul></div>';
    }

    // Footer: tool stats
    html += '<div class="sv-summary-footer">';
    html += '<span class="sv-summary-tools"><i class="bi bi-gear"></i> ' + (toolsOk + toolsFailed) + ' tools executed';
    if (toolsFailed > 0) html += ', <span class="text-danger">' + toolsFailed + ' failed</span>';
    html += '</span></div>';

    // Analysis text
    if (analysisText && analysisText.length > 10) {
      html += '<div class="sv-summary-analysis">' + esc(analysisText) + '</div>';
    }

    html += '</div>';

    var row = document.createElement('div');
    row.className = 'copilot-msg copilot-msg-ai';

    var avatar = document.createElement('div');
    avatar.className = 'copilot-msg-avatar copilot-msg-avatar-ai';
    avatar.innerHTML = '<i class="bi bi-robot"></i>';

    var body = document.createElement('div');
    body.className = 'copilot-msg-body';
    body.innerHTML = html;

    row.appendChild(avatar);
    row.appendChild(body);
    chatMessages.appendChild(row);
    scrollChatToBottom();
  }

  /**
   * Convert a minimal markdown subset to HTML (bold, lists, headers, line breaks).
   */
  function miniMarkdown(text) {
    if (!text) return '';
    var lines = String(text).split('\n');
    var html = '';
    var inList = false;
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      // Blank line
      if (!line.trim()) {
        if (inList) { html += '</ul>'; inList = false; }
        continue;
      }
      // Bullet list
      if (/^\s*[-*]\s+/.test(line)) {
        if (!inList) { html += '<ul class="chat-md-list">'; inList = true; }
        html += '<li>' + mdInline(line.replace(/^\s*[-*]\s+/, '')) + '</li>';
        continue;
      }
      if (inList) { html += '</ul>'; inList = false; }
      // Headers
      if (/^###\s+/.test(line)) { html += '<div class="chat-md-h3">' + mdInline(line.replace(/^###\s+/, '')) + '</div>'; continue; }
      if (/^##\s+/.test(line)) { html += '<div class="chat-md-h2">' + mdInline(line.replace(/^##\s+/, '')) + '</div>'; continue; }
      // Regular paragraph
      html += '<div class="chat-md-p">' + mdInline(line) + '</div>';
    }
    if (inList) html += '</ul>';
    return html;
  }
  function mdInline(text) {
    // Escape HTML first
    var s = esc(text);
    // Bold: **text**
    s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Inline code: `text`
    s = s.replace(/`(.+?)`/g, '<code>$1</code>');
    return s;
  }

  /**
   * Render a structured copilot response (summary + evidence + follow-up prompts).
   */
  function appendRichResponse(response) {
    if (chatWelcome) chatWelcome.style.display = 'none';

    // Handle plain string responses
    if (typeof response === 'string') {
      appendChatMessage('assistant', response);
      return;
    }

    var summary = response.summary || response.answer || response.text || '';
    var evidence = response.evidence || [];
    var followUps = response.follow_up_prompts || [];

    // If no structured data, fall back to plain text
    if (!summary && !evidence.length) {
      appendChatMessage('assistant', JSON.stringify(response));
      return;
    }

    var row = document.createElement('div');
    row.className = 'copilot-msg copilot-msg-ai';

    var avatar = document.createElement('div');
    avatar.className = 'copilot-msg-avatar copilot-msg-avatar-ai';
    avatar.innerHTML = '<i class="bi bi-robot"></i>';

    var body = document.createElement('div');
    body.className = 'copilot-msg-body';

    var html = '<div class="chat-rich-response">';

    // Summary with markdown rendering
    if (summary) {
      html += '<div class="chat-rich-summary">' + miniMarkdown(summary) + '</div>';
    }

    // Evidence cards
    if (evidence.length) {
      html += '<div class="chat-evidence-grid">';
      for (var i = 0; i < evidence.length; i++) {
        var ev = evidence[i];
        var evType = ev.type || 'info';
        var evLabel = ev.label || evType;
        var evData = ev.data || {};

        // Icon + color per type
        var evIcon = 'info-circle';
        var evColor = '#64748b';
        if (evType === 'invoice') { evIcon = 'receipt'; evColor = '#2563eb'; }
        else if (evType === 'exception') { evIcon = 'exclamation-triangle-fill'; evColor = '#dc2626'; }
        else if (evType === 'decision') { evIcon = 'signpost-split'; evColor = '#7c3aed'; }
        else if (evType === 'match') { evIcon = 'check2-circle'; evColor = '#16a34a'; }
        else if (evType === 'vendor') { evIcon = 'building'; evColor = '#0891b2'; }
        else if (evType === 'po') { evIcon = 'file-earmark-text'; evColor = '#d97706'; }

        html += '<div class="chat-evidence-card" style="border-left-color:' + evColor + '">';
        html += '<div class="chat-ev-header">';
        html += '<i class="bi bi-' + evIcon + '" style="color:' + evColor + '"></i>';
        html += '<span class="chat-ev-label">' + esc(evLabel) + '</span>';

        // Severity badge for exceptions
        if (evData.severity) {
          var sevCls = evData.severity === 'HIGH' ? 'danger' : (evData.severity === 'MEDIUM' ? 'warning' : 'secondary');
          html += '<span class="badge bg-' + sevCls + ' chat-ev-badge">' + esc(evData.severity) + '</span>';
        }
        html += '</div>';

        // Data fields
        html += '<div class="chat-ev-body">';
        var dataKeys = Object.keys(evData);
        for (var j = 0; j < dataKeys.length; j++) {
          var k = dataKeys[j];
          if (k === 'severity') continue; // already shown as badge
          var v = evData[k];
          if (v === null || v === undefined || v === '') continue;
          var displayVal = v;
          if (typeof v === 'number' && k.indexOf('confidence') >= 0) {
            displayVal = Math.round(v * 100) + '%';
          }
          var kLabel = k.replace(/_/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); });
          html += '<div class="chat-ev-field">';
          html += '<span class="chat-ev-key">' + esc(kLabel) + '</span>';
          html += '<span class="chat-ev-val">' + esc(String(displayVal)) + '</span>';
          html += '</div>';
        }
        html += '</div></div>';
      }
      html += '</div>';
    }

    // Follow-up prompt chips
    if (followUps.length) {
      html += '<div class="chat-followups">';
      for (var f = 0; f < followUps.length; f++) {
        html += '<button class="chat-followup-chip" onclick="chatSendQuick(\'' + esc(followUps[f]).replace(/'/g, "\\'") + '\')">'
          + '<i class="bi bi-arrow-return-right"></i> ' + esc(followUps[f]) + '</button>';
      }
      html += '</div>';
    }

    html += '</div>';

    body.innerHTML = html;
    row.appendChild(avatar);
    row.appendChild(body);
    chatMessages.appendChild(row);
    scrollChatToBottom();
  }

  function appendThinking() {
    var div = document.createElement('div');
    div.className = 'chat-thinking';
    div.innerHTML = '<div class="chat-thinking-dots"><span></span><span></span><span></span></div> <span>Thinking...</span>';
    chatMessages.appendChild(div);
    scrollChatToBottom();
    return div;
  }

  function removeThinking(el) {
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  function scrollChatToBottom() {
    if (chatMessages) chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  // ==================================================================
  // TAB: Lazy loading
  // ==================================================================
  var loadedTabs = {};

  function onTabShown(e) {
    var target = e.target.getAttribute('data-bs-target');
    if (!target) return;

    if (target === '#panel-timeline' && !loadedTabs.timeline) {
      loadTimeline();
      loadedTabs.timeline = true;
    }
    if (target === '#panel-governance' && !loadedTabs.governance) {
      loadGovernance();
      loadedTabs.governance = true;
    }
    if (target === '#panel-matching' && !loadedTabs.matching) {
      loadMatchingData();
      loadedTabs.matching = true;
    }
    if (target === '#panel-po-grn' && !loadedTabs.pogrn) {
      loadPOGRNData();
      loadedTabs.pogrn = true;
    }
  }

  // ==================================================================
  // DATA LOADERS
  // ==================================================================

  // Line items for invoice tab
  async function loadLineItems() {
    if (!CFG.invoiceId) return;
    try {
      var data = await apiFetch(CFG.urls.caseEvidence, { method: 'GET' });
      if (data && data.evidence) {
        populateInvoiceLines(data.evidence);
        populatePOLines(data.evidence);
        populateGRNLines(data.evidence);
      }
    } catch (err) {
      console.error('[case-ws] loadLineItems failed', err);
    }
  }

  function populateInvoiceLines(evidence) {
    var tbody = document.getElementById('invoiceLineItems');
    if (!tbody) return;
    var lines = [];
    evidence.forEach(function(e) {
      if (e.type === 'invoice' && e.details && e.details.line_items) {
        lines = e.details.line_items;
      }
    });
    if (!lines.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-3">No line items found.</td></tr>';
      return;
    }
    tbody.innerHTML = lines.map(function(li, i) {
      return '<tr>'
        + '<td>' + (li.line_number || (i + 1)) + '</td>'
        + '<td>' + esc(li.description || '-') + '</td>'
        + '<td>' + esc(li.item_code || '-') + '</td>'
        + '<td class="text-end">' + (li.quantity != null ? li.quantity : '-') + '</td>'
        + '<td>' + esc(li.uom || '-') + '</td>'
        + '<td class="text-end">' + (li.unit_price != null ? li.unit_price : '-') + '</td>'
        + '<td class="text-end">' + (li.amount != null ? li.amount : '-') + '</td>'
        + '</tr>';
    }).join('');
  }

  function populatePOLines(evidence) {
    var tbody = document.getElementById('poLineItems');
    if (!tbody) return;
    var lines = [];
    evidence.forEach(function(e) {
      if (e.type === 'purchase_order' && e.details && e.details.line_items) {
        lines = e.details.line_items;
      }
    });
    if (!lines.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-3">No PO line items found.</td></tr>';
      return;
    }
    tbody.innerHTML = lines.map(function(li, i) {
      return '<tr>'
        + '<td>' + (li.line_number || (i + 1)) + '</td>'
        + '<td>' + esc(li.description || '-') + '</td>'
        + '<td>' + esc(li.item_code || '-') + '</td>'
        + '<td class="text-end">' + (li.quantity != null ? li.quantity : '-') + '</td>'
        + '<td>' + esc(li.uom || '-') + '</td>'
        + '<td class="text-end">' + (li.unit_price != null ? li.unit_price : '-') + '</td>'
        + '<td class="text-end">' + (li.amount != null ? li.amount : '-') + '</td>'
        + '</tr>';
    }).join('');
  }

  function populateGRNLines(evidence) {
    var tbody = document.getElementById('grnLineItems');
    if (!tbody) return;
    var lines = [];
    evidence.forEach(function(e) {
      if (e.type === 'grn' && e.details && e.details.line_items) {
        lines = e.details.line_items;
      }
    });
    if (!lines.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-3">No GRN data found.</td></tr>';
      return;
    }
    tbody.innerHTML = lines.map(function(li, i) {
      return '<tr>'
        + '<td>' + esc(li.grn_number || '-') + '</td>'
        + '<td>' + esc(li.date || '-') + '</td>'
        + '<td>' + esc(li.description || '-') + '</td>'
        + '<td>' + esc(li.item_code || '-') + '</td>'
        + '<td class="text-end">' + (li.quantity_received != null ? li.quantity_received : '-') + '</td>'
        + '<td>' + esc(li.uom || '-') + '</td>'
        + '</tr>';
    }).join('');
  }

  // Timeline
  window.loadTimeline = async function () {
    var el = document.getElementById('timelineContent');
    if (!el) return;
    el.innerHTML = '<div class="text-center py-4"><div class="spinner-border spinner-border-sm text-primary"></div></div>';
    try {
      var data = await apiFetch(CFG.urls.caseTimeline, { method: 'GET' });
      if (data && data.entries && data.entries.length) {
        el.innerHTML = data.entries.map(function(entry) {
          var iconClass = 'bg-secondary-subtle text-secondary';
          if (entry.category === 'agent_run') iconClass = 'bg-primary-subtle text-primary';
          if (entry.category === 'decision') iconClass = 'bg-warning-subtle text-warning';
          if (entry.category === 'review') iconClass = 'bg-info-subtle text-info';
          if (entry.category === 'audit') iconClass = 'bg-secondary-subtle text-secondary';
          return '<div class="timeline-entry">'
            + '<div class="timeline-icon ' + iconClass + '"><i class="bi bi-circle-fill"></i></div>'
            + '<div class="timeline-content">'
            + '<div class="fw-medium">' + esc(entry.title || entry.event_type || 'Event') + '</div>'
            + '<div class="small text-muted">' + esc(entry.description || '') + '</div>'
            + '<div class="timeline-time">' + esc(entry.timestamp || '') + '</div>'
            + '</div></div>';
        }).join('');
      } else {
        el.innerHTML = '<p class="text-muted text-center py-3">No timeline entries yet.</p>';
      }
    } catch (err) {
      el.innerHTML = '<p class="text-danger small">Failed to load timeline.</p>';
    }
  };

  // Governance
  window.loadGovernance = async function () {
    var el = document.getElementById('governanceContent');
    if (!el) return;
    el.innerHTML = '<div class="text-center py-4"><div class="spinner-border spinner-border-sm text-primary"></div></div>';
    try {
      var data = await apiFetch(CFG.urls.caseGovernance, { method: 'GET' });
      if (data && data.entries && data.entries.length) {
        el.innerHTML = data.entries.map(function(entry) {
          return '<div class="gov-entry">'
            + '<span class="gov-label">' + esc(entry.event_type || entry.label || 'Event') + '</span>'
            + '<span class="gov-value">' + esc(entry.detail || entry.description || '') + '</span>'
            + '</div>';
        }).join('');
      } else if (data && typeof data === 'object') {
        // Render as key-value pairs
        var html = '';
        for (var key in data) {
          if (data.hasOwnProperty(key) && key !== 'error') {
            html += '<div class="gov-entry"><span class="gov-label">' + esc(key) + '</span><span class="gov-value">' + esc(String(data[key])) + '</span></div>';
          }
        }
        el.innerHTML = html || '<p class="text-muted text-center py-3">No governance data.</p>';
      } else {
        el.innerHTML = '<p class="text-muted text-center py-3">No governance data available.</p>';
      }
    } catch (err) {
      el.innerHTML = '<p class="text-danger small">Failed to load governance data.</p>';
    }
  };

  // Matching data
  async function loadMatchingData() {
    if (!CFG.reconciliationResultId) return;
    try {
      var data = await apiFetch(CFG.urls.caseEvidence, { method: 'GET' });
      if (data) {
        populateHeaderComparison(data);
        populateLineMatching(data);
      }
    } catch (err) {
      console.error('[case-ws] loadMatchingData failed', err);
    }
  }

  function populateHeaderComparison(data) {
    var tbody = document.getElementById('headerComparison');
    if (!tbody) return;
    // Build from evidence if available
    var evidence = data.evidence || [];
    var invData = null, poData = null;
    evidence.forEach(function(e) {
      if (e.type === 'invoice') invData = e.details || {};
      if (e.type === 'purchase_order') poData = e.details || {};
    });
    if (!invData || !poData) {
      tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3">Comparison data not available.</td></tr>';
      return;
    }
    var fields = [
      { label: 'Total Amount', inv: invData.total_amount, po: poData.total_amount },
      { label: 'Vendor', inv: invData.vendor_name, po: poData.vendor_name },
      { label: 'Currency', inv: invData.currency, po: poData.currency },
    ];
    tbody.innerHTML = fields.map(function(f) {
      var invVal = f.inv != null ? String(f.inv) : '-';
      var poVal = f.po != null ? String(f.po) : '-';
      var match = invVal === poVal;
      var variance = '-';
      if (!isNaN(parseFloat(invVal)) && !isNaN(parseFloat(poVal))) {
        var diff = parseFloat(invVal) - parseFloat(poVal);
        variance = diff === 0 ? '0' : (diff > 0 ? '+' : '') + diff.toFixed(2);
      }
      return '<tr>'
        + '<td class="fw-medium">' + esc(f.label) + '</td>'
        + '<td>' + esc(invVal) + '</td>'
        + '<td>' + esc(poVal) + '</td>'
        + '<td>' + esc(variance) + '</td>'
        + '<td><span class="badge ' + (match ? 'bg-success-subtle text-success-emphasis' : 'bg-warning-subtle text-warning-emphasis') + '">'
        + (match ? 'Match' : 'Mismatch') + '</span></td>'
        + '</tr>';
    }).join('');
  }

  function populateLineMatching(data) {
    var tbody = document.getElementById('lineMatching');
    var countEl = document.getElementById('lineMatchCount');
    if (!tbody) return;
    // Try to find line match from evidence
    var evidence = data.evidence || [];
    var lineMatches = [];
    evidence.forEach(function(e) {
      if (e.type === 'decision' && e.details && e.details.line_matches) {
        lineMatches = e.details.line_matches;
      }
    });
    if (countEl) countEl.textContent = lineMatches.length + ' lines';
    if (!lineMatches.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted py-3">No line matching data available.</td></tr>';
      return;
    }
    tbody.innerHTML = lineMatches.map(function(m) {
      var confBadge = 'bg-secondary';
      if (m.confidence >= 0.8) confBadge = 'bg-success';
      else if (m.confidence >= 0.5) confBadge = 'bg-warning text-dark';
      else confBadge = 'bg-danger';
      return '<tr>'
        + '<td>' + (m.inv_line || '-') + '</td>'
        + '<td>' + (m.po_line || '-') + '</td>'
        + '<td>' + esc(m.description || '-') + '</td>'
        + '<td class="text-end">' + (m.inv_qty != null ? m.inv_qty : '-') + '</td>'
        + '<td class="text-end">' + (m.po_qty != null ? m.po_qty : '-') + '</td>'
        + '<td class="text-end">' + (m.inv_price != null ? m.inv_price : '-') + '</td>'
        + '<td class="text-end">' + (m.po_price != null ? m.po_price : '-') + '</td>'
        + '<td><span class="badge ' + confBadge + '">' + Math.round((m.confidence || 0) * 100) + '%</span></td>'
        + '<td><span class="badge ' + (m.matched ? 'bg-success-subtle text-success-emphasis' : 'bg-danger-subtle text-danger-emphasis') + '">'
        + (m.matched ? 'Matched' : 'Unmatched') + '</span></td>'
        + '</tr>';
    }).join('');
  }

  // PO & GRN tab
  async function loadPOGRNData() {
    // Already loaded with evidence, just trigger if needed
    if (!CFG.invoiceId) return;
    loadLineItems();
  }

  // ==================================================================
  // ACTIONS
  // ==================================================================
  window.caseAction = async function (action) {
    if (!confirm('Are you sure you want to ' + action.replace('_', ' ') + ' this case?')) return;
    try {
      var res = await apiFetch(CFG.urls.caseAction, {
        method: 'POST',
        body: { action: action },
      });
      if (res && res.success) {
        appendChatMessage('system', 'Action "' + action + '" completed successfully.');
        setTimeout(function() { window.location.reload(); }, 1500);
      } else {
        appendChatMessage('system', 'Action failed: ' + (res.error || 'Unknown error'));
      }
    } catch (err) {
      appendChatMessage('system', 'Action failed: ' + ((err && err.message) || 'Unknown'));
    }
  };

  window.downloadInvoice = function (invoiceId) {
    window.open('/api/v1/documents/invoices/' + invoiceId + '/download/', '_blank');
  };

  window.exportReport = function () {
    appendChatMessage('system', 'Export functionality coming soon.');
  };

  // ==================================================================
  // PROGRESS HELPERS (shared by upload and supervisor)
  // ==================================================================
  function appendProgressMessage(icon, title) {
    if (chatWelcome) chatWelcome.style.display = 'none';
    var row = document.createElement('div');
    row.className = 'copilot-msg copilot-msg-progress';

    var avatar = document.createElement('div');
    avatar.className = 'copilot-msg-avatar copilot-msg-avatar-ai';
    avatar.innerHTML = '<i class="bi bi-' + icon + '"></i>';

    var body = document.createElement('div');
    body.className = 'copilot-msg-body';

    var role = document.createElement('div');
    role.className = 'copilot-msg-role';
    role.textContent = title;

    var stepsContainer = document.createElement('div');
    stepsContainer.className = 'copilot-progress-steps';

    body.appendChild(role);
    body.appendChild(stepsContainer);
    row.appendChild(avatar);
    row.appendChild(body);
    chatMessages.appendChild(row);
    scrollChatToBottom();
    return { row: row, stepsContainer: stepsContainer };
  }

  function updateProgressSteps(container, steps) {
    container.innerHTML = steps.map(function (s) {
      var cls = s.failed ? 'failed' : (s.done ? 'done' : 'pending');
      var icon = s.failed
        ? '<i class="bi bi-x-circle-fill"></i>'
        : (s.done
          ? '<i class="bi bi-check-circle-fill"></i>'
          : '<div class="spinner-border spinner-border-sm text-primary"></div>');
      return '<div class="copilot-progress-step ' + cls + '">'
        + '<span class="step-icon">' + icon + '</span>'
        + '<span>' + esc(s.label) + '</span>'
        + '</div>';
    }).join('');
  }

  /**
   * Render streaming supervisor steps with rounds, tool details, and statuses.
   */
  var TOOL_LABELS = {
    get_ocr_text: 'Read document text',
    classify_document: 'Classify document',
    extract_invoice_fields: 'Extract invoice fields',
    validate_extraction: 'Validate extracted data',
    repair_extraction: 'Repair extraction issues',
    check_duplicate: 'Check for duplicates',
    verify_vendor: 'Verify vendor',
    verify_tax_computation: 'Verify tax computation',
    vendor_search: 'Search vendor directory',
    po_lookup: 'Look up purchase order',
    grn_lookup: 'Look up goods receipt',
    run_header_match: 'Match header fields',
    run_line_match: 'Match line items',
    run_grn_match: 'Match goods receipt',
    re_extract_field: 'Re-extract field',
    invoke_po_retrieval_agent: 'Retrieve purchase order',
    invoke_grn_retrieval_agent: 'Retrieve goods receipt',
    get_vendor_history: 'Check vendor history',
    get_case_history: 'Review case history',
    get_tolerance_config: 'Check tolerance settings',
    persist_invoice: 'Save invoice data',
    create_case: 'Create AP case',
    submit_recommendation: 'Submit recommendation',
    assign_reviewer: 'Assign reviewer',
    generate_case_summary: 'Generate case summary',
    invoice_details: 'Get invoice details',
    exception_list: 'Get exception list',
    reconciliation_summary: 'Get reconciliation summary'
  };

  function updateStreamingSteps(container, steps) {
    var html = '';
    for (var i = 0; i < steps.length; i++) {
      var s = steps[i];
      if (s.kind === 'round') {
        var roundIcon = s.active
          ? '<div class="spinner-border spinner-border-sm text-primary"></div>'
          : '<i class="bi bi-lightbulb-fill"></i>';
        html += '<div class="sv-step sv-round' + (s.active ? ' active' : '') + '">'
          + '<span class="step-icon">' + roundIcon + '</span>'
          + '<span>Round ' + s.round + (s.active ? ' -- Thinking...' : '') + '</span>'
          + '</div>';
      } else if (s.kind === 'reasoning') {
        // Show the LLM's reasoning / thinking text
        html += '<div class="sv-step sv-reasoning">'
          + '<span class="step-icon"><i class="bi bi-chat-left-text-fill"></i></span>'
          + '<div class="sv-reasoning-body">';
        if (s.tools_planned && s.tools_planned.length) {
          var toolNames = s.tools_planned.map(function (t) {
            return TOOL_LABELS[t] || t.replace(/_/g, ' ');
          });
          html += '<div class="sv-reasoning-plan"><strong>Plan:</strong> ' + esc(toolNames.join(', ')) + '</div>';
        }
        if (s.text) {
          html += '<div class="sv-reasoning-text">' + esc(s.text) + '</div>';
        }
        html += '</div></div>';
      } else if (s.kind === 'tool') {
        var toolLabel = TOOL_LABELS[s.tool] || (s.tool || '').replace(/_/g, ' ');
        var cls = 'running';
        var icon = '<div class="spinner-border spinner-border-sm text-primary"></div>';
        if (s.status === 'done') {
          cls = 'done';
          icon = '<i class="bi bi-check-circle-fill"></i>';
        } else if (s.status === 'failed') {
          cls = 'failed';
          icon = '<i class="bi bi-x-circle-fill"></i>';
        }
        var dur = s.duration_ms != null ? ' (' + (s.duration_ms / 1000).toFixed(1) + 's)' : '';
        var hasDetail = s.output_summary && s.status !== 'running';
        html += '<div class="sv-step sv-tool ' + cls + '">'
          + '<span class="step-icon">' + icon + '</span>'
          + '<span class="sv-tool-label">' + esc(toolLabel) + dur + '</span>';
        if (hasDetail) {
          html += '<button class="sv-detail-toggle" onclick="this.parentElement.classList.toggle(\'expanded\')" title="Show details">'
            + '<i class="bi bi-chevron-down"></i></button>'
            + '<div class="sv-detail">'
            + '<span class="sv-detail-text">' + esc(s.output_summary) + '</span>'
            + '</div>';
        }
        html += '</div>';
      } else if (s.kind === 'complete') {
        html += '<div class="sv-step sv-complete">'
          + '<span class="step-icon"><i class="bi bi-check-circle-fill"></i></span>'
          + '<span>Analysis complete</span>'
          + '</div>';
      } else if (s.kind === 'error') {
        html += '<div class="sv-step sv-error">'
          + '<span class="step-icon"><i class="bi bi-exclamation-triangle-fill"></i></span>'
          + '<span>Error: ' + esc(s.message) + '</span>'
          + '</div>';
      }
    }
    container.innerHTML = html;
  }

  // ==================================================================
  // INVOICE UPLOAD (via attach button in chat)
  // ==================================================================
  function onFileSelected() {
    if (invoiceFileInput.files.length) handleFileUpload(invoiceFileInput.files[0]);
    invoiceFileInput.value = '';
  }

  async function handleFileUpload(file) {
    var allowed = ['application/pdf', 'image/png', 'image/jpeg', 'image/tiff'];
    if (allowed.indexOf(file.type) === -1) {
      appendChatMessage('system', 'Unsupported file type. Upload PDF, PNG, JPG, or TIFF.');
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      appendChatMessage('system', 'File too large. Maximum size is 20 MB.');
      return;
    }

    // Switch to chat tab if not active
    var chatTab = document.getElementById('tab-chat');
    if (chatTab && !chatTab.classList.contains('active')) {
      new bootstrap.Tab(chatTab).show();
    }
    if (chatWelcome) chatWelcome.style.display = 'none';

    appendChatMessage('user', 'Uploaded invoice: ' + file.name);

    var prog = appendProgressMessage('cloud-arrow-up', 'Processing Invoice');
    updateProgressSteps(prog.stepsContainer, [{ label: 'Uploading document...', done: false }]);

    var formData = new FormData();
    formData.append('file', file);

    try {
      var res = await fetch(CFG.urls.invoiceUpload, {
        method: 'POST',
        headers: { 'X-CSRFToken': CFG.csrfToken },
        credentials: 'same-origin',
        body: formData,
      });
      var data = await res.json();
      if (!res.ok) {
        updateProgressSteps(prog.stepsContainer, [
          { label: 'Upload failed: ' + (data.error || 'Unknown error'), done: true, failed: true },
        ]);
        return;
      }
      updateProgressSteps(prog.stepsContainer, [{ label: 'Document received', done: true }]);
      pollUploadStatus(data.upload_id, prog.stepsContainer);
    } catch (err) {
      updateProgressSteps(prog.stepsContainer, [
        { label: 'Upload error: ' + ((err && err.message) || 'Network error'), done: true, failed: true },
      ]);
    }
  }

  function pollUploadStatus(uploadId, stepsContainer) {
    var url = CFG.urls.uploadStatus.replace('{id}', uploadId);
    var redirected = false;
    var interval = setInterval(async function () {
      try {
        var data = await apiFetch(url, { method: 'GET' });
        if (data.steps) {
          updateProgressSteps(stepsContainer, data.steps);
          scrollChatToBottom();
        }
        // Redirect as soon as case_id is available (don't wait for pipeline)
        if (!redirected && data.case_id) {
          redirected = true;
          clearInterval(interval);
          var steps = data.steps || [];
          steps.push({ label: 'Opening case workspace...', done: true });
          updateProgressSteps(stepsContainer, steps);
          setTimeout(function () {
            window.location.href = CFG.urls.caseBase + data.case_id + '/?auto_run=1';
          }, 600);
          return;
        }
        if (data.completed) {
          clearInterval(interval);
          if (data.error) {
            var steps = data.steps || [];
            steps.push({ label: data.error, done: true, failed: true });
            updateProgressSteps(stepsContainer, steps);
          }
        }
      } catch (err) {
        clearInterval(interval);
        updateProgressSteps(stepsContainer, [
          { label: 'Status check failed', done: true, failed: true },
        ]);
      }
    }, 2000);
  }

  // ==================================================================
  // WAIT FOR INVOICE (poll case context until invoice is linked)
  // ==================================================================
  function waitForInvoiceThenRunSupervisor() {
    var attempts = 0;
    var maxAttempts = 90; // ~3 minutes at 2s interval

    // Show a waiting message in chat
    if (chatWelcome) chatWelcome.style.display = 'none';
    appendChatMessage('system', 'Waiting for extraction to complete before running analysis...');

    var pollInterval = setInterval(async function () {
      attempts++;
      try {
        var ctx = await apiFetch(CFG.urls.caseContext, { method: 'GET' });
        if (ctx && ctx.invoice && ctx.invoice.id) {
          clearInterval(pollInterval);
          // Update CFG with resolved values
          CFG.invoiceId = ctx.invoice.id;
          if (ctx.reconciliation && ctx.reconciliation.id) {
            CFG.reconciliationResultId = ctx.reconciliation.id;
          }
          runSupervisor('Analyze this newly uploaded invoice');
          return;
        }
      } catch (err) {
        console.warn('[case-ws] polling case context failed', err);
      }
      if (attempts >= maxAttempts) {
        clearInterval(pollInterval);
        appendChatMessage('system', 'Extraction is taking longer than expected. Refresh the page and try again.');
      }
    }, 2000);
  }

  // ==================================================================
  // SUPERVISOR AGENT (SSE real-time streaming)
  // ==================================================================
  window.runSupervisor = async function (promptText) {
    if (supervisorRunning) return;
    supervisorRunning = true;

    // Switch to chat tab if not active
    var chatTab = document.getElementById('tab-chat');
    if (chatTab && !chatTab.classList.contains('active')) {
      new bootstrap.Tab(chatTab).show();
    }
    if (chatWelcome) chatWelcome.style.display = 'none';

    if (promptText) appendChatMessage('user', promptText);

    // Show progress message in chat
    var prog = appendProgressMessage('cpu', 'Supervisor Agent');
    var steps = [];
    var currentRound = 0;

    function renderSteps() {
      updateStreamingSteps(prog.stepsContainer, steps);
      scrollChatToBottom();
    }

    function handleEvent(evt) {
      if (evt.type === 'thinking') {
        currentRound = evt.round || currentRound + 1;
        steps.push({ kind: 'round', round: currentRound, active: true });
        renderSteps();
      } else if (evt.type === 'reasoning') {
        // Mark current round as no longer active (LLM finished thinking)
        for (var r = steps.length - 1; r >= 0; r--) {
          if (steps[r].kind === 'round' && steps[r].round === (evt.round || currentRound)) {
            steps[r].active = false;
            break;
          }
        }
        steps.push({
          kind: 'reasoning',
          round: evt.round || currentRound,
          text: evt.text || '',
          tools_planned: evt.tools_planned || [],
        });
        renderSteps();
      } else if (evt.type === 'tool_start') {
        steps.push({
          kind: 'tool', tool: evt.tool, round: evt.round || currentRound,
          status: 'running', duration_ms: null, output_summary: '',
        });
        renderSteps();
      } else if (evt.type === 'tool_complete') {
        // Update the last matching tool_start for this tool
        for (var i = steps.length - 1; i >= 0; i--) {
          if (steps[i].kind === 'tool' && steps[i].tool === evt.tool && steps[i].status === 'running') {
            steps[i].status = evt.status === 'SUCCESS' ? 'done' : 'failed';
            steps[i].duration_ms = evt.duration_ms;
            steps[i].output_summary = evt.output_summary || '';
            break;
          }
        }
        // Mark round as no longer active
        for (var j = steps.length - 1; j >= 0; j--) {
          if (steps[j].kind === 'round' && steps[j].round === (evt.round || currentRound)) {
            steps[j].active = false;
            break;
          }
        }
        renderSteps();
      } else if (evt.type === 'complete') {
        steps.push({ kind: 'complete' });
        renderSteps();

        // Render structured summary card
        var s = evt.summary || {};
        if (typeof s === 'string') {
          // Fallback for legacy text summaries
          appendChatMessage('assistant', s || 'Supervisor analysis completed.');
        } else {
          appendSummaryCard(s, evt.confidence, evt.recommendation);
        }
      } else if (evt.type === 'error') {
        steps.push({ kind: 'error', message: evt.message || 'Unknown error' });
        renderSteps();
      }
    }

    try {
      var response = await fetch(CFG.urls.supervisorRunStream, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CFG.csrfToken,
        },
        credentials: 'same-origin',
        body: JSON.stringify({
          invoice_id: CFG.invoiceId,
          reconciliation_result_id: CFG.reconciliationResultId,
          case_id: CFG.caseId,
        }),
      });

      if (!response.ok) {
        var errText = 'Server error ' + response.status;
        try { errText = (await response.json()).error || errText; } catch (_) {}
        throw new Error(errText);
      }

      var reader = response.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';

      while (true) {
        var chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });

        // Parse SSE events separated by double newline
        var parts = buffer.split('\n\n');
        buffer = parts.pop() || '';

        for (var p = 0; p < parts.length; p++) {
          var line = parts[p].trim();
          if (line.indexOf('data: ') === 0) {
            try {
              var evt = JSON.parse(line.substring(6));
              handleEvent(evt);
            } catch (_parseErr) {
              // skip malformed events
            }
          }
        }
      }

    } catch (err) {
      // Fallback: if SSE fails, try the regular endpoint
      try {
        var res = await apiFetch(CFG.urls.supervisorRun, {
          method: 'POST',
          body: {
            invoice_id: CFG.invoiceId,
            reconciliation_result_id: CFG.reconciliationResultId,
            case_id: CFG.caseId,
          },
        });
        if (res && !res.error) {
          steps = [];
          if (res.tool_calls && res.tool_calls.length) {
            for (var i = 0; i < res.tool_calls.length; i++) {
              var tc = res.tool_calls[i];
              steps.push({
                kind: 'tool', tool: tc.tool_name,
                status: tc.status === 'SUCCESS' ? 'done' : 'failed',
                duration_ms: tc.duration_ms, output_summary: '',
              });
            }
          }
          steps.push({ kind: 'complete' });
          renderSteps();
          if (res.summary && typeof res.summary === 'object') {
            appendSummaryCard(res.summary, res.confidence, res.recommendation);
          } else {
            var summary = '';
            if (res.recommendation) summary += 'Recommendation: ' + res.recommendation;
            if (res.confidence) summary += '\nConfidence: ' + Math.round(res.confidence * 100) + '%';
            if (!summary) summary = 'Supervisor analysis completed.';
            appendChatMessage('assistant', summary);
          }
        } else {
          steps.push({ kind: 'error', message: (res && res.error) || err.message || 'Unknown error' });
          renderSteps();
        }
      } catch (fallbackErr) {
        steps.push({ kind: 'error', message: err.message || 'Connection failed' });
        renderSteps();
      }
    } finally {
      supervisorRunning = false;
    }
  };

  // ==================================================================
  // UTILS
  // ==================================================================
  function esc(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(String(str)));
    return div.innerHTML;
  }

  async function apiFetch(url, opts) {
    opts = opts || {};
    var fetchOpts = {
      method: opts.method || 'GET',
      headers: {
        'X-CSRFToken': CFG.csrfToken,
      },
      credentials: 'same-origin',
    };
    if (opts.body) {
      fetchOpts.headers['Content-Type'] = 'application/json';
      fetchOpts.body = JSON.stringify(opts.body);
    }
    var response = await fetch(url, fetchOpts);
    if (!response.ok) {
      var errText = '';
      try { errText = (await response.json()).error || response.statusText; } catch (_) { errText = response.statusText; }
      throw new Error(response.status + ': ' + errText);
    }
    return response.json();
  }

  // ── Kickoff ──
  init();
})();
