# Adam's Marathon Planner

Personal marathon training platform built with Streamlit, SQLite/PostgreSQL, SQLAlchemy, Pydantic, Plotly and Gemini (optional).
Default goal: marathon on 26 September 2027. Weekly order is fixed: Mon Push+Core, Tue Quality, Wed Pull,
Thu Legs, Fri Long Run, Sat/Sun Easy/Event. Only volume and intensity ever change.

> Not medical advice. See a qualified professional for pain, injury or health concerns.

## Run locally (Windows)
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
pytest
```

## Key rules implemented
- Phases generated from race date: Base, Aerobic Build, Marathon Build, Peak (32 km default), Taper (3 weeks), Race Week.
- Recovery week every 4th build week; long-run increases capped at 3 km/week.
- Completion >= 90%: no change; 75-89%: -5%; < 75%: -10% on upcoming runs. Missed sessions are never moved or made up,
  and the next long run after a missed one is capped at the missed distance.
- Event weeks: Tuesday reduced, Friday shakeout (or reduced), weekend = event.
- Legs: reduced volume if long run > 28 km, minimal in taper, none in race week.
- Gemini can suggest safe increases or reductions. Changes apply automatically only after validation: no more than
  110% of planned distance per run or week, no long run above the configured peak, no event-session edits, and no
  increases during taper or race week. Each change is logged and can be reverted from the AI Coach page.
- Accepted friends can view and directly edit each other's future training plan and goal race. Edits preserve the
  fixed schedule and the same distance/intensity limits.
- Strength logs are included in Gemini's recent-history context alongside notes, completed weeks, and Garmin runs.

## AI setup (optional)
Set `GEMINI_API_KEY` as an environment variable or in Streamlit secrets, or paste it in Settings (session only).

## Deployment
**Free-first: Streamlit Community Cloud + Supabase**
1. Create a Supabase project, enable email/password authentication, and set its Auth **Site URL** to the deployed app URL.
2. In Supabase, copy the project URL and publishable key. From **Connect**, copy the PostgreSQL **Shared Pooler** connection URL (the `aws-...pooler.supabase.com:5432` URL works with this app's psycopg driver). Replace `[YOUR-PASSWORD]` with the database password, URL-encoding special characters, and append `?sslmode=require`.
3. Push this repository to GitHub and deploy `app.py` on Streamlit Community Cloud.
4. In the app's Streamlit **Settings > Secrets**, add the following values (replace every placeholder; never commit real secrets):

   ```toml
   DATABASE_URL = "postgresql://postgres.[project-ref]:[password]@[pooler-host]:5432/postgres?sslmode=require"
   SUPABASE_URL = "https://[project-ref].supabase.co"
   SUPABASE_ANON_KEY = "[publishable-or-anon-key]"
   GEMINI_API_KEY = "[optional-gemini-key]"
   GARMIN_ENCRYPTION_KEY = "[optional-generated-fernet-key]"
   ```

   Generate a Fernet key locally with:
   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   Add that output to Streamlit Secrets for per-account Garmin credential encryption. Keep a secure backup:
   changing or losing the key makes saved Garmin credentials unreadable. Garmin credentials are excluded from
   user backups and exports. Do not commit or share the encryption key.

5. The app creates/updates its tables on startup. Sign up with your email, confirm it if Supabase requires confirmation, then sign in. Each account gets its own plan and data. The first account claims the legacy single-user profile if the existing database has one.
6. To transfer local SQLite data, download a JSON backup from the local app's **Settings** page, sign in to the hosted app, and restore that backup there. Restore only replaces the signed-in account's data.

The app enables PostgreSQL row-level security without public table policies; the private server-side database connection performs account-scoped access. The hosted database keeps progress across app restarts and redeploys. Free plans have limits and may pause or change over time, so check current provider terms and keep periodic JSON backups. Hosted Garmin credentials are encrypted per account at rest using the server-side Fernet key; each account's Garmin token files are stored in its own directory. Garmin may require additional account verification, and provider login behavior can change.

**Private deployment** (VM or container)
```powershell
pip install -r requirements.txt
streamlit run app.py --server.port 8501 --server.address 0.0.0.0
```
Put it behind HTTPS and authentication (reverse proxy or VPN). Persist the `data/` folder.