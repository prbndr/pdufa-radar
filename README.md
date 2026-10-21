# PDUFA Radar

An Exa-powered screen for upcoming FDA decisions, built for biotech investors.

1. **Find** upcoming PDUFA dates with Exa deep search (press releases, filings, catalyst calendars).
2. **Read the signals** for one decision: advisory committees, review extensions, manufacturing and inspection issues, launch prep, management language, financing. Almost none of this is on ClinicalTrials.gov.
3. **Lean**: start from your own prior, shift it by evidence-weighted signals (log-odds), and see every source.

## Run

```bash
pip install streamlit requests
export EXA_API_KEY=your_key
streamlit run app.py
```

Every live run is saved to `runs/`. "Replay saved runs" mode shows the last saved results with no API calls, so the demo never depends on the network.

`runs/` ships with a seeded example (Capricor deramiocel, PDUFA Nov 22, 2026) assembled from public sources. Do one live run before the demo so the replay is a real Exa deep-search output.

## Notes

- The lean is a screening view, not a calibrated forecast. Weights: low 0.15, medium 0.35, high 0.70 in log-odds.
- Deep search can take 10 to 60 seconds per call.
