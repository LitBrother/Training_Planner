# Adam's Marathon Planner

Personal marathon training platform built with Streamlit, SQLite, SQLAlchemy, Pydantic, Plotly and OpenAI (optional).
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
- AI can only reduce volume, simplify quality, or add recovery. Proposals are validated and need your approval.

## AI setup (optional)
Set `OPENAI_API_KEY` as an environment variable or in Streamlit secrets, or paste it in Settings (session only).

## Deployment
**Streamlit Community Cloud**
1. Push this folder to a private GitHub repository.
2. At share.streamlit.io choose the repo, branch and `app.py`.
3. Under Advanced settings > Secrets add: `OPENAI_API_KEY = "sk-..."`.
4. Storage is ephemeral: download a JSON backup from Settings regularly and restore it after redeploys.

**Private deployment** (VM or container)
```powershell
pip install -r requirements.txt
streamlit run app.py --server.port 8501 --server.address 0.0.0.0
```
Put it behind HTTPS and authentication (reverse proxy or VPN). Persist the `data/` folder.