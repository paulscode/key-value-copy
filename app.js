/* ============================================================
   Key Value Copy — Application Logic
   ============================================================ */

(() => {
  'use strict';

  // ── Constants ──────────────────────────────────────────────
  const CLIPBOARD_CLEAR_MS = 30000;
  const PEEK_TIMEOUT_MS    = 5000;
  const TOAST_DURATION_MS  = 3500;
  const PARTICLE_COUNT     = 45;

  // ── State ──────────────────────────────────────────────────
  let entries = [];
  let cryptoKey = null;
  let hasCrypto = !!(window.crypto && window.crypto.subtle);
  let editingId = null;
  let deletingId = null;
  let clipboardTimer = null;
  let peekTimers = {};
  let sessionToken = null;

  // ── DOM References ─────────────────────────────────────────
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  const dom = {
    particles:      $('#particles'),
    emptyState:     $('#emptyState'),
    entryList:      $('#entryList'),
    countNumber:    $('#countNumber'),
    fabAdd:         $('#fabAdd'),
    // Add/Edit modal
    modalOverlay:   $('#modalOverlay'),
    modalTitle:     $('#modalTitle'),
    modalClose:     $('#modalClose'),
    entryForm:      $('#entryForm'),
    inputKey:       $('#inputKey'),
    inputValue:     $('#inputValue'),
    toggleVis:      $('#toggleValueVisibility'),
    modalCancel:    $('#modalCancel'),
    modalSave:      $('#modalSave'),
    // Delete modal
    deleteOverlay:  $('#deleteOverlay'),
    deleteKeyName:  $('#deleteKeyName'),
    deleteCancel:   $('#deleteCancel'),
    deleteConfirm:  $('#deleteConfirm'),
    // Export modal
    btnExport:      $('#btnExport'),
    exportOverlay:  $('#exportOverlay'),
    exportForm:     $('#exportForm'),
    exportPassword: $('#exportPassword'),
    exportPasswordConfirm: $('#exportPasswordConfirm'),
    exportClose:    $('#exportClose'),
    exportCancel:   $('#exportCancel'),
    exportError:    $('#exportError'),
    toggleExportPw1:$('#toggleExportPw1'),
    toggleExportPw2:$('#toggleExportPw2'),
    // Import modal
    btnImport:      $('#btnImport'),
    importOverlay:  $('#importOverlay'),
    importForm:     $('#importForm'),
    importFile:     $('#importFile'),
    importPassword: $('#importPassword'),
    importClose:    $('#importClose'),
    importCancel:   $('#importCancel'),
    importSubmit:   $('#importSubmit'),
    importError:    $('#importError'),
    toggleImportPw: $('#toggleImportPw'),
    fileDropZone:   $('#fileDropZone'),
    fileDropContent:$('#fileDropContent'),
    fileSelectedInfo:$('#fileSelectedInfo'),
    fileName:       $('#fileName'),
    // Toast
    toastContainer: $('#toastContainer'),
    // Login
    loginOverlay:   $('#loginOverlay'),
    loginForm:      $('#loginForm'),
    loginPassword:  $('#loginPassword'),
    loginError:     $('#loginError'),
    loginSubmit:    $('#loginSubmit'),
    toggleLoginPw:  $('#toggleLoginPw'),
    // Devices
    btnDevices:     $('#btnDevices'),
    devicesOverlay: $('#devicesOverlay'),
    devicesClose:   $('#devicesClose'),
    devicesCancel:  $('#devicesCancel'),
    devicesList:    $('#devicesList'),
    devicesEmpty:   $('#devicesEmpty'),
    devicesActions: $('#devicesActions'),
    btnAddDevice:   $('#btnAddDevice'),
    pairingPanel:   $('#pairingPanel'),
    pairingQr:      $('#pairingQr'),
    pairingCode:    $('#pairingCode'),
    pairingEndpoints: $('#pairingEndpoints'),
    pairingCountdown: $('#pairingCountdown'),
    pairingCopy:    $('#pairingCopy'),
    pairingDone:    $('#pairingDone'),
  };

  // ── API Helper ─────────────────────────────────────────────
  async function api(method, path, body = null) {
    const headers = {};
    if (sessionToken) headers['Authorization'] = `Bearer ${sessionToken}`;
    const opts = { method, headers };
    if (body !== null) {
      headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(path, opts);
    const data = await resp.json();
    if (!resp.ok) {
      const err = new Error(data.error || 'Request failed');
      err.status = resp.status;
      throw err;
    }
    return data;
  }

  // ── Authentication ─────────────────────────────────────────
  function applyApiEnabled(enabled) {
    // Hide the Devices screen entirely when the device-sync API is turned off.
    dom.btnDevices.style.display = (enabled === false) ? 'none' : '';
  }

  async function checkAuth() {
    const stored = sessionStorage.getItem('kvc_session');
    if (!stored) return false;
    sessionToken = stored;
    try {
      const data = await api('GET', '/api/check');
      applyApiEnabled(data.api_enabled);
      return true;
    } catch {
      sessionToken = null;
      sessionStorage.removeItem('kvc_session');
      return false;
    }
  }

  async function login(password) {
    const data = await api('POST', '/api/login', { password });
    sessionToken = data.token;
    sessionStorage.setItem('kvc_session', sessionToken);
    applyApiEnabled(data.api_enabled);
  }

  function showLoginOverlay() {
    dom.loginOverlay.classList.add('open');
    dom.loginPassword.value = '';
    dom.loginError.classList.add('hidden');
    setTimeout(() => dom.loginPassword.focus(), 200);
  }

  function hideLoginOverlay() {
    dom.loginOverlay.classList.remove('open');
  }

  async function handleLogin(e) {
    e.preventDefault();
    const pw = dom.loginPassword.value;
    if (!pw) return;
    dom.loginSubmit.disabled = true;
    try {
      await login(pw);
      await loadFromServer();
    } catch {
      dom.loginError.textContent = 'Incorrect password';
      dom.loginError.classList.remove('hidden');
      dom.loginPassword.select();
    } finally {
      dom.loginSubmit.disabled = false;
    }
  }

  async function loadFromServer() {
    await initCrypto();
    vault.clear();
    sinceSeq = 0;
    await enqueueSync(() => pull(0));
    hideLoginOverlay();
    startPolling();
  }

  // ── Crypto Module ──────────────────────────────────────────
  async function initCrypto() {
    if (!hasCrypto) return;
    try {
      const data = await api('GET', '/api/v1/vault-key');
      if (data && data.cryptoKey) {
        cryptoKey = await crypto.subtle.importKey(
          'jwk', data.cryptoKey,
          { name: 'AES-GCM', length: 256 },
          true,
          ['encrypt', 'decrypt']
        );
      } else {
        cryptoKey = await crypto.subtle.generateKey(
          { name: 'AES-GCM', length: 256 },
          true,
          ['encrypt', 'decrypt']
        );
        const jwk = await crypto.subtle.exportKey('jwk', cryptoKey);
        await api('PUT', '/api/v1/vault-key', { cryptoKey: jwk });
      }
    } catch {
      hasCrypto = false;
    }
  }

  async function encryptValue(text) {
    if (!hasCrypto || !cryptoKey) {
      // Fallback: base64 obfuscation
      return { _b64: btoa(unescape(encodeURIComponent(text))) };
    }
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const encoded = new TextEncoder().encode(text);
    const ciphertext = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv },
      cryptoKey,
      encoded
    );
    return {
      iv: Array.from(iv),
      ct: Array.from(new Uint8Array(ciphertext))
    };
  }

  async function decryptValue(enc) {
    if (enc._b64) {
      return decodeURIComponent(escape(atob(enc._b64)));
    }
    if (!hasCrypto || !cryptoKey) return '●●●●●●●●';
    const iv = new Uint8Array(enc.iv);
    const ct = new Uint8Array(enc.ct);
    const decrypted = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv },
      cryptoKey,
      ct
    );
    return new TextDecoder().decode(decrypted);
  }

  // ── Sync client ────────────────────────────────────────────
  // `vault` is the authoritative local mirror (id -> full record incl. rev/hlc/
  // order/deleted/conflict). `entries` is the derived, live, ordered view the UI
  // renders. Every mutation updates the vault optimistically, re-renders, then
  // pushes a delta to /api/v1/sync and reconciles the server's response — so two
  // browsers (or, later, the Android app) editing concurrently converge instead
  // of clobbering each other the way the old whole-blob PUT did.
  const vault = new Map();
  const dirty = new Set();          // ids with un-pushed local changes
  let sinceSeq = 0;
  let pollTimer = null;
  let syncChain = Promise.resolve();

  // Client Hybrid Logical Clock (mirrors the server/sync-engine format).
  const ORDER_GAP = 1024;
  const deviceNode = (() => {
    let n = localStorage.getItem('kvc_node');
    if (!n) { n = Math.random().toString(16).slice(2, 6); localStorage.setItem('kvc_node', n); }
    return n;
  })();
  const hlcState = { p: 0, c: 0 };
  function hlcNow() {
    const pt = Date.now();
    if (pt > hlcState.p) { hlcState.p = pt; hlcState.c = 0; }
    else { hlcState.c += 1; }
    return `${hlcState.p}:${hlcState.c}:${deviceNode}`;
  }
  function observeHlc(hlc) {
    if (!hlc) return;
    const parts = String(hlc).split(':');
    const rp = parseInt(parts[0], 10) || 0;
    const rc = parseInt(parts[1], 10) || 0;
    if (rp > hlcState.p) { hlcState.p = rp; hlcState.c = rc; }
    else if (rp === hlcState.p && rc > hlcState.c) { hlcState.c = rc; }
  }

  function generateId() {
    if (crypto && crypto.randomUUID) return crypto.randomUUID();
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }

  function newOrder() {
    let max = 0;
    for (const e of vault.values()) if ((e.order || 0) > max) max = e.order;
    return max + ORDER_GAP;
  }

  function rebuildEntries() {
    entries = [...vault.values()]
      .filter(e => !e.deleted)
      .sort((a, b) => (a.order || 0) - (b.order || 0));
  }

  // Serialize all sync calls so `sinceSeq` never races between a push and a poll.
  function enqueueSync(fn) {
    syncChain = syncChain.then(fn).catch(err => { console.error('sync error', err); });
    return syncChain;
  }

  function applyServerChanges(resp) {
    if (!resp) return;
    observeHlc(resp.server_hlc);
    if (typeof resp.new_since === 'number') sinceSeq = resp.new_since;
    for (const e of (resp.changes || [])) {
      observeHlc(e.hlc);
      // Never overwrite an entry with an un-pushed local edit still pending:
      // its optimistic value (and its last-confirmed base_rev) must survive until
      // its own flush reconciles it, or the edit is silently lost with no
      // conflict sibling.
      if (dirty.has(e.id)) continue;
      vault.set(e.id, e);
    }
    rebuildEntries();
    renderIfChanged();
  }

  async function pull(since) {
    const resp = await api('POST', '/api/v1/sync',
      { since, device_hlc: hlcNow(), changes: [] });
    applyServerChanges(resp);
  }

  function buildChange(e) {
    // Built at SEND time from current vault state, so base_rev reflects the
    // latest server-assigned rev — this is what prevents a fast add-then-edit
    // from racing itself into a spurious conflict.
    const c = { id: e.id, key: e.key, value: e.value,
                base_rev: e.rev || 0, hlc: e.hlc, deleted: !!e.deleted };
    if (e.order != null) c.order = e.order;
    return c;
  }

  function markDirty(id) {
    dirty.add(id);
  }

  function flushDirty() {
    return enqueueSync(async () => {
      if (!dirty.size) return;
      const ids = [...dirty];
      dirty.clear();
      const changes = [];
      for (const id of ids) {
        const e = vault.get(id);
        if (e) changes.push(buildChange(e));
      }
      if (!changes.length) return;
      try {
        const resp = await api('POST', '/api/v1/sync',
          { since: sinceSeq, device_hlc: hlcNow(), changes });
        applyServerChanges(resp);
      } catch (err) {
        console.error('Failed to sync:', err);
        showToast('Sync failed — check connection', 'error');
        ids.forEach(id => dirty.add(id));   // requeue for the next flush/poll
      }
    });
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(() => {
      if (dirty.size) flushDirty();                       // retry pending pushes
      else enqueueSync(() => pull(sinceSeq).catch(() => {}));  // pick up others' edits
    }, 15000);
  }

  // ── Rendering ──────────────────────────────────────────────
  function updateCount() {
    dom.countNumber.textContent = entries.length;
  }

  function toggleEmptyState() {
    if (entries.length === 0) {
      dom.emptyState.classList.remove('hidden');
    } else {
      dom.emptyState.classList.add('hidden');
    }
  }

  // Signature of everything the rendered list shows (value is masked, so it's
  // deliberately excluded — a background value change shouldn't repaint the DOM).
  let lastRenderSig = null;
  function renderSignature() {
    return entries
      .map(e => `${e.id}\x1f${e.order}\x1f${e.key}\x1f${e.deleted ? 1 : 0}\x1f${e.conflict ? 1 : 0}`)
      .join('\x1e');
  }

  // Keys shared by 2+ live entries — surfaced as a soft "duplicate name" hint.
  let dupKeys = new Set();
  function computeDupKeys() {
    const counts = {};
    entries.forEach(e => { counts[e.key] = (counts[e.key] || 0) + 1; });
    dupKeys = new Set(Object.keys(counts).filter(k => counts[k] > 1));
  }

  function render() {
    computeDupKeys();
    dom.entryList.innerHTML = '';
    entries.forEach((entry, index) => {
      dom.entryList.appendChild(createEntryRow(entry, index));
    });
    updateCount();
    toggleEmptyState();
    initDragDrop();
    lastRenderSig = renderSignature();
  }

  // Repaint only when the visible list actually changed — this is what keeps the
  // 15s background poll (and the echo of our own pushes) from flashing the UI.
  function renderIfChanged() {
    if (renderSignature() === lastRenderSig) return;
    render();
  }

  function createEntryRow(entry, index) {
    const row = document.createElement('div');
    row.className = 'entry-row';
    row.dataset.id = entry.id;
    row.setAttribute('role', 'listitem');

    // Drag handle
    const handle = document.createElement('div');
    handle.className = 'drag-handle';
    handle.setAttribute('aria-label', 'Drag to reorder');
    handle.innerHTML = `
      <div class="drag-handle__dots">
        <span class="drag-handle__dot"></span>
        <span class="drag-handle__dot"></span>
        <span class="drag-handle__dot"></span>
        <span class="drag-handle__dot"></span>
        <span class="drag-handle__dot"></span>
        <span class="drag-handle__dot"></span>
      </div>`;

    // Content
    const content = document.createElement('div');
    content.className = 'entry-content';

    const keyLine = document.createElement('div');
    keyLine.className = 'entry-key-line';
    const keySpan = document.createElement('span');
    keySpan.className = 'entry-key';
    keySpan.textContent = entry.key;
    keySpan.title = entry.key;
    keyLine.appendChild(keySpan);
    if (entry.conflict) {
      const b = document.createElement('span');
      b.className = 'entry-badge entry-badge--conflict';
      b.textContent = 'conflict';
      keyLine.appendChild(b);
    } else if (dupKeys.has(entry.key)) {
      const b = document.createElement('span');
      b.className = 'entry-badge entry-badge--dup';
      b.textContent = 'duplicate name';
      b.title = 'Another entry has the same name';
      keyLine.appendChild(b);
    }

    const valueSpan = document.createElement('span');
    valueSpan.className = 'entry-value';
    valueSpan.textContent = '••••••••';
    valueSpan.dataset.entryId = entry.id;

    content.appendChild(keyLine);
    content.appendChild(valueSpan);

    // Actions
    const actions = document.createElement('div');
    actions.className = 'entry-actions';

    // Peek button
    const peekBtn = createActionButton('Reveal', 'btn-action', `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
        <circle cx="12" cy="12" r="3"/>
      </svg>`);
    peekBtn.addEventListener('click', () => togglePeek(entry.id, valueSpan, peekBtn));

    // Edit button
    const editBtn = createActionButton('Edit', 'btn-action', `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
      </svg>`);
    editBtn.addEventListener('click', () => openEditModal(entry.id));

    // Copy button
    const copyBtn = createActionButton('Copy value', 'btn-action btn-action--copy', `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
      </svg>`);
    copyBtn.addEventListener('click', () => copyValue(entry.id, copyBtn));

    // Delete button
    const deleteBtn = createActionButton('Delete', 'btn-action btn-action--danger', `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="3 6 5 6 21 6"/>
        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
      </svg>`);
    deleteBtn.addEventListener('click', () => confirmDelete(entry.id));

    actions.appendChild(peekBtn);
    actions.appendChild(editBtn);
    actions.appendChild(copyBtn);
    actions.appendChild(deleteBtn);

    row.appendChild(handle);
    row.appendChild(content);
    row.appendChild(actions);

    if (entry.conflict) {
      row.classList.add('entry-row--conflict');
      row.appendChild(createConflictBar(entry));
    }

    return row;
  }

  // ── Conflict resolution UI ─────────────────────────────────
  function mkResolveBtn(label, fn) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn-conflict';
    b.textContent = label;
    b.addEventListener('click', fn);
    return b;
  }

  function createConflictBar(entry) {
    const bar = document.createElement('div');
    bar.className = 'conflict-bar';
    const other = entry.conflict && entry.conflict.of ? vault.get(entry.conflict.of) : null;
    const otherLive = other && !other.deleted;

    const msg = document.createElement('span');
    msg.className = 'conflict-bar__msg';
    const acts = document.createElement('div');
    acts.className = 'conflict-bar__actions';

    if (otherLive) {
      msg.textContent = `A different version of "${other.key}" was kept — which do you want?`;
      acts.appendChild(mkResolveBtn('Keep this', () => resolveConflict(entry.id, 'keep-this')));
      acts.appendChild(mkResolveBtn('Keep other', () => resolveConflict(entry.id, 'keep-other')));
      acts.appendChild(mkResolveBtn('Keep both', () => resolveConflict(entry.id, 'keep-both')));
    } else {
      // No live "other" (it was deleted/renamed away, or this is a resurrected
      // entry flagged for review) — the only meaningful action is to acknowledge.
      msg.textContent = 'Flagged after a sync conflict. Review the value, then dismiss.';
      acts.appendChild(mkResolveBtn('Dismiss', () => resolveConflict(entry.id, 'dismiss')));
    }
    bar.appendChild(msg);
    bar.appendChild(acts);
    return bar;
  }

  // Resolving is an ordinary edit/delete that syncs normally (which also clears
  // the conflict flag server-side), so each action just mutates the vault and
  // flushes. Robust to nested "(conflict)" rows and a missing/deleted "other".
  function resolveConflict(id, action) {
    const s = vault.get(id);
    if (!s || !s.conflict) return;
    const other = s.conflict.of ? vault.get(s.conflict.of) : null;
    const otherLive = other && !other.deleted;

    if (action === 'keep-this' && otherLive) {
      other.value = s.value;              // the original entry adopts this value
      other.hlc = hlcNow();
      other.conflict = null;
      markDirty(other.id);
      s.deleted = true;                   // drop this conflict copy
      s.hlc = hlcNow();
      markDirty(s.id);
      showToast('Kept this version', 'success');
    } else if (action === 'keep-other' && otherLive) {
      s.deleted = true;                   // drop this copy; the original stays
      s.hlc = hlcNow();
      markDirty(s.id);
      showToast('Kept the other version', 'success');
    } else {
      // keep-both / dismiss / (keep-this|keep-other with no live other):
      // just clear the flag so this entry becomes a normal, standalone entry.
      s.conflict = null;
      s.hlc = hlcNow();
      markDirty(s.id);
      showToast('Conflict resolved', 'success');
    }
    rebuildEntries();
    render();
    flushDirty();
  }

  function createActionButton(title, className, svgHTML) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = className;
    btn.title = title;
    btn.setAttribute('aria-label', title);
    btn.innerHTML = svgHTML;
    return btn;
  }

  // ── Peek / Reveal ──────────────────────────────────────────
  async function togglePeek(id, valueSpan, btn) {
    const isRevealed = valueSpan.classList.contains('revealed');

    if (isRevealed) {
      // Hide
      valueSpan.textContent = '••••••••';
      valueSpan.classList.remove('revealed');
      btn.innerHTML = `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
          <circle cx="12" cy="12" r="3"/>
        </svg>`;
      btn.title = 'Reveal';
      if (peekTimers[id]) {
        clearTimeout(peekTimers[id]);
        delete peekTimers[id];
      }
    } else {
      // Reveal
      const entry = entries.find(e => e.id === id);
      if (!entry) return;
      try {
        const plainValue = await decryptValue(entry.value);
        valueSpan.textContent = plainValue;
        valueSpan.classList.add('revealed');
        btn.innerHTML = `
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/>
            <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>
            <path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/>
            <line x1="1" y1="1" x2="23" y2="23"/>
          </svg>`;
        btn.title = 'Hide';

        // Auto-hide after timeout
        peekTimers[id] = setTimeout(() => {
          valueSpan.textContent = '••••••••';
          valueSpan.classList.remove('revealed');
          btn.innerHTML = `
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
              <circle cx="12" cy="12" r="3"/>
            </svg>`;
          btn.title = 'Reveal';
          delete peekTimers[id];
        }, PEEK_TIMEOUT_MS);
      } catch {
        showToast('Failed to reveal value', 'error');
      }
    }
  }

  // ── Copy to Clipboard ──────────────────────────────────────
  async function copyValue(id, btn) {
    const entry = entries.find(e => e.id === id);
    if (!entry) return;

    try {
      const plainValue = await decryptValue(entry.value);
      await navigator.clipboard.writeText(plainValue);

      // Visual feedback
      btn.classList.add('copied');
      setTimeout(() => btn.classList.remove('copied'), 600);

      showToast('Value copied — clipboard clears in 30s', 'success');

      // Auto-clear clipboard after timeout
      if (clipboardTimer) clearTimeout(clipboardTimer);
      clipboardTimer = setTimeout(async () => {
        try {
          await navigator.clipboard.writeText('');
          showToast('Clipboard cleared for security', 'info');
        } catch { /* ignore — tab may be inactive */ }
        clipboardTimer = null;
      }, CLIPBOARD_CLEAR_MS);

    } catch {
      showToast('Failed to copy to clipboard', 'error');
    }
  }

  // ── Add / Edit Modal ──────────────────────────────────────
  function openAddModal() {
    editingId = null;
    dom.modalTitle.textContent = 'Add New Entry';
    dom.modalSave.querySelector('svg + text, span')?.remove();
    dom.inputKey.value = '';
    dom.inputValue.value = '';
    dom.inputValue.type = 'password';
    resetToggleVis();
    openModal(dom.modalOverlay);
    setTimeout(() => dom.inputKey.focus(), 200);
  }

  async function openEditModal(id) {
    const entry = entries.find(e => e.id === id);
    if (!entry) return;

    editingId = id;
    dom.modalTitle.textContent = 'Edit Entry';
    dom.inputKey.value = entry.key;

    try {
      const plainValue = await decryptValue(entry.value);
      dom.inputValue.value = plainValue;
    } catch {
      dom.inputValue.value = '';
    }

    dom.inputValue.type = 'password';
    resetToggleVis();
    openModal(dom.modalOverlay);
    setTimeout(() => dom.inputKey.focus(), 200);
  }

  async function handleSave(e) {
    e.preventDefault();

    const key = dom.inputKey.value.trim();
    const value = dom.inputValue.value;

    if (!key) {
      dom.inputKey.focus();
      return;
    }
    if (!value) {
      dom.inputValue.focus();
      return;
    }

    const encryptedValue = await encryptValue(value);

    if (editingId) {
      // Update existing
      const entry = vault.get(editingId);
      if (entry) {
        entry.key = key;
        entry.value = encryptedValue;
        entry.hlc = hlcNow();
        markDirty(entry.id);
      }
      showToast('Entry updated', 'success');
    } else {
      // Add new
      const id = generateId();
      const order = newOrder();
      const hlc = hlcNow();
      vault.set(id, { id, key, value: encryptedValue, order, created: Date.now(),
                      rev: 0, hlc, deleted: false, conflict: null });
      markDirty(id);
      showToast('Entry added', 'success');
    }

    rebuildEntries();
    render();
    closeModal(dom.modalOverlay);
    editingId = null;
    flushDirty();
  }

  function resetToggleVis() {
    const eyeIcon = dom.toggleVis.querySelector('.icon-eye');
    const eyeOffIcon = dom.toggleVis.querySelector('.icon-eye-off');
    eyeIcon.classList.remove('hidden');
    eyeOffIcon.classList.add('hidden');
  }

  function toggleValueVisibility() {
    const isPassword = dom.inputValue.type === 'password';
    dom.inputValue.type = isPassword ? 'text' : 'password';
    const eyeIcon = dom.toggleVis.querySelector('.icon-eye');
    const eyeOffIcon = dom.toggleVis.querySelector('.icon-eye-off');
    eyeIcon.classList.toggle('hidden');
    eyeOffIcon.classList.toggle('hidden');
  }

  // ── Delete ─────────────────────────────────────────────────
  function confirmDelete(id) {
    const entry = entries.find(e => e.id === id);
    if (!entry) return;
    deletingId = id;
    dom.deleteKeyName.textContent = entry.key;
    openModal(dom.deleteOverlay);
  }

  function applyDelete(id) {
    const entry = vault.get(id);
    if (!entry) return;
    entry.deleted = true;          // tombstone; the record stays for sync
    entry.hlc = hlcNow();
    markDirty(entry.id);
    rebuildEntries();
    render();
    flushDirty();
  }

  function handleDelete() {
    if (!deletingId) return;
    const id = deletingId;
    const row = dom.entryList.querySelector(`[data-id="${id}"]`);
    if (row) {
      row.classList.add('removing');
      row.addEventListener('animationend', () => applyDelete(id), { once: true });
    } else {
      applyDelete(id);
    }
    deletingId = null;
    closeModal(dom.deleteOverlay);
    showToast('Entry deleted', 'info');
  }

  // ── Modal Helpers ──────────────────────────────────────────
  function openModal(overlay) {
    overlay.classList.add('open');
    document.body.style.overflow = 'hidden';
  }

  function closeModal(overlay) {
    overlay.classList.remove('open');
    document.body.style.overflow = '';
    editingId = null;
  }

  function closeAllModals() {
    closeModal(dom.modalOverlay);
    closeModal(dom.deleteOverlay);
    closeModal(dom.exportOverlay);
    closeModal(dom.importOverlay);
    closeDevicesModal();
  }

  // ── Drag & Drop ────────────────────────────────────────────
  let dragState = null;

  function initDragDrop() {
    const handles = dom.entryList.querySelectorAll('.drag-handle');
    handles.forEach(handle => {
      handle.addEventListener('mousedown', onDragStart);
      handle.addEventListener('touchstart', onDragStart, { passive: false });
    });
  }

  function onDragStart(e) {
    e.preventDefault();
    const row = e.target.closest('.entry-row');
    if (!row) return;

    const rect = row.getBoundingClientRect();
    const clientY = e.type === 'touchstart' ? e.touches[0].clientY : e.clientY;

    dragState = {
      el: row,
      id: row.dataset.id,
      startY: clientY,
      offsetY: clientY - rect.top,
      initialIndex: getEntryIndex(row),
      currentIndex: getEntryIndex(row),
      rows: Array.from(dom.entryList.querySelectorAll('.entry-row')),
      rects: [],
    };

    // Cache rects
    dragState.rects = dragState.rows.map(r => r.getBoundingClientRect());

    row.classList.add('dragging');
    row.style.position = 'relative';
    row.style.zIndex = '100';

    document.addEventListener('mousemove', onDragMove);
    document.addEventListener('mouseup', onDragEnd);
    document.addEventListener('touchmove', onDragMove, { passive: false });
    document.addEventListener('touchend', onDragEnd);
  }

  function onDragMove(e) {
    if (!dragState) return;
    e.preventDefault();

    const clientY = e.type === 'touchmove' ? e.touches[0].clientY : e.clientY;
    const deltaY = clientY - dragState.startY;

    // Move dragged element
    dragState.el.style.transform = `translateY(${deltaY}px) scale(1.02)`;

    // Determine new position
    const draggedCenter = dragState.rects[dragState.initialIndex].top + dragState.rects[dragState.initialIndex].height / 2 + deltaY;

    let newIndex = dragState.initialIndex;
    for (let i = 0; i < dragState.rects.length; i++) {
      const rowCenter = dragState.rects[i].top + dragState.rects[i].height / 2;
      if (draggedCenter > rowCenter) {
        newIndex = i;
      }
    }

    // Move other rows
    dragState.rows.forEach((row, i) => {
      if (row === dragState.el) return;

      if (i >= Math.min(dragState.initialIndex, newIndex) &&
          i <= Math.max(dragState.initialIndex, newIndex)) {
        const direction = newIndex > dragState.initialIndex ? -1 : 1;
        if ((direction === -1 && i > dragState.initialIndex && i <= newIndex) ||
            (direction === 1 && i < dragState.initialIndex && i >= newIndex)) {
          const rowHeight = dragState.rects[dragState.initialIndex].height + 8; // gap
          row.style.transition = 'transform 0.2s ease';
          row.style.transform = `translateY(${direction * rowHeight}px)`;
        } else {
          row.style.transition = 'transform 0.2s ease';
          row.style.transform = '';
        }
      } else {
        row.style.transition = 'transform 0.2s ease';
        row.style.transform = '';
      }
    });

    dragState.currentIndex = newIndex;
  }

  function onDragEnd() {
    if (!dragState) return;

    document.removeEventListener('mousemove', onDragMove);
    document.removeEventListener('mouseup', onDragEnd);
    document.removeEventListener('touchmove', onDragMove);
    document.removeEventListener('touchend', onDragEnd);

    const { initialIndex, currentIndex, el, rows } = dragState;

    // Reset inline styles
    rows.forEach(row => {
      row.style.transition = '';
      row.style.transform = '';
      row.style.position = '';
      row.style.zIndex = '';
    });
    el.classList.remove('dragging');

    // Reorder data: reassign sparse order values and push only what moved.
    if (initialIndex !== currentIndex) {
      const [moved] = entries.splice(initialIndex, 1);
      entries.splice(currentIndex, 0, moved);
      entries.forEach((e, i) => {
        const ord = (i + 1) * ORDER_GAP;
        if (e.order !== ord) {
          e.order = ord;
          e.hlc = hlcNow();
          markDirty(e.id);
        }
      });
      render();
      flushDirty();
    }

    dragState = null;
  }

  function getEntryIndex(row) {
    const rows = Array.from(dom.entryList.querySelectorAll('.entry-row'));
    return rows.indexOf(row);
  }

  // ── Toast System ───────────────────────────────────────────
  function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast--${type}`;

    const iconSvg = getToastIcon(type);
    toast.innerHTML = `
      <span class="toast__icon">${iconSvg}</span>
      <span class="toast__message">${escapeHtml(message)}</span>`;

    dom.toastContainer.appendChild(toast);

    setTimeout(() => {
      toast.classList.add('removing');
      toast.addEventListener('animationend', () => toast.remove(), { once: true });
    }, TOAST_DURATION_MS);
  }

  function getToastIcon(type) {
    const icons = {
      success: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`,
      info:    `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`,
      warning: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
      error:   `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,
    };
    return icons[type] || icons.info;
  }

  // ── Particles ──────────────────────────────────────────────
  function createParticles() {
    const container = dom.particles;
    const colors = ['#00ff41', '#00ff41', '#00ff41', '#39ff14', '#00e5a0', '#7dffb3'];

    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const particle = document.createElement('div');
      particle.className = 'particle';
      const size = Math.random() * 3.5 + 1.5;
      const color = colors[Math.floor(Math.random() * colors.length)];
      particle.style.setProperty('--x', (Math.random() * 100).toFixed(1) + '%');
      particle.style.setProperty('--size', size.toFixed(1) + 'px');
      particle.style.setProperty('--duration', (Math.random() * 22 + 12).toFixed(1) + 's');
      particle.style.setProperty('--delay', (Math.random() * 25).toFixed(1) + 's');
      particle.style.setProperty('--drift', (Math.random() * 80 - 40).toFixed(0) + 'px');
      particle.style.setProperty('--peak-opacity', (Math.random() * 0.5 + 0.15).toFixed(2));
      particle.style.setProperty('--color', color);
      container.appendChild(particle);
    }
  }

  // ── Utilities ──────────────────────────────────────────────
  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ── Password-Based Crypto (PBKDF2 + AES-GCM) ──────────────
  const PBKDF2_ITERATIONS = 600000;
  const EXPORT_MAGIC = 'KVC_EXPORT_V1';

  async function deriveKeyFromPassword(password, salt) {
    if (!crypto.subtle) {
      throw new Error('Export/Import requires a secure context (use https:// or localhost).');
    }
    const enc = new TextEncoder();
    const keyMaterial = await crypto.subtle.importKey(
      'raw', enc.encode(password), 'PBKDF2', false, ['deriveKey']
    );
    return crypto.subtle.deriveKey(
      { name: 'PBKDF2', salt, iterations: PBKDF2_ITERATIONS, hash: 'SHA-256' },
      keyMaterial,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt']
    );
  }

  async function encryptWithPassword(plaintext, password) {
    const salt = crypto.getRandomValues(new Uint8Array(16));
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const key = await deriveKeyFromPassword(password, salt);
    const encoded = new TextEncoder().encode(plaintext);
    const ciphertext = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, encoded);
    return {
      magic: EXPORT_MAGIC,
      salt: Array.from(salt),
      iv: Array.from(iv),
      ct: Array.from(new Uint8Array(ciphertext))
    };
  }

  async function decryptWithPassword(envelope, password) {
    const salt = new Uint8Array(envelope.salt);
    const iv = new Uint8Array(envelope.iv);
    const ct = new Uint8Array(envelope.ct);
    const key = await deriveKeyFromPassword(password, salt);
    const decrypted = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, ct);
    return new TextDecoder().decode(decrypted);
  }

  // ── Export ──────────────────────────────────────────────────
  function openExportModal() {
    if (entries.length === 0) {
      showToast('Nothing to export', 'warning');
      return;
    }
    dom.exportPassword.value = '';
    dom.exportPasswordConfirm.value = '';
    dom.exportPassword.type = 'password';
    dom.exportPasswordConfirm.type = 'password';
    resetPeekButton(dom.toggleExportPw1);
    resetPeekButton(dom.toggleExportPw2);
    dom.exportError.classList.add('hidden');
    openModal(dom.exportOverlay);
    setTimeout(() => dom.exportPassword.focus(), 200);
  }

  async function handleExport(e) {
    e.preventDefault();
    const pw = dom.exportPassword.value;
    const pwConfirm = dom.exportPasswordConfirm.value;

    if (pw !== pwConfirm) {
      dom.exportError.textContent = 'Passwords do not match.';
      dom.exportError.classList.remove('hidden');
      dom.exportPasswordConfirm.focus();
      return;
    }

    if (!pw) {
      dom.exportError.textContent = 'Password is required.';
      dom.exportError.classList.remove('hidden');
      dom.exportPassword.focus();
      return;
    }

    if (!hasCrypto) {
      dom.exportError.textContent = 'Export requires a secure context (use https:// or localhost).';
      dom.exportError.classList.remove('hidden');
      return;
    }

    try {
      // Decrypt all values to plaintext for export
      const plainEntries = [];
      for (const entry of entries) {
        try {
          const plainValue = await decryptValue(entry.value);
          plainEntries.push({ key: entry.key, value: plainValue });
        } catch (decErr) {
          console.error('Failed to decrypt entry:', entry.key, decErr);
          throw new Error(`Could not decrypt entry "${entry.key}". The encryption key may have changed.`);
        }
      }

      const payload = JSON.stringify(plainEntries);
      const envelope = await encryptWithPassword(payload, pw);
      const blob = new Blob([JSON.stringify(envelope, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);

      const a = document.createElement('a');
      a.href = url;
      const date = new Date().toISOString().slice(0, 10);
      a.download = `key-value-copy-${date}.kvc`;
      document.body.appendChild(a);
      a.click();

      // Delay cleanup so the download can start
      setTimeout(() => {
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      }, 100);

      closeModal(dom.exportOverlay);
      showToast(`Exported ${plainEntries.length} entries`, 'success');
    } catch (err) {
      console.error('Export failed:', err);
      dom.exportError.textContent = err.message || 'Export failed. Please try again.';
      dom.exportError.classList.remove('hidden');
    }
  }

  // ── Import ──────────────────────────────────────────────────
  let importFileData = null;

  function openImportModal() {
    importFileData = null;
    dom.importFile.value = '';
    dom.importPassword.value = '';
    dom.importPassword.type = 'password';
    resetPeekButton(dom.toggleImportPw);
    dom.importError.classList.add('hidden');
    dom.fileDropContent.classList.remove('hidden');
    dom.fileSelectedInfo.classList.add('hidden');
    openModal(dom.importOverlay);
  }

  function handleFileSelect(file) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const parsed = JSON.parse(ev.target.result);
        if (parsed.magic !== EXPORT_MAGIC) {
          dom.importError.textContent = 'Invalid file format. Please select a valid .kvc export file.';
          dom.importError.classList.remove('hidden');
          return;
        }
        importFileData = parsed;
        dom.fileName.textContent = file.name;
        dom.fileDropContent.classList.add('hidden');
        dom.fileSelectedInfo.classList.remove('hidden');
        dom.importError.classList.add('hidden');
      } catch {
        dom.importError.textContent = 'Could not read file. Please select a valid .kvc export file.';
        dom.importError.classList.remove('hidden');
      }
    };
    reader.readAsText(file);
  }

  function deduplicateKey(newKey, existingKeys) {
    if (!existingKeys.has(newKey)) return newKey;
    let counter = 1;
    while (existingKeys.has(`${newKey} (${counter})`)) {
      counter++;
    }
    return `${newKey} (${counter})`;
  }

  async function handleImport(e) {
    e.preventDefault();

    if (!importFileData) {
      dom.importError.textContent = 'Please select a file first.';
      dom.importError.classList.remove('hidden');
      return;
    }

    const pw = dom.importPassword.value;
    if (!pw) {
      dom.importError.textContent = 'Password is required.';
      dom.importError.classList.remove('hidden');
      dom.importPassword.focus();
      return;
    }

    if (!hasCrypto) {
      dom.importError.textContent = 'Import requires a secure context (use https:// or localhost).';
      dom.importError.classList.remove('hidden');
      return;
    }

    try {
      const decryptedJson = await decryptWithPassword(importFileData, pw);
      const importedEntries = JSON.parse(decryptedJson);

      if (!Array.isArray(importedEntries)) {
        throw new Error('Invalid data');
      }

      // Build set of existing keys for dedup
      const existingKeys = new Set(entries.map(e => e.key));
      let renamedCount = 0;
      let order = newOrder();

      for (const item of importedEntries) {
        if (!item.key || typeof item.value !== 'string') continue;

        const originalKey = item.key;
        const finalKey = deduplicateKey(originalKey, existingKeys);
        if (finalKey !== originalKey) renamedCount++;

        existingKeys.add(finalKey);

        const encryptedValue = await encryptValue(item.value);
        const id = generateId();
        const hlc = hlcNow();
        vault.set(id, { id, key: finalKey, value: encryptedValue, order,
                        created: Date.now(), rev: 0, hlc, deleted: false, conflict: null });
        markDirty(id);
        order += ORDER_GAP;
      }

      rebuildEntries();
      render();
      flushDirty();
      closeModal(dom.importOverlay);

      let msg = `Imported ${importedEntries.length} entries`;
      if (renamedCount > 0) {
        msg += ` (${renamedCount} renamed to avoid duplicates)`;
      }
      showToast(msg, 'success');
      importFileData = null;

    } catch (err) {
      // AES-GCM decryption fails with DOMException if wrong password
      if (err instanceof DOMException || err.name === 'OperationError') {
        dom.importError.textContent = 'Incorrect password. Please try again.';
      } else {
        dom.importError.textContent = 'Import failed. The file may be corrupted.';
      }
      dom.importError.classList.remove('hidden');
      dom.importPassword.focus();
      dom.importPassword.select();
    }
  }

  // ── Devices / API keys ─────────────────────────────────────
  let pairingTimer = null;
  let pairingPoll = null;

  async function openDevicesModal() {
    hidePairingPanel();
    openModal(dom.devicesOverlay);
    await loadDevices();
  }

  function closeDevicesModal() {
    hidePairingPanel();
    closeModal(dom.devicesOverlay);
  }

  function hidePairingPanel() {
    if (pairingTimer) { clearInterval(pairingTimer); pairingTimer = null; }
    if (pairingPoll) { clearInterval(pairingPoll); pairingPoll = null; }
    dom.pairingPanel.classList.add('hidden');
    dom.devicesActions.classList.remove('hidden');
  }

  function fmtWhen(ms) {
    if (!ms) return 'never';
    const s = Math.floor((Date.now() - ms) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
  }

  async function loadDevices() {
    try {
      const data = await api('GET', '/api/keys');
      renderDevices(data.keys || []);
    } catch {
      showToast('Failed to load devices', 'error');
    }
  }

  function renderDevices(keys) {
    dom.devicesList.innerHTML = '';
    dom.devicesEmpty.classList.toggle('hidden', keys.length > 0);
    keys.forEach(k => {
      const name = (k.label && k.label.trim())
        ? k.label
        : (k.status === 'pending' ? 'Awaiting pairing…' : 'Unnamed device');
      const meta = k.status === 'pending'
        ? 'Pending — share the pairing code'
        : `${k.key_prefix || ''} · ${k.last_transport || 'no'} transport · last synced ${fmtWhen(k.last_used)}`;

      const row = document.createElement('div');
      row.className = 'device-row';

      const info = document.createElement('div');
      info.className = 'device-info';
      const nameEl = document.createElement('div');
      nameEl.className = 'device-name';
      nameEl.textContent = name + ' ';
      const badge = document.createElement('span');
      badge.className = `device-badge device-badge--${k.status}`;
      badge.textContent = k.status;
      nameEl.appendChild(badge);
      const metaEl = document.createElement('div');
      metaEl.className = 'device-meta text-dim';
      metaEl.textContent = meta;
      info.appendChild(nameEl);
      info.appendChild(metaEl);

      const revoke = document.createElement('button');
      revoke.className = 'btn btn--danger btn--sm';
      revoke.textContent = 'Revoke';
      revoke.addEventListener('click', () => revokeDevice(k.id, name));

      row.appendChild(info);
      row.appendChild(revoke);
      dom.devicesList.appendChild(row);
    });
  }

  async function revokeDevice(id, name) {
    try {
      await api('DELETE', '/api/keys/' + encodeURIComponent(id));
      showToast(`Revoked ${name}`, 'info');
      await loadDevices();
    } catch {
      showToast('Failed to revoke device', 'error');
    }
  }

  async function addDevice() {
    try {
      // The device supplies its own name when it pairs, so no prompt here.
      const res = await api('POST', '/api/keys', { label: '' });
      showPairing(res.pairing, res.pairing_qr, res.key && res.key.id);
      await loadDevices();
    } catch {
      showToast('Failed to create device', 'error');
    }
  }

  function showPairing(pairing, qrSvg, keyId) {
    if (qrSvg) {
      dom.pairingQr.innerHTML = qrSvg;      // server-rendered SVG (trusted markup)
      dom.pairingQr.classList.remove('hidden');
    } else {
      dom.pairingQr.classList.add('hidden');
    }
    dom.pairingCode.textContent = pairing.enroll_code;
    // Endpoints the app should connect to. Prefer server-discovered LAN/Tor URLs;
    // fall back to the origin this browser is using if discovery isn't wired up.
    const endpoints = [];
    if (pairing.lan_url) endpoints.push(['LAN', pairing.lan_url]);
    if (pairing.onion_url) endpoints.push(['Tor', pairing.onion_url]);
    if (!endpoints.length) endpoints.push(['Server', window.location.origin]);
    dom.pairingEndpoints.innerHTML = '';
    endpoints.forEach(([label, url]) => {
      const row = document.createElement('div');
      row.className = 'pairing-endpoint';
      const l = document.createElement('span');
      l.className = 'pairing-endpoint__label';
      l.textContent = label;
      const v = document.createElement('span');
      v.className = 'pairing-endpoint__url';
      v.textContent = url;
      row.appendChild(l);
      row.appendChild(v);
      dom.pairingEndpoints.appendChild(row);
    });
    dom.pairingPanel.classList.remove('hidden');
    dom.devicesActions.classList.add('hidden');
    const exp = pairing.enroll_exp;
    const tick = () => {
      const left = Math.max(0, Math.floor((exp - Date.now()) / 1000));
      const m = Math.floor(left / 60), s = left % 60;
      dom.pairingCountdown.textContent = `${m}:${String(s).padStart(2, '0')}`;
      if (left <= 0) { hidePairingPanel(); loadDevices(); }
    };
    tick();
    pairingTimer = setInterval(tick, 1000);

    // Watch for the device claiming the code so the panel doesn't sit on
    // "Awaiting pairing…" after a successful pair. Poll the key's status and,
    // once it goes active, dismiss the panel and refresh the list.
    if (keyId) {
      pairingPoll = setInterval(async () => {
        try {
          const data = await api('GET', '/api/keys');
          const k = (data.keys || []).find(x => x.id === keyId);
          if (k && k.status === 'active') {
            hidePairingPanel();
            renderDevices(data.keys || []);
            showToast(`Paired ${k.label || 'device'}`, 'success');
          } else if (!k) {
            // Key vanished (expired sweep / revoked) — stop waiting.
            hidePairingPanel();
            loadDevices();
          }
        } catch { /* transient; keep polling */ }
      }, 2500);
    }
  }

  async function copyPairingCode() {
    try {
      await navigator.clipboard.writeText(dom.pairingCode.textContent);
      showToast('Pairing code copied', 'success');
    } catch {
      showToast('Copy failed', 'error');
    }
  }

  // ── Generic Peek Toggle for any password input ─────────────
  function setupPeekToggle(btn, input) {
    btn.addEventListener('click', () => {
      const isPassword = input.type === 'password';
      input.type = isPassword ? 'text' : 'password';
      btn.querySelector('.icon-eye').classList.toggle('hidden');
      btn.querySelector('.icon-eye-off').classList.toggle('hidden');
    });
  }

  function resetPeekButton(btn) {
    const eye = btn.querySelector('.icon-eye');
    const eyeOff = btn.querySelector('.icon-eye-off');
    if (eye) eye.classList.remove('hidden');
    if (eyeOff) eyeOff.classList.add('hidden');
  }

  // ── Event Binding ──────────────────────────────────────────
  function bindEvents() {
    // Login
    dom.loginForm.addEventListener('submit', handleLogin);
    setupPeekToggle(dom.toggleLoginPw, dom.loginPassword);

    // FAB
    dom.fabAdd.addEventListener('click', openAddModal);

    // Add/Edit modal
    dom.entryForm.addEventListener('submit', handleSave);
    dom.modalClose.addEventListener('click', () => closeModal(dom.modalOverlay));
    dom.modalCancel.addEventListener('click', () => closeModal(dom.modalOverlay));
    dom.toggleVis.addEventListener('click', toggleValueVisibility);

    // Delete modal
    dom.deleteCancel.addEventListener('click', () => closeModal(dom.deleteOverlay));
    dom.deleteConfirm.addEventListener('click', handleDelete);

    // Export modal
    dom.btnExport.addEventListener('click', openExportModal);
    dom.exportForm.addEventListener('submit', handleExport);
    dom.exportClose.addEventListener('click', () => closeModal(dom.exportOverlay));
    dom.exportCancel.addEventListener('click', () => closeModal(dom.exportOverlay));
    dom.exportOverlay.addEventListener('click', (e) => {
      if (e.target === dom.exportOverlay) closeModal(dom.exportOverlay);
    });
    setupPeekToggle(dom.toggleExportPw1, dom.exportPassword);
    setupPeekToggle(dom.toggleExportPw2, dom.exportPasswordConfirm);

    // Import modal
    dom.btnImport.addEventListener('click', openImportModal);
    dom.importForm.addEventListener('submit', handleImport);
    dom.importClose.addEventListener('click', () => closeModal(dom.importOverlay));
    dom.importCancel.addEventListener('click', () => closeModal(dom.importOverlay));
    dom.importOverlay.addEventListener('click', (e) => {
      if (e.target === dom.importOverlay) closeModal(dom.importOverlay);
    });
    setupPeekToggle(dom.toggleImportPw, dom.importPassword);

    // Devices modal
    dom.btnDevices.addEventListener('click', openDevicesModal);
    dom.devicesClose.addEventListener('click', closeDevicesModal);
    dom.devicesCancel.addEventListener('click', closeDevicesModal);
    dom.btnAddDevice.addEventListener('click', addDevice);
    dom.pairingCopy.addEventListener('click', copyPairingCode);
    dom.pairingDone.addEventListener('click', () => { hidePairingPanel(); loadDevices(); });
    dom.devicesOverlay.addEventListener('click', (e) => {
      if (e.target === dom.devicesOverlay) closeDevicesModal();
    });

    // File input change
    dom.importFile.addEventListener('change', (e) => {
      if (e.target.files.length > 0) handleFileSelect(e.target.files[0]);
    });

    // File drop zone drag events
    dom.fileDropZone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dom.fileDropZone.classList.add('dragover');
    });
    dom.fileDropZone.addEventListener('dragleave', () => {
      dom.fileDropZone.classList.remove('dragover');
    });
    dom.fileDropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dom.fileDropZone.classList.remove('dragover');
      if (e.dataTransfer.files.length > 0) {
        handleFileSelect(e.dataTransfer.files[0]);
      }
    });

    // Close modals on overlay click
    dom.modalOverlay.addEventListener('click', (e) => {
      if (e.target === dom.modalOverlay) closeModal(dom.modalOverlay);
    });
    dom.deleteOverlay.addEventListener('click', (e) => {
      if (e.target === dom.deleteOverlay) closeModal(dom.deleteOverlay);
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
      // Escape to close modals
      if (e.key === 'Escape') {
        closeAllModals();
      }
      // Ctrl+N or Cmd+N to add new entry
      if ((e.ctrlKey || e.metaKey) && e.key === 'n') {
        e.preventDefault();
        openAddModal();
      }
    });
  }

  // ── Initialize ─────────────────────────────────────────────
  async function init() {
    createParticles();
    bindEvents();

    const authed = await checkAuth();
    if (authed) {
      try {
        await loadFromServer();
      } catch {
        showLoginOverlay();
      }
    } else {
      showLoginOverlay();
    }
  }

  // Boot
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
