# PDUFA Radar: notes for Claude Code

Streamlit app (`app.py`) that uses Exa deep search (`type: "deep"` with `outputSchema`) to find upcoming FDA decisions and score non-ClinicalTrials.gov signals. Built for an Exa forward-deployed-engineer demo.

## Goal of this session
Deploy the app so it can be shown live, and do one real Exa run so `runs/` holds genuine deep-search output.

## Secrets
- `EXA_API_KEY` (required for live mode) and `APP_PASSWORD` (optional gate). Read from env or `st.secrets`.
- Never commit keys. `.streamlit/secrets.toml` is gitignored.
- The key is used server-side only; the UI never echoes it.

## Deploy (recommended: Streamlit Community Cloud)
1. share.streamlit.io, New app, this repo, branch `main`, file `app.py`.
2. Advanced settings, Secrets:
   ```
   EXA_API_KEY = "..."
   APP_PASSWORD = "..."
   ```
3. Keep the app private or rely on APP_PASSWORD, since live runs spend Exa credits.

Alternatives: Render or Railway (`streamlit run app.py --server.port $PORT --server.address 0.0.0.0`). Vercel does not suit Streamlit.

## Local
```
pip install -r requirements.txt
export EXA_API_KEY=...
streamlit run app.py
```

## Notes
- Deep search can take 10 to 60s per call; timeout is 180s.
- `runs/` replays saved results with no API calls. The `seeded_example` files were assembled from public sources, not produced by the app; replace them with a live run and commit the new JSON so the deployed replay is real.
- Lean = prior shifted in log-odds by signal weights (low 0.15, medium 0.35, high 0.70). Screening view, not a calibrated forecast.
