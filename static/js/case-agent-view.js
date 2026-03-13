/**
 * Case Agent View — ChatGPT-style agentic investigation page.
 * Handles feed filtering, copilot chat (reuses copilot-panel logic), context panel toggles.
 * Uses window.CASE_CONTEXT injected by the template.
 */
document.addEventListener('DOMContentLoaded', function () {

  var feedScroll = document.getElementById('feedScroll');
  var chatMessages = document.getElementById('avChatMessages');
  var inputEl = document.getElementById('avInput');
  var sendBtn = document.getElementById('avSend');
  var promptChips = document.querySelectorAll('.av-prompt-chip');
  var filterBtns = document.querySelectorAll('.av-filter-btn');
  var eventCountEl = document.getElementById('eventCount');
  var ctx = window.CASE_CONTEXT || {};

  // ---------------------------------------------------------------
  // 1. Feed Filtering
  // ---------------------------------------------------------------
  var overviewEl = document.getElementById('avOverview');

  function applyFilter(filter) {
    var msgs = feedScroll.querySelectorAll('.av-msg');
    var shown = 0;

    if (filter === 'overview') {
      // Show overview block, hide feed messages but NOT overview's own children
      if (overviewEl) overviewEl.style.display = '';
      msgs.forEach(function (msg) {
        if (!overviewEl || !overviewEl.contains(msg)) {
          msg.style.display = 'none';
        }
      });
      if (eventCountEl) eventCountEl.textContent = '';
      return;
    }

    // Hide overview, show matching feed messages
    if (overviewEl) overviewEl.style.display = 'none';
    msgs.forEach(function (msg) {
      if (overviewEl && overviewEl.contains(msg)) return;
      var cat = msg.dataset.category || '';
      var cats = cat.split(/\s+/);
      if (filter === 'all' || cats.indexOf(filter) !== -1) {
        msg.style.display = '';
        shown++;
      } else {
        msg.style.display = 'none';
      }
    });
    if (eventCountEl) eventCountEl.textContent = shown + ' events';
  }

  filterBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      filterBtns.forEach(function (b) { b.classList.remove('active'); });
      this.classList.add('active');
      applyFilter(this.dataset.filter);
    });
  });

  // Initial state: overview
  applyFilter('overview');

  // ---------------------------------------------------------------
  // 2. Prompt Chips
  // ---------------------------------------------------------------
  promptChips.forEach(function (chip) {
    chip.addEventListener('click', function () {
      var prompt = this.dataset.prompt;
      if (prompt && inputEl) {
        inputEl.value = prompt;
        sendMessage();
      }
    });
  });

  // ---------------------------------------------------------------
  // 3. Send Message
  // ---------------------------------------------------------------
  if (inputEl) {
    inputEl.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });
    // Auto-resize textarea
    inputEl.addEventListener('input', function () {
      this.style.height = 'auto';
      this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });
  }
  if (sendBtn) sendBtn.addEventListener('click', sendMessage);

  function sendMessage() {
    var text = inputEl.value.trim();
    if (!text) return;

    appendChatMessage('user', 'You', text);
    inputEl.value = '';
    inputEl.style.height = 'auto';

    // Hide prompt chips after first message
    var prompts = document.getElementById('avPrompts');
    if (prompts) prompts.style.display = 'none';

    // Show typing
    var typingEl = showTyping();

    setTimeout(function () {
      removeEl(typingEl);
      var response = generateResponse(text);
      appendChatMessage('assistant', 'Copilot', response, true);
    }, 300 + Math.random() * 500);
  }

  // ---------------------------------------------------------------
  // 4. Append Message to Chat
  // ---------------------------------------------------------------
  function appendChatMessage(role, sender, content, isHtml) {
    // Hide welcome message on first interaction
    var welcome = chatMessages ? chatMessages.querySelector('.av-chat__welcome') : null;
    if (welcome) welcome.style.display = 'none';

    var msg = document.createElement('div');
    msg.className = 'av-chat-msg av-chat-msg--' + role;

    if (role === 'user') {
      msg.innerHTML = '<div class="av-chat-msg__text">' + escapeHtml(content) + '</div>';
    } else {
      msg.innerHTML =
        '<div class="av-chat-msg__icon"><i class="bi bi-stars"></i></div>' +
        '<div class="av-chat-msg__text">' + (isHtml ? content : escapeHtml(content)) + '</div>';
    }

    chatMessages.appendChild(msg);
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  // ---------------------------------------------------------------
  // 5. Typing Indicator
  // ---------------------------------------------------------------
  function showTyping() {
    var div = document.createElement('div');
    div.className = 'av-chat-msg av-chat-msg--assistant';
    div.innerHTML = '<div class="av-chat-msg__icon"><i class="bi bi-stars"></i></div>' +
      '<div class="av-chat-msg__text"><div class="av-typing"><span></span><span></span><span></span></div></div>';
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return div;
  }

  function removeEl(el) {
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  // ---------------------------------------------------------------
  // 6. Context-Aware Response Engine (replicates copilot logic)
  // ---------------------------------------------------------------
  function generateResponse(query) {
    var q = query.toLowerCase().trim();
    var inv = ctx.invoice || {};
    var po = ctx.purchase_order;

    if (match(q, ['invoice number', 'invoice no', 'invoice #'])) return field('Invoice Number', inv.invoice_number);
    if (match(q, ['invoice date'])) return field('Invoice Date', inv.invoice_date);
    if (match(q, ['total amount', 'invoice amount', 'how much', 'amount'])) {
      return field('Total Amount', inv.total_amount ? (inv.currency + ' ' + inv.total_amount) : 'N/A');
    }
    if (match(q, ['vendor', 'supplier'])) return field('Vendor', inv.vendor_name);
    if (match(q, ['po number', 'purchase order number'])) {
      return field('PO Number', po ? po.po_number : (inv.po_number || 'No PO linked'));
    }
    if (match(q, ['status', 'case status'])) return field('Status', ctx.status);
    if (match(q, ['path', 'processing path'])) return field('Path', ctx.processing_path);
    if (match(q, ['priority'])) return field('Priority', ctx.priority);
    if (match(q, ['assigned'])) return field('Assigned To', ctx.assigned_to || 'Unassigned');
    if (match(q, ['confidence'])) {
      return field('Confidence', inv.extraction_confidence ? (Math.round(inv.extraction_confidence * 100) + '%') : 'N/A');
    }

    if (match(q, ['line item', 'items'])) {
      var items = inv.line_items || [];
      if (!items.length) return 'No line items found.';
      var rows = items.map(function (li, i) {
        var p = [(i + 1) + '.'];
        if (li.description) p.push(escapeHtml(li.description));
        if (li.quantity) p.push('Qty: ' + li.quantity);
        if (li.unit_price) p.push('@ ' + li.unit_price);
        if (li.amount) p.push('= ' + li.amount);
        return p.join(' ');
      });
      return '<strong>Line Items (' + items.length + '):</strong><br>' + rows.join('<br>');
    }

    if (match(q, ['grn', 'goods receipt'])) {
      var grns = ctx.grns || [];
      if (!grns.length) return 'No GRNs linked.';
      return '<strong>GRNs (' + grns.length + '):</strong><br>' +
        grns.map(function (g) { return g.grn_number + (g.receipt_date ? ' (' + g.receipt_date + ')' : ''); }).join('<br>');
    }

    if (match(q, ['exception', 'issue', 'problem', 'error', 'discrepanc'])) return buildExceptions();
    if (match(q, ['stage', 'pipeline', 'step'])) return buildStages();
    if (match(q, ['decision', 'rationale', 'reasoning'])) return buildDecisions();
    if (match(q, ['risk'])) return buildRisks();
    if (match(q, ['summar'])) return ctx.summary ? escapeHtml(ctx.summary) : buildSummary();
    if (match(q, ['flag', 'why was'])) return buildFlagged();
    if (match(q, ['verify', 'check', 'what should'])) return buildVerify();
    if (match(q, ['missing'])) return buildMissing();
    if (match(q, ['overview', 'tell me', 'explain', 'describe'])) return buildSummary();

    return buildSummary() +
      '<br><br><em style="color:var(--ap-text-muted)">Ask about: invoice, vendor, amount, line items, PO, GRN, ' +
      'status, stages, decisions, exceptions, risks, or what to verify.</em>';
  }

  // ---------------------------------------------------------------
  // Response Builders
  // ---------------------------------------------------------------
  function buildSummary() {
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

  function buildExceptions() {
    var exc = ctx.exceptions || [];
    var val = ctx.validation_issues || [];
    if (!exc.length && !val.length) return 'No exceptions or issues found.';
    var lines = [];
    exc.forEach(function (e) {
      lines.push('&#9888; <strong>[' + escapeHtml(e.severity) + ']</strong> ' +
        escapeHtml(e.type) + (e.description ? ': ' + escapeHtml(e.description) : ''));
    });
    val.forEach(function (v) {
      var icon = v.status === 'FAIL' ? '&#10060;' : '&#9888;';
      lines.push(icon + ' ' + escapeHtml(v.check_name) + ': ' + escapeHtml(v.message));
    });
    return '<strong>Issues (' + (exc.length + val.length) + '):</strong><br>' + lines.join('<br>');
  }

  function buildStages() {
    var stages = ctx.stages || [];
    if (!stages.length) return 'No processing stages recorded.';
    return '<strong>Stages:</strong><br>' + stages.map(function (s) {
      var icon = s.status === 'COMPLETED' ? '&#9989;' : s.status === 'FAILED' ? '&#10060;' : '&#9203;';
      return icon + ' ' + escapeHtml(s.name) + ': ' + s.status;
    }).join('<br>');
  }

  function buildDecisions() {
    var decs = ctx.decisions || [];
    if (!decs.length) return 'No decisions recorded.';
    return decs.map(function (d) {
      var line = '<strong>' + escapeHtml(d.type) + ':</strong> ' + escapeHtml(d.value);
      if (d.rationale) line += '<br><em>' + escapeHtml(d.rationale) + '</em>';
      return line;
    }).join('<br><br>');
  }

  function buildRisks() {
    var risks = [];
    if (!ctx.purchase_order) risks.push('No PO linked — higher risk of unauthorized spend');
    if ((ctx.validation_issues || []).some(function (v) { return v.status === 'FAIL'; }))
      risks.push('Failed validation checks detected');
    if ((ctx.exceptions || []).length > 0) risks.push((ctx.exceptions || []).length + ' reconciliation exception(s)');
    var inv = ctx.invoice || {};
    if (inv.extraction_confidence && inv.extraction_confidence < 0.8)
      risks.push('Low extraction confidence (' + Math.round(inv.extraction_confidence * 100) + '%)');
    if (!risks.length) return 'No significant risks identified.';
    return '<strong>Key Risks:</strong><br>' + risks.map(function (r, i) { return (i + 1) + '. ' + r; }).join('<br>');
  }

  function buildFlagged() {
    var reasons = [];
    if (ctx.status === 'Ready For Review' || ctx.status === 'In Review') reasons.push('Case requires human review');
    if ((ctx.exceptions || []).length > 0) reasons.push((ctx.exceptions || []).length + ' exception(s) detected');
    var fails = (ctx.validation_issues || []).filter(function (v) { return v.status === 'FAIL'; });
    if (fails.length > 0) reasons.push(fails.length + ' validation check(s) failed');
    if (!ctx.purchase_order) reasons.push('Non-PO invoice');
    if (!reasons.length) return 'No specific flags identified.';
    return '<strong>Why Flagged:</strong><br>' + reasons.map(function (r, i) { return (i + 1) + '. ' + r; }).join('<br>');
  }

  function buildVerify() {
    var steps = [];
    if (!ctx.purchase_order) {
      steps.push('Confirm invoice is legitimate and authorized without a PO');
      steps.push('Verify vendor is approved for direct billing');
    } else {
      steps.push('Cross-check invoice line items against PO terms');
    }
    if ((ctx.grns || []).length > 0) steps.push('Confirm GRN quantities match delivery receipts');
    var fails = (ctx.validation_issues || []).filter(function (v) { return v.status === 'FAIL'; });
    fails.forEach(function (f) { steps.push('Resolve: ' + f.check_name); });
    if (!steps.length) steps.push('Review the case summary and approve or reject');
    return '<strong>Verification Steps:</strong><br>' + steps.map(function (s, i) { return (i + 1) + '. ' + s; }).join('<br>');
  }

  function buildMissing() {
    var missing = [];
    if (!ctx.purchase_order) missing.push('Purchase Order');
    if (!(ctx.grns || []).length && ctx.purchase_order) missing.push('GRN documentation');
    if (!ctx.assigned_to) missing.push('Case is unassigned');
    var inv = ctx.invoice || {};
    if (!inv.vendor_name || inv.vendor_name === 'Unknown') missing.push('Vendor identification');
    if (!missing.length) return 'All expected data appears present.';
    return '<strong>Missing:</strong><br>' + missing.map(function (m, i) { return (i + 1) + '. ' + m; }).join('<br>');
  }

  // ---------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------
  function match(q, phrases) {
    for (var i = 0; i < phrases.length; i++) {
      if (q.indexOf(phrases[i]) !== -1) return true;
    }
    return false;
  }

  function field(label, value) {
    return '<strong>' + label + ':</strong> ' + escapeHtml(value || 'N/A');
  }

  function escapeHtml(text) {
    if (!text) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(String(text)));
    return div.innerHTML;
  }

  // ---------------------------------------------------------------
  // 7. Bootstrap Tooltips
  // ---------------------------------------------------------------
  var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
  tooltipTriggerList.forEach(function (el) {
    new bootstrap.Tooltip(el);
  });

  // Scroll to bottom on load
  if (feedScroll) {
    feedScroll.scrollTop = feedScroll.scrollHeight;
  }
});

