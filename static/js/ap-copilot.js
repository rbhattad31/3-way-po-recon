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

  // ── Initialisation ──
  function init() {
    if (chatForm)  chatForm.addEventListener('submit', onSubmit);
    if (chatInput) {
      chatInput.addEventListener('input', onInputChange);
      chatInput.addEventListener('keydown', onKeyDown);
    }

    const btnNew = document.getElementById('btnNewConversation');
    if (btnNew) btnNew.addEventListener('click', startSession);

    const btnToggleSidebar = document.getElementById('btnToggleSidebar');
    if (btnToggleSidebar) btnToggleSidebar.addEventListener('click', () => sidebar.classList.toggle('collapsed'));

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

    scrollChatToBottom();
    if (currentCaseId && !contextPanel.classList.contains('collapsed')) {
      loadCaseContext(currentCaseId);
    }
  }

  // ── Input Handling ──
  function onInputChange() {
    btnSend.disabled = !chatInput.value.trim();
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
  async function onSubmit(e) {
    e.preventDefault();
    const text = chatInput.value.trim();
    if (!text || isSending) return;

    // Ensure we have a session
    if (!currentSessionId) {
      const body = {};
      if (currentCaseId) body.case_id = currentCaseId;
      const session = await apiFetch(CONFIG.urls.sessionStart, { method: 'POST', body });
      if (!session || !session.id) return;
      currentSessionId = session.id;
    }

    // Hide welcome state
    if (welcomeState) welcomeState.style.display = 'none';

    // Render user message
    renderUserMessage(text);
    chatInput.value = '';
    chatInput.style.height = 'auto';
    btnSend.disabled = true;

    // Show thinking indicator
    const thinkingEl = showThinking();
    isSending = true;

    try {
      const res = await sendMessage(currentSessionId, text);
      removeThinking(thinkingEl);
      if (res && res.response) {
        renderAssistantMessage(res.response);
      }
    } catch (err) {
      removeThinking(thinkingEl);
      renderSystemMessage('Something went wrong. Please try again.');
      console.error('Chat error:', err);
    } finally {
      isSending = false;
    }
  }

  async function sendMessage(sessionId, message) {
    return apiFetch(CONFIG.urls.chat, {
      method: 'POST',
      body: { session_id: sessionId, message, case_id: currentCaseId },
    });
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

    // Evidence cards
    if (payload.evidence && payload.evidence.length) {
      parts.push(renderEvidenceCards(payload.evidence));
    }

    // Consulted agents
    if (payload.consulted_agents && payload.consulted_agents.length) {
      parts.push(`
        <div class="copilot-agents-section mt-2">
          <div class="copilot-section-label"><i class="bi bi-robot me-1"></i>Consulted Agents</div>
          <div class="copilot-agent-chips">
            ${payload.consulted_agents.map(a => `<span class="copilot-agent-chip">${escapeHtml(a)}</span>`).join('')}
          </div>
        </div>`);
    }

    // Recommendation
    if (payload.recommendation) {
      const rec = payload.recommendation;
      const conf = rec.confidence != null ? `${Math.round(rec.confidence * 100)}%` : '';
      parts.push(`
        <div class="copilot-recommendation-block mt-2">
          <div class="copilot-section-label">
            <i class="bi bi-lightbulb me-1"></i>Recommendation
            ${conf ? `<span class="copilot-recommendation-confidence">${conf}</span>` : ''}
          </div>
          <div class="small">${escapeHtml(rec.text || '')}</div>
          <div class="copilot-recommendation-readonly">Read-only guidance — no action taken</div>
        </div>`);
    }

    // Governance
    if (payload.governance && payload.governance.permitted) {
      parts.push(renderGovernanceBlock(payload.governance));
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
      <div class="copilot-evidence-section mt-3">
        <div class="copilot-section-label"><i class="bi bi-card-list me-1"></i>Evidence</div>
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
      <div class="copilot-governance-block mt-2">
        <div class="copilot-section-label"><i class="bi bi-shield-lock me-1"></i>Governance Trace</div>
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
    html += buildCtxCard('Case Summary', [
      ['Case', ctx.case.case_number],
      ['Status', `<span class="badge bg-primary-subtle text-primary-emphasis">${escapeHtml(ctx.case.status)}</span>`],
      ['Priority', ctx.case.priority],
      ['Path', ctx.case.processing_path],
    ]);

    if (ctx.invoice) {
      html += buildCtxCard('<i class="bi bi-file-earmark-text me-1"></i>Invoice', [
        ['Number', ctx.invoice.invoice_number],
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

  // ── Global functions (called from onclick in templates) ──
  window.useSuggestion = function(btn) {
    const text = btn.textContent.trim();
    if (chatInput) {
      chatInput.value = text;
      chatInput.dispatchEvent(new Event('input'));
      chatInput.focus();
    }
  };

  window.togglePin = togglePin;
  window.startSession = startSession;
  window.loadCaseContext = loadCaseContext;
  window.setCurrentCaseScope = setCurrentCaseScope;
  window.loadRecentConversations = loadRecentConversations;
  window.loadSuggestions = loadSuggestions;

  // ── Boot ──
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
