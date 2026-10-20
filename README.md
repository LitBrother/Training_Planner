# Training Planner

Personalized running and strength training planner built with Streamlit, SQLite/PostgreSQL, SQLAlchemy, Pydantic,
Plotly and Gemini (optional). Athletes choose running, strength training, or both; set an optional goal event or
open-ended goal; and can track their training history and progress.

> Not medical advice. See a qualified professional for pain, injury or health concerns.

## Run locally (Windows)
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
pytest
```

## Copilot agent team
Workspace agents are in `.github/agents/`. In VS Code Chat, choose **Product Delivery Lead** for a
bounded end-to-end feature workflow, or run `/market-quality-feature` and describe the feature. The
workflow moves from Product Owner to Developer and independent Tester, with optional Product Designer,
Security & Privacy Reviewer, and Release Manager checks when relevant. Each agent has a distinct role;
the Tester is read-only and returns verified issues to the Developer for fixes.

For a single task, select **Product Owner**, **Developer**, **Tester**, **Product Designer**,
**Security & Privacy Reviewer**, or **Release Manager** directly from the agent picker. Agents support
delivery work but do not run indefinitely or deploy/push changes without explicit authorization.

## Key rules implemented
- Goal-event plans generate Base, Aerobic Build, Goal-specific Build, Peak, Taper, and Race Week phases.
  Open-ended plans do not invent a race date or race week.
- Recovery week every 4th build week; long-run increases capped at 3 km/week.
- Completion >= 90%: no change; 75-89%: -5%; < 75%: -10% on upcoming runs. Missed sessions are never moved or made up,
  and the next long run after a missed one is capped at the missed distance.
- Event weeks: reduced sessions before the selected event date and an event session on that date.
- Running-only plans omit strength sessions; strength-only plans omit running sessions. Combined plans retain both.
- Gemini can suggest safe increases or reductions. Plan changes are previewed and require explicit confirmation;
  only the exact selected proposals are applied after revalidation: no more than 110% of planned distance per run or
  week, no long run above the configured peak, no event-session edits, and no increases during taper or race week.
  Each change is logged and can be reverted from the AI Coach page.
- Accepted friends can view and directly edit each other's future training plan and goal race. Edits preserve the
  fixed schedule and the same distance/intensity limits.
- Strength logs are included in Gemini's recent-history context alongside completed weeks and Garmin runs.
  Recent notes are excluded by default; a separate affirmative opt-in on each AI review sends recent note date,
  category, and text to Google Gemini for that review. Strength suggestions are restricted to logged exercises and
  capped at a 10% load increase. Detailed strength logs retain each set's metric load and reps; their estimated
  1RM uses Epley from the highest-weight set, breaking ties by using the set with the most reps. Legacy aggregate
  logs remain aggregate and are never expanded into fabricated set history.
- Fixed research references are available in the athlete profile and supplied to Gemini as evidence constraints.
- Garmin recovery metrics are fetched for sleep, HRV, readiness, stress, body battery, and resting heart rate.
  They are sent to Gemini only after an unchecked, affirmative choice for that specific review; saved preferences do
  not grant consent. They are not medical diagnostics. Age and body weight are excluded from AI reviews.

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