# Project context

Personal marathon training planner built with Streamlit, SQLAlchemy, Pydantic,
Plotly, and optional Gemini integration. Local development uses SQLite; hosted
multi-account deployments use PostgreSQL with Supabase authentication.

## Structure
- `app.py`: Streamlit app entry point.
- `pages/`: Streamlit pages.
- `services/`: planning, coaching, adjustments, exports, and integrations.
- `models/`: schemas and ORM models.
- `database/`: SQLite/PostgreSQL setup and session handling.
- `utils/`: shared UI, date, chart, and bootstrap helpers.
- `tests/`: pytest tests.

## Important behavior
- The weekly training order is fixed; adjust volume and intensity, not the schedule.
- AI schedule changes must be validated and must preserve the fixed weekly structure and taper. The requested automatic increase/decrease workflow is not implemented yet; do not claim otherwise.
- Hosted records must always be scoped to the authenticated account. Never use the local single-user fallback in hosted mode.
- Never commit database URLs, Supabase keys, Gemini keys, or Garmin credentials.
- Follow the training rules in `README.md`; don't change them unless asked.

## Commands
- Run the app: `streamlit run app.py`
- Run tests: `pytest`
- Hosted setup and local-data transfer: see `README.md`.

## Working approach
- Trace the relevant UI-to-service-to-database flow before editing.
- Make the smallest change that addresses the request.
- Run the most relevant tests after changing behavior.