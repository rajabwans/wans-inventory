# WANPLAN Offline — Progress (resume point)

## STATUS: DONE — offline works on normal pages, deployed & verified on VPS (Sep 9 2026)

## What works now
- **Reading offline**: service worker (`static/sw.js`, v7) caches every page you visit, keyed by URL.
  Offline you get the last-synced version of every screen (dashboard, lists, reports) — not just one shell page.
- **Writing offline**: `static/offline.js` (loaded on every logged-in page via base.html):
  - Keeps a local IndexedDB copy of products/customers/categories from `GET /api/offline-snapshot`.
  - Intercepts submits on forms marked `data-offline-op` when offline, queues the op, shows a
    bottom-right chip ("n saved offline"), redirects to the return page.
  - On reconnect (`online` event) it flushes the queue oldest-first to `POST /api/sync`.
  - Pending count + "Syncing…" shown live in the chip.
- **OPS SUPPORTED offline** (match server `_apply_sync_op`): product, customer, expense, sale, adjust.
- **forms wired** (all have `data-offline-op` + `data-offline-return`):
  - templates/add_product.html            -> product
  - templates/add_sale.html               -> sale (single-line cart -> items[0])
  - templates/add_expense.html            -> expense
  - templates/customers/form.html          -> customer (only in ADD mode; edit posts normally)
  - templates/products.html (adjust modal) -> adjust
- **Quick Sell / offline_page concept is GONE** — no separate page. The normal app is offline.

## Sync API (restored from git HEAD, identical)
- `GET  /api/offline-snapshot` -> meta + products/customers/categories (auth, in `allowed` list).
- `POST /api/sync` -> {ops:[...]} -> {results:[{client_id,status,real_id?,message?}]}, savepoint per op,
  max 500 ops, CSRF via `X-CSRFToken` header (from `window.WANPLAN.csrf`).
- resolved-refs contract: creating op uses temp client_id (e.g. `p_...`,`c_...`); later ops reference it by
  that temp id or an int; ints pass straight through. Oldest-first ordering required (client sorts by created_at).
- Verified end-to-end on VPS: snapshot 200, sync created expense (real_id 3), missing CSRF rejected 400.

## Deploy notes
- `static/sw.js` VERSION = 'wanplan-v7' — bump when PRECACHE list changes (offline.js is precached).
- Live site: https://jarvis.wanland.org/wans/  (VPS rajfx, :10000)
- To re-test offline in a browser: DevTools > Network > Offline, then use pages you've already visited.

## Files added/changed this session
- app.py: restored sync API block (byte-identical to git HEAD) + `allowed` list back to include the 2 APIs
- static/sw.js: rewritten (per-URL nav cache, v7)
- static/offline.js: NEW client offline layer
- templates/base.html: load offline.js when session.user_id present
- templates/{add_product,add_sale,add_expense,products,customers/form}.html: data-offline-op attributes
- removed permanently: templates/offline_page.html, static/offline.js OLD, /wans/offline route, Quick Sell nav

## Still open (nice-to-haves, not required)
- Deletes while offline are NOT queued (delete forms have no data-offline-op) — they fail with network error.
- Product/customer EDIT forms also post normally (not queued).
- The offline product/customer/expense/sale/adjust cache uses latest snapshot; new offline-created rows show
  up only after a sync (they're in the local queue with temp ids).