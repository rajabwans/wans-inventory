# WANS COLLECTION Inventoy — WANPLAN Memory & Handbook

Everything learned in our work sessions. Read this first; nothing is obvious.

## Project Overview
Multi-tenant SaaS inventory system, branded **WANPLAN** (tagline: *wanland planner*).
Each business (tenant) gets its own portal showing its OWN business name; the system
itself is called WANPLAN.

- Live product URL: **https://jarvis.wanland.org/wans/** (app lives under the jarvis
  domain at path `/wans`). Raw IP `http://92.4.141.94/` returns 404 by design.
- Owner/superadmin login: slug `wans`, user `admin`, pass `admin123`.
- Repository: `github.com/rajabwans/wans-inventory` (local clone is the project root).
- Stack: Flask + SQLite (local dev) / Supabase Postgres (production), Gunicorn on VPS.
  Python 3.14 locally, 3.12 in the VPS venv.

## Monetisation / Billing Model
- Every business gets a **7-day free trial** (`TRIAL_DAYS`, default 7).
  - Trial is seeded when superadmin approves (`platform_approve` sets
    `trial_ends_at = now + trial_days`).
  - Superadmin can also set/extend it from the platform Plan modal via a
    **"Trial ends (date)"** field (`platform_set_plan`).
- **Pro = UGX 15,000 / month** paid by Mobile Money to **0763750114**
  (`PAYMENT_PHONE`); `PRO_PRICE` env is `"UGX 15,000 / month"`.
- `get_effective_plan()` returns one of `'trial' | 'pro' | 'expired'`.
  - `pro`: `paid_until` set and not past → `pro`; past → `expired`.
  - non-pro: `trial_ends_at` in future → `trial`; else → `expired` (locked).
- `PLAN_LIMITS`: `trial` = 50 products / 2 users / 100 customers, `pro` = unlimited,
  `expired` = 0 (all blocked). `check_limit(conn, kind)` enforces.
- Expired businesses are locked read-only by `block_suspended` (before_request):
  only `logout`, `static`, `billing`, `request_upgrade`, `platform`, `serve_upload`
  are allowed; everything redirects to `/billing` with flash
  "Your free trial has ended. Activate Pro to continue using WANPLAN."
- `biz_plan_state(biz)` = same logic for a raw DB row, used on the platform page and
  injected into templates.

### Upgrade requests carry payment proof to the superadmin
- `request_upgrade` (POST `/billing/request-upgrade`) accepts:
  - `transaction_ref` (MoMo transaction ID, required-ish),
  - `note` (message/phone/amount, optional),
  - `proof` (image file upload; allowed `png/jpg/jpeg/webp/gif`, ≤5MB) saved to
    `UPLOAD_FOLDER` (default `uploads/` next to app, env-overridable) as
    `proof_<bid>_<token>.<ext>`.
  - Stores `upgrade_note`, `upgrade_proof`, `upgrade_requested_at`,
    sets `upgrade_requested = 1`.
- `serve_upload` route `/uploads/<file>` serves proofs; owner business (via
  `upgrade_proof` match) or superadmin only. `_delete_proof_file()` removes the file
  when the request is dismissed or payment confirmed.
- Platform rows get an envelope button opening an upgrade modal (message, timestamp,
  proof image), with **"Confirm payment → set plan"** (jumps to plan modal and
  pre-selects Pro) and **Dismiss**.
- Confirming payment = set plan to `pro` in the plan modal — this clears
  `upgrade_requested`/note/proof and deletes the uploaded proof file.

## Naming & Display Rules (IMPORTANT)
- `PRODUCT_NAME` env (default `WANPLAN`) = the system name; shown on auth pages,
  landing, auth-aside brand, "trial ended" flash.
- `COMPANY_NAME` = the LOGGED-IN business's name (`biz.get('name')`). Inside each
  portal the navbar, dashboard subtitle, invoices/receipts, page titles show the
  tenant's own business name — NEVER change `COMPANY_NAME` in those places to the
  product name.
- `PAYMENT_NAME` (default = env `COMPANY_NAME`, i.e. "WANS COLLECTION") = the Mobile
  Money receiver shown in the Billing "How to pay" box. Do NOT render the tenant's
  own name there.

## Architecture Notes
- `PrefixMiddleware` strips `/wans` from PATH_INFO and sets SCRIPT_NAME so
  `url_for` builds `/wans/...` URLs. VPS Caddy: `jarvis.wanland.org/wans/*` →
  127.0.0.1:10000; `jarvis.wanland.org/*` → :3000 (jarvis web app); `:80` unknown
  hosts → :10000.
- DB: `DATABASE_URL` env. If set → Postgres (Supabase pooler, IPv4
  `aws-1-eu-west-1.pooler.supabase.com`, `#` in password must be URL-encoded `%23`).
  Else local SQLite (`DB_PATH`, default `inventory.db`). `IS_PG` gates behavior;
  `q()` swaps `?`→`%s`.
- Schemas: `SCHEMA_SQLITE` / `SCHEMA_PG`; migrations `MIGRATION_SQLITE` /
  `MIGRATION_PG` (PG uses `IF NOT EXISTS`). `init_db()` runs both. `shortdate`
  template filter formats dates.
- `GETDATE GOTCHA`: **Postgres returns `datetime.datetime` for TIMESTAMP columns;
  SQLite returns strings.** Never slice `[:10]` directly on a date in templates —
  use the `shortdate` filter or the `is string`/`.strftime` pattern. This caused a
  production 500 on `/platform` earlier.
- JSESSION/roles: `login_required`, `admin_required`, `superadmin_required`
  decorators. `session['biz']` carries `plan`, `paid_until`, `trial_ends_at`, etc.
  `refresh_biz` reloads biz on each request (before_request).

## Features Build (per-business, multi-tenant)
- **Categories**: `categories` table (business_id, name, kind product|expense,
  UNIQUE(business_id,kind,name)). Routes `/categories` (+ `/categories/delete/<id>`).
  `seed_default_categories()` seeds the `wans` business (5 product: Perfumes, Scented
  Oils, Toys, Womens Bags, Suitcases; 6 expense: Rent, Utilities, Transport,
  Salaries, Marketing, Other).
- **Reports**: stock valuation, sales, profit & loss (HTML + PDF + CSV).
- **Simplified product form**: only title(name), category, quantity, buying_price,
  selling_price (author/isbn/publisher/notes kept as empty backend defaults).
  Slick forms: `form-page-header`, `input-icon-wrap` icon inputs, currency prefix,
  live `margin-banner` profit preview (turns red when negative).

## Design System (UI)
- Brand: indigo→violet (`--brand #4f46e5`, `--brand-2 #7c3aed`,
  `--brand-grad` 135deg). Ink `#0f172a`, canvas `#f4f6fb`, radius 14px. Inter font.
- Dashboard KPI cards = white `.stat-card` + gradient `.stat-icon` chips
  (indigo/violet/sky/emerald/amber/teal/rose/slate/red). No rainbow gradients.
- Primary CTAs = `.btn-brand` (gradient). Table headers = light uppercase
  (`.table thead th`), NOT `table-dark`. Topnav = deep indigo glass.
- Auth screens: `.auth-shell` split-screen, purple gradient aside + feature tiles,
  `.auth-card`. Landing: gradient hero.
- **Logo**: `static/logo.svg` — rounded gradient tile + white "W" monogram + shelf
  baseline. Used for brand-mark, auth brand, landing logo. Replace the old
  `bi bi-box2-fill` icons with the logo where it represents the brand.

## Capital Recovery metric (dashboard)
- `total_invested` = current stock at cost (SUM buying_price*quantity).
- `total_cogs = total_sales_amount - total_profit`
  (profit per sale = (unit_price - buying_price) * qty).
- Recovery = `total_cogs / (total_cogs + total_invested) * 100`, clamped to 0–100%.
  Old formula divided by stock value only and produced absurd numbers (900%).

## PWA (installable app + offline)
- Full PWA: `static/manifest.webmanifest`, `static/sw.js`, `static/offline.html`, generated PNG icons.
- Manifest start_url/scope = `/wans/`, theme-color `#4338ca`, `display: standalone` (phone + desktop install).
- Icons (drawn with Pillow via `/tmp/gen_icons.py`, supersampled x6): `icon-192/512.png` (rounded tile),
  `icon-maskable-512.png` (full-bleed, safe zone), `apple-touch-icon.png` 180, `favicon.png` 32.
- `/wans/sw.js` route (Flask `sw()`) serves the worker WITH `Service-Worker-Allowed: /wans/` so the wider
  scope works; `/wans/manifest.webmanifest` route forces `application/manifest+json`.
- Strategy: navigations = network-first (cached copy under key `/wanplan-shell` reused offline); same-origin
  static = stale-while-revalidate; CDN (jsdelivr/fonts) = stale-while-revalidate; final offline fallback =
  `static/offline.html`. Registration snippet in both base templates (`scope:'/wans/'`).
- Bump `VERSION` in `sw.js` when the precache list changes. Currently `wanplan-v6`.
- **Bootstrap is vendored locally** at `static/vendor/bootstrap/` and `static/vendor/bootstrap-icons/`
  (JS bundle, CSS, woff/woff2). Templates + sw.js PRECACHE use the local copies — NO CDN dependency.
  This is how the hamburger/dropdowns work reliably offline.
- **Nav on mobile**: topnav must use `min-height` (NOT fixed `height`) so the hamburger
  collapse grows cleanly; the mobile menu is its own scrollable panel (`max-height` +
  `overflow-y`) and `backdrop-filter` is off on small screens to avoid rendering flicker.

