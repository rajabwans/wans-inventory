(function () {
  'use strict';

  var DB_NAME = 'wanplan';
  var DB_VERSION = 1;
  var db = null;

  function idb() {
    return new Promise(function (resolve, reject) {
      var req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = function (e) {
        var d = e.target.result;
        if (!d.objectStoreNames.contains('kv')) d.createObjectStore('kv');
        if (!d.objectStoreNames.contains('products')) d.createObjectStore('products', { keyPath: 'pid' });
        if (!d.objectStoreNames.contains('customers')) d.createObjectStore('customers', { keyPath: 'cid' });
        if (!d.objectStoreNames.contains('categories')) d.createObjectStore('categories', { keyPath: 'id' });
        if (!d.objectStoreNames.contains('queue')) d.createObjectStore('queue', { keyPath: 'client_id' });
      };
      req.onsuccess = function () { db = req.result; resolve(db); };
      req.onerror = function () { reject(req.error); };
    });
  }

  function tx(store, mode, fn) {
    var t = db.transaction(store, mode);
    var s = t.objectStore(store);
    return new Promise(function (resolve, reject) {
      var out;
      try { out = fn(s); } catch (err) { reject(err); return; }
      t.oncomplete = function () { resolve(out); };
      t.onerror = function () { reject(t.error); };
      t.onabort = function () { reject(t.error); };
    });
  }

  function reqToPromise(r) {
    return new Promise(function (resolve, reject) {
      r.onsuccess = function () { resolve(r.result); };
      r.onerror = function () { reject(r.error); };
    });
  }

  function getAll(store) {
    return tx(store, 'readonly', function (s) { return reqToPromise(s.getAll()); });
  }
  function put(store, value) {
    return tx(store, 'readwrite', function (s) { s.put(value); });
  }
  function bulkPut(store, values) {
    return tx(store, 'readwrite', function (s) { values.forEach(function (v) { s.put(v); }); });
  }
  function del(store, key) {
    return tx(store, 'readwrite', function (s) { s.delete(key); });
  }
  function clearStore(store) {
    return tx(store, 'readwrite', function (s) { s.clear(); });
  }
  function replaceStore(store, values) {
    return clearStore(store).then(function () { return bulkPut(store, values); });
  }

  var state = {
    meta: null,
    products: [],
    customers: [],
    categories: [],
    queue: [],
    cart: [],
    online: navigator.onLine
  };

  var els = {};

  function $(id) { return document.getElementById(id); }
  function fmtNum(n) { return Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }); }
  function uid(prefix) { return prefix + '_' + Math.random().toString(36).slice(2, 10); }

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function api(url, options) {
    options = options || {};
    options.headers = options.headers || {};
    options.headers['X-CSRFToken'] = window.WANPLAN && window.WANPLAN.csrf || '';
    if (options.json !== undefined) {
      options.method = options.method || 'POST';
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(options.json);
    }
    return fetch(url, { credentials: 'same-origin', headers: options.headers, method: options.method || 'GET', body: options.body })
      .then(function (resp) {
        var ct = resp.headers.get('content-type') || '';
        if (ct.indexOf('application/json') !== -1) {
          return resp.json().then(function (j) { return { status: resp.status, json: j }; });
        }
        return { status: resp.status, text: resp.status, html: true, redirect: resp.redirected };
      });
  }

  function setBadge(text, cls) {
    var b = els.statusBadge;
    if (!b) return;
    b.textContent = text;
    b.className = 'badge rounded-pill status-badge ' + (cls || 'bg-secondary');
  }

  function renderConnBanner() {
    var banner = els.connBanner;
    if (!banner) return;
    if (!state.online) {
      banner.classList.remove('d-none');
      banner.textContent = 'Offline — everything you add is saved on this device and syncs when you reconnect.';
      banner.className = 'alert alert-warning mb-2 d-flex align-items-center';
    } else {
      banner.classList.add('d-none');
    }
  }

  function updateHeader() {
    if (state.meta) {
      var t = els.bizName;
      if (t) t.textContent = state.meta.name || '';
      var cur = state.meta.currency || '';
      if (els.currency) els.currency.textContent = cur;
      if (els.currency2) els.currency2.textContent = cur;
      if (els.currency3) els.currency3.textContent = cur;
    }
    updateQueueBadge();
    renderConnBanner();
    var pill = els.connPill;
    if (pill) {
      pill.textContent = state.online ? 'Online' : 'Offline';
      pill.className = state.online ? 'badge rounded-pill bg-success' : 'badge rounded-pill bg-secondary';
    }
    var syncEl = els.lastSync;
    if (syncEl) {
      var last = state.lastSyncAt;
      syncEl.textContent = last ? 'Last sync: ' + new Date(last).toLocaleTimeString() : 'Not synced yet';
    }
  }

  function updateQueueBadge() {
    var count = state.queue.length;
    var b = els.pendingBadge;
    if (b) {
      b.textContent = count + ' pending';
      b.className = 'badge rounded-pill ' + (count ? 'bg-warning text-dark' : 'bg-success');
    }
  }

  function productSummary(p) {
    var qty = Number(p.quantity || 0);
    return p.title + ' — ' + fmtNum(qty) + ' left' + (qty > 0 ? ' @ ' + fmtNum(p.selling_price) : '');
  }

  function fillProductOptions(selectId, inStock) {
    var sel = $(selectId);
    if (!sel) return;
    sel.innerHTML = '<option value="">Select product…</option>';
    state.products.forEach(function (p) {
      var qty = Number(p.quantity || 0);
      if (inStock && qty <= 0) return;
      var opt = document.createElement('option');
      opt.value = p.pid;
      opt.textContent = productSummary(p);
      sel.appendChild(opt);
    });
  }

  function fillCustomerOptions(selectId, includeWalkin) {
    var sel = $(selectId);
    if (!sel) return;
    sel.innerHTML = '';
    if (includeWalkin) {
      var w = document.createElement('option');
      w.value = '';
      w.textContent = 'Walk-in (no customer)';
      sel.appendChild(w);
    }
    state.customers.forEach(function (cu) {
      var opt = document.createElement('option');
      opt.value = cu.cid;
      opt.textContent = cu.name;
      sel.appendChild(opt);
    });
  }

  function fillCategoryDatalist(listId, kind) {
    var dl = $(listId);
    if (!dl) return;
    dl.innerHTML = '';
    state.categories.forEach(function (cat) {
      if (cat.kind !== kind) return;
      var o = document.createElement('option');
      o.value = cat.name;
      dl.appendChild(o);
    });
  }

  function renderProductTab() { fillProductOptions('sellProduct', true); fillProductOptions('stockProduct', true); }
  function renderCustomerTab() { fillCustomerOptions('sellCustomer', true); }
  function renderCategoriesTab() { fillCategoryDatalist('productCategoryList', 'product'); fillCategoryDatalist('expenseCategoryList', 'expense'); }

  function renderCart() {
    var tbody = els.cartLines;
    if (!tbody) return;
    tbody.innerHTML = '';
    var total = 0;
    state.cart.forEach(function (line) {
      var tr = document.createElement('tr');
      tr.innerHTML = '<td>' + esc(line.title) + '</td><td>' + fmtNum(line.price) + '</td><td>' + line.qty +
        '</td><td>' + fmtNum(line.price * line.qty) + '</td><td><button class="btn btn-sm btn-outline-danger" data-idx="' + state.cart.indexOf(line) + '"><i class="bi bi-x"></i></button></td>';
      tbody.appendChild(tr);
      total += line.price * line.qty;
    });
    var t = els.cartTotal;
    if (t) t.textContent = fmtNum(total);
    tbody.querySelectorAll('[data-idx]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        state.cart.splice(Number(btn.getAttribute('data-idx')), 1);
        renderCart();
      });
    });
  }

  function renderQueueTab() {
    var list = els.queueList;
    if (!list) return;
    list.innerHTML = '';
    if (!state.queue.length) {
      list.innerHTML = '<div class="text-muted small p-3 text-center"><i class="bi bi-check2-all"></i> Nothing queued. Add sales, products, customers, expenses or stock while offline and they sync automatically.</div>';
    }
    state.queue.slice().reverse().forEach(function (op) {
      var item = document.createElement('div');
      item.className = 'list-group-item d-flex justify-content-between align-items-start';
      var err = op.last_error ? '<div class="small text-danger mt-1"><i class="bi bi-exclamation-triangle"></i> ' + esc(op.last_error) + '</div>' : '';
      var badge = op.last_error ? '<span class="badge bg-danger">Sync failed</span>' : '<span class="badge bg-secondary">Queued</span>';
      item.innerHTML = '<div class="me-2">' +
        '<div class="fw-semibold small">' + esc(opLabel(op)) + '</div>' +
        '<div class="text-muted" style="font-size:.75rem;">' + esc(opTime(op)) + '</div>' + err + '</div>' + badge;
      list.appendChild(item);
    });
  }

  function opLabel(op) {
    var p = op.payload || {};
    switch (op.op) {
      case 'product': return 'Add product: ' + p.title;
      case 'customer': return 'Add customer: ' + p.name;
      case 'expense': return 'Expense: ' + p.description + ' (' + fmtNum(p.amount) + ')';
      case 'adjust': return 'Stock ' + p.adjustment_type + ': ' + fmtNum(p.quantity) + ' × ' + (p.product_label || '');
      case 'sale': return 'Sale (' + p.items.length + ' item' + (p.items.length === 1 ? '' : 's') + ') ' + fmtNum(opTotal(p));
      default: return op.op;
    }
  }
  function opTotal(op) {
    var t = 0;
    (op.payload.items || []).forEach(function (i) { t += i.qty * i.price; });
    return t;
  }
  function opTime(op) {
    var d = new Date(op.created_at);
    return d.toLocaleString();
  }

  function persist() {
    var p = Promise.resolve();
    if (state.meta) p = p.then(function () { return put('kv', { k: 'meta', v: state.meta }); });
    if (state.lastSyncAt) p = p.then(function () { return put('kv', { k: 'lastSyncAt', v: state.lastSyncAt }); });
    return p.then(function () { return bulkPut('products', state.products.filter(function (x) { return x.local; }))
      .then(function () { return bulkPut('customers', state.customers.filter(function (x) { return x.local; })); }); })
      .then(function () { return replaceStore('queue', state.queue); });
  }

  function queueOp(op, payload, client_id) {
    var record = {
      client_id: client_id || uid('q'),
      op: op,
      payload: payload,
      created_at: Date.now(),
      last_error: null
    };
    state.queue.push(record);
    persist().then(renderQueueTab);
    updateQueueBadge();
    return record;
  }

  function localProductLookup(pid) {
    for (var i = 0; i < state.products.length; i++) if (String(state.products[i].pid) === String(pid)) return state.products[i];
    return null;
  }

  function queueSale() {
    var sel = els.sellProduct, qtyEl = els.sellQty, priceEl = els.sellPrice;
    var pid = sel && sel.value;
    var qty = parseInt(qtyEl && qtyEl.value, 10);
    var price = parseFloat(priceEl && priceEl.value);
    var prod = localProductLookup(pid);
    var lines = state.cart.slice();
    if (pid) {
      if (!prod) { flashMsg('Pick a product first.'); return; }
      if (!(qty > 0)) { flashMsg('Quantity must be greater than zero.'); return; }
      if (!(price > 0)) { flashMsg('Unit price must be greater than zero.'); return; }
      if (Number(prod.quantity || 0) < qty) { flashMsg('Only ' + prod.quantity + ' of "' + prod.title + '" available.'); return; }
      lines.push({ product_ref: prod.pid, qty: qty, price: price, title: prod.title });
      prod.quantity = Number(prod.quantity) - qty;
    }
    if (!lines.length) { flashMsg('Add at least one item to the sale.'); return; }
    var custSel = els.sellCustomer;
    var custRef = custSel && custSel.value || null;
    var custNameEl = els.sellCustomerName;
    var custName = custNameEl && custNameEl.value.trim() || '';
    queueOp('sale', { items: lines, customer_ref: custRef, customer_name: custName });
    state.cart = [];
    if (qtyEl) qtyEl.value = '';
    if (custNameEl) custNameEl.value = '';
    persist().then(render);
    flashMsg('Sale queued — will sync automatically.');
  }

  function queueProduct() {
    var title = (els.pTitle.value || '').trim();
    if (!title) { flashMsg('Product title is required.'); return; }
    var qty = parseInt(els.pQty.value, 10) || 0;
    var buy = parseFloat(els.pBuy.value) || 0;
    var sell = parseFloat(els.pSell.value) || 0;
    var pid = uid('p');
    var payload = { title: title, category: (els.pCategory.value || '').trim(), quantity: Math.max(0, qty), buying_price: Math.max(0, buy), selling_price: Math.max(0, sell), notes: (els.pNotes.value || '').trim() };
    queueOp('product', payload, pid);
    state.products.push({ pid: pid, title: title, category: payload.category, quantity: payload.quantity, buying_price: payload.buying_price, selling_price: payload.selling_price, local: true });
    els.pTitle.value = ''; els.pQty.value = ''; els.pBuy.value = ''; els.pSell.value = ''; els.pNotes.value = '';
    persist().then(render);
    flashMsg('Product queued locally.');
  }

  function queueCustomer() {
    var name = (els.cName.value || '').trim();
    if (!name) { flashMsg('Customer name is required.'); return; }
    var cid = uid('c');
    var payload = { name: name, phone: (els.cPhone.value || '').trim(), email: (els.cEmail.value || '').trim(), address: (els.cAddress.value || '').trim() };
    queueOp('customer', payload, cid);
    state.customers.push({ cid: cid, name: name, phone: payload.phone, email: payload.email, address: payload.address, local: true });
    els.cName.value = ''; els.cPhone.value = ''; els.cEmail.value = ''; els.cAddress.value = '';
    persist().then(render);
    flashMsg('Customer queued locally.');
  }

  function queueExpense() {
    var desc = (els.eDesc.value || '').trim();
    var amount = parseFloat(els.eAmount.value);
    if (!desc) { flashMsg('Description is required.'); return; }
    if (!(amount > 0)) { flashMsg('Amount must be positive.'); return; }
    queueOp('expense', { description: desc, amount: amount, category: (els.eCategory.value || '').trim() });
    els.eDesc.value = ''; els.eAmount.value = ''; els.eCategory.value = '';
    persist().then(renderQueueTab);
    flashMsg(('Expense queued — ' + fmtNum(amount)) + '.');
  }

  function queueAdjust() {
    var pid = els.stockProduct.value;
    var type = els.stockType.value;
    var qty = parseInt(els.stockQty.value, 10);
    var prod = localProductLookup(pid);
    if (!prod) { flashMsg('Pick a product first.'); return; }
    if (!(qty > 0)) { flashMsg('Quantity must be positive.'); return; }
    var payload = { product_ref: prod.pid, product_label: prod.title, adjustment_type: type, quantity: qty, reason: (els.stockReason.value || '').trim() };
    queueOp('adjust', payload);
    if (type === 'correction') prod.quantity = qty;
    else if (type === 'damaged' || type === 'stolen') prod.quantity = Math.max(0, Number(prod.quantity) - qty);
    else prod.quantity = Number(prod.quantity) + qty;
    els.stockQty.value = ''; els.stockReason.value = '';
    persist().then(render);
    flashMsg('Stock adjustment queued.');
  }

  function flashMsg(text) {
    var el = els.flash;
    if (!el) return;
    el.textContent = text;
    el.classList.remove('d-none');
    el.classList.add('show');
    clearTimeout(el._t);
    el._t = setTimeout(function () { el.classList.add('d-none'); }, 3500);
  }

  function loadFromDb() {
    return Promise.all([getAll('kv'), getAll('products'), getAll('customers'), getAll('categories'), getAll('queue')])
      .then(function (res) {
        var kv = res[0];
        state.meta = null; state.lastSyncAt = null;
        kv.forEach(function (r) { if (r.k === 'meta') state.meta = r.v; if (r.k === 'lastSyncAt') state.lastSyncAt = r.v; });
        state.products = res[1] || [];
        state.customers = res[2] || [];
        state.categories = res[3] || [];
        state.queue = res[4] || [];
      });
  }

  function refreshFromServer() {
    if (!state.online) return Promise.resolve();
    return api('/wans/api/offline-snapshot')
      .then(function (r) {
        if (r.status !== 200) return;
        var j = r.json;
        state.meta = j.meta;
        var localProducts = {};
        state.products.forEach(function (p) { if (p.local) localProducts[p.pid] = p; });
        var localCustomers = {};
        state.customers.forEach(function (cu) { if (cu.local) localCustomers[cu.cid] = cu; });
        state.products = j.products.map(function (p) {
          return { pid: p.id, title: p.title, category: p.category, quantity: p.quantity, buying_price: p.buying_price, selling_price: p.selling_price, local: false };
        });
        Object.keys(localProducts).forEach(function (k) {
          var keeps = true;
          state.products.forEach(function (p) { if (String(p.pid) === String(k)) keeps = false; });
          if (keeps) state.products.push(localProducts[k]);
        });
        state.customers = j.customers.map(function (cu) {
          return { cid: cu.id, name: cu.name, phone: cu.phone, email: cu.email, address: cu.address, local: false };
        });
        Object.keys(localCustomers).forEach(function (k) {
          var keeps = true;
          state.customers.forEach(function (cu) { if (String(cu.cid) === String(k)) keeps = false; });
          if (keeps) state.customers.push(localCustomers[k]);
        });
        state.categories = j.categories || [];
        state.lastSyncAt = Date.now();
        return persist().then(function () {
          return Promise.all([
            clearStore('products').then(function () { return bulkPut('products', state.products); }),
            clearStore('customers').then(function () { return bulkPut('customers', state.customers); }),
            clearStore('categories').then(function () { return bulkPut('categories', state.categories); })
          ]);
        });
      });
  }

  function syncAll() {
    if (!state.online) { flashMsg('You are offline. Reconnect to sync.'); return Promise.resolve(); }
    if (syncing) { flashMsg('Already syncing…'); return Promise.resolve(); }
    if (!state.queue.length) { flashMsg('Nothing to sync — you are up to date.'); return Promise.resolve(); }
    syncing = true;
    renderSyncBtn(true);
    var ops = state.queue.map(function (op) {
      return {
        op: op.op,
        client_id: op.client_id,
        items: op.payload.items && op.payload.items.map(function (i) { return { product_ref: i.product_ref, qty: i.qty, unit_price: i.price }; }),
        product_ref: op.payload.product_ref,
        adjustment_type: op.payload.adjustment_type,
        quantity: op.payload.quantity,
        reason: op.payload.reason,
        title: op.payload.title,
        category: op.payload.category,
        buying_price: op.payload.buying_price,
        selling_price: op.payload.selling_price,
        notes: op.payload.notes,
        name: op.payload.name,
        phone: op.payload.phone,
        email: op.payload.email,
        address: op.payload.address,
        description: op.payload.description,
        amount: op.payload.amount,
        customer_ref: op.payload.customer_ref,
        customer_name: op.payload.customer_name
      };
    });
    return api('/wans/api/sync', { json: { ops: ops } })
      .then(function (r) {
        if (r.html) { flashMsg('Session expired — please log in again.'); renderSyncBtn(false); return; }
        if (r.status === 403 && r.json && r.json.blocked) { flashMsg(r.json.message); renderSyncBtn(false); return; }
        if (r.status !== 200) { flashMsg('Sync failed (' + r.status + '). Your data stays safely queued.'); renderSyncBtn(false); return; }
        var results = r.json.results || [];
        var idMap = {};
        var okIds = {};
        results.forEach(function (res) {
          if (res.status === 'ok') {
            okIds[res.client_id] = true;
            if (res.real_id) idMap[res.client_id] = res.real_id;
          } else {
            var op = state.queue.filter(function (o) { return o.client_id === res.client_id; })[0];
            if (op) op.last_error = res.message || 'Sync failed';
          }
        });
        state.queue = state.queue.filter(function (op) { return !okIds[op.client_id]; });
        state.products = state.products.map(function (p) { if (p.local && idMap[p.pid]) { p.pid = idMap[p.pid]; p.local = false; } return p; });
        state.customers = state.customers.map(function (cu) { if (cu.local && idMap[cu.cid]) { cu.cid = idMap[cu.cid]; cu.local = false; } return cu; });
        state.lastSyncAt = Date.now();
        return Promise.all([persist(), replaceStore('queue', state.queue)]).then(function () {
          return refreshFromServer();
        });
      })
      .catch(function () { flashMsg('Could not connect. Your data stays safely queued.'); })
      .then(function () { syncing = false; renderSyncBtn(false); render(); });
  }

  var syncing = false;
  function renderSyncBtn(active) {
    var b = els.syncBtn;
    if (!b) return;
    b.disabled = active;
    b.innerHTML = active ? '<span class="spinner-border spinner-border-sm me-1"></span>Syncing…' : '<i class="bi bi-arrow-repeat me-1"></i>Sync now';
  }

  function render() {
    updateHeader();
    renderProductTab();
    renderCustomerTab();
    renderCategoriesTab();
    renderCart();
    renderQueueTab();
  }

  function setup() {
    els.statusBadge = $('statusBadge');
    els.connPill = $('connPill');
    els.pendingBadge = $('pendingBadge');
    els.connBanner = $('connBanner');
    els.bizName = $('bizName');
    els.currency = $('currency');
    els.currency2 = $('currency2');
    els.currency3 = $('currency3');
    els.lastSync = $('lastSync');
    els.syncBtn = $('syncBtn');
    els.flash = $('flashMsg');
    els.cartLines = $('cartLines');
    els.cartTotal = $('cartTotal');
    els.queueList = $('queueList');
    els.sellProduct = $('sellProduct');
    els.sellQty = $('sellQty');
    els.sellPrice = $('sellPrice');
    els.sellCustomer = $('sellCustomer');
    els.sellCustomerName = $('sellCustomerName');
    els.addLineBtn = $('addLineBtn');
    els.pTitle = $('pTitle'); els.pCategory = $('pCategory'); els.pQty = $('pQty'); els.pBuy = $('pBuy'); els.pSell = $('pSell'); els.pNotes = $('pNotes');
    els.cName = $('cName'); els.cPhone = $('cPhone'); els.cEmail = $('cEmail'); els.cAddress = $('cAddress');
    els.eDesc = $('eDesc'); els.eAmount = $('eAmount'); els.eCategory = $('eCategory');
    els.stockProduct = $('stockProduct'); els.stockType = $('stockType'); els.stockQty = $('stockQty'); els.stockReason = $('stockReason');

    els.addLineBtn.addEventListener('click', function () {
      var pid = els.sellProduct.value, qty = parseInt(els.sellQty.value, 10), price = parseFloat(els.sellPrice.value);
      var prod = localProductLookup(pid);
      if (!prod) { flashMsg('Pick a product first.'); return; }
      if (!(qty > 0)) { flashMsg('Quantity must be greater than zero.'); return; }
      if (!(price > 0)) { flashMsg('Unit price must be greater than zero.'); return; }
      if (Number(prod.quantity || 0) < qty) { flashMsg('Only ' + prod.quantity + ' of "' + prod.title + '" available.'); return; }
      state.cart.push({ product_ref: prod.pid, qty: qty, price: price, title: prod.title });
      prod.quantity = Number(prod.quantity) - qty;
      els.sellQty.value = '';
      renderCart();
      renderProductTab();
    });
    els.sellPrice.addEventListener('input', function () {
      var prod = localProductLookup(els.sellProduct.value);
      if (prod && !els.sellPrice.value) els.sellPrice.value = prod.selling_price;
    });
    els.sellProduct.addEventListener('change', function () {
      var prod = localProductLookup(els.sellProduct.value);
      if (prod) els.sellPrice.value = prod.selling_price;
    });
    $('queueSaleBtn').addEventListener('click', queueSale);
    $('queueProductBtn').addEventListener('click', queueProduct);
    $('queueCustomerBtn').addEventListener('click', queueCustomer);
    $('queueExpenseBtn').addEventListener('click', queueExpense);
    $('queueAdjustBtn').addEventListener('click', queueAdjust);
    els.syncBtn.addEventListener('click', function () { syncAll(); });

    window.addEventListener('online', function () { state.online = true; render(); syncAll(); });
    window.addEventListener('offline', function () { state.online = false; render(); });
  }

  idb()
    .then(loadFromDb)
    .then(function () { setup(); render(); updateHeader(); })
    .then(function () { if (navigator.onLine) { return refreshFromServer().then(render).then(function () { return syncAll(); }); } })
    .catch(function (e) { console && console.error && console.error('offline init', e); });
})();