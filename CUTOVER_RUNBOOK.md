# Phase 3 cutover runbook — SQLite → Postgres

This is the exact sequence to run against your **real** Render deployment.
Everything in this runbook was tested end-to-end against a real local
Postgres instance (not simulated) before being written down here — see the
summary in chat for what was tested.

## 0. What changed in the code (already done, just apply these 3 files)

- `backend/app.py` — normalizes `postgres://` → `postgresql://` connection
  strings, and only changes behavior when `DATABASE_URL` is actually a
  Postgres URL. No `DATABASE_URL` set → behaves exactly as it does today
  (local SQLite file, zero config needed).
- `backend/requirements.txt` — added `psycopg2-binary` (the Postgres driver).
  Nothing removed.
- `backend/scripts/migrate_sqlite_to_postgres.py` — new, standalone script.
  Doesn't run automatically, doesn't touch your SQLite file, only reads it.

## 1. Before touching production

1. Deploy these 3 file changes to Render as a normal deploy. Since
   `DATABASE_URL` isn't set to a Postgres URL yet, the app keeps using
   SQLite exactly as it does now — this step alone changes nothing
   user-visible. Confirm the app still works normally after this deploy.
2. In the Render dashboard, create a new **Postgres** instance (free or
   starter tier is fine to begin with). Copy its **Internal Connection
   String** (Render gives you both an internal and external URL — use the
   internal one for the web service's `DATABASE_URL`, it's faster and
   doesn't leave Render's network).
3. **Do not set `DATABASE_URL` on the web service yet.** Postgres now
   exists, empty, alongside your still-running SQLite-backed app.

## 2. Get a copy of the live SQLite file

Render's web service filesystem is ephemeral but reachable while the
instance is running. Options, in order of preference:

- If you have shell access to the running instance (Render's "Shell" tab),
  copy `ai_team_brain.db` out via `render` CLI or by temporarily exposing
  it and downloading — whichever your Render plan supports.
- Simplest reliable option: add a temporary one-off admin-only route (or a
  Render Job) that streams the current `ai_team_brain.db` file back to you,
  run once, then remove it. I can write this for you if you don't have
  direct shell/file access on your plan — just say so.

Whatever method you use: **get the file down to your own machine and keep
a backup copy of it before doing anything else.**

## 3. Run the migration from your own machine

With the SQLite file copied locally and the Postgres external connection
string from step 1:

```bash
cd backend
pip install -r requirements.txt   # picks up psycopg2-binary
python scripts/migrate_sqlite_to_postgres.py \
    --sqlite ./ai_team_brain.db \
    --postgres "postgresql://<render-postgres-external-url>"
```

Read the per-table summary it prints. It will refuse to report success
unless every table's row count matches exactly between source and
destination — don't proceed past a MISMATCH.

## 4. Cut over

1. In Render, set `DATABASE_URL` on the **web service** to the Postgres
   **internal** connection string (not the external one you used for the
   migration script — internal is faster and private).
2. Redeploy the web service. Watch the boot log for the line
   `Database: PostgreSQL` — that confirms it picked up the new connection.
3. Run through your normal smoke test: log in, view teams/tasks/chat,
   create something new, confirm it's really landing in Postgres (e.g. by
   checking it's still there after a redeploy — that's the whole point).
4. Also add a **persistent disk** to the web service (or drop file uploads
   from local disk entirely — that's the separate storage phase you said
   to hold off on). Until then, `./uploads` is still local and still
   ephemeral; only the database is fixed by this phase.

## 5. After cutover

- Keep the SQLite file backup for a while — don't delete it immediately.
- The old `ai_team_brain.db` on the Render instance itself will simply stop
  being read/written once `DATABASE_URL` points at Postgres; it's inert,
  no need to delete it manually.
- If anything looks wrong after cutover, revert by unsetting `DATABASE_URL`
  on the web service and redeploying — this puts you back on the SQLite
  file exactly as it was (assuming you haven't deleted it), while you
  investigate.
