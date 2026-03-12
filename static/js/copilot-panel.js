/**
 * Copilot Panel — Context-aware chat for case investigation.
 * Uses window.CASE_CONTEXT (injected by template) to answer questions.
 */
document.addEventListener('DOMContentLoaded', function () {

  var messagesEl = document.getElementById('copilotMessages');
  var inputEl = document.getElementById('copilotInput');
  var sendBtn = document.getElementById('copilotSend');
  var promptChips = document.querySelectorAll('.ap-copilot-prompt-chip');
  var ctx = window.CASE_CONTEXT || {};

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

    appendMessage('user', text);
    inputEl.value = '';
    inputEl.focus();

    var promptsEl = document.getElementById('copilotPrompts');
    if (promptsEl) promptsEl.style.display = 'none';

    var typingEl = showTyping();

    setTimeout(function () {
      removeTyping(typingEl);
      var response = generateContextualResponse(text);
      appendMessage('assistant', response);
    }, 300 + Math.random() * 400);
  }

  // ---------------------------------------------------------------
  // Append a message bubble
  // ---------------------------------------------------------------
  function appendMessage(role, text) {
    var div = document.createElement('div');
    div.className = 'ap-copilot-msg ' + role;
    if (role === 'assistant') {
      div.innerHTML = '<div style="font-size:var(--ap-font-size-xs);color:var(--ap-text-muted);margin-bottom:.25rem">' +
        '<i class="bi bi-robot me-1"></i>Copilot</div>' + text;
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
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  // ---------------------------------------------------------------
  // Context-aware response engine
  // ---------------------------------------------------------------
  function generateContextualResponse(query) {
    var q = query.toLowerCase().trim();
    var inv = ctx.invoice || {};
    var po = ctx.purchase_order;

    // --- Direct field lookups ---
    if (match(q, ['invoice number', 'invoice no', 'invoice #', 'inv number', 'inv no'])) {
      return field('Invoice Number', inv.invoice_number);
    }
    if (match(q, ['invoice date'])) {
      return field('Invoice Date', inv.invoice_date);
    }
    if (match(q, ['due date'])) {
      return field('Due Date', inv.due_date || 'Not specified');
    }
    if (match(q, ['total amount', 'invoice amount', 'invoice total', 'how much', 'amount'])) {
      var amt = inv.total_amount ? (inv.currency + ' ' + inv.total_amount) : 'Not available';
      return field('Total Amount', amt);
    }
    if (match(q, ['vendor', 'supplier', 'who is the vendor', 'vendor name'])) {
      return field('Vendor', inv.vendor_name);
    }
    if (match(q, ['currency'])) {
      return field('Currency', inv.currency || 'Not specified');
    }
    if (match(q, ['po number', 'purchase order number', 'po #', 'po no'])) {
      if (po) return field('PO Number', po.po_number);
      return field('PO Number', inv.po_number || 'No PO linked (Non-PO case)');
    }
    if (match(q, ['case number', 'case #', 'case no'])) {
      return field('Case Number', ctx.case_number);
    }
    if (match(q, ['status', 'case status', 'current status'])) {
      return field('Case Status', ctx.status);
    }
    if (match(q, ['path', 'processing path', 'route'])) {
      return field('Processing Path', ctx.processing_path);
    }
    if (match(q, ['priority'])) {
      return field('Priority', ctx.priority);
    }
    if (match(q, ['assigned', 'assignee', 'who is assigned'])) {
      return field('Assigned To', ctx.assigned_to || 'Unassigned');
    }
    if (match(q, ['confidence', 'extraction confidence'])) {
      var conf = inv.extraction_confidence ? (Math.round(inv.extraction_confidence * 100) + '%') : 'N/A';
      return field('Extraction Confidence', conf);
    }
    if (match(q, ['created', 'when was', 'creation date'])) {
      return field('Created At', ctx.created_at);
    }

    // --- Line items ---
    if (match(q, ['line item', 'items', 'what was ordered', 'what is on the invoice'])) {
      var items = inv.line_items || [];
      if (items.length === 0) return 'No line items found on this invoice.';
      var rows = items.map(function (li, i) {
        var parts = [(i + 1) + '.'];
        if (li.description) parts.push(escapeHtml(li.description));
        if (li.quantity) parts.push('Qty: ' + li.quantity);
        if (li.unit_price) parts.push('@ ' + li.unit_price);
        if (li.amount) parts.push('= ' + li.amount);
        return parts.join(' ');
      });
      return '<strong>Line Items (' + items.length + '):</strong><br>' + rows.join('<br>');
    }

    // --- GRNs ---
    if (match(q, ['grn', 'goods receipt', 'delivery'])) {
      var grns = ctx.grns || [];
      if (grns.length === 0) return 'No GRNs are linked to this case.';
      var grnList = grns.map(function (g) { return g.grn_number + (g.receipt_date ? ' (' + g.receipt_date + ')' : ''); });
      return '<strong>GRNs (' + grns.length + '):</strong><br>' + grnList.join('<br>');
    }

    // --- PO details ---
    if (match(q, ['purchase order', 'po detail', 'po info', 'tell me about the po'])) {
      if (!po) return 'No Purchase Order is linked to this case. This is a Non-PO invoice.';
      return '<strong>Purchase Order:</strong><br>' +
        'PO Number: ' + escapeHtml(po.po_number) + '<br>' +
        'Vendor: ' + escapeHtml(po.vendor_name) + '<br>' +
        'Total: ' + (po.total_amount || 'N/A');
    }

    // --- Exceptions / Issues ---
    if (match(q, ['exception', 'issue', 'problem', 'error', 'discrepanc'])) {
      return buildExceptionsResponse();
    }

    // --- Stages ---
    if (match(q, ['stage', 'step', 'pipeline', 'processing stage', 'what happened'])) {
      var stages = ctx.stages || [];
      if (stages.length === 0) return 'No processing stages recorded.';
      var stageLines = stages.map(function (s) {
        var icon = s.status === 'COMPLETED' ? '✅' : s.status === 'FAILED' ? '❌' : '⏳';
        return icon + ' ' + escapeHtml(s.name) + ': ' + s.status;
      });
      return '<strong>Processing Stages:</strong><br>' + stageLines.join('<br>');
    }

    // --- Decisions ---
    if (match(q, ['decision', 'why was this', 'rationale', 'reasoning'])) {
      var decisions = ctx.decisions || [];
      if (decisions.length === 0) return 'No decisions recorded for this case.';
      var decLines = decisions.map(function (d) {
        var line = '<strong>' + escapeHtml(d.type) + ':</strong> ' + escapeHtml(d.value);
        if (d.rationale) line += '<br><em>' + escapeHtml(d.rationale) + '</em>';
        return line;
      });
      return decLines.join('<br><br>');
    }

    // --- Risk ---
    if (match(q, ['risk'])) {
      return buildRiskResponse();
    }

    // --- Summary ---
    if (match(q, ['summar'])) {
      if (ctx.summary) return escapeHtml(ctx.summary);
      return buildCaseSummaryResponse();
    }

    // --- Flagged ---
    if (match(q, ['flag', 'why was this flag'])) {
      return buildFlagResponse();
    }

    // --- Verify ---
    if (match(q, ['verify', 'check', 'what should'])) {
      return buildVerifyResponse();
    }

    // --- Missing ---
    if (match(q, ['missing', 'what is missing', "what's missing"])) {
      return buildMissingResponse();
    }

    // --- Overview / tell me about this case ---
    if (match(q, ['overview', 'tell me about', 'explain', 'describe', 'what is this'])) {
      return buildCaseSummaryResponse();
    }

    // --- Fallback: try to find any matching field ---
    return buildCaseSummaryResponse() +
      '<br><br><em>You can ask about: invoice number, vendor, amount, line items, PO, GRN, status, ' +
      'stages, decisions, exceptions, risks, or what to verify.</em>';
  }

  // ---------------------------------------------------------------
  // Helper builders
  // ---------------------------------------------------------------
  function field(label, value) {
    return '<strong>' + label + ':</strong> ' + escapeHtml(value || 'N/A');
  }

  function buildCaseSummaryResponse() {
    var inv = ctx.invoice || {};
    var parts = [];
    parts.push('Case <strong>' + escapeHtml(ctx.case_number) + '</strong>');
    parts.push('Invoice ' + escapeHtml(inv.invoice_number || 'N/A') +
      ' from <strong>' + escapeHtml(inv.vendor_name || 'Unknown') + '</strong>');
    if (inv.total_amount) parts.push('for ' + escapeHtml(inv.currency + ' ' + inv.total_amount));
    parts.push('Status: <strong>' + escapeHtml(ctx.status) + '</strong>');
    parts.push('Path: ' + escapeHtml(ctx.processing_path));

    var issues = (ctx.exceptions || []).length + (ctx.validation_issues || []).length;
    if (issues > 0) parts.push(issues + ' issue(s) found');

    return parts.join('. ') + '.';
  }

  function buildExceptionsResponse() {
    var exc = ctx.exceptions || [];
    var val = ctx.validation_issues || [];
    if (exc.length === 0 && val.length === 0) return 'No exceptions or issues found on this case.';

    var lines = [];
    exc.forEach(function (e) {
      lines.push('⚠️ <strong>[' + escapeHtml(e.severity) + ']</strong> ' +
        escapeHtml(e.type) + (e.description ? ': ' + escapeHtml(e.description) : ''));
    });
    val.forEach(function (v) {
      var icon = v.status === 'FAIL' ? '❌' : '⚠️';
      lines.push(icon + ' ' + escapeHtml(v.check_name) + ': ' + escapeHtml(v.message));
    });
    return '<strong>Issues (' + (exc.length + val.length) + '):</strong><br>' + lines.join('<br>');
  }

  function buildRiskResponse() {
    var risks = [];
    var exc = ctx.exceptions || [];
    var val = ctx.validation_issues || [];

    if (!ctx.purchase_order) risks.push('No PO linked — higher risk of unauthorized spend');
    if (val.some(function (v) { return v.status === 'FAIL'; }))
      risks.push('Failed validation checks flagged');
    if (exc.length > 0) risks.push(exc.length + ' reconciliation exception(s)');

    var inv = ctx.invoice || {};
    if (inv.extraction_confidence && inv.extraction_confidence < 0.8)
      risks.push('Low extraction confidence (' + Math.round(inv.extraction_confidence * 100) + '%) — data may be inaccurate');

    if (risks.length === 0) return 'No significant risks identified.';
    return '<strong>Key Risks:</strong><br>' + risks.map(function (r, i) { return (i + 1) + '. ' + r; }).join('<br>');
  }

  function buildFlagResponse() {
    var reasons = [];
    if (ctx.status === 'Ready For Review' || ctx.status === 'In Review')
      reasons.push('Case requires human review');
    if ((ctx.exceptions || []).length > 0)
      reasons.push((ctx.exceptions || []).length + ' reconciliation exception(s) detected');
    if ((ctx.validation_issues || []).length > 0) {
      var fails = (ctx.validation_issues || []).filter(function (v) { return v.status === 'FAIL'; });
      if (fails.length > 0) reasons.push(fails.length + ' validation check(s) failed');
    }
    if (!ctx.purchase_order) reasons.push('Non-PO invoice — no PO match possible');
    if (reasons.length === 0) return 'No specific flags identified.';
    return '<strong>Why Flagged:</strong><br>' + reasons.map(function (r, i) { return (i + 1) + '. ' + r; }).join('<br>');
  }

  function buildVerifyResponse() {
    var steps = [];
    if (!ctx.purchase_order) {
      steps.push('Confirm this invoice is legitimate and authorized without a PO');
      steps.push('Verify the vendor is approved for direct billing');
    } else {
      steps.push('Cross-check invoice line items against PO terms');
    }
    if ((ctx.grns || []).length > 0)
      steps.push('Confirm GRN quantities match delivery receipts');
    if ((ctx.validation_issues || []).length > 0) {
      var fails = (ctx.validation_issues || []).filter(function (v) { return v.status === 'FAIL'; });
      fails.forEach(function (f) { steps.push('Resolve: ' + f.check_name + ' — ' + f.message); });
    }
    if (steps.length === 0) steps.push('Review the case summary and approve or reject');
    return '<strong>Verification Steps:</strong><br>' + steps.map(function (s, i) { return (i + 1) + '. ' + s; }).join('<br>');
  }

  function buildMissingResponse() {
    var missing = [];
    if (!ctx.purchase_order) missing.push('Purchase Order — no PO is linked');
    if ((ctx.grns || []).length === 0 && ctx.purchase_order) missing.push('GRN documentation');
    if (!ctx.assigned_to) missing.push('Case is unassigned');
    var inv = ctx.invoice || {};
    if (!inv.vendor_name || inv.vendor_name === 'Unknown') missing.push('Vendor identification');
    if (missing.length === 0) return 'All expected data appears to be present.';
    return '<strong>Missing Items:</strong><br>' + missing.map(function (m, i) { return (i + 1) + '. ' + m; }).join('<br>');
  }

  // ---------------------------------------------------------------
  // Match helper — checks if query contains any of the phrases
  // ---------------------------------------------------------------
  function match(q, phrases) {
    for (var i = 0; i < phrases.length; i++) {
      if (q.indexOf(phrases[i]) !== -1) return true;
    }
    return false;
  }

  // ---------------------------------------------------------------
  // HTML escape utility
  // ---------------------------------------------------------------
  function escapeHtml(text) {
    if (!text) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(String(text)));
    return div.innerHTML;
  }
});
