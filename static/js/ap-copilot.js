/**
 * AP Copilot -- Unified Controller
 * Handles both plain chat mode and case workspace mode from a single file.
 * Mode is determined by the presence of CFG.hasCaseWorkspace flag.
 */
(function () {
  'use strict';

  // ── Configuration ──
  var cfgEl = document.getElementById('copilotConfig');
  if (!cfgEl) return;
  var CFG = JSON.parse(cfgEl.textContent);

  // Detect mode
  var IS_CASE = !!CFG.hasCaseWorkspace;

  // ── State ──
  var sessionId = CFG.sessionId;
  var caseId = CFG.caseId;
  var isSending = false;
  var supervisorRunning = false;

  // ── DOM (shared) ──
  var chatMessages  = document.getElementById('chatMessages');
  var chatInput     = document.getElementById('chatInput');
  var chatForm      = document.getElementById('chatForm');
  var btnSend       = document.getElementById('btnSend') || document.getElementById('chatSend');
  var welcomeState  = document.getElementById('welcomeState') || document.getElementById('chatWelcome');
  var quickQuestions = document.getElementById('quickQuestions');
  var btnAttachFile = document.getElementById('btnAttachFile');
  var invoiceFileInput = document.getElementById('invoiceFileInput');

  // ── DOM (plain chat mode) ──
  var sidebar        = document.getElementById('copilotSidebar');
  var topbarTitle    = document.getElementById('topbarTitle');
  var btnCollapse    = document.getElementById('btnCollapseSidebar');
  var btnExpand      = document.getElementById('btnExpandSidebar');
  var btnNewChat     = document.getElementById('btnNewChat');
  var sidebarSearch  = document.getElementById('sidebarSearch');

  // Case search modal (plain chat mode)
  var btnLinkCase        = document.getElementById('btnLinkCase');
  var caseSearchBg       = document.getElementById('caseSearchBackdrop');
  var caseSearchInput    = document.getElementById('caseSearchInput');
  var caseSearchResults  = document.getElementById('caseSearchResults');
  var btnCloseCaseSearch = document.getElementById('btnCloseCaseSearch');

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

    // Upload attach button
    if (btnAttachFile) btnAttachFile.addEventListener('click', function () { invoiceFileInput.click(); });
    if (invoiceFileInput) invoiceFileInput.addEventListener('change', onFileSelected);

    if (IS_CASE) {
      initCaseMode();
    } else {
      initChatMode();
    }

    if (chatInput) chatInput.focus();
    rehydrateRichMessages();
    scrollToBottom();
  }

  /**
   * On page load, upgrade any server-rendered assistant messages that
   * carry a hidden JSON payload (`<script class="rich-payload-data">`)
   * so they display with the same rich format as live chat responses.
   */
  function rehydrateRichMessages() {
    if (!chatMessages) return;
    var scripts = chatMessages.querySelectorAll('script.rich-payload-data');
    for (var i = 0; i < scripts.length; i++) {
      try {
        var payload = JSON.parse(scripts[i].textContent);
        var summary = payload.summary || payload.answer || payload.text || '';
        var evidence = payload.evidence || [];
        var followUps = payload.follow_up_prompts || [];
        var toolDetails = payload.tool_details || [];
        if (!summary && !evidence.length) continue;

        // Find the sibling .copilot-msg-text and replace its contents
        var msgBody = scripts[i].closest('.copilot-msg-body');
        if (!msgBody) continue;
        var msgText = msgBody.querySelector('.copilot-msg-text');
        if (!msgText) continue;

        var html = '<div class="chat-rich-response">';
        if (summary) {
          html += '<div class="chat-rich-summary">' + miniMarkdown(summary) + '</div>';
        }
        if (evidence.length) {
          html += buildEvidenceHTML(evidence);
        }
        if (toolDetails.length) {
          html += buildToolDetailsHTML(toolDetails);
        }
        if (followUps.length) {
          html += buildFollowUpHTML(followUps);
        }
        html += '</div>';
        msgText.innerHTML = html;
      } catch (e) {
        // Parsing failed -- leave the plain text in place
      }
    }
  }

  function buildEvidenceHTML(evidence) {
    var toggleId = 'evToggle_' + Date.now() + '_' + Math.random().toString(36).slice(2, 7);
    var html = '<div class="chat-evidence-toggle" onclick="(function(el){var g=el.nextElementSibling;var open=g.style.display!==\'none\';g.style.display=open?\'none\':\'grid\';el.querySelector(\'.chat-ev-chevron\').classList.toggle(\'open\',!open);})(this)">';
    html += '<i class="bi bi-card-list me-1"></i>';
    html += '<span>Evidence (' + evidence.length + ')</span>';
    html += '<i class="bi bi-chevron-down chat-ev-chevron"></i>';
    html += '</div>';
    html += '<div class="chat-evidence-grid" style="display:none">';
    for (var i = 0; i < evidence.length; i++) {
      var ev = evidence[i];
      var evType = ev.type || 'info';
      var evLabel = ev.label || evType;
      var evData = ev.data || {};

      var evIcon = 'info-circle'; var evColor = '#64748b';
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
      if (evData.severity) {
        var sevCls = evData.severity === 'HIGH' ? 'danger' : (evData.severity === 'MEDIUM' ? 'warning' : 'secondary');
        html += '<span class="badge bg-' + sevCls + ' chat-ev-badge">' + esc(evData.severity) + '</span>';
      }
      html += '</div>';

      html += '<div class="chat-ev-body">';
      var dataKeys = Object.keys(evData);
      for (var j = 0; j < dataKeys.length; j++) {
        var k = dataKeys[j];
        if (k === 'severity') continue;
        var v = evData[k];
        if (v === null || v === undefined || v === '') continue;
        var displayVal = v;
        if (typeof v === 'number' && k.indexOf('confidence') >= 0) {
          displayVal = Math.round(v * 100) + '%';
        }
        var kLabel = k.replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
        html += '<div class="chat-ev-field">';
        html += '<span class="chat-ev-key">' + esc(kLabel) + '</span>';
        html += '<span class="chat-ev-val">' + esc(String(displayVal)) + '</span>';
        html += '</div>';
      }
      html += '</div></div>';
    }
    html += '</div>';
    return html;
  }

  function buildFollowUpHTML(followUps) {
    var html = '<div class="chat-followup-row">';
    for (var f = 0; f < followUps.length; f++) {
      var chipFunc = IS_CASE ? 'chatSendQuick' : 'chipSend';
      html += '<button class="chat-followup-chip" onclick="' + chipFunc + '(\'' + esc(followUps[f]).replace(/'/g, "\\'") + '\')"><i class="bi bi-arrow-return-right me-1"></i>' + esc(followUps[f]) + '</button>';
    }
    html += '</div>';
    return html;
  }

  function buildToolDetailsHTML(toolDetails) {
    if (!toolDetails || !toolDetails.length) return '';
    var html = '<div class="chat-tool-details-toggle" onclick="(function(el){var g=el.nextElementSibling;var open=g.style.display!==\'none\';g.style.display=open?\'none\':\'block\';el.querySelector(\'.chat-td-chevron\').classList.toggle(\'open\',!open);})(this)">';
    html += '<i class="bi bi-tools me-1"></i>';
    html += '<span>Tool Execution Details (' + toolDetails.length + ')</span>';
    html += '<i class="bi bi-chevron-down chat-td-chevron"></i>';
    html += '</div>';
    html += '<div class="chat-tool-details-list" style="display:none">';
    for (var i = 0; i < toolDetails.length; i++) {
      var td = toolDetails[i];
      var ok = td.success;
      var color = ok ? '#16a34a' : '#dc2626';
      var icon = ok ? 'check-circle-fill' : 'x-circle-fill';
      var dur = td.duration_ms ? ' (' + td.duration_ms + 'ms)' : '';
      html += '<div class="chat-tool-item" style="border-left-color:' + color + '">';
      html += '<div class="chat-tool-item-header">';
      html += '<i class="bi bi-' + icon + '" style="color:' + color + '"></i>';
      html += '<span class="chat-tool-item-label">' + esc(td.label || td.name) + '</span>';
      html += '<span class="chat-tool-item-status" style="color:' + color + '">' + (ok ? 'OK' : 'FAILED') + dur + '</span>';
      html += '</div>';
      if (td.input_summary) {
        html += '<div class="chat-tool-item-row"><span class="chat-tool-item-key">Input:</span> ' + esc(td.input_summary) + '</div>';
      }
      if (td.output_summary) {
        html += '<div class="chat-tool-item-row"><span class="chat-tool-item-key">Result:</span> ' + esc(td.output_summary) + '</div>';
      }
      html += '</div>';
    }
    html += '</div>';
    return html;
  }

  // -- Plain chat mode init --
  function initChatMode() {
    if (btnCollapse) btnCollapse.addEventListener('click', toggleSidebar);
    if (btnExpand)   btnExpand.addEventListener('click', toggleSidebar);
    if (btnNewChat)  btnNewChat.addEventListener('click', startNewChat);
    if (sidebarSearch) sidebarSearch.addEventListener('input', filterSessions);

    // Case search modal
    if (btnLinkCase) btnLinkCase.addEventListener('click', openCaseSearch);
    if (btnCloseCaseSearch) btnCloseCaseSearch.addEventListener('click', closeCaseSearch);
    if (caseSearchBg) caseSearchBg.addEventListener('click', function (e) {
      if (e.target === caseSearchBg) closeCaseSearch();
    });
    if (caseSearchInput) {
      var searchTimer = null;
      caseSearchInput.addEventListener('input', function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(performCaseSearch, 300);
      });
    }
  }

  // ── Case workspace init ──
  function initCaseMode() {
    // Lazy-load tab data when tab is shown
    var tabEls = document.querySelectorAll('[data-bs-toggle="tab"]');
    tabEls.forEach(function (tab) {
      tab.addEventListener('shown.bs.tab', onTabShown);
    });

    // Pre-load data for tabs
    loadLineItems();

    // Auto-run supervisor agent if flagged
    if (CFG.autoRunSupervisor) {
      if (CFG.invoiceId) {
        setTimeout(function () { runSupervisor('Analyze this newly uploaded invoice'); }, 500);
      } else {
        waitForInvoiceThenRunSupervisor();
      }
    }
  }

  // ==================================================================
  // SIDEBAR (plain chat mode)
  // ==================================================================
  function toggleSidebar() {
    sidebar.classList.toggle('collapsed');
    if (sidebar.classList.contains('collapsed')) {
      btnExpand.classList.remove('d-none');
    } else {
      btnExpand.classList.add('d-none');
    }
  }

  function filterSessions() {
    var q = sidebarSearch.value.toLowerCase().trim();
    var items = sidebar.querySelectorAll('.copilot-session-item');
    items.forEach(function (item) {
      var title = item.querySelector('.copilot-session-title');
      var text = (title ? title.textContent : '').toLowerCase();
      item.style.display = text.indexOf(q) !== -1 || !q ? '' : 'none';
    });
  }

  function startNewChat() {
    window.location.href = CFG.urls.hubBase;
  }

  // ==================================================================
  // SESSION
  // ==================================================================
  async function ensureSession() {
    if (sessionId) return sessionId;
    try {
      var body = {};
      if (caseId) body.case_id = caseId;
      var res = await apiFetch(CFG.urls.sessionStart, {
        method: 'POST',
        body: body,
      });
      if (res && res.id) {
        sessionId = res.id;
        if (!IS_CASE) {
          var newUrl = CFG.urls.sessionBase + sessionId + '/';
          window.history.replaceState({}, '', newUrl);
        }
        return sessionId;
      }
    } catch (err) {
      console.error('[copilot] ensureSession failed', err);
    }
    return null;
  }

  // ==================================================================
  // CHAT
  // ==================================================================
  function onInputChange() {
    btnSend.disabled = !chatInput.value.trim();
    chatInput.style.height = 'auto';
    chatInput.style.height = Math.min(chatInput.scrollHeight, 150) + 'px';
  }

  async function onChatSubmit(e) {
    e.preventDefault();
    var text = chatInput.value.trim();
    if (!text || isSending) return;

    // Check for supervisor trigger (plain chat mode)
    if (!IS_CASE && (
        text.toLowerCase().indexOf('run supervisor') !== -1 ||
        text.toLowerCase().indexOf('supervisor agent') !== -1)) {
      chatInput.value = '';
      chatInput.style.height = 'auto';
      btnSend.disabled = true;
      runSupervisor(text);
      return;
    }

    var sid = await ensureSession();
    if (!sid) return;

    hideWelcome();
    appendMessage('user', text);
    chatInput.value = '';
    chatInput.style.height = 'auto';
    btnSend.disabled = true;
    isSending = true;

    var thinkingEl = appendThinking();

    try {
      var res = await apiFetch(CFG.urls.chat, {
        method: 'POST',
        body: { session_id: sid, message: text, case_id: caseId },
      });
      removeEl(thinkingEl);
      if (res && res.response) {
        appendRichResponse(res.response);
      } else {
        appendMessage('system', 'No response received.');
      }
    } catch (err) {
      removeEl(thinkingEl);
      appendMessage('system', 'Error: ' + ((err && err.message) || 'Unknown'));
    } finally {
      isSending = false;
    }
  }

  // Quick send from chips (both modes)
  window.chipSend = function (text) {
    if (chatInput) {
      chatInput.value = text;
      chatForm.dispatchEvent(new Event('submit'));
    }
  };

  // Quick send that also switches to chat tab (case mode)
  window.chatSendQuick = function (text) {
    if (chatInput) {
      chatInput.value = text;
      chatForm.dispatchEvent(new Event('submit'));
    }
    var chatTab = document.getElementById('tab-chat');
    if (chatTab && !chatTab.classList.contains('active')) {
      new bootstrap.Tab(chatTab).show();
    }
  };

  // ==================================================================
  // MESSAGE RENDERING
  // ==================================================================
  function hideWelcome() {
    if (welcomeState) welcomeState.style.display = 'none';
    // Keep bottom quick-question chips hidden -- follow-ups come inline with responses
    if (quickQuestions) quickQuestions.classList.add('d-none');
  }

  function appendMessage(type, text) {
    hideWelcome();

    var row = document.createElement('div');
    row.className = 'copilot-msg';
    if (type === 'user') row.className += ' copilot-msg-user';
    if (type === 'system') row.className += ' copilot-msg-system';

    var avatar = document.createElement('div');
    avatar.className = 'copilot-msg-avatar';
    if (type === 'user') {
      avatar.className += ' copilot-msg-avatar-user';
      avatar.innerHTML = '<i class="bi bi-person-fill"></i>';
    } else {
      avatar.className += ' copilot-msg-avatar-ai';
      avatar.innerHTML = '<i class="bi bi-stars"></i>';
    }

    var body = document.createElement('div');
    body.className = 'copilot-msg-body';

    var role = document.createElement('div');
    role.className = 'copilot-msg-role';
    role.textContent = type === 'user' ? 'You' : 'AP Copilot';

    var msgText = document.createElement('div');
    msgText.className = 'copilot-msg-text';
    msgText.textContent = text;

    body.appendChild(role);
    body.appendChild(msgText);
    row.appendChild(avatar);
    row.appendChild(body);
    chatMessages.appendChild(row);
    scrollToBottom();
  }

  function appendThinking() {
    var row = document.createElement('div');
    row.className = 'copilot-thinking';

    var avatar = document.createElement('div');
    avatar.className = 'copilot-msg-avatar copilot-msg-avatar-ai';
    avatar.innerHTML = '<i class="bi bi-stars"></i>';

    var content = document.createElement('div');
    content.className = 'copilot-thinking-content';
    content.innerHTML = '<div class="copilot-thinking-dots"><span></span><span></span><span></span></div> <span>Thinking...</span>';

    row.appendChild(avatar);
    row.appendChild(content);
    chatMessages.appendChild(row);
    scrollToBottom();
    return row;
  }

  function removeEl(el) {
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  function scrollToBottom() {
    if (chatMessages) chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  // ==================================================================
  // PROGRESS MESSAGE (shared helper for upload + supervisor)
  // ==================================================================
  function appendProgressMessage(icon, title) {
    hideWelcome();
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
    scrollToBottom();
    return { row: row, stepsContainer: stepsContainer };
  }

  function updateProgressSteps(container, steps) {
    container.innerHTML = steps.map(function (s) {
      var cls = s.failed ? 'failed' : (s.done ? 'done' : (s.pending ? 'pending' : 'running'));
      var icon;
      if (s.failed) icon = '<i class="bi bi-x-circle-fill"></i>';
      else if (s.done) icon = '<i class="bi bi-check-circle-fill"></i>';
      else if (s.pending) icon = '<i class="bi bi-circle"></i>';
      else icon = '<div class="spinner-border spinner-border-sm text-primary"></div>';
      return '<div class="copilot-progress-step ' + cls + '">'
        + '<span class="step-icon">' + icon + '</span>'
        + '<span>' + esc(s.label) + '</span>'
        + '</div>';
    }).join('');
  }

  // ==================================================================
  // STREAMING SUPERVISOR STEPS (case mode rich display)
  // ==================================================================
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
    invoke_po_retrieval_agent: 'Run PO Retrieval Agent',
    invoke_grn_retrieval_agent: 'Run GRN Retrieval Agent',
    invoke_exception_analysis_agent: 'Run Exception Analysis Agent',
    invoke_reconciliation_assist_agent: 'Run Reconciliation Assist Agent',
    invoke_review_routing_agent: 'Run Review Routing Agent',
    invoke_case_summary_agent: 'Run Case Summary Agent',
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
    reconciliation_summary: 'Get reconciliation summary',
    get_field_confidence: 'Score field confidence',
    detect_self_company: 'Detect self-company swap',
    get_decision_codes: 'Check decision codes',
    check_approval_status: 'Check approval status',
    auto_close_case: 'Auto-close case',
    escalate_case: 'Escalate case'
  };

  function updateStreamingSteps(container, steps) {
    var html = '';
    for (var i = 0; i < steps.length; i++) {
      var s = steps[i];

      // Skip internal tracking entries that are not tasks
      if (s.kind === 'round' || s.kind === 'reasoning') continue;

      if (s.kind === 'pipeline') {
        var pIcon, pCls;
        if (s.status === 'done')        { pIcon = '<i class="bi bi-check-circle-fill"></i>'; pCls = 'done'; }
        else if (s.status === 'failed') { pIcon = '<i class="bi bi-x-circle-fill"></i>'; pCls = 'failed'; }
        else                            { pIcon = '<div class="spinner-border spinner-border-sm"></div>'; pCls = 'running'; }
        html += '<div class="sv-task sv-task-pipeline ' + pCls + '">'
          + '<span class="sv-task-icon">' + pIcon + '</span>'
          + '<span class="sv-task-label">' + esc(s.message || s.stage) + '</span>'
          + '</div>';

      } else if (s.kind === 'tool') {
        var toolLabel = TOOL_LABELS[s.tool] || (s.tool || '').replace(/_/g, ' ');
        var tIcon, tCls;
        if (s.status === 'done')        { tIcon = '<i class="bi bi-check-circle-fill"></i>'; tCls = 'done'; }
        else if (s.status === 'failed') { tIcon = '<i class="bi bi-x-circle-fill"></i>'; tCls = 'failed'; }
        else if (s.status === 'running'){ tIcon = '<div class="spinner-border spinner-border-sm"></div>'; tCls = 'running'; }
        else                            { tIcon = '<i class="bi bi-circle"></i>'; tCls = 'pending'; }
        var dur = (s.status === 'done' || s.status === 'failed') && s.duration_ms != null
          ? ' <span class="sv-task-dur">(' + (s.duration_ms / 1000).toFixed(1) + 's)</span>' : '';
        var hasDetail = s.output_summary && (s.status === 'done' || s.status === 'failed');
        html += '<div class="sv-task sv-task-tool ' + tCls + '">'
          + '<span class="sv-task-icon">' + tIcon + '</span>'
          + '<span class="sv-task-label">' + esc(toolLabel) + dur + '</span>';
        if (hasDetail) {
          html += '<button class="sv-detail-toggle" onclick="this.parentElement.classList.toggle(\'expanded\')" title="Show details">'
            + '<i class="bi bi-chevron-down"></i></button>'
            + '<div class="sv-detail">'
            + '<span class="sv-detail-text">' + esc(s.output_summary) + '</span>'
            + '</div>';
        }
        html += '</div>';

      } else if (s.kind === 'complete') {
        html += '<div class="sv-task sv-task-complete done">'
          + '<span class="sv-task-icon"><i class="bi bi-check-circle-fill"></i></span>'
          + '<span class="sv-task-label">Generating recommendation</span>'
          + '</div>';

      } else if (s.kind === 'error') {
        html += '<div class="sv-task sv-task-error failed">'
          + '<span class="sv-task-icon"><i class="bi bi-exclamation-triangle-fill"></i></span>'
          + '<span class="sv-task-label">' + esc(s.message || 'Error') + '</span>'
          + '</div>';
      }
    }
    container.innerHTML = html;
  }

  // ==================================================================
  // SUPERVISOR SUMMARY CARD (case mode)
  // ==================================================================
  function appendSummaryCard(s, evtConfidence, evtRec) {
    hideWelcome();

    var confidence = s.confidence || (evtConfidence ? Math.round(evtConfidence * 100) : 0);
    var rec = s.recommendation || (evtRec || '').replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, function (c) { return c.toUpperCase(); });
    var severity = s.recommendation_severity || 'warning';
    var findings = s.findings || [];
    var issues = s.issues || [];
    var toolsOk = s.tools_ok || 0;
    var toolsFailed = s.tools_failed || 0;
    var analysisText = s.analysis_text || '';

    var sevColor = { success: '#16a34a', warning: '#d97706', danger: '#dc2626' };
    var sevBg    = { success: '#f0fdf4', warning: '#fffbeb', danger: '#fef2f2' };
    var sevBorder= { success: '#bbf7d0', warning: '#fde68a', danger: '#fecaca' };
    var sevIcon  = { success: 'check-circle-fill', warning: 'exclamation-triangle-fill', danger: 'x-circle-fill' };

    var color  = sevColor[severity]  || sevColor.warning;
    var bg     = sevBg[severity]     || sevBg.warning;
    var border = sevBorder[severity] || sevBorder.warning;
    var ic     = sevIcon[severity]   || sevIcon.warning;

    var confColor = '#dc2626';
    if (confidence >= 80) confColor = '#16a34a';
    else if (confidence >= 50) confColor = '#d97706';

    var html = '<div class="sv-summary-card" style="border-color:' + border + '">';
    html += '<div class="sv-summary-header" style="background:' + bg + ';border-color:' + border + '">';
    html += '<div class="sv-summary-rec">';
    html += '<i class="bi bi-' + ic + '" style="color:' + color + ';font-size:1.1rem"></i>';
    html += '<div><div class="sv-summary-rec-label">Recommendation</div>';
    html += '<div class="sv-summary-rec-value" style="color:' + color + '">' + esc(rec) + '</div></div>';
    html += '</div>';
    html += '<div class="sv-summary-confidence">';
    html += '<svg class="sv-conf-ring" viewBox="0 0 36 36">';
    html += '<path class="sv-conf-ring-bg" d="M18 2.0845a15.9155 15.9155 0 0 1 0 31.831a15.9155 15.9155 0 0 1 0-31.831" />';
    html += '<path class="sv-conf-ring-fg" stroke="' + confColor + '" stroke-dasharray="' + confidence + ', 100" d="M18 2.0845a15.9155 15.9155 0 0 1 0 31.831a15.9155 15.9155 0 0 1 0-31.831" />';
    html += '</svg>';
    html += '<span class="sv-conf-pct" style="color:' + confColor + '">' + confidence + '%</span>';
    html += '</div></div>';

    if (findings.length) {
      html += '<div class="sv-summary-section">';
      html += '<div class="sv-summary-section-title"><i class="bi bi-search"></i> Findings</div>';
      html += '<div class="sv-summary-findings">';
      for (var i = 0; i < findings.length; i++) {
        var f = findings[i];
        var fBadge = '';
        if (f.severity === 'success') fBadge = ' sv-finding-success';
        else if (f.severity === 'danger') fBadge = ' sv-finding-danger';
        html += '<div class="sv-finding' + fBadge + '">';
        html += '<span class="sv-finding-label">' + esc(f.label) + '</span>';
        html += '<span class="sv-finding-value">' + esc(f.value) + '</span>';
        html += '</div>';
      }
      html += '</div></div>';
    }

    if (issues.length) {
      html += '<div class="sv-summary-section">';
      html += '<div class="sv-summary-section-title sv-issues-title"><i class="bi bi-exclamation-circle"></i> Issues</div>';
      html += '<ul class="sv-summary-issues">';
      for (var j = 0; j < issues.length; j++) {
        html += '<li>' + esc(issues[j]) + '</li>';
      }
      html += '</ul></div>';
    }

    html += '<div class="sv-summary-footer">';
    html += '<span class="sv-summary-tools"><i class="bi bi-gear"></i> ' + (toolsOk + toolsFailed) + ' tools executed';
    if (toolsFailed > 0) html += ', <span class="text-danger">' + toolsFailed + ' failed</span>';
    html += '</span></div>';

    if (analysisText && analysisText.length > 10) {
      html += '<div class="sv-summary-analysis">' + esc(analysisText) + '</div>';
    }

    // Tool details as separate collapsible (reuses shared builder)
    var toolDetails = s.tool_details || [];
    if (toolDetails.length > 0) {
      html += buildToolDetailsHTML(toolDetails);
    }

    html += '</div>';

    var row = document.createElement('div');
    row.className = 'copilot-msg';

    var avatar = document.createElement('div');
    avatar.className = 'copilot-msg-avatar copilot-msg-avatar-ai';
    avatar.innerHTML = '<i class="bi bi-stars"></i>';

    var body = document.createElement('div');
    body.className = 'copilot-msg-body';
    body.innerHTML = html;

    row.appendChild(avatar);
    row.appendChild(body);
    chatMessages.appendChild(row);
    scrollToBottom();
  }

  // ==================================================================
  // INVOICE UPLOAD (in chat)
  // ==================================================================
  function onFileSelected() {
    if (invoiceFileInput.files.length) handleFileUpload(invoiceFileInput.files[0]);
    invoiceFileInput.value = '';
  }

  async function handleFileUpload(file) {
    var allowed = ['application/pdf', 'image/png', 'image/jpeg', 'image/tiff'];
    if (allowed.indexOf(file.type) === -1) {
      appendMessage('system', 'Unsupported file type. Upload PDF, PNG, JPG, or TIFF.');
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      appendMessage('system', 'File too large. Maximum size is 20 MB.');
      return;
    }

    // Switch to chat tab if in case mode and not on chat tab
    if (IS_CASE) {
      var chatTab = document.getElementById('tab-chat');
      if (chatTab && !chatTab.classList.contains('active')) {
        new bootstrap.Tab(chatTab).show();
      }
    }

    appendMessage('user', 'Uploaded invoice: ' + file.name);

    var prog = appendProgressMessage('cloud-arrow-up', 'Uploading Invoice');
    updateProgressSteps(prog.stepsContainer, [{ label: 'Uploading document...', done: false }]);

    var formData = new FormData();
    formData.append('file', file);
    formData.append('supervisor_driven', 'true');

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

      // Supervisor drives the full pipeline: extraction -> reconciliation -> analysis
      // Store IDs so the supervisor SSE handler can find them
      if (data.case_id) caseId = data.case_id;
      CFG._uploadId = data.upload_id;
      CFG.invoiceId = data.invoice_id || null;

      runSupervisor('Analyze this newly uploaded invoice');
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
          scrollToBottom();
        }
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
  // WAIT FOR INVOICE (case mode -- poll until invoice is linked)
  // ==================================================================
  function waitForInvoiceThenRunSupervisor() {
    // The supervisor SSE endpoint now orchestrates the full pipeline
    // (extraction -> reconciliation -> analysis) if needed. Just call it.
    hideWelcome();
    // If we have a case_id, the supervisor will figure out the rest
    if (caseId) {
      // Resolve invoice_id from case context first (best effort)
      apiFetch(CFG.urls.caseContext, { method: 'GET' }).then(function (ctx) {
        if (ctx && ctx.invoice && ctx.invoice.id) {
          CFG.invoiceId = ctx.invoice.id;
        }
        if (ctx && ctx.reconciliation && ctx.reconciliation.id) {
          CFG.reconciliationResultId = ctx.reconciliation.id;
        }
        runSupervisor('Analyze this newly uploaded invoice');
      }).catch(function () {
        runSupervisor('Analyze this newly uploaded invoice');
      });
    } else {
      runSupervisor('Analyze this newly uploaded invoice');
    }
  }

  // ==================================================================
  // SUPERVISOR AGENT
  // ==================================================================
  async function runSupervisor(promptText) {
    if (supervisorRunning) return;
    supervisorRunning = true;

    // Switch to chat tab if in case mode and not on chat tab
    if (IS_CASE) {
      var chatTab = document.getElementById('tab-chat');
      if (chatTab && !chatTab.classList.contains('active')) {
        new bootstrap.Tab(chatTab).show();
      }
    }

    var sid = await ensureSession();
    if (!sid && !IS_CASE) { supervisorRunning = false; return; }

    hideWelcome();
    if (promptText) appendMessage('user', promptText);

    var prog = appendProgressMessage('cpu', 'Supervisor Agent');
    var steps = [];
    var currentRound = 0;

    function renderSteps() {
      if (IS_CASE) {
        updateStreamingSteps(prog.stepsContainer, steps);
      } else {
        // Plain chat mode: simpler progress steps
        var simpleSteps = steps.filter(function (s) {
          return s.kind === 'tool' || s.kind === 'complete' || s.kind === 'error' || s.kind === 'pipeline';
        });
        updateProgressSteps(prog.stepsContainer, simpleSteps.map(function (s) {
          if (s.kind === 'complete') return { label: 'Analysis complete', done: true };
          if (s.kind === 'error') return { label: s.message || 'Error', done: true, failed: true };
          if (s.kind === 'pipeline') return {
            label: s.message || s.stage,
            done: s.status === 'done' || s.status === 'failed',
            failed: s.status === 'failed',
            pending: s.status === 'running',
          };
          var lbl = TOOL_LABELS[s.tool] || (s.tool || '').replace(/_/g, ' ');
          return {
            label: lbl,
            done: s.status === 'done' || s.status === 'failed',
            failed: s.status === 'failed',
            pending: s.status === 'pending',
          };
        }));
      }
      scrollToBottom();
    }

    function handleEvent(evt) {
      if (evt.type === 'pipeline_stage') {
        // Pipeline orchestration events: extraction, reconciliation, analysis
        var stageLabel = {
          extraction: 'Extracting invoice data',
          reconciliation: 'Matching against PO & receipts',
          analysis: 'Running AI analysis',
        }[evt.stage] || evt.stage;
        var existing = null;
        for (var ps = steps.length - 1; ps >= 0; ps--) {
          if (steps[ps].kind === 'pipeline' && steps[ps].stage === evt.stage) {
            existing = steps[ps]; break;
          }
        }
        if (existing) {
          existing.status = evt.status;
          existing.message = evt.message || stageLabel;
        } else {
          steps.push({
            kind: 'pipeline', stage: evt.stage,
            status: evt.status, message: evt.message || stageLabel,
          });
        }
        // Update CFG IDs if the complete event carries them
        if (evt.invoice_id) CFG.invoiceId = evt.invoice_id;
        if (evt.reconciliation_result_id) CFG.reconciliationResultId = evt.reconciliation_result_id;
        renderSteps();
      } else if (evt.type === 'thinking') {
        // No-op: rounds are not displayed; tasks are revealed by reasoning
        currentRound = evt.round || currentRound + 1;
      } else if (evt.type === 'reasoning') {
        // Pre-create pending task entries for each planned tool
        var planned = evt.tools_planned || [];
        for (var tp = 0; tp < planned.length; tp++) {
          var toolName = planned[tp];
          // Only add if no entry exists yet for this tool
          var alreadyHas = false;
          for (var ex = 0; ex < steps.length; ex++) {
            if (steps[ex].kind === 'tool' && steps[ex].tool === toolName) {
              alreadyHas = true; break;
            }
          }
          if (!alreadyHas) {
            steps.push({
              kind: 'tool', tool: toolName,
              status: 'pending', duration_ms: null, output_summary: '',
            });
          }
        }
        renderSteps();
      } else if (evt.type === 'tool_start') {
        // Find a pending entry for this tool and mark running; create if missing
        var found = false;
        for (var ts = 0; ts < steps.length; ts++) {
          if (steps[ts].kind === 'tool' && steps[ts].tool === evt.tool && steps[ts].status === 'pending') {
            steps[ts].status = 'running';
            found = true;
            break;
          }
        }
        if (!found) {
          steps.push({
            kind: 'tool', tool: evt.tool,
            status: 'running', duration_ms: null, output_summary: '',
          });
        }
        renderSteps();
      } else if (evt.type === 'tool_complete') {
        for (var i = steps.length - 1; i >= 0; i--) {
          if (steps[i].kind === 'tool' && steps[i].tool === evt.tool
              && (steps[i].status === 'running' || steps[i].status === 'pending')) {
            steps[i].status = evt.status === 'SUCCESS' ? 'done' : 'failed';
            steps[i].duration_ms = evt.duration_ms;
            steps[i].output_summary = evt.output_summary || '';
            break;
          }
        }
        renderSteps();
      } else if (evt.type === 'complete') {
        steps.push({ kind: 'complete' });
        renderSteps();
        // Capture IDs from the complete event
        if (evt.invoice_id) CFG.invoiceId = evt.invoice_id;
        if (evt.reconciliation_result_id) CFG.reconciliationResultId = evt.reconciliation_result_id;
        var s = evt.summary || {};
        if (typeof s === 'string') {
          appendMessage('assistant', s || 'Supervisor analysis completed.');
        } else if (IS_CASE) {
          appendSummaryCard(s, evt.confidence, evt.recommendation);
        } else {
          var summary = '';
          if (evt.recommendation) summary += 'Recommendation: ' + evt.recommendation;
          if (evt.confidence) summary += '\nConfidence: ' + Math.round(evt.confidence * 100) + '%';
          if (s.analysis_text) summary += '\n\n' + s.analysis_text;
          if (!summary) summary = 'Supervisor analysis completed.';
          appendMessage('assistant', summary);
        }
        // Redirect to case workspace (if we have a case_id and are not already in case mode)
        if (!IS_CASE && caseId && CFG.urls.caseBase) {
          appendMessage('system', 'Opening case workspace...');
          setTimeout(function () {
            window.location.href = CFG.urls.caseBase + caseId + '/';
          }, 1500);
        }
        // Refresh page in case mode so header/tabs reflect updated data
        if (IS_CASE) {
          setTimeout(function () {
            var url = new URL(window.location.href);
            url.searchParams.delete('auto_run');
            window.location.href = url.toString();
          }, 2500);
        }
      } else if (evt.type === 'error') {
        steps.push({ kind: 'error', message: evt.message || 'Unknown error' });
        renderSteps();
      }
    }

    // Try SSE streaming first (if the URL is available)
    if (CFG.urls.supervisorRunStream) {
      try {
        var response = await fetch(CFG.urls.supervisorRunStream, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': CFG.csrfToken,
          },
          credentials: 'same-origin',
          body: JSON.stringify({
            invoice_id: CFG.invoiceId || null,
            upload_id: CFG._uploadId || null,
            reconciliation_result_id: CFG.reconciliationResultId || null,
            case_id: caseId,
            session_id: sid || sessionId || null,
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

          var parts = buffer.split('\n\n');
          buffer = parts.pop() || '';

          for (var p = 0; p < parts.length; p++) {
            var line = parts[p].trim();
            if (line.indexOf('data: ') === 0) {
              try {
                var evt = JSON.parse(line.substring(6));
                handleEvent(evt);
              } catch (_parseErr) {}
            }
          }
        }

        supervisorRunning = false;
        return;
      } catch (streamErr) {
        // Fall through to non-streaming endpoint
        console.warn('[copilot] SSE stream failed, using fallback', streamErr);
      }
    }

    // Fallback: non-streaming supervisor
    try {
      var res = await apiFetch(CFG.urls.supervisorRun, {
        method: 'POST',
        body: {
          invoice_id: CFG.invoiceId || null,
          upload_id: CFG._uploadId || null,
          reconciliation_result_id: CFG.reconciliationResultId || null,
          case_id: caseId,
          session_id: sid || sessionId || null,
        },
      });
      if (res && !res.error) {
        steps = [];
        if (res.tool_calls && res.tool_calls.length) {
          for (var tc = 0; tc < res.tool_calls.length; tc++) {
            var call = res.tool_calls[tc];
            steps.push({
              kind: 'tool', tool: call.tool_name,
              status: call.status === 'SUCCESS' ? 'done' : 'failed',
              duration_ms: call.duration_ms, output_summary: '',
            });
          }
        }
        steps.push({ kind: 'complete' });
        renderSteps();

        if (IS_CASE && res.summary && typeof res.summary === 'object') {
          appendSummaryCard(res.summary, res.confidence, res.recommendation);
        } else {
          var summary = '';
          if (res.recommendation) summary += 'Recommendation: ' + res.recommendation;
          if (res.confidence) summary += '\nConfidence: ' + Math.round(res.confidence * 100) + '%';
          if (res.summary) summary += '\n\n' + (typeof res.summary === 'string' ? res.summary : res.summary.analysis_text || '');
          if (!summary) summary = 'Supervisor analysis completed.';
          appendMessage('assistant', summary);
        }
        // Refresh page in case mode so header/tabs reflect updated data
        if (IS_CASE) {
          setTimeout(function () {
            var url = new URL(window.location.href);
            url.searchParams.delete('auto_run');
            window.location.href = url.toString();
          }, 2500);
        }
      } else {
        steps.push({ kind: 'error', message: (res && res.error) || 'Unknown error' });
        renderSteps();
      }
    } catch (fallbackErr) {
      steps.push({ kind: 'error', message: fallbackErr.message || 'Connection failed' });
      renderSteps();
    } finally {
      supervisorRunning = false;
    }
  }

  window.runSupervisor = runSupervisor;

  // ==================================================================
  // CASE SEARCH MODAL (plain chat mode)
  // ==================================================================
  function openCaseSearch() {
    if (caseSearchBg) {
      caseSearchBg.classList.remove('d-none');
      if (caseSearchInput) caseSearchInput.focus();
    }
  }

  function closeCaseSearch() {
    if (caseSearchBg) caseSearchBg.classList.add('d-none');
    if (caseSearchInput) caseSearchInput.value = '';
    if (caseSearchResults) {
      caseSearchResults.innerHTML = '<div class="copilot-case-search-empty"><i class="bi bi-briefcase"></i><p class="mb-0">Type to search for a case</p></div>';
    }
  }

  async function performCaseSearch() {
    var q = caseSearchInput.value.trim();
    if (!q || q.length < 2) {
      caseSearchResults.innerHTML = '<div class="copilot-case-search-empty"><i class="bi bi-briefcase"></i><p class="mb-0">Type to search for a case</p></div>';
      return;
    }
    try {
      var data = await apiFetch(CFG.urls.caseSearch + '?q=' + encodeURIComponent(q), { method: 'GET' });
      if (data && data.results && data.results.length) {
        caseSearchResults.innerHTML = data.results.map(function (c) {
          return '<div class="copilot-case-result-item" onclick="selectCase(' + c.id + ', \'' + esc(c.case_number) + '\')">'
            + '<div><span class="badge bg-primary-subtle text-primary-emphasis">' + esc(c.case_number) + '</span></div>'
            + '<div class="flex-1 small">'
            + (c.invoice_number ? esc(c.invoice_number) + ' - ' : '')
            + (c.vendor_name ? esc(c.vendor_name) : '')
            + '</div>'
            + '<div><span class="badge bg-secondary-subtle text-secondary-emphasis">' + esc(c.status || '') + '</span></div>'
            + '</div>';
        }).join('');
      } else {
        caseSearchResults.innerHTML = '<div class="copilot-case-search-empty"><p class="mb-0 text-muted">No cases found</p></div>';
      }
    } catch (err) {
      caseSearchResults.innerHTML = '<div class="copilot-case-search-empty"><p class="mb-0 text-danger">Search failed</p></div>';
    }
  }

  window.selectCase = function (id, number) {
    window.location.href = CFG.urls.caseBase + id + '/';
  };

  // ==================================================================
  // TAB LAZY LOADING (case mode)
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
  // DATA LOADERS (case mode)
  // ==================================================================
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
      console.error('[copilot] loadLineItems failed', err);
    }
  }

  function populateInvoiceLines(evidence) {
    var tbody = document.getElementById('invoiceLineItems');
    if (!tbody) return;
    var lines = [];
    evidence.forEach(function (e) {
      if (e.type === 'invoice' && e.details && e.details.line_items) lines = e.details.line_items;
    });
    if (!lines.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-3">No line items found.</td></tr>';
      return;
    }
    tbody.innerHTML = lines.map(function (li, i) {
      return '<tr>'
        + '<td>' + (li.line_number || (i + 1)) + '</td>'
        + '<td>' + esc(li.description || '-') + '</td>'
        + '<td>' + esc(li.item_code || '-') + '</td>'
        + '<td class="text-end">' + (li.quantity != null ? li.quantity : '-') + '</td>'
        + '<td class="text-end">' + (li.unit_price != null ? li.unit_price : '-') + '</td>'
        + '<td class="text-end">' + (li.tax_amount != null ? li.tax_amount : (li.tax_percentage != null ? li.tax_percentage + '%' : '-')) + '</td>'
        + '<td class="text-end">' + (li.amount != null ? li.amount : '-') + '</td>'
        + '</tr>';
    }).join('');
  }

  function populatePOLines(evidence) {
    var tbody = document.getElementById('poLineItems');
    if (!tbody) return;
    var lines = [];
    evidence.forEach(function (e) {
      if (e.type === 'purchase_order' && e.details && e.details.line_items) lines = e.details.line_items;
    });
    if (!lines.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-3">No PO line items found.</td></tr>';
      return;
    }
    tbody.innerHTML = lines.map(function (li, i) {
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
    evidence.forEach(function (e) {
      if (e.type === 'grn' && e.details && e.details.line_items) lines = e.details.line_items;
    });
    if (!lines.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-3">No GRN data found.</td></tr>';
      return;
    }
    tbody.innerHTML = lines.map(function (li, i) {
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

  window.loadTimeline = async function () {
    var el = document.getElementById('timelineContent');
    if (!el) return;
    el.innerHTML = '<div class="text-center py-4"><div class="spinner-border spinner-border-sm text-primary"></div></div>';
    try {
      var data = await apiFetch(CFG.urls.caseTimeline, { method: 'GET' });
      if (data && data.entries && data.entries.length) {
        el.innerHTML = data.entries.map(function (entry) {
          var iconClass = 'bg-secondary-subtle text-secondary';
          if (entry.category === 'agent_run') iconClass = 'bg-primary-subtle text-primary';
          if (entry.category === 'decision') iconClass = 'bg-warning-subtle text-warning';
          if (entry.category === 'review') iconClass = 'bg-info-subtle text-info';
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

  window.loadGovernance = async function () {
    var el = document.getElementById('governanceContent');
    if (!el) return;
    el.innerHTML = '<div class="text-center py-4"><div class="spinner-border spinner-border-sm text-primary"></div></div>';
    try {
      var data = await apiFetch(CFG.urls.caseGovernance, { method: 'GET' });
      if (data && data.entries && data.entries.length) {
        el.innerHTML = data.entries.map(function (entry) {
          return '<div class="gov-entry">'
            + '<span class="gov-label">' + esc(entry.event_type || entry.label || 'Event') + '</span>'
            + '<span class="gov-value">' + esc(entry.detail || entry.description || '') + '</span>'
            + '</div>';
        }).join('');
      } else if (data && typeof data === 'object') {
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

  async function loadMatchingData() {
    if (!CFG.reconciliationResultId) return;
    try {
      var data = await apiFetch(CFG.urls.caseEvidence, { method: 'GET' });
      if (data) {
        populateHeaderComparison(data);
        populateLineMatching(data);
      }
    } catch (err) {
      console.error('[copilot] loadMatchingData failed', err);
    }
  }

  function populateHeaderComparison(data) {
    var tbody = document.getElementById('headerComparison');
    if (!tbody) return;
    var evidence = data.evidence || [];
    var invData = null, poData = null;
    evidence.forEach(function (e) {
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
    tbody.innerHTML = fields.map(function (f) {
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
    var evidence = data.evidence || [];
    var lineMatches = [];
    evidence.forEach(function (e) {
      if (e.type === 'decision' && e.details && e.details.line_matches) lineMatches = e.details.line_matches;
    });
    if (countEl) countEl.textContent = lineMatches.length + ' lines';
    if (!lineMatches.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted py-3">No line matching data available.</td></tr>';
      return;
    }
    tbody.innerHTML = lineMatches.map(function (m) {
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

  async function loadPOGRNData() {
    if (!CFG.invoiceId) return;
    loadLineItems();
  }

  // ==================================================================
  // CASE ACTIONS (case mode)
  // ==================================================================
  window.caseAction = async function (action) {
    if (!confirm('Are you sure you want to ' + action.replace('_', ' ') + ' this case?')) return;
    try {
      var res = await apiFetch(CFG.urls.caseAction, {
        method: 'POST',
        body: { action: action },
      });
      if (res && res.success) {
        appendMessage('system', 'Action "' + action + '" completed successfully.');
        setTimeout(function () { window.location.reload(); }, 1500);
      } else {
        appendMessage('system', 'Action failed: ' + (res.error || 'Unknown error'));
      }
    } catch (err) {
      appendMessage('system', 'Action failed: ' + ((err && err.message) || 'Unknown'));
    }
  };

  window.downloadInvoice = function (invoiceId) {
    window.open('/api/v1/documents/invoices/' + invoiceId + '/download/', '_blank');
  };

  window.exportReport = function () {
    appendMessage('system', 'Export functionality coming soon.');
  };

  // ==================================================================
  // RICH RESPONSE RENDERING
  // ==================================================================
  function miniMarkdown(text) {
    if (!text) return '';
    var lines = String(text).split('\n');
    var html = '';
    var inList = false;
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (!line.trim()) { if (inList) { html += '</ul>'; inList = false; } continue; }
      if (/^\s*[-*]\s+/.test(line)) {
        if (!inList) { html += '<ul class="chat-md-list">'; inList = true; }
        html += '<li>' + mdInline(line.replace(/^\s*[-*]\s+/, '')) + '</li>';
        continue;
      }
      if (inList) { html += '</ul>'; inList = false; }
      if (/^###\s+/.test(line)) { html += '<div class="chat-md-h3">' + mdInline(line.replace(/^###\s+/, '')) + '</div>'; continue; }
      if (/^##\s+/.test(line)) { html += '<div class="chat-md-h2">' + mdInline(line.replace(/^##\s+/, '')) + '</div>'; continue; }
      html += '<div class="chat-md-p">' + mdInline(line) + '</div>';
    }
    if (inList) html += '</ul>';
    return html;
  }

  function mdInline(text) {
    var s = esc(text);
    s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/`(.+?)`/g, '<code>$1</code>');
    return s;
  }

  function appendRichResponse(response) {
    hideWelcome();

    if (typeof response === 'string') {
      appendRichMessage(response, [], []);
      return;
    }

    var summary = response.summary || response.answer || response.text || '';
    var evidence = response.evidence || [];
    var followUps = response.follow_up_prompts || [];
    var toolDetails = response.tool_details || [];

    if (!summary && !evidence.length) {
      appendMessage('assistant', JSON.stringify(response));
      return;
    }

    appendRichMessage(summary, evidence, followUps, toolDetails);
  }

  function appendRichMessage(summary, evidence, followUps, toolDetails) {
    var row = document.createElement('div');
    row.className = 'copilot-msg';

    var avatar = document.createElement('div');
    avatar.className = 'copilot-msg-avatar copilot-msg-avatar-ai';
    avatar.innerHTML = '<i class="bi bi-stars"></i>';

    var body = document.createElement('div');
    body.className = 'copilot-msg-body';

    var role = document.createElement('div');
    role.className = 'copilot-msg-role';
    role.textContent = 'AP Copilot';

    var html = '<div class="chat-rich-response">';

    if (summary) {
      html += '<div class="chat-rich-summary">' + miniMarkdown(summary) + '</div>';
    }

    if (evidence.length) {
      html += buildEvidenceHTML(evidence);
    }

    if (toolDetails && toolDetails.length) {
      html += buildToolDetailsHTML(toolDetails);
    }

    if (followUps.length) {
      html += buildFollowUpHTML(followUps);
    }

    html += '</div>';

    var msgText = document.createElement('div');
    msgText.className = 'copilot-msg-text';
    msgText.innerHTML = html;

    body.appendChild(role);
    body.appendChild(msgText);
    row.appendChild(avatar);
    row.appendChild(body);
    chatMessages.appendChild(row);
    scrollToBottom();
  }

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
