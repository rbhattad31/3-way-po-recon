/**
 * AP Copilot Workspace — Client-side logic
 * Handles chat interaction, session management, and structured response rendering.
 */
(function () {
  'use strict';

  // ── Configuration ──
  const configEl = document.getElementById('copilotConfig');
  if (!configEl) return;
  const CONFIG = JSON.parse(configEl.textContent);

  // ── DOM References ──
  const chatMessages = document.getElementById('chatMessages');
  const chatInput    = document.getElementById('chatInput');
  const chatForm     = document.getElementById('chatForm');
  const btnSend      = document.getElementById('btnSend');
  const welcomeState = document.getElementById('welcomeState');
  const contextPanel = document.getElementById('copilotContext');
  const sidebar      = document.getElementById('copilotSidebar');

  let currentSessionId = CONFIG.sessionId;
  let currentCaseId    = CONFIG.caseId;
  let isSending        = false;
  let pendingFile      = null; // File object selected for upload

  // ── Initialisation ──
  function init() {
    if (chatForm)  chatForm.addEventListener('submit', onSubmit);
    if (chatInput) {
      chatInput.addEventListener('input', onInputChange);
      chatInput.addEventListener('keydown', onKeyDown);
    }

    const btnNew = document.getElementById('btnNewConversation');
    if (btnNew) btnNew.addEventListener('click', function(e) {
      e.preventDefault();
      window.location.href = CONFIG.urls.workspaceBase;
    });

    const mainSidebar = document.getElementById('sidebar');
    const btnToggleMainMenu = document.getElementById('btnToggleMainMenu');
    if (btnToggleMainMenu && mainSidebar) {
      btnToggleMainMenu.addEventListener('click', () => mainSidebar.classList.toggle('show'));
      // Close main menu when clicking outside
      document.addEventListener('click', (e) => {
        if (mainSidebar.classList.contains('show') &&
            !mainSidebar.contains(e.target) &&
            !btnToggleMainMenu.contains(e.target)) {
          mainSidebar.classList.remove('show');
        }
      });
    }

    const btnToggleCtx = document.getElementById('btnToggleContext');
    if (btnToggleCtx) btnToggleCtx.addEventListener('click', () => contextPanel.classList.toggle('collapsed'));

    const btnCloseCtx = document.getElementById('btnCloseContext');
    if (btnCloseCtx) btnCloseCtx.addEventListener('click', () => contextPanel.classList.add('collapsed'));

    const searchInput = document.getElementById('sidebarSearch');
    if (searchInput) searchInput.addEventListener('input', onSidebarSearch);

    // Upload button & file input
    const btnUpload = document.getElementById('btnUpload');
    const fileInput = document.getElementById('fileInput');
    if (btnUpload && fileInput) {
      btnUpload.addEventListener('click', () => fileInput.click());
      fileInput.addEventListener('change', onFileSelected);
    }
    const btnRemoveFile = document.getElementById('btnRemoveFile');
    if (btnRemoveFile) btnRemoveFile.addEventListener('click', clearPendingFile);

    scrollChatToBottom();
    if (currentCaseId && !contextPanel.classList.contains('collapsed')) {
      loadCaseContext(currentCaseId);
    }
  }

  // ── Input Handling ──
  function onInputChange() {
    btnSend.disabled = !chatInput.value.trim() && !pendingFile;
    chatInput.style.height = 'auto';
    chatInput.style.height = Math.min(chatInput.scrollHeight, 120) + 'px';
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (chatInput.value.trim()) chatForm.dispatchEvent(new Event('submit'));
    }
  }

  // ── Session Management ──
  async function startSession(caseId) {
    const body = {};
    const cid = typeof caseId === 'number' ? caseId : currentCaseId;
    if (cid) body.case_id = cid;

    try {
      const res = await apiFetch(CONFIG.urls.sessionStart, { method: 'POST', body });
      if (res && res.id) {
        window.location.href = CONFIG.urls.sessionBase + res.id + '/';
      }
    } catch (err) {
      console.error('Failed to start session:', err);
    }
  }

  // ── Chat ──
  async function ensureSession() {
    // If we already have a valid session, return it
    if (currentSessionId) {
      console.log('[copilot] ensureSession: reusing', currentSessionId);
      return currentSessionId;
    }
    var body = {};
    if (currentCaseId) body.case_id = currentCaseId;
    console.log('[copilot] ensureSession: creating new session...');
    try {
      var session = await apiFetch(CONFIG.urls.sessionStart, { method: 'POST', body: body });
      if (!session || !session.id) {
        console.error('[copilot] ensureSession: no id in response', session);
        return null;
      }
      currentSessionId = session.id;
      console.log('[copilot] ensureSession: created', currentSessionId);
      loadRecentConversations();
      return currentSessionId;
    } catch (err) {
      console.error('[copilot] ensureSession: failed', err);
      return null;
    }
  }

  async function onSubmit(e) {
    e.preventDefault();
    var text = chatInput.value.trim();
    if ((!text && !pendingFile) || isSending) return;

    // If a file is pending, handle upload flow instead of chat
    if (pendingFile) {
      await handleUpload(text);
      return;
    }

    // Ensure we have a session
    var sid = await ensureSession();
    if (!sid) return;

    // Hide welcome state
    if (welcomeState) welcomeState.style.display = 'none';

    // Render user message
    renderUserMessage(text);
    chatInput.value = '';
    chatInput.style.height = 'auto';
    btnSend.disabled = true;

    // Show thinking indicator
    var thinkingEl = showThinking();
    isSending = true;

    try {
      var res = await sendMessage(sid, text);
      removeThinking(thinkingEl);
      if (res && res.response) {
        renderAssistantMessage(res.response);
      } else {
        renderSystemMessage('No response received. The case may not be linked to this session.');
      }
    } catch (err) {
      // If session was deleted (404), recreate and retry once
      if (err && err.message && err.message.indexOf('404') !== -1) {
        console.warn('Session expired, creating a new one...');
        currentSessionId = null;
        try {
          var newSid = await ensureSession();
          if (newSid) {
            var retryRes = await sendMessage(newSid, text);
            removeThinking(thinkingEl);
            if (retryRes && retryRes.response) {
              renderAssistantMessage(retryRes.response);
            } else {
              renderSystemMessage('No response received. The case may not be linked to this session.');
            }
            isSending = false;
            return;
          }
        } catch (retryErr) {
          console.error('Retry after session recreation failed:', retryErr);
        }
      }
      removeThinking(thinkingEl);
      var errDetail = (err && err.message) ? err.message : 'Unknown error';
      renderSystemMessage('Something went wrong: ' + errDetail);
      console.error('Chat error:', err);
    } finally {
      isSending = false;
    }
  }

  async function sendMessage(sessionId, message) {
    console.log('[copilot] sendMessage: session_id=', sessionId, 'case_id=', currentCaseId);
    return apiFetch(CONFIG.urls.chat, {
      method: 'POST',
      body: { session_id: sessionId, message: message, case_id: currentCaseId },
    });
  }

  // ── File Upload Handling ──
  function onFileSelected(e) {
    const file = e.target.files[0];
    if (!file) return;

    const allowed = ['application/pdf', 'image/png', 'image/jpeg', 'image/tiff'];
    if (!allowed.includes(file.type)) {
      renderSystemMessage('Unsupported file type. Please upload a PDF, PNG, JPG, or TIFF.');
      e.target.value = '';
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      renderSystemMessage('File too large. Maximum size is 20 MB.');
      e.target.value = '';
      return;
    }

    pendingFile = file;
    const preview = document.getElementById('filePreview');
    const nameEl = document.getElementById('filePreviewName');
    const sizeEl = document.getElementById('filePreviewSize');
    if (preview && nameEl && sizeEl) {
      nameEl.textContent = file.name;
      sizeEl.textContent = formatBytes(file.size);
      preview.classList.remove('d-none');
    }
    btnSend.disabled = false;
  }

  function clearPendingFile() {
    pendingFile = null;
    const preview = document.getElementById('filePreview');
    if (preview) preview.classList.add('d-none');
    const fileInput = document.getElementById('fileInput');
    if (fileInput) fileInput.value = '';
    onInputChange();
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }

  async function handleUpload(optionalText) {
    if (!pendingFile || isSending) return;
    isSending = true;

    // Hide welcome state
    if (welcomeState) welcomeState.style.display = 'none';

    // Ensure we have a session
    var sid = await ensureSession();
    if (!sid) { isSending = false; return; }

    // Show user message with file attachment
    const userText = optionalText
      ? 'Upload: ' + pendingFile.name + ' -- ' + optionalText
      : 'Upload: ' + pendingFile.name;
    renderUserMessage(userText);

    chatInput.value = '';
    chatInput.style.height = 'auto';
    btnSend.disabled = true;

    // Create the progress message container
    const progressEl = createProgressMessage();
    const file = pendingFile;
    clearPendingFile();

    try {
      // POST the file -- returns immediately with upload_id
      const formData = new FormData();
      formData.append('file', file);

      const response = await fetch(CONFIG.urls.upload, {
        method: 'POST',
        headers: { 'X-CSRFToken': CONFIG.csrfToken },
        credentials: 'same-origin',
        body: formData,
      });

      if (!response.ok) {
        let errMsg = 'Upload failed (HTTP ' + response.status + ')';
        try {
          const errData = await response.json();
          if (errData.error) errMsg = errData.error;
        } catch (_) {}
        updateThinkingLog(progressEl, [
          { label: 'Upload failed: ' + errMsg, done: true, failed: true }
        ], true);
        finalizeThinking(progressEl, [{ label: errMsg, done: true, failed: true }]);
        isSending = false;
        return;
      }

      const data = await response.json();
      const uploadId = data.upload_id;

      // Start polling for progressive status
      updateThinkingLog(progressEl, [{ label: 'Document received', done: true }], false);
      await pollUploadStatus(uploadId, progressEl);

    } catch (err) {
      updateThinkingLog(progressEl, [
        { label: 'Upload failed. Please try again.', done: true, failed: true }
      ], true);
      finalizeThinking(progressEl, [{ label: 'Upload failed', done: true, failed: true }]);
      console.error('Upload error:', err);
    } finally {
      isSending = false;
    }
  }

  // ── ChatGPT-style Thinking UI ──

  async function pollUploadStatus(uploadId, thinkingEl) {
    const statusUrl = CONFIG.urls.upload + uploadId + '/status/';
    const seenLabels = new Set(['Document received']);
    let accumulated = [{ label: 'Document received', done: true }];

    for (let i = 0; i < 300; i++) { // max ~4 min at 800ms
      await sleep(800);

      try {
        const res = await apiFetch(statusUrl);
        if (!res || !res.steps) continue;

        // Merge response steps into accumulated list
        const newLabels = new Set(res.steps.map(function(s) { return s.label; }));

        // Mark disappeared active steps as done
        for (let j = 0; j < accumulated.length; j++) {
          var st = accumulated[j];
          if (!st.done && !st.failed && !newLabels.has(st.label)) {
            st.done = true;
          }
        }

        // Add or update steps from response
        for (let j = 0; j < res.steps.length; j++) {
          var step = res.steps[j];
          if (seenLabels.has(step.label)) {
            var existing = accumulated.find(function(s) { return s.label === step.label; });
            if (existing) {
              existing.done = step.done;
              existing.failed = step.failed;
            }
          } else {
            seenLabels.add(step.label);
            accumulated.push({ label: step.label, done: step.done, failed: !!step.failed });
          }
        }

        updateThinkingLog(thinkingEl, accumulated, res.completed);

        if (res.completed) {
          // Mark all remaining active steps as done
          accumulated.forEach(function(s) { if (!s.failed) s.done = true; });
          updateThinkingLog(thinkingEl, accumulated, true);
          finalizeThinking(thinkingEl, accumulated);

          // Link case to session if available
          if (res.case_id) {
            currentCaseId = res.case_id;
            var badge = document.getElementById('caseBadge');
            if (badge) {
              badge.textContent = 'Case ' + (res.case_number || '#' + res.case_id);
              badge.classList.remove('d-none');
            }
            var label = document.getElementById('linkCaseLabel');
            if (label) label.textContent = 'Change Case';

            // Ensure a valid session exists before linking
            console.log('[copilot] link_case: case_id=', res.case_id, 'currentSessionId=', currentSessionId);
            try {
              var sid = await ensureSession();
              console.log('[copilot] link_case: ensureSession returned', sid);
              if (sid) {
                await apiFetch('/api/v1/copilot/session/' + sid + '/', {
                  method: 'PATCH',
                  body: { action: 'link_case', case_id: res.case_id },
                });
                console.log('[copilot] link_case: success');
              }
            } catch (linkErr) {
              console.error('[copilot] link_case: failed', linkErr);
              // If session expired mid-upload, recreate and retry link
              if (linkErr && linkErr.message && linkErr.message.indexOf('404') !== -1) {
                currentSessionId = null;
                try {
                  var newSid = await ensureSession();
                  if (newSid) {
                    await apiFetch('/api/v1/copilot/session/' + newSid + '/', {
                      method: 'PATCH',
                      body: { action: 'link_case', case_id: res.case_id },
                    });
                  }
                } catch (_) {}
              }
            }
            loadCaseContext(res.case_id);
            if (contextPanel) contextPanel.classList.remove('collapsed');
          }

          // Refresh sidebar to show the new session
          loadRecentConversations();
          renderUploadFollowUps(thinkingEl, res);
          return;
        }
      } catch (err) {
        console.warn('Status poll failed:', err);
      }
    }
    // Timed out
    accumulated.push({ label: 'Processing is taking longer than expected. Refresh to check status.', done: true });
    updateThinkingLog(thinkingEl, accumulated, false);
  }

  function sleep(ms) { return new Promise(function(r) { return setTimeout(r, ms); }); }

  function createProgressMessage() {
    const html =
      '<div class="copilot-msg copilot-msg-assistant">' +
        '<div class="copilot-msg-avatar copilot-msg-avatar-ai"><i class="bi bi-stars"></i></div>' +
        '<div class="copilot-msg-body">' +
          '<div class="copilot-thinking-block" data-start-time="' + Date.now() + '">' +
            '<div class="copilot-thinking-header">' +
              '<span class="copilot-thinking-icon"><i class="bi bi-stars"></i></span>' +
              '<span class="copilot-thinking-title">Analyzing your invoice...</span>' +
              '<span class="copilot-thinking-timer"></span>' +
              '<span class="copilot-thinking-toggle"><i class="bi bi-chevron-down"></i></span>' +
            '</div>' +
            '<div class="copilot-thinking-log"></div>' +
          '</div>' +
          '<div class="copilot-progress-followups"></div>' +
        '</div>' +
      '</div>';
    chatMessages.insertAdjacentHTML('beforeend', html);
    scrollChatToBottom();

    const el = chatMessages.lastElementChild;
    const block = el.querySelector('.copilot-thinking-block');
    const timerEl = block.querySelector('.copilot-thinking-timer');
    const hdr = block.querySelector('.copilot-thinking-header');
    const startTime = Date.now();

    // Elapsed timer
    const timerInterval = setInterval(function() {
      var elapsed = Math.round((Date.now() - startTime) / 1000);
      timerEl.textContent = elapsed + 's';
    }, 1000);
    block._timerInterval = timerInterval;
    block._startTime = startTime;

    // Toggle collapse on header click
    hdr.addEventListener('click', function() {
      block.classList.toggle('collapsed');
    });

    return el;
  }

  function updateThinkingLog(el, steps, completed) {
    const block = el.querySelector('.copilot-thinking-block');
    if (!block) return;
    const log = block.querySelector('.copilot-thinking-log');
    const titleEl = block.querySelector('.copilot-thinking-title');
    if (!log) return;

    const existingSteps = log.querySelectorAll('.copilot-thinking-step');
    const existingCount = existingSteps.length;

    // Update existing steps (mark done/failed)
    for (let i = 0; i < Math.min(existingCount, steps.length); i++) {
      var stepEl = existingSteps[i];
      var s = steps[i];
      if (s.done && stepEl.classList.contains('step-current')) {
        stepEl.classList.remove('step-current');
        stepEl.classList.add('step-done');
        var dot = stepEl.querySelector('.copilot-td');
        if (dot) dot.innerHTML = '<i class="bi bi-check2"></i>';
        var txt = stepEl.querySelector('.copilot-step-text');
        if (txt) txt.classList.remove('copilot-shimmer');
      }
      if (s.failed && !stepEl.classList.contains('step-failed')) {
        stepEl.classList.remove('step-current', 'step-done');
        stepEl.classList.add('step-failed');
        var dot2 = stepEl.querySelector('.copilot-td');
        if (dot2) dot2.innerHTML = '<i class="bi bi-x-circle-fill"></i>';
      }
    }

    // Append new steps
    var added = false;
    for (let i = existingCount; i < steps.length; i++) {
      var step = steps[i];
      var div = document.createElement('div');
      var cls = step.failed ? 'step-failed' : step.done ? 'step-done' : 'step-current';
      div.className = 'copilot-thinking-step ' + cls;

      var dotHtml, textCls;
      if (step.failed) {
        dotHtml = '<i class="bi bi-x-circle-fill"></i>';
        textCls = '';
      } else if (step.done) {
        dotHtml = '<i class="bi bi-check2"></i>';
        textCls = '';
      } else {
        dotHtml = '<span class="copilot-pulse-dot"></span>';
        textCls = ' copilot-shimmer';
      }

      div.innerHTML = '<span class="copilot-td">' + dotHtml + '</span>' +
                      '<span class="copilot-step-text' + textCls + '">' + escapeHtml(step.label) + '</span>';
      log.appendChild(div);
      added = true;
    }

    // Update header title with current action
    if (!completed) {
      var activeStep = null;
      for (let i = steps.length - 1; i >= 0; i--) {
        if (!steps[i].done && !steps[i].failed) { activeStep = steps[i]; break; }
      }
      if (activeStep) {
        titleEl.textContent = activeStep.label;
      }
    }

    if (added) scrollChatToBottom();
  }

  function finalizeThinking(el, steps) {
    const block = el.querySelector('.copilot-thinking-block');
    if (!block) return;

    // Stop timer
    if (block._timerInterval) clearInterval(block._timerInterval);
    var elapsed = Math.round((Date.now() - (block._startTime || Date.now())) / 1000);
    var stepCount = steps.filter(function(s) { return s.done; }).length;

    var titleEl = block.querySelector('.copilot-thinking-title');
    var timerEl = block.querySelector('.copilot-thinking-timer');

    titleEl.textContent = 'Analyzed in ' + elapsed + 's';
    timerEl.textContent = stepCount + ' steps';

    block.classList.add('done');

    // Collapse after a brief pause
    setTimeout(function() {
      block.classList.add('collapsed');
    }, 1200);
  }

  function renderUploadFollowUps(thinkingEl, data) {
    const container = thinkingEl.querySelector('.copilot-progress-followups');
    if (!container) return;

    const prompts = [];
    if (data.case_id) {
      prompts.push('Show me the case summary');
      prompts.push('What exceptions were found?');
    }
    if (data.invoice_status === 'PENDING_APPROVAL') {
      prompts.push('What fields need review?');
    }
    if (data.match_status && data.match_status !== 'MATCHED') {
      prompts.push('Why is it a ' + data.match_status.replace(/_/g, ' ').toLowerCase() + '?');
    }
    if (prompts.length) {
      container.innerHTML = renderFollowUpPrompts(prompts);
    }
    scrollChatToBottom();
  }

  // ── Rendering ──
  function renderUserMessage(text) {
    const html = `
      <div class="copilot-msg copilot-msg-user">
        <div class="copilot-msg-avatar"><i class="bi bi-person-fill"></i></div>
        <div class="copilot-msg-body">
          <div class="copilot-msg-content">${escapeHtml(text)}</div>
          <div class="copilot-msg-time text-muted small">Just now</div>
        </div>
      </div>`;
    chatMessages.insertAdjacentHTML('beforeend', html);
    scrollChatToBottom();
  }

  function renderAssistantMessage(payload) {
    const parts = [];

    // Summary
    const summary = payload.summary || 'No response generated.';
    parts.push(`<div class="copilot-msg-content">${formatMarkdown(summary)}</div>`);

    // Collect detail sub-sections
    const detailParts = [];

    // Evidence cards
    if (payload.evidence && payload.evidence.length) {
      detailParts.push(renderEvidenceCards(payload.evidence));
    }

    // Consulted agents
    if (payload.consulted_agents && payload.consulted_agents.length) {
      const chips = payload.consulted_agents.map(a => `<span class="copilot-agent-chip">${escapeHtml(a)}</span>`).join('');
      detailParts.push(`
        <div class="copilot-detail-sub">
          <div class="copilot-detail-sub-label"><i class="bi bi-robot me-1"></i>Consulted Agents (${payload.consulted_agents.length})</div>
          <div class="copilot-agent-chips">${chips}</div>
        </div>`);
    }

    // Recommendation
    if (payload.recommendation) {
      const rec = payload.recommendation;
      const conf = rec.confidence != null ? `${Math.round(rec.confidence * 100)}%` : '';
      detailParts.push(`
        <div class="copilot-detail-sub copilot-recommendation-block">
          <div class="copilot-detail-sub-label"><i class="bi bi-lightbulb me-1"></i>Recommendation ${conf ? `(${conf})` : ''}</div>
          <div class="small copilot-recommendation-text">${escapeHtml(rec.text || '')}</div>
          <div class="copilot-recommendation-readonly">Read-only guidance — no action taken</div>
        </div>`);
    }

    // Governance
    if (payload.governance && payload.governance.permitted) {
      detailParts.push(renderGovernanceBlock(payload.governance));
    }

    // Wrap all detail sections in one master collapsible panel
    if (detailParts.length) {
      parts.push(`
        <div class="copilot-details-panel mt-3">
          <button type="button" class="copilot-details-toggle" onclick="this.classList.toggle('expanded');this.nextElementSibling.classList.toggle('show')">
            <i class="bi bi-chevron-right"></i>Details
          </button>
          <div class="copilot-details-body">
            ${detailParts.join('\n')}
          </div>
        </div>`);
    }

    // Follow-up prompts
    if (payload.follow_up_prompts && payload.follow_up_prompts.length) {
      parts.push(renderFollowUpPrompts(payload.follow_up_prompts));
    }

    const html = `
      <div class="copilot-msg copilot-msg-assistant">
        <div class="copilot-msg-avatar copilot-msg-avatar-ai"><i class="bi bi-stars"></i></div>
        <div class="copilot-msg-body">
          ${parts.join('\n')}
          <div class="copilot-msg-time text-muted small">Just now</div>
        </div>
      </div>`;
    chatMessages.insertAdjacentHTML('beforeend', html);
    scrollChatToBottom();
  }

  function renderEvidenceCards(evidence) {
    const cards = evidence.map(ev => {
      const details = ev.data ? Object.entries(ev.data)
        .filter(([, v]) => v != null)
        .map(([k, v]) => `<div class="copilot-evidence-detail"><strong>${escapeHtml(k)}:</strong> ${escapeHtml(String(v))}</div>`)
        .join('') : '';
      return `
        <div class="copilot-evidence-card copilot-evidence-${escapeHtml(ev.type || '')}">
          <div class="copilot-evidence-label">${escapeHtml(ev.label || '')}</div>
          <div class="copilot-evidence-type badge bg-secondary-subtle text-secondary-emphasis">${escapeHtml(ev.type || '')}</div>
          ${details}
        </div>`;
    }).join('');

    return `
      <div class="copilot-detail-sub">
        <div class="copilot-detail-sub-label"><i class="bi bi-card-list me-1"></i>Evidence (${evidence.length})</div>
        <div class="copilot-evidence-cards">${cards}</div>
      </div>`;
  }

  function renderGovernanceBlock(gov) {
    if (!gov.events || !gov.events.length) return '';
    const rows = gov.events.slice(0, 8).map(e => `
      <div class="copilot-governance-row">
        <span>${escapeHtml(e.event_type || '')}</span>
        <span class="text-muted">${escapeHtml(e.actor_primary_role || '')}</span>
        <span>${e.access_granted === true ? '✓' : e.access_granted === false ? '✗' : '—'}</span>
      </div>`).join('');

    return `
      <div class="copilot-detail-sub copilot-governance-block">
        <div class="copilot-detail-sub-label"><i class="bi bi-shield-lock me-1"></i>Governance Trace (${gov.events.length})</div>
        ${rows}
      </div>`;
  }

  function renderFollowUpPrompts(prompts) {
    const chips = prompts.map(p =>
      `<button type="button" class="copilot-followup-chip" onclick="useSuggestion(this)">${escapeHtml(p)}</button>`
    ).join('');
    return `<div class="copilot-followups">${chips}</div>`;
  }

  function renderSystemMessage(text) {
    const html = `
      <div class="copilot-msg copilot-msg-system">
        <div class="copilot-msg-content text-muted small text-center">${escapeHtml(text)}</div>
      </div>`;
    chatMessages.insertAdjacentHTML('beforeend', html);
    scrollChatToBottom();
  }

  function showThinking() {
    const html = `
      <div class="copilot-msg copilot-msg-assistant copilot-thinking-container">
        <div class="copilot-msg-avatar copilot-msg-avatar-ai"><i class="bi bi-stars"></i></div>
        <div class="copilot-thinking">
          <div class="copilot-thinking-dots"><span></span><span></span><span></span></div>
          <span>Analysing…</span>
        </div>
      </div>`;
    chatMessages.insertAdjacentHTML('beforeend', html);
    scrollChatToBottom();
    return chatMessages.querySelector('.copilot-thinking-container:last-child');
  }

  function removeThinking(el) {
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  // ── Case Context Panel ──
  async function loadCaseContext(caseId) {
    if (!caseId) return;
    const url = `/api/v1/copilot/case/${caseId}/context/`;
    try {
      const data = await apiFetch(url);
      if (data && !data.error) {
        renderCaseContextPanel(data);
        contextPanel.classList.remove('collapsed');
      }
    } catch (err) {
      console.error('Failed to load case context:', err);
    }
  }

  function renderCaseContextPanel(ctx) {
    const inner = contextPanel.querySelector('.copilot-context-inner') || contextPanel;
    if (!ctx.case) return;

    let html = `
      <div class="copilot-context-header">
        <h6 class="mb-0"><i class="bi bi-briefcase me-2"></i>Case Context</h6>
        <button class="btn btn-sm btn-outline-secondary" onclick="document.getElementById('copilotContext').classList.add('collapsed')">
          <i class="bi bi-x-lg"></i>
        </button>
      </div>`;

    // Case summary
    // Case summary with link to case console
    const caseLink = ctx.case.id
      ? `<a href="/cases/${ctx.case.id}/" target="_blank" class="text-decoration-none">${escapeHtml(ctx.case.case_number)} <i class="bi bi-box-arrow-up-right small"></i></a>`
      : escapeHtml(ctx.case.case_number);
    html += buildCtxCard('Case Summary', [
      ['Case', caseLink],
      ['Status', `<span class="badge bg-primary-subtle text-primary-emphasis">${escapeHtml(ctx.case.status)}</span>`],
      ['Priority', ctx.case.priority],
      ['Path', ctx.case.processing_path],
    ]);

    if (ctx.invoice) {
      const invUrl = ctx.invoice.extraction_result_id
        ? `/extraction/console/${ctx.invoice.extraction_result_id}/`
        : null;
      const invLink = invUrl
        ? `<a href="${invUrl}" target="_blank" class="text-decoration-none">${escapeHtml(ctx.invoice.invoice_number)} <i class="bi bi-box-arrow-up-right small"></i></a>`
        : escapeHtml(ctx.invoice.invoice_number);
      html += buildCtxCard('<i class="bi bi-file-earmark-text me-1"></i>Invoice', [
        ['Number', invLink],
        ['Amount', `${ctx.invoice.currency || ''} ${ctx.invoice.amount || 'N/A'}`],
      ]);
    }

    if (ctx.reconciliation) {
      html += buildCtxCard('<i class="bi bi-check2-square me-1"></i>Reconciliation', [
        ['Match', ctx.reconciliation.match_status],
        ['Mode', ctx.reconciliation.reconciliation_mode],
      ]);
    }

    if (ctx.exceptions && ctx.exceptions.length) {
      const excHtml = ctx.exceptions.map(e =>
        `<div class="copilot-exception-item">
          <span class="badge bg-warning text-dark">${escapeHtml(e.severity)}</span>
          <span class="small">${escapeHtml(e.exception_type)}</span>
        </div>`
      ).join('');
      html += `<div class="copilot-ctx-card">
        <div class="copilot-ctx-card-title"><i class="bi bi-exclamation-triangle me-1"></i>Exceptions (${ctx.exceptions.length})</div>
        ${excHtml}
      </div>`;
    }

    // Actions card
    var sc = ctx.case.status_code || '';
    var reviewable = ['READY_FOR_REVIEW','IN_REVIEW','REVIEW_COMPLETED','READY_FOR_APPROVAL','APPROVAL_IN_PROGRESS'].indexOf(sc) !== -1;
    var retryable  = ['FAILED','ESCALATED','REJECTED'].indexOf(sc) !== -1;
    var closed     = sc === 'CLOSED';

    html += '<div class="copilot-ctx-card"><div class="copilot-ctx-card-title"><i class="bi bi-hand-index-thumb me-1"></i>Actions</div><div class="d-grid gap-2">';
    if (reviewable) {
      html += '<button class="btn btn-success btn-sm" onclick="copilotCaseAction(\'approve\')"><i class="bi bi-check-circle me-1"></i>Approve</button>';
      html += '<button class="btn btn-danger btn-sm" onclick="copilotCaseAction(\'reject\')"><i class="bi bi-x-circle me-1"></i>Reject</button>';
      html += '<button class="btn btn-outline-warning btn-sm" onclick="copilotCaseAction(\'reprocess\')"><i class="bi bi-arrow-clockwise me-1"></i>Reprocess</button>';
      html += '<button class="btn btn-outline-danger btn-sm" onclick="copilotCaseAction(\'escalate\')"><i class="bi bi-exclamation-triangle me-1"></i>Escalate</button>';
    } else if (retryable) {
      html += '<button class="btn btn-outline-warning btn-sm" onclick="copilotCaseAction(\'reprocess\')"><i class="bi bi-arrow-clockwise me-1"></i>Reprocess</button>';
    } else if (closed) {
      html += '<span class="text-muted small text-center"><i class="bi bi-check-circle-fill text-success me-1"></i>Case closed</span>';
    } else {
      html += '<span class="text-muted small text-center"><i class="bi bi-hourglass-split me-1"></i>Processing...</span>';
    }
    html += '<hr class="my-1">';
    html += '<a href="/cases/' + ctx.case.id + '/" class="btn btn-sm btn-outline-primary"><i class="bi bi-display me-1"></i>Open Case Console</a>';
    if (ctx.invoice && ctx.invoice.id) {
      html += '<a href="/documents/invoices/' + ctx.invoice.id + '/" class="btn btn-sm btn-outline-secondary"><i class="bi bi-file-earmark-text me-1"></i>Invoice Detail</a>';
    }
    html += '</div></div>';

    inner.innerHTML = html;
  }

  function buildCtxCard(title, rows) {
    const rowsHtml = rows.map(([label, val]) =>
      `<div class="copilot-ctx-row">
        <span class="copilot-ctx-label">${escapeHtml(label)}</span>
        <span>${typeof val === 'string' && val.includes('<') ? val : escapeHtml(String(val || 'N/A'))}</span>
      </div>`
    ).join('');
    return `<div class="copilot-ctx-card">
      <div class="copilot-ctx-card-title">${title}</div>
      ${rowsHtml}
    </div>`;
  }

  // ── Sidebar Search ──
  function onSidebarSearch(e) {
    const q = e.target.value.toLowerCase();
    document.querySelectorAll('.copilot-conv-item').forEach(el => {
      const text = el.textContent.toLowerCase();
      el.style.display = text.includes(q) ? '' : 'none';
    });
  }

  // ── Sidebar Conversations (reload) ──
  async function loadRecentConversations() {
    try {
      const sessions = await apiFetch(CONFIG.urls.sessions);
      if (!sessions) return;
      const list = document.getElementById('recentList');
      const pinned = document.getElementById('pinnedList');
      if (!list) return;

      let recentHtml = '';
      let pinnedHtml = '';
      sessions.forEach(s => {
        const isActive = currentSessionId && String(s.id) === String(currentSessionId);
        const item = `
          <a href="${CONFIG.urls.sessionBase}${s.id}/"
             class="copilot-conv-item ${isActive ? 'active' : ''}"
             data-session-id="${s.id}">
            <div class="copilot-conv-title">${escapeHtml(s.title || 'Untitled')}</div>
            <div class="copilot-conv-meta">
              ${s.case_number ? `<span class="badge bg-info-subtle text-info-emphasis">${escapeHtml(s.case_number)}</span>` : ''}
            </div>
          </a>`;
        if (s.is_pinned) pinnedHtml += item;
        else recentHtml += item;
      });

      list.innerHTML = recentHtml || '<div class="text-muted small p-3">No conversations yet.</div>';
      if (pinned) {
        pinned.innerHTML = pinnedHtml ? `<div class="copilot-sidebar-label">Pinned</div><div class="copilot-sidebar-list">${pinnedHtml}</div>` : '';
      }
    } catch (err) {
      console.error('Failed to load conversations:', err);
    }
  }

  // ── Suggestions ──
  async function loadSuggestions() {
    try {
      const data = await apiFetch(CONFIG.urls.suggestions);
      if (data && data.suggestions) {
        const container = document.getElementById('suggestedPrompts');
        if (container) {
          container.innerHTML = data.suggestions.map(p =>
            `<button type="button" class="copilot-suggestion-chip" onclick="useSuggestion(this)">
              <i class="bi bi-chat-dots me-1"></i>${escapeHtml(p)}
            </button>`
          ).join('');
        }
      }
    } catch (err) {
      console.error('Failed to load suggestions:', err);
    }
  }

  // ── Pin / Archive ──
  async function togglePin(sessionId) {
    const url = `/api/v1/copilot/session/${sessionId}/`;
    await apiFetch(url, { method: 'PATCH', body: { action: 'pin' } });
    loadRecentConversations();
  }

  // ── Helpers ──
  function scrollChatToBottom() {
    if (chatMessages) {
      requestAnimationFrame(() => {
        chatMessages.scrollTop = chatMessages.scrollHeight;
      });
    }
  }

  function setCurrentCaseScope(caseId) {
    currentCaseId = caseId;
    if (caseId) loadCaseContext(caseId);
  }

  async function apiFetch(url, options = {}) {
    const fetchOptions = {
      method: options.method || 'GET',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': CONFIG.csrfToken,
      },
      credentials: 'same-origin',
    };
    if (options.body) {
      fetchOptions.body = JSON.stringify(options.body);
    }
    const response = await fetch(url, fetchOptions);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function formatMarkdown(text) {
    // Minimal markdown: bold, italic, line breaks
    return escapeHtml(text)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g, '<em>$1</em>')
      .replace(/\n\n/g, '</p><p>')
      .replace(/\n/g, '<br>');
  }

  // ── Case Search Modal ──
  const caseSearchBackdrop = document.getElementById('caseSearchBackdrop');
  const caseSearchInput    = document.getElementById('caseSearchInput');
  const caseSearchResults  = document.getElementById('caseSearchResults');
  let searchDebounce = null;

  function openCaseSearch() {
    if (!caseSearchBackdrop) return;
    caseSearchBackdrop.classList.remove('d-none');
    setTimeout(() => { if (caseSearchInput) caseSearchInput.focus(); }, 100);
  }

  function closeCaseSearch() {
    if (!caseSearchBackdrop) return;
    caseSearchBackdrop.classList.add('d-none');
    if (caseSearchInput) caseSearchInput.value = '';
    if (caseSearchResults) {
      caseSearchResults.innerHTML = '<div class="copilot-case-search-empty"><i class="bi bi-briefcase"></i><p class="mb-0">Type to search for a case</p></div>';
    }
  }

  async function searchCases(query) {
    if (!query.trim()) {
      caseSearchResults.innerHTML = '<div class="copilot-case-search-empty"><i class="bi bi-briefcase"></i><p class="mb-0">Type to search for a case</p></div>';
      return;
    }
    caseSearchResults.innerHTML = '<div class="copilot-case-search-empty"><div class="spinner-border spinner-border-sm text-primary"></div><p class="mb-0">Searching…</p></div>';
    try {
      const data = await apiFetch(CONFIG.urls.caseSearch + '?q=' + encodeURIComponent(query));
      if (!data || !data.results || !data.results.length) {
        caseSearchResults.innerHTML = '<div class="copilot-case-search-empty"><i class="bi bi-inbox"></i><p class="mb-0">No cases found</p></div>';
        return;
      }
      const html = data.results.map(c => `
        <button type="button" class="copilot-case-result" data-case-id="${c.id}">
          <div class="copilot-case-result-main">
            <strong>${escapeHtml(c.case_number)}</strong>
            <span class="badge bg-${c.status === 'CLOSED' ? 'secondary' : c.status === 'ESCALATED' ? 'danger' : 'primary'}-subtle copilot-case-result-status">${escapeHtml(c.status)}</span>
          </div>
          <div class="copilot-case-result-meta">
            ${c.invoice_number ? '<span><i class="bi bi-receipt me-1"></i>' + escapeHtml(c.invoice_number) + '</span>' : ''}
            ${c.vendor_name ? '<span><i class="bi bi-building me-1"></i>' + escapeHtml(c.vendor_name) + '</span>' : ''}
            ${c.priority ? '<span class="text-muted">' + escapeHtml(c.priority) + '</span>' : ''}
          </div>
        </button>
      `).join('');
      caseSearchResults.innerHTML = html;

      // Attach click handlers
      caseSearchResults.querySelectorAll('.copilot-case-result').forEach(btn => {
        btn.addEventListener('click', () => linkCase(parseInt(btn.dataset.caseId, 10)));
      });
    } catch (err) {
      caseSearchResults.innerHTML = '<div class="copilot-case-search-empty"><p class="mb-0 text-danger">Search failed</p></div>';
      console.error('Case search error:', err);
    }
  }

  async function linkCase(caseId) {
    if (!currentSessionId) {
      // No session yet — start one with this case
      const body = { case_id: caseId };
      try {
        const session = await apiFetch(CONFIG.urls.sessionStart, { method: 'POST', body });
        if (session && session.id) {
          window.location.href = CONFIG.urls.sessionBase + session.id + '/';
        }
      } catch (err) {
        console.error('Failed to start session with case:', err);
      }
      return;
    }
    const url = `/api/v1/copilot/session/${currentSessionId}/`;
    try {
      const res = await apiFetch(url, { method: 'PATCH', body: { action: 'link_case', case_id: caseId } });
      if (res && res.linked) {
        currentCaseId = res.case_id;
        // Update header
        const badge = document.getElementById('caseBadge');
        if (badge) { badge.textContent = 'Case #' + res.case_id; badge.classList.remove('d-none'); }
        const title = document.getElementById('chatTitle');
        if (title && res.title) title.textContent = res.title;
        const label = document.getElementById('linkCaseLabel');
        if (label) label.textContent = 'Change Case';
        // Load context
        loadCaseContext(caseId);
        if (contextPanel) contextPanel.classList.remove('collapsed');
        closeCaseSearch();
        loadRecentConversations();
      }
    } catch (err) {
      console.error('Failed to link case:', err);
    }
  }

  async function unlinkCase() {
    if (!currentSessionId) return;
    const url = `/api/v1/copilot/session/${currentSessionId}/`;
    try {
      const res = await apiFetch(url, { method: 'PATCH', body: { action: 'unlink_case' } });
      if (res && res.unlinked) {
        currentCaseId = null;
        const badge = document.getElementById('caseBadge');
        if (badge) badge.classList.add('d-none');
        const label = document.getElementById('linkCaseLabel');
        if (label) label.textContent = 'Link Case';
        if (contextPanel) contextPanel.classList.add('collapsed');
        closeCaseSearch();
        loadRecentConversations();
      }
    } catch (err) {
      console.error('Failed to unlink case:', err);
    }
  }

  // Wire up case search UI
  (function initCaseSearch() {
    const btnLink = document.getElementById('btnLinkCase');
    if (btnLink) btnLink.addEventListener('click', openCaseSearch);
    const btnClose = document.getElementById('btnCloseCaseSearch');
    if (btnClose) btnClose.addEventListener('click', closeCaseSearch);
    if (caseSearchBackdrop) {
      caseSearchBackdrop.addEventListener('click', (e) => {
        if (e.target === caseSearchBackdrop) closeCaseSearch();
      });
    }
    if (caseSearchInput) {
      caseSearchInput.addEventListener('input', () => {
        clearTimeout(searchDebounce);
        searchDebounce = setTimeout(() => searchCases(caseSearchInput.value), 300);
      });
    }
    const btnUnlink = document.getElementById('btnUnlinkCase');
    if (btnUnlink) btnUnlink.addEventListener('click', unlinkCase);
  })();

  // ── Global functions (called from onclick in templates) ──
  window.useSuggestion = function(btn) {
    const text = btn.textContent.trim();
    if (chatInput && text && !isSending) {
      chatInput.value = text;
      chatInput.dispatchEvent(new Event('input'));
      // Use requestSubmit to properly trigger the submit event listener,
      // with fallback for older browsers.
      if (chatForm) {
        if (chatForm.requestSubmit) {
          chatForm.requestSubmit();
        } else {
          chatForm.dispatchEvent(new Event('submit', { cancelable: true }));
        }
      }
    }
  };

  window.togglePin = togglePin;
  window.startSession = startSession;
  window.loadCaseContext = loadCaseContext;
  window.setCurrentCaseScope = setCurrentCaseScope;
  window.loadRecentConversations = loadRecentConversations;
  window.loadSuggestions = loadSuggestions;

  // ── Case action from context panel ──
  window.copilotCaseAction = function (action) {
    if (!currentCaseId) { alert('No case linked.'); return; }

    if (action === 'approve') {
      if (!confirm('Approve this case?')) return;
    } else if (action === 'reject') {
      var reason = prompt('Rejection reason:');
      if (reason === null) return;
    } else if (action === 'escalate') {
      var reason = prompt('Escalation reason (optional):');
      if (reason === null) return;
    } else if (action === 'reprocess') {
      if (!confirm('Reprocess this case from intake?')) return;
    }

    var decisionMap = {approve: 'APPROVED', reject: 'REJECTED', reprocess: 'REPROCESSED', escalate: 'ESCALATED'};
    var decision = decisionMap[action] || action.toUpperCase();
    var formData = new FormData();
    formData.append('csrfmiddlewaretoken', CONFIG.csrfToken);
    formData.append('decision', decision);
    if (typeof reason === 'string') formData.append('reason', reason);

    var decideUrl = '/cases/' + currentCaseId + '/decide/';
    fetch(decideUrl, { method: 'POST', body: formData, credentials: 'same-origin' })
      .then(function (resp) {
        if (resp.redirected) {
          // Successful -- reload to reflect new status
          window.location.reload();
          return;
        }
        if (!resp.ok) throw new Error('Action failed: ' + resp.status);
        window.location.reload();
      })
      .catch(function (err) {
        alert('Action failed: ' + err.message);
      });
  };

  // ── Boot ──
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