## Offline mode (Quick Sell)
- Route `/offline` (login_required) → `templates/offline_page.html` + `static/offline.js`.
  Nav entry "Quick Sell (Offline)" (topnav + dropdown). Expired plan → redirect to billing.
- **Client (offline.js)**: IndexedDB `wanplan` v1 — stores kv/products/customers/categories/queue.
  Everything is queued locally (created_at order via push), synced oldest-first, retried on errors
  (failed ops get `last_error` and stay in queue). Cart = multi-line quick sale; auto-sync on reconnect.
- **`window.WANPLAN.csrf`** is embedded in `base.html` (after bootstrap bundle) and reused for the sync API
  header `X-CSRFToken`. NOTE: `session.clear()` on login wipes the Flask-WTF token, so the sync page must
  read a token from a POST-login render (the embedded `WANPLAN.csrf`), NOT the login page.
- **Sync API contract** (`POST /api/sync`, JSON `{ops:[...]}`, max 500, CSRF via `X-CSRFToken` header):
  per-op `results[]` with `{client_id, status, real_id?, message?}`; SAVEPOINT per op on SQLite so failures
  are independent. `GET /api/offline-snapshot` returns products/customers/categories/meta. Both are exempted
  from the expired-plan redirect (return clean JSON error instead).
- **Resolved-refs contract (CRITICAL)**: for an op referencing a product/customer created in the SAME batch,
  the reference is the temp id (`p_...`/`c_...`), and the creating op MUST use that same temp id as its
  `client_id` — the server records `resolved[client_id] = real_id` per product/customer op, then later ops
  resolve string refs through that map (ints pass straight through). Ops must be sent oldest-first so the
  creating op arrives before any op referencing it. Temp ids on client side also let the client rekey its
  local copies to real ids from `resolved` (idMap).
- Op types: `product` (title, category, quantity, buying_price, selling_price, notes), `customer`
  (name, phone, email, address), `expense` (description, amount, category), `adjust`
  (product_ref, adjustment_type ∈ `ALLOWED_ADJUST_TYPES` = damaged/stolen/returned/correction/restock,
  quantity, reason), `sale` (items[{product_ref, qty, unit_price}], customer_ref, customer_name — used to
  auto-create a walk-in customer if no customer_ref). Stock is re-checked server-side; `check_limit`
  enforced for trial plans.
- `insert_row(conn, sql, params)` helper: SQLite → `cursor.lastrowid`, PG → `RETURNING id`. Use it for
  any INSERT that must return the id (avoids IS_PG branching).

## Email upgrade notifications
- `send_upgrade_notification(name, slug, ref, proof)` in app.py emails `NOTIFY_EMAIL`
  (default `wanandarajab@gmail.com`) on every `/billing/request-upgrade`.
- Config via env: `SMTP_HOST/`SMTP_PORT/`SMTP_USER/`SMTP_PASS/`NOTIFY_EMAIL/`APP_URL`.
  Gmail needs an App Password (2-Step Verification + google.com/apppasswords).
- If `SMTP_USER`/`SMTP_PASS` unset it just logs `[notify]` and does NOT fail the request —
  so the feature is dormant until creds are added to the VPS `.env` (NOT committed).

## Deploy (VPS)
- VPS: `ubuntu@92.4.141.94`, SSH key `$HOME/Downloads/vps.key`
  (`ssh -i ~/Downloads/vps.key ubuntu@92.4.141.94`).
- App dir `/home/ubuntu/wans-inventory` (git clone), systemd unit `wans-inventory`
  (gunicorn on :10000). `.env` has DATABASE_URL (Supabase), SECRET_KEY,
  ADMIN_PASSWORD, PAYMENT_PHONE=0763750114, PRO_PRICE="UGX 15,000 / month".
- Deploy = commit + push to main, then on VPS:
  `cd /home/ubuntu/wans-inventory && git pull && sudo systemctl restart wans-inventory`
  Check: `sudo systemctl is-active wans-inventory`.
- Logs: `sudo journalctl -u wans-inventory --no-pager -n 100`.
- `uploads/` dir auto-created at import on VPS; proof images live there.

## Local Testing
- Run the app locally against SQLite (dates = strings): `DB_PATH=inventory.db python3 app.py`
- Fresh DB: `DB_PATH=/tmp/x.db python3 app.py`
- To mimic prod dates you can call `A.shortdate(datetime(...))` directly.
- Use the Flask test client per scenario (login needs csrf token from GET page).

## User Constraints
- ONLY touch this app and its VPS deployment. DO NOT touch: GoldBot, OSRM Docker,
  the jarvis web app (port 3000), or the local VPS Postgres server directly.
- Do not add code comments unless asked. Keep responses short.
- Only commit/push when the user asks (or when explicitly following the deploy flow
  the user expects). Commit message style: concise past-tense summary line(s).