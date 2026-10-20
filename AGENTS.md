# Project context

Personalized running and strength training planner built with Streamlit,
SQLAlchemy, Pydantic, Plotly, and optional Gemini coaching. Local development
uses SQLite; hosted multi-account deployments use PostgreSQL with Supabase
authentication.

## Structure
- `app.py`: Streamlit app entry point.
- `pages/`: Streamlit pages.
- `services/`: planning, coaching, adjustments, exports, and integrations.
- `models/`: schemas and ORM models.
- `database/`: SQLite/PostgreSQL setup and session handling.
- `utils/`: shared UI, date, chart, and bootstrap helpers.
- `tests/`: pytest tests.
- `.github/agents/`: role-specific Copilot agents for product, design,
  implementation, QA, security/privacy, release checks, and delivery workflow.
- `.github/prompts/`: reusable multi-role feature workflow.

## Important behavior
- The weekly training order is fixed; adjust volume and intensity, not the schedule.
- AI schedule changes are automatically applied only after validation: max 110% of planned daily and weekly distance, never above peak long-run target, and no increases during taper/race week. Changes are logged and can be reverted.
- Shared plan access and edits require an accepted friend relationship. Keep server-side authorization checks on every shared read/write path.
- Hosted records must always be scoped to the authenticated account. Never use the local single-user fallback in hosted mode.
- Never commit database URLs, Supabase keys, Gemini keys, or Garmin credentials.
- Follow the training rules in `README.md`; do not relax safety bounds without explicit user approval.
- Treat age, body weight, training history, and Garmin data as sensitive personal data. Recovery metrics must not be sent to Gemini unless the athlete opted in.
- Do not present AI-generated training advice as medical diagnosis or claim scientific certainty. Keep recommendations bounded by the app's validation rules and identify uncertainty.
- Preserve the existing user experience and logged data when changing or regenerating plans. Add regression tests for behavior changes.

## Commands
- Run the app: `streamlit run app.py`
- Run tests: `pytest`
- Hosted setup and local-data transfer: see `README.md`.

## Working approach
- Trace the relevant UI-to-service-to-database flow before editing.
- Make the smallest change that addresses the request.
- Run the most relevant tests after changing behavior.
- For larger feature requests, use the Product Owner → Developer → Tester workflow in `.github/prompts/market-quality-feature.prompt.md`. Ask the Security & Privacy Reviewer to inspect changes involving identity, sharing, secrets, external services, AI, or personal data.
- Scope each delivery to a verified milestone. Never claim the product is market-ready, medically validated, or deployed solely because one feature or test suite passes.