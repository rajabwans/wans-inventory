/* WANPLAN offline layer — enables recording work offline and auto-syncing.
   Loaded on every page. Strategy:
   - Keep a local copy (IndexedDB) of products/customers/categories from /api/offline-snapshot.
   - Intercept forms marked data-offline-op="..." when offline; queue the op instead of submitting.
   - When back online, flush the queue to /api/sync oldest-first.
   - Show a small sync status chip in the top-right corner.
*/
(function () {
  if (!('indexedDB' in window)) return;

  var DB_NAME = 'wanplan-offline';
  var DB_VERSION = 1;
  var db = null;
  var queueOps = [];
  var syncing = false;

  function px(name) {
    var d = document.createElement('div');
    d.id = 'wanplan-sync-chip';
    d.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:9999;display:none;align-items:center;gap:8px;background:#4f46e5;color:#fff;padding:8px 14px;border-radius:999px;font:600 13px/1 Inter,sans-serif;box-shadow:0 4px 14px rgba(15,23,42,.25);';
    d.textContent = name;
    document.body.appendChild(d);
    return d;
  }

  function openDB() {
    return new Promise(function (resolve, reject) {
      var req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = function (e) {
        var d = e.target.result;
        if (!d.objectStoreNames.contains('kv')) d.createObjectStore('kv', { keyPath: 'key' });
        if (!d.objectStoreNames.contains('products')) d.createObjectStore('products', { keyPath: 'id' });
        if (!d.objectStoreNames.contains('customers')) d.createObjectStore('customers', { keyPath: 'id' });
        if (!d.objectStoreNames.contains('categories')) d.createObjectStore('categories', { keyPath: 'id' });
        if (!d.objectStoreNames.contains('queue')) d.createObjectStore('queue', { keyPath: 'client_id', autoIncrement: false });
      };
      req.onsuccess = function () { db = req.result; resolve(db); };
      req.onerror = function () { reject(req.error); };
    });
  }

  function storeInto(store, data) {
    return new Promise(function (resolve, reject) {
      var tx = db.transaction(store, 'readwrite');
      var s = tx.objectStore(store);
      data.forEach(function (row) { s.put(row); });
      tx.oncomplete = resolve;
      tx.onerror = function () { reject(tx.error); };
    });
  }

  function clearStore(store) {
    return new Promise(function (resolve, reject) {
      var tx = db.transaction(store, 'readwrite');
      var r = tx.objectStore(store).clear();
      r.onsuccess = resolve;
      r.onerror = function () { reject(r.error); };
    });
  }

  function getAll(store) {
    return new Promise(function (resolve, reject) {
      var tx = db.transaction(store, 'readonly');
      var r = tx.objectStore(store).getAll();
      r.onsuccess = function () { resolve(r.result || []); };
      r.onerror = function () { reject(r.error); };
    });
  }

  function getKV(key) {
    return new Promise(function (resolve) {
      var tx = db.transaction('kv', 'readonly');
      var r = tx.objectStore('kv').get(key);
      r.onsuccess = function () { resolve(r.result ? r.result.value : null); };
      r.onerror = function () { resolve(null); };
    });
  }

  function setKV(key, value) {
    return new Promise(function (resolve) {
      var tx = db.transaction('kv', 'readwrite');
      tx.objectStore('kv').put({ key: key, value: value });
      tx.oncomplete = resolve;
    });
  }

  function putQueue(op) {
    return new Promise(function (resolve, reject) {
      var tx = db.transaction('queue', 'readwrite');
      tx.objectStore('queue').put(op);
      tx.oncomplete = resolve;
      tx.onerror = function () { reject(tx.error); };
    });
  }

  function deleteQueue(clientId) {
    return new Promise(function (resolve) {
      var tx = db.transaction('queue', 'readwrite');
      tx.objectStore('queue').delete(clientId);
      tx.oncomplete = resolve;
    });
  }

  function getQueue() {
    return new Promise(function (resolve, reject) {
      var tx = db.transaction('queue', 'readonly');
      var r = tx.objectStore('queue').getAll();
      r.onsuccess = function () {
        var all = r.result || [];
        all.sort(function (a, b) { return (a.created_at || 0) - (b.created_at || 0); });
        resolve(all);
      };
      r.onerror = function () { reject(r.error); };
    });
  }

  function isOnline() { return navigator.onLine !== false; }

  function refresh(job) {
    clearStore('products').then(function () {
      return clearStore('customers');
    }).then(function () {
      return clearStore('categories');
    }).then(function () {
      return Promise.all([
        storeInto('products', job.products || []),
        storeInto('customers', job.customers || []),
        storeInto('categories', job.categories || []),
        setKV('meta', job.meta || {})
      ]);
    });
  }

  function fetchSnapshot() {
    if (!isOnline()) return Promise.resolve(false);
    return fetch('/wans/api/offline-snapshot', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) return false;
        return r.json();
      })
      .then(function (data) {
        if (!data || !data.products) return false;
        refresh(data);
        return true;
      })
      .catch(function () { return false; });
  }

  function csrfToken() {
    if (window.WANPLAN && window.WANPLAN.csrf) return window.WANPLAN.csrf;
    var el = document.querySelector('input[name="csrf_token"]');
    return el ? el.value : '';
  }

  function flushQueue() {
    if (syncing || !db || !isOnline()) return Promise.resolve({ sent: 0, failed: 0 });
    return getQueue().then(function (queue) {
      if (!queue.length) { updateChip(); return { sent: 0, failed: 0 }; }
      syncing = true;
      updateChip();
      return fetch('/wans/api/sync', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken()
        },
        body: JSON.stringify({ ops: queue })
      }).then(function (r) { return r.json(); }).then(function (data) {
        var results = data.results || [];
        var byClient = {};
        results.forEach(function (res) { byClient[res.client_id] = res; });
        var remove = [], failed = 0;
        queue.forEach(function (op) {
          var res = byClient[op.client_id];
          if (res && res.status === 'ok') {
            remove.push(op.client_id);
            rekeyLocal(op, res);
          } else {
            failed++;
          }
        });
        return Promise.all(remove.map(deleteQueue)).then(function () {
          syncing = false;
          updateChip();
          return { sent: remove.length, failed: failed };
        });
      }).catch(function () {
        syncing = false;
        return { sent: 0, failed: queue.length };
      });
    });
  }

  function rekeyLocal(op, res) {
    if (!res.real_id) return;
    var isP = op.op === 'product', isC = op.op === 'customer';
    if (!isP && !isC) return;
    var store = isP ? 'products' : 'customers';
    getAll(store).then(function (rows) {
      var found = rows.filter(function (r) { return String(r.id) === String(op.client_id); });
      if (!found.length) return;
      found.forEach(function (r) {
        r.id = res.real_id;
        storeInto(store, [r]);
      });
    });
  }

  var chip = px('');

  function updateChip() {
    if (!db || !document.body) return;
    getQueue().then(function (q) {
      var n = q.length;
      if (!n) { chip.style.display = 'none'; return; }
      chip.style.display = 'flex';
      var text = syncing ? 'Syncing…' : (n + ' pending — will sync');
      chip.textContent = syncing
        ? '⇅ Syncing…'
        : (n + ' saved offline' + (isOnline() ? ' — waiting for network' : ''));
    });
  }

  function enqueue(op) {
    op.created_at = Date.now();
    return putQueue(op).then(updateChip);
  }

  function formValue(form, name) {
    var el = form.elements.namedItem(name);
    if (!el) return null;
    if (el.type === 'checkbox') return el.checked ? 1 : 0;
    return el.value == null ? '' : '' + el.value;
  }

  function buildOp(form, kind) {
    var f = function (n) { return formValue(form, n); };
    if (kind === 'product') {
      return {
        client_id: tempId('p'),
        op: 'product',
        title: f('title'),
        category: f('category') || '',
        quantity: parseInt(f('quantity')) || 0,
        buying_price: parseFloat(f('buying_price')) || 0,
        selling_price: parseFloat(f('selling_price')) || 0
      };
    }
    if (kind === 'customer') {
      return {
        client_id: tempId('c'),
        op: 'customer',
        name: f('name'),
        phone: f('phone') || '',
        email: f('email') || '',
        address: f('address') || ''
      };
    }
    if (kind === 'expense') {
      return {
        client_id: tempId('e'),
        op: 'expense',
        description: f('description'),
        amount: parseFloat(f('amount')) || 0,
        category: f('category') || ''
      };
    }
    if (kind === 'sale') {
      var productRef = f('product_id');
      var customerRef = f('customer_id');
      return {
        client_id: tempId('s'),
        op: 'sale',
        items: [{
          product_ref: productRef ? parseInt(productRef) : null,
          qty: parseInt(f('quantity')) || 1,
          unit_price: parseFloat(f('unit_price')) || 0
        }]
        .filter(function (i) { return i.product_ref; }),
        customer_ref: customerRef ? parseInt(customerRef) : null,
        customer_name: f('customer_name') || ''
      };
    }
    if (kind === 'adjust') {
      return {
        client_id: tempId('a'),
        op: 'adjust',
        product_ref: form.dataset.productId ? parseInt(form.dataset.productId) : null,
        adjustment_type: f('adjustment_type'),
        quantity: Math.abs(parseInt(f('quantity'))) || 0,
        reason: f('reason') || ''
      };
    }
    return null;
  }

  var idCounter = 0;
  function tempId(prefix) {
    idCounter++;
    return prefix + '_' + Date.now() + '_' + idCounter;
  }

  function interceptForms() {
    document.querySelectorAll('form[data-offline-op]').forEach(function (form) {
      form.addEventListener('submit', function (e) {
        if (isOnline()) return;
        var kind = form.getAttribute('data-offline-op');
        var op = buildOp(form, kind);
        if (!op) return;
        e.preventDefault();
        var btn = form.querySelector('button[type="submit"]');
        var orig = btn ? btn.innerHTML : '';
        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="bi bi-wifi-off"></i> Saved offline'; }
        enqueue(op).then(function () {
          setTimeout(function () {
            var url = form.getAttribute('data-offline-return') || '/wans/';
            window.location.href = url;
          }, 900);
        });
      });
    });
  }

  function boot() {
    openDB().then(function () {
      updateChip();
      interceptForms();
      fetchSnapshot().then(updateChip);
      window.addEventListener('online', function () {
        updateChip();
        fetchSnapshot().then(function () { return flushQueue(); }).then(updateChip);
      });
      window.addEventListener('offline', updateChip);
      window.WANPLAN = window.WANPLAN || {};
      window.WANPLAN.offline = {
        isOnline: isOnline,
        flush: flushQueue,
        enqueue: enqueue,
        pending: getQueue,
        lastSync: function () { return getKV('meta'); }
      };
      if (isOnline()) flushQueue().then(updateChip);
    }).catch(function () {});
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();