"""PDUFA Radar: an Exa-powered screen for upcoming FDA decisions.

Finds near-term FDA approval decisions, then uses Exa deep search to pull
signals that never show up on ClinicalTrials.gov (manufacturing/inspection
issues, advisory committees, review extensions, launch hiring, management
language, financing) and turns them into a transparent, editable lean.

Run:  pip install streamlit requests
      export EXA_API_KEY=...
      streamlit run app.py
"""

import json
import math
import os
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests
import streamlit as st

EXA_URL = "https://api.exa.ai/search"
RUNS = Path(__file__).parent / "runs"
RUNS.mkdir(exist_ok=True)

CATEGORIES = [
    "Manufacturing / inspection",
    "FDA review process",
    "Advisory committee",
    "Launch readiness",
    "Management language",
    "Financing / insider activity",
    "Class precedent / competitors",
    "FDA environment",
]
STRENGTH = {"low": 0.15, "medium": 0.35, "high": 0.7}
DIRECTION = {"positive": 1, "negative": -1, "neutral": 0}

DISCOVER_SCHEMA = {
    "type": "object",
    "properties": {
        "catalysts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "drug": {"type": "string"},
                    "company": {"type": "string"},
                    "ticker": {"type": "string"},
                    "indication": {"type": "string"},
                    "pdufa_date": {"type": "string", "description": "YYYY-MM-DD if known"},
                    "application": {"type": "string", "description": "NDA, BLA, sNDA, resubmission, etc."},
                },
                "required": ["drug", "company", "pdufa_date"],
            },
        }
    },
    "required": ["catalysts"],
}

SIGNAL_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "Two sentences on the setup going into the decision"},
        "signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": CATEGORIES},
                    "finding": {"type": "string", "description": "One factual sentence"},
                    "direction": {"type": "string", "enum": list(DIRECTION)},
                    "strength": {"type": "string", "enum": list(STRENGTH)},
                    "date": {"type": "string", "description": "Date of the underlying evidence"},
                },
                "required": ["category", "finding", "direction", "strength"],
            },
        },
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "signals"],
}


def exa_deep_safe(*args, **kwargs):
    try:
        return exa_deep(*args, **kwargs)
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        hint = " (out of credits?)" if code == 402 else ""
        st.error(f"Exa API error {code}{hint}. Switch to 'Replay saved runs' to keep going.")
    except requests.RequestException as e:
        st.error(f"Could not reach Exa: {e}. Switch to 'Replay saved runs' to keep going.")
    st.stop()


def exa_deep(api_key, query, extra_queries, system_prompt, schema, start_date=None, n=15):
    body = {
        "query": query,
        "additionalQueries": extra_queries,
        "type": "deep",
        "numResults": n,
        "systemPrompt": system_prompt,
        "outputSchema": schema,
        "contents": {"highlights": True},
    }
    if start_date:
        body["startPublishedDate"] = start_date
    r = requests.post(
        EXA_URL,
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        json=body,
        timeout=180,
    )
    r.raise_for_status()
    data = r.json()
    out = (data.get("output") or {}).get("content")
    if isinstance(out, str):
        try:
            out = json.loads(out)
        except json.JSONDecodeError:
            out = {"summary": out}
    return {
        "output": out or {},
        "grounding": (data.get("output") or {}).get("grounding", []),
        "results": [
            {
                "title": x.get("title"),
                "url": x.get("url"),
                "date": (x.get("publishedDate") or "")[:10],
                "highlight": (x.get("highlights") or [""])[0],
            }
            for x in data.get("results", [])
        ],
        "cost": (data.get("costDollars") or {}).get("total"),
    }


def save_run(kind, key, payload):
    safe = "".join(c if c.isalnum() else "_" for c in key)[:60]
    (RUNS / f"{kind}__{safe}.json").write_text(json.dumps(payload, indent=2))


def load_runs(kind):
    out = {}
    for p in sorted(RUNS.glob(f"{kind}__*.json")):
        out[p.stem.split("__", 1)[1]] = json.loads(p.read_text())
    return out


def logit(p):
    return math.log(p / (1 - p))


def score(prior, signals):
    rows, total = [], logit(prior)
    for s in signals:
        w = DIRECTION.get(s.get("direction"), 0) * STRENGTH.get(s.get("strength"), 0)
        total += w
        rows.append({**s, "weight": round(w, 2)})
    return 1 / (1 + math.exp(-total)), rows


def _has_secrets():
    try:
        return bool(st.secrets)
    except Exception:
        return False


def _default_key():
    try:
        return st.secrets.get("EXA_API_KEY", "") or os.getenv("EXA_API_KEY", "")
    except Exception:
        return os.getenv("EXA_API_KEY", "")


# ---------------- UI ----------------
st.set_page_config(page_title="PDUFA Radar", layout="wide")

_pw = os.getenv("APP_PASSWORD") or (st.secrets.get("APP_PASSWORD") if hasattr(st, "secrets") and _has_secrets() else None)
if _pw and not st.session_state.get("authed"):
    entered = st.text_input("Password", type="password")
    if entered == _pw:
        st.session_state["authed"] = True
        st.rerun()
    st.stop()
st.title("PDUFA Radar")
st.caption(
    "For biotech investors: what the web is saying about upcoming FDA decisions, "
    "beyond what ClinicalTrials.gov can tell you. Powered by Exa deep search."
)

with st.sidebar:
    server_key = _default_key()
    if server_key:
        api_key = server_key
        st.caption("Using the configured Exa key.")
    else:
        api_key = st.text_input("Exa API key", type="password")
    mode = st.radio("Mode", ["Live (Exa API)", "Replay saved runs"], index=0 if api_key else 1)
    prior = st.slider(
        "Your prior probability of approval", 0.30, 0.95, 0.75, 0.01,
        help="Set your own base rate. Signals shift this in log-odds; the result is a screening lean, not a calibrated forecast.",
    )
    st.caption("Signal weights (log-odds): low 0.15, medium 0.35, high 0.70")

live = mode.startswith("Live") and bool(api_key)

# Step 1: discover catalysts
st.subheader("1. Upcoming FDA decisions")
today = date.today()
window = st.selectbox("Window", ["next 30 days", "next 60 days", "next 90 days"], index=1)
days = int(window.split()[1])
disc_key = f"{today.isoformat()}_{days}"

catalysts = []
if live and st.button("Find upcoming PDUFA dates"):
    end = today + timedelta(days=days)
    with st.spinner("Exa deep search: scanning press releases, filings and catalyst calendars..."):
        t0 = time.time()
        res = exa_deep_safe(
            api_key,
            f"Upcoming FDA PDUFA target action dates between {today:%B %d, %Y} and {end:%B %d, %Y} for publicly traded biotech companies",
            [
                f"PDUFA date {end:%B %Y} biotech",
                f"FDA decision expected {today:%B %Y} NDA BLA",
                "company announces FDA accepted application PDUFA target action date",
            ],
            "Prefer company press releases and SEC filings. Only include decisions with a stated PDUFA or target action date inside the window. One row per drug.",
            DISCOVER_SCHEMA,
            start_date=(today - timedelta(days=365)).isoformat(),
            n=25,
        )
        res["seconds"] = round(time.time() - t0, 1)
    save_run("discover", disc_key, res)
    st.session_state["discover"] = res

if not live:
    saved = load_runs("discover")
    if saved:
        st.session_state["discover"] = saved[sorted(saved)[-1]]

disc = st.session_state.get("discover")
if disc:
    catalysts = disc["output"].get("catalysts", [])
    st.dataframe(catalysts, hide_index=True)
    meta = f"{len(catalysts)} decisions found"
    if disc.get("seconds"):
        meta += f" in {disc['seconds']}s"
    if disc.get("cost"):
        meta += f" for ${disc['cost']:.3f}"
    st.caption(meta)
else:
    st.info("Click 'Find upcoming PDUFA dates' (live) or add a saved run to the runs/ folder.")

# Step 2: signals for one decision
st.subheader("2. Read the signals")
labels = [f"{c['drug']} ({c['company']}) - {c.get('pdufa_date', '?')}" for c in catalysts]
choice = st.selectbox("Decision", labels) if labels else None
manual = st.text_input("...or type one", placeholder="e.g. drug name, company, PDUFA date")
target = manual or choice

if target:
    sig_key = target
    if live and st.button("Run deep signal scan"):
        with st.spinner("Exa deep search: manufacturing, FDA process, launch prep, management language..."):
            t0 = time.time()
            res = exa_deep_safe(
                api_key,
                f"Evidence relevant to the upcoming FDA approval decision for {target}",
                [
                    f"{target} FDA inspection Form 483 manufacturing facility contract manufacturer",
                    f"{target} advisory committee meeting FDA",
                    f"{target} PDUFA extension major amendment review",
                    f"{target} commercial launch preparation hiring sales force",
                    f"{target} earnings call management comments FDA review on track label discussions",
                    f"{target} complete response letter same drug class precedent",
                ],
                "You are a biotech analyst preparing for an FDA decision. Extract only evidence-backed signals, "
                "with the date of each. Distinguish company statements from independent reporting. "
                "Do not invent signals; return fewer if evidence is thin.",
                SIGNAL_SCHEMA,
                start_date=(today - timedelta(days=540)).isoformat(),
                n=20,
            )
            res["seconds"] = round(time.time() - t0, 1)
        save_run("signals", sig_key, res)
        st.session_state["signals"] = res

    if not live:
        saved = load_runs("signals")
        if saved:
            pick = st.selectbox("Saved scan", sorted(saved))
            st.session_state["signals"] = saved[pick]

    sig = st.session_state.get("signals")
    if sig:
        out = sig["output"]
        p, rows = score(prior, out.get("signals", []))
        c1, c2, c3 = st.columns([1, 1, 2])
        c1.metric("Prior", f"{prior:.0%}")
        c2.metric("Lean after signals", f"{p:.0%}", f"{(p - prior) * 100:+.0f} pts")
        c3.write(out.get("summary", ""))
        st.caption("Screening lean from your prior plus evidence-weighted signals. Edit weights or the prior to test your view.")

        st.markdown("**Signals** (each one is cited below)")
        st.dataframe(
            [{k: r.get(k) for k in ["category", "direction", "strength", "weight", "finding", "date"]} for r in rows],
            hide_index=True,
        )

        if out.get("open_questions"):
            st.markdown("**What would change the view**")
            for q in out["open_questions"]:
                st.write(f"- {q}")

        st.markdown("**Sources**")
        domains = {}
        for r in sig["results"]:
            d = urlparse(r["url"] or "").netloc.replace("www.", "")
            domains[d] = domains.get(d, 0) + 1
        ctgov = domains.get("clinicaltrials.gov", 0)
        st.caption(
            f"{len(sig['results'])} sources across {len(domains)} domains; "
            f"{ctgov} from ClinicalTrials.gov"
            + (f"; {sig['seconds']}s" if sig.get("seconds") else "")
            + (f"; ${sig['cost']:.3f}" if sig.get("cost") else "")
        )
        for r in sig["results"]:
            st.markdown(f"- [{r['title'] or r['url']}]({r['url']}) {r['date']}  \n  _{(r['highlight'] or '')[:220]}_")

        if sig.get("grounding"):
            with st.expander("Field-level citations from Exa"):
                st.json(sig["grounding"])