// ---------------------------------------------------------------
// Context Panel Card Toggle
// ---------------------------------------------------------------
function toggleContextCard(headerEl) {
  var isOpening = headerEl.classList.contains('collapsed');

  // Close all cards first
  var allHeaders = document.querySelectorAll('.av-context-card__header');
  allHeaders.forEach(function (h) {
    h.classList.add('collapsed');
    var b = h.nextElementSibling;
    if (b) b.classList.add('collapsed');
  });

  // If we were opening, expand the clicked one
  if (isOpening) {
    headerEl.classList.remove('collapsed');
    var body = headerEl.nextElementSibling;
    if (body) body.classList.remove('collapsed');
  }
}

// ---------------------------------------------------------------
// Case Actions (reprocess etc.)
// ---------------------------------------------------------------
function caseAction(action) {
  var labels = {
    'approve': 'Approve this case',
    'reject': 'Reject this case',
    'escalate': 'Escalate this case',
  };
  if (!confirm('Are you sure you want to: ' + (labels[action] || action) + '?')) return;

  var csrfEl = document.querySelector('[name=csrfmiddlewaretoken]');
  if (!csrfEl) return;

  var actionsEl = document.querySelector('.av-identity__actions');
  var form = document.createElement('form');
  form.method = 'POST';
  form.style.display = 'none';

  // Route approve/reject to review decide endpoint, escalate to reprocess
  var decisionMap = { 'approve': 'APPROVED', 'reject': 'REJECTED' };
  if (decisionMap[action] && actionsEl && actionsEl.dataset.decideUrl) {
    form.action = actionsEl.dataset.decideUrl;
    var dec = document.createElement('input');
    dec.type = 'hidden'; dec.name = 'decision'; dec.value = decisionMap[action];
    form.appendChild(dec);
  } else {
    var reprocessUrl = actionsEl ? actionsEl.dataset.reprocessUrl : '';
    if (!reprocessUrl) return;
    form.action = reprocessUrl;
    var stg = document.createElement('input');
    stg.type = 'hidden'; stg.name = 'stage'; stg.value = 'INTAKE';
    form.appendChild(stg);
  }

  var csrf = document.createElement('input');
  csrf.type = 'hidden'; csrf.name = 'csrfmiddlewaretoken'; csrf.value = csrfEl.value;
  form.appendChild(csrf);
  document.body.appendChild(form);
  form.submit();
}
