/**
 * AP Copilot -- Unified ChatGPT-style Controller
 * Handles: sessions, chat, case linking, sidebar, supervisor agent
 */
(function () {
  'use strict';

  // ── Configuration ──
  var cfgEl = document.getElementById('copilotConfig');
  if (!cfgEl) return;
  var CFG = JSON.parse(cfgEl.textContent);

  // ── State ──
  var sessionId = CFG.sessionId;
  var caseId = CFG.caseId;
  var isSending = false;
  var supervisorRunning = false;

  // ── DOM ──
  var sidebar       = document.getElementById('copilotSidebar');
  var chatMessages  = document.getElementById('chatMessages');
  var chatInput     = document.getElementById('chatInput');
  var chatForm      = document.getElementById('chatForm');
  var btnSend       = document.getElementById('btnSend');
  var welcomeState  = document.getElementById('welcomeState');
  var topbarTitle   = document.getElementById('topbarTitle');

  // Sidebar controls
  var btnCollapse   = document.getElementById('btnCollapseSidebar');
  var btnExpand     = document.getElementById('btnExpandSidebar');
  var btnNewChat    = document.getElementById('btnNewChat');
  var sidebarSearch = document.getElementById('sidebarSearch');

  // Case search
  var btnLinkCase       = document.getElementById('btnLinkCase');
  var caseSearchBg      = document.getElementById('caseSearchBackdrop');
  var caseSearchInput   = document.getElementById('caseSearchInput');
  var caseSearchResults = document.getElementById('caseSearchResults');
  var btnCloseCaseSearch= document.getElementById('btnCloseCaseSearch');

  // Upload (attach button in chat input bar)
  var btnAttachFile  = document.getElementById('btnAttachFile');
  var invoiceFileInput= document.getElementById('invoiceFileInput');

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

    // Sidebar
    if (btnCollapse) btnCollapse.addEventListener('click', toggleSidebar);
    if (btnExpand) btnExpand.addEventListener('click', toggleSidebar);
    if (btnNewChat) btnNewChat.addEventListener('click', startNewChat);
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

    // Upload attach button
    if (btnAttachFile) btnAttachFile.addEventListener('click', function () { invoiceFileInput.click(); });
    if (invoiceFileInput) invoiceFileInput.addEventListener('change', onFileSelected);

    // Focus chat input
    if (chatInput) chatInput.focus();
    scrollToBottom();
  }

  // ==================================================================
  // SIDEBAR
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
    // Navigate to base hub (no session)
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
        // Update URL without full reload
        var newUrl = CFG.urls.sessionBase + sessionId + '/';
        window.history.replaceState({}, '', newUrl);
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

    // Check for supervisor trigger
    if (text.toLowerCase().indexOf('run supervisor') !== -1 ||
        text.toLowerCase().indexOf('supervisor agent') !== -1) {
      chatInput.value = '';
      chatInput.style.height = 'auto';
      btnSend.disabled = true;
      runSupervisor(text);
      return;
    }

    var sid = await ensureSession();
    if (!sid) return;

    // Hide welcome
    if (welcomeState) welcomeState.style.display = 'none';

    // Render user message
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

  // Quick send from chips
  window.chipSend = function (text) {
    if (chatInput) {
      chatInput.value = text;
      chatForm.dispatchEvent(new Event('submit'));
    }
  };

  // ==================================================================
  // MESSAGE RENDERING
  // ==================================================================
  function appendMessage(type, text) {
    // Remove welcome if still visible
    if (welcomeState && welcomeState.parentNode) {
      welcomeState.style.display = 'none';
    }

    var row = document.createElement('div');
    row.className = 'copilot-msg';
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
    if (welcomeState) welcomeState.style.display = 'none';
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

    // Show file attachment as user message
    appendMessage('user', 'Uploaded invoice: ' + file.name);

    // Create progress message in chat
    var prog = appendProgressMessage('cloud-arrow-up', 'Processing Invoice');
    updateProgressSteps(prog.stepsContainer, [{ label: 'Uploading document...', done: false }]);

    // Upload via FormData
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
          scrollToBottom();
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
  // SUPERVISOR AGENT (with progress in chat)
  // ==================================================================
  async function runSupervisor(promptText) {
    if (supervisorRunning) return;
    supervisorRunning = true;

    var sid = await ensureSession();
    if (!sid) { supervisorRunning = false; return; }

    if (welcomeState) welcomeState.style.display = 'none';
    if (promptText) appendMessage('user', promptText);

    // Show progress message in chat
    var prog = appendProgressMessage('cpu', 'Supervisor Agent');
    updateProgressSteps(prog.stepsContainer, [
      { label: 'Starting supervisor agent...', done: false },
    ]);

    try {
      var res = await apiFetch(CFG.urls.supervisorRun, {
        method: 'POST',
        body: {
          invoice_id: null,
          reconciliation_result_id: null,
          case_id: caseId,
        },
      });

      if (res && !res.error) {
        // Build completed steps from tool_calls if available
        var steps = [];
        if (res.tool_calls && res.tool_calls.length) {
          for (var i = 0; i < res.tool_calls.length; i++) {
            var tc = res.tool_calls[i];
            var stepLabel = tc.tool_name.replace(/_/g, ' ');
            if (tc.status === 'SUCCESS') stepLabel += ' -- done';
            else if (tc.status === 'FAILED') stepLabel += ' -- failed';
            steps.push({
              label: stepLabel,
              done: true,
              failed: tc.status === 'FAILED',
            });
          }
        }
        steps.push({ label: 'Analysis complete', done: true });
        updateProgressSteps(prog.stepsContainer, steps);

        // Show summary as chat message
        var summary = '';
        if (res.recommendation) summary += 'Recommendation: ' + res.recommendation;
        if (res.confidence) summary += '\nConfidence: ' + Math.round(res.confidence * 100) + '%';
        if (res.summary) summary += '\n\n' + res.summary;
        if (!summary) summary = 'Supervisor analysis completed.';
        appendMessage('assistant', summary);
      } else {
        updateProgressSteps(prog.stepsContainer, [
          { label: 'Supervisor failed: ' + (res.error || 'Unknown error'), done: true, failed: true },
        ]);
      }
    } catch (err) {
      updateProgressSteps(prog.stepsContainer, [
        { label: 'Supervisor error: ' + ((err && err.message) || 'Unknown'), done: true, failed: true },
      ]);
    } finally {
      supervisorRunning = false;
    }
  }

  window.runSupervisor = runSupervisor;

  // ==================================================================
  // CASE SEARCH MODAL
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
    // Navigate to case-linked session
    window.location.href = CFG.urls.caseBase + id + '/';
  };

  // ==================================================================
  // RICH RESPONSE RENDERING
  // ==================================================================

  /** Convert a markdown-like string to safe HTML. */
  function miniMarkdown(text) {
    if (!text) return '';
    var lines = text.split('\n');
    var html = '';
    var inList = false;
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (!line.trim()) { if (inList) { html += '</ul>'; inList = false; } continue; }
      if (/^[-*]\s+/.test(line)) {
        if (!inList) { html += '<ul class="chat-md-list">'; inList = true; }
        html += '<li>' + mdInline(line.replace(/^[-*]\s+/, '')) + '</li>';
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

  /**
   * Render a structured copilot response (summary + evidence + follow-up).
   */
  function appendRichResponse(response) {
    if (welcomeState) welcomeState.style.display = 'none';

    // Plain string fallback
    if (typeof response === 'string') {
      appendRichMessage(response, [], []);
      return;
    }

    var summary = response.summary || response.answer || response.text || '';
    var evidence = response.evidence || [];
    var followUps = response.follow_up_prompts || [];

    if (!summary && !evidence.length) {
      appendMessage('assistant', JSON.stringify(response));
      return;
    }

    appendRichMessage(summary, evidence, followUps);
  }

  function appendRichMessage(summary, evidence, followUps) {
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

    // Summary with markdown
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

    // Follow-up chips
    if (followUps.length) {
      html += '<div class="chat-followup-row">';
      for (var f = 0; f < followUps.length; f++) {
        html += '<button class="chat-followup-chip" onclick="chipSend(\'' + esc(followUps[f]).replace(/'/g, "\\'") + '\')"><i class="bi bi-arrow-return-right me-1"></i>' + esc(followUps[f]) + '</button>';
      }
      html += '</div>';
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
