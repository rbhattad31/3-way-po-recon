/**
 * Copilot Panel — Chat-like UI interactions
 * Handles prompt chips, message send/receive, and typing indicator.
 */
document.addEventListener('DOMContentLoaded', function () {

  var messagesEl = document.getElementById('copilotMessages');
  var inputEl = document.getElementById('copilotInput');
  var sendBtn = document.getElementById('copilotSend');
  var promptChips = document.querySelectorAll('.ap-copilot-prompt-chip');

  if (!messagesEl || !inputEl || !sendBtn) return;

  // ---------------------------------------------------------------
  // 1. Prompt chip click → set input and send
  // ---------------------------------------------------------------
  promptChips.forEach(function (chip) {
    chip.addEventListener('click', function () {
      var prompt = this.dataset.prompt;
      if (prompt) {
        inputEl.value = prompt;
        sendMessage();
      }
    });
  });

  // ---------------------------------------------------------------
  // 2. Send on Enter key
  // ---------------------------------------------------------------
  inputEl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  // ---------------------------------------------------------------
  // 3. Send button click
  // ---------------------------------------------------------------
  sendBtn.addEventListener('click', sendMessage);

  // ---------------------------------------------------------------
  // Core send function
  // ---------------------------------------------------------------
  function sendMessage() {
    var text = inputEl.value.trim();
    if (!text) return;

    // Add user message
    appendMessage('user', text);
    inputEl.value = '';
    inputEl.focus();

    // Hide prompt chips after first interaction
    var promptsEl = document.getElementById('copilotPrompts');
    if (promptsEl) promptsEl.style.display = 'none';

    // Show typing indicator
    var typingEl = showTyping();

    // Simulate response (in production, this calls the copilot API)
    setTimeout(function () {
      removeTyping(typingEl);
      var response = generateContextualResponse(text);
      appendMessage('assistant', response);
    }, 800 + Math.random() * 700);
  }

  // ---------------------------------------------------------------
  // Append a message bubble
  // ---------------------------------------------------------------
  function appendMessage(role, text) {
    var div = document.createElement('div');
    div.className = 'ap-copilot-msg ' + role;
    if (role === 'assistant') {
      div.innerHTML = '<div style="font-size:var(--ap-font-size-xs);color:var(--ap-text-muted);margin-bottom:.25rem">' +
        '<i class="bi bi-robot me-1"></i>Copilot</div>' + escapeHtml(text);
    } else {
      div.textContent = text;
    }
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  // ---------------------------------------------------------------
  // Typing indicator
  // ---------------------------------------------------------------
  function showTyping() {
    var div = document.createElement('div');
    div.className = 'ap-copilot-msg assistant ap-copilot-typing';
    div.innerHTML = '<span class="typing-dots"><span></span><span></span><span></span></span>';
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
  }

  function removeTyping(el) {
    if (el && el.parentNode) {
      el.parentNode.removeChild(el);
    }
  }

  // ---------------------------------------------------------------
  // Contextual response simulation
  // In production: POST to /api/v1/cases/{id}/copilot/ endpoint
  // ---------------------------------------------------------------
  function generateContextualResponse(query) {
    var q = query.toLowerCase();
    if (q.indexOf('summar') !== -1) {
      return 'This case involves a reconciliation between the submitted invoice and the corresponding PO/GRN documents. ' +
        'The system has identified exceptions that require human review. Check the Exceptions panel for details.';
    }
    if (q.indexOf('flag') !== -1 || q.indexOf('why') !== -1) {
      return 'This case was flagged because the reconciliation engine detected discrepancies during matching. ' +
        'Common reasons include quantity mismatches, price variances, or missing GRN documentation.';
    }
    if (q.indexOf('risk') !== -1) {
      return 'Key risks to evaluate: (1) Potential overpayment if price variance is confirmed, ' +
        '(2) Inventory discrepancy if quantity mismatch is real, (3) Compliance gap if GRN is missing.';
    }
    if (q.indexOf('verify') !== -1 || q.indexOf('check') !== -1) {
      return 'Recommended verification steps: (1) Cross-check invoice line items against PO terms, ' +
        '(2) Confirm GRN quantities match delivery receipts, (3) Validate vendor pricing against contract.';
    }
    if (q.indexOf('missing') !== -1) {
      return 'Check for: (1) Missing GRN documentation — the GRN agent may have flagged gaps, ' +
        '(2) Incomplete vendor information, (3) Unresolved prior exceptions on the same PO.';
    }
    return 'I can help investigate this case. Try asking about the summary, why it was flagged, ' +
      'key risks, what to verify, or what might be missing.';
  }

  // ---------------------------------------------------------------
  // HTML escape utility
  // ---------------------------------------------------------------
  function escapeHtml(text) {
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(text));
    return div.innerHTML;
  }
});
