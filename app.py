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
SOURCE_TYPES = ["FDA", "independent", "company"]
MARKET_DOMAINS = ["kalshi.com", "polymarket.com", "nextconsensus.com", "manifold.markets", "metaculus.com"]

MARKET_SCHEMA = {
    "type": "object",
    "properties": {
        "markets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "venue": {"type": "string", "description": "Kalshi, Polymarket, NextConsensus, Manifold, Metaculus"},
                    "question": {"type": "string"},
                    "probability": {"type": "number", "description": "Implied probability of YES, 0 to 1"},
                    "deadline": {"type": "string", "description": "Resolve-by date in the contract"},
                    "volume": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["venue", "question", "probability", "url"],
            },
        }
    },
    "required": ["markets"],
}

RUBRIC = """Grade each signal with this rubric. Regulators decide, so FDA evidence outweighs company claims.
NEGATIVE high: advisory committee vote against approval or efficacy; FDA briefing document or review finding that endpoints were missed, analyses were post hoc, or the statistical plan changed after unblinding; a refuse-to-file or a Complete Response Letter on this application within the last 12 months for efficacy reasons.
NEGATIVE medium: major amendment or review extension triggered by efficacy questions; incomplete response; FDA safety concerns in briefing documents; a narrowed or changed proposed indication late in review; unresolved manufacturing or inspection findings (Form 483, warning letter) at the drug's facility.
NEGATIVE low: older CRL history resolved since; class precedent of rejections; FDA staffing or delay risk.
POSITIVE high: advisory committee vote in favor; FDA statement that no review issues were identified late in review; Breakthrough or priority status plus a clean, pre-specified pivotal win confirmed by FDA documents.
POSITIVE medium: high unmet need with no approved therapy and FDA explicitly citing it; accepted label negotiations reported by independent sources; clean inspection completed.
POSITIVE low: company statements that a trial met its endpoint, sponsor-authored publications, company launch preparation, management saying the review is on track.
Rules: a claim whose only source is the company is at most low strength. If company and FDA disagree, record both and weight the FDA view. Never grade the same fact twice."""

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
                    "source_url": {"type": "string", "description": "URL of the single document that best supports this finding, taken from the search results"},
                    "source_type": {"type": "string", "enum": SOURCE_TYPES,
                                    "description": "Who is making the claim: FDA, independent (press, analysts, journals' editorial view, other companies), or company (the sponsor's own statements, press releases, sponsor-authored papers)"},
                },
                "required": ["category", "finding", "direction", "strength", "source_type", "source_url"],
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


def exa_deep(api_key, query, extra_queries, system_prompt, schema, start_date=None, n=15, include_domains=None):
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
    if include_domains:
        body["includeDomains"] = include_domains
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


def in_window(rows, start, end):
    keep, seen = [], set()
    for r in rows:
        try:
            d = date.fromisoformat((r.get("pdufa_date") or "")[:10])
        except ValueError:
            continue
        key = (r.get("drug", "").lower(), d)
        if start <= d <= end and key not in seen:
            seen.add(key)
            keep.append(r)
    return sorted(keep, key=lambda r: r["pdufa_date"])


def evidence_ranking(prior, rows, results, weights=None, cap_company=True):
    """Leave-one-out: how many points each source moved the lean."""
    p_all, _ = score(prior, rows, weights, cap_company)
    titles = {r["url"]: (r.get("title") or r["url"], r.get("date", "")) for r in results}
    by_src = {}
    for i, r in enumerate(rows):
        url = r.get("source_url") or "(no source given)"
        by_src.setdefault(url, []).append(i)
    out = []
    for url, idx in by_src.items():
        p_wo, _ = score(prior, [r for j, r in enumerate(rows) if j not in idx], weights, cap_company)
        moved = round((p_all - p_wo) * 100, 1)
        t, d = titles.get(url, (url, ""))
        out.append({
            "moved the lean (pts)": moved,
            "document": t,
            "source type": rows[idx[0]].get("source_type", ""),
            "date": d or rows[idx[0]].get("date", ""),
            "signals": len(idx),
            "url": url,
        })
    return sorted(out, key=lambda x: -abs(x["moved the lean (pts)"]))


def logit(p):
    return math.log(p / (1 - p))


def score(prior, signals, weights=None, cap_company=True):
    weights = weights or STRENGTH
    rows, total = [], logit(prior)
    for s in signals:
        strength = s.get("strength")
        if cap_company and s.get("source_type") == "company" and strength in ("medium", "high"):
            s = {**s, "strength": "low", "capped": True}
            strength = "low"
        w = DIRECTION.get(s.get("direction"), 0) * weights.get(strength, 0)
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
    if st.query_params.get("access") == _pw:
        st.session_state["authed"] = True
    else:
        entered = st.text_input("Password", type="password")
        if entered == _pw:
            st.session_state["authed"] = True
            st.query_params["access"] = _pw
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
    st.markdown("**Model parameters**")
    weights = {
        "low": st.slider("Low-strength signal weight", 0.0, 1.5, STRENGTH["low"], 0.05),
        "medium": st.slider("Medium-strength signal weight", 0.0, 1.5, STRENGTH["medium"], 0.05),
        "high": st.slider("High-strength signal weight", 0.0, 1.5, STRENGTH["high"], 0.05),
    }
    cap_company = st.toggle("Cap company-only claims at low", value=True)
    st.caption("Weights are in log-odds. 0.7 roughly halves the odds (negative) or doubles them (positive).")

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
                f"PDUFA calendar {today:%B %Y}",
                f"PDUFA calendar {end:%B %Y}",
                "FDA extends PDUFA target action date major amendment",
                f"FDA decision expected {today:%B %Y} NDA BLA",
                "company announces FDA accepted application PDUFA target action date",
            ],
            "Prefer company press releases and SEC filings. Only include decisions with a stated PDUFA or target action date inside the window. One row per drug.",
            DISCOVER_SCHEMA,
            start_date=(today - timedelta(days=365)).isoformat(),
            n=40,
        )
        res["seconds"] = round(time.time() - t0, 1)
        res["output"]["catalysts"] = in_window(res["output"].get("catalysts", []), today, end)
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
                    f"{target} FDA briefing document advisory committee vote",
                ],
                "You are a skeptical biotech analyst preparing for an FDA decision. Extract only evidence-backed signals, "
                "with the date and source type of each. Look hardest for FDA documents: advisory committee briefing "
                "documents and votes, CRLs, review extensions. Do not invent signals; return fewer if evidence is thin.\n\n"
                + RUBRIC,
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
        top = st.container()
        st.markdown("**Signals** (edit direction or strength to test your own read; each is cited)")
        base = [{k: r.get(k) for k in ["category", "source_type", "direction", "strength", "finding", "date", "source_url"]}
                for r in out.get("signals", [])]
        edited = st.data_editor(
            base, hide_index=True, key=f"edit_{sig_key}",
            disabled=["category", "source_type", "finding", "date", "source_url"],
            column_config={
                "direction": st.column_config.SelectboxColumn("direction", options=list(DIRECTION)),
                "strength": st.column_config.SelectboxColumn("strength", options=list(STRENGTH)),
                "source_url": st.column_config.LinkColumn("source"),
            },
        )
        signals_now = list(edited) if isinstance(edited, list) else edited.to_dict("records")
        p, rows = score(prior, signals_now, weights, cap_company)
        with top:
            c1, c2, c3 = st.columns([1, 1, 2])
            c1.metric("Prior", f"{prior:.0%}")
            c2.metric("Lean after signals", f"{p:.0%}", f"{(p - prior) * 100:+.0f} pts")
            c3.write(out.get("summary", ""))
            st.caption("Screening lean from your prior plus evidence-weighted signals. Change the parameters in the sidebar or edit a signal below.")

        with st.expander("How the lean is calculated"):
            terms = " ".join(f"{r['weight']:+.2f}" for r in rows) or "0"
            st.latex(r"\text{logit}(p) = \text{logit}(\text{prior}) + \sum_i d_i \cdot w_{s_i}")
            st.markdown(
                f"- Prior **{prior:.0%}** gives a starting log-odds of **{logit(prior):+.2f}**.\n"
                f"- Each signal adds direction (+1, 0, -1) times its strength weight: low **{weights['low']}**, medium **{weights['medium']}**, high **{weights['high']}**.\n"
                f"- Company-only claims capped at low: **{'on' if cap_company else 'off'}**.\n"
                f"- This scan adds: {terms}\n"
                f"- Final log-odds **{logit(prior) + sum(r['weight'] for r in rows):+.2f}**, which is **{p:.0%}**."
            )
            st.markdown("**Grading rubric Exa is given**")
            st.code(RUBRIC, language=None)

        st.markdown("**Documents ranked by how much they moved the lean**")
        rank = evidence_ranking(prior, signals_now, sig["results"], weights, cap_company)
        st.dataframe(rank, hide_index=True, column_config={"url": st.column_config.LinkColumn("link")})
        st.caption("Each row: the lean with all signals, minus the lean without that document's signals. Negative = pushed toward a rejection.")

        if out.get("open_questions"):
            st.markdown("**What would change the view**")
            for q in out["open_questions"]:
                st.write(f"- {q}")

        st.markdown("**All sources retrieved** (search relevance order, not importance)")
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

        st.subheader("3. Check against the crowd")
        st.caption("Prediction markets and forecasters on the same decision. Thin markets can be stale; contracts resolve on a date, so a delay counts as NO.")
        if live and st.button("Find prediction markets"):
            with st.spinner("Exa deep search across Kalshi, Polymarket and forecasting sites..."):
                mk = exa_deep_safe(
                    api_key,
                    f"Prediction market or forecast on whether the FDA approves {target}",
                    [f"{target} FDA approval odds", f"Will the FDA approve {target.split(' (')[0]}"],
                    "Return only markets or forecasts about this specific drug's FDA approval. Read the current YES price as a probability from 0 to 1. Skip markets on other drugs.",
                    MARKET_SCHEMA,
                    n=10,
                    include_domains=MARKET_DOMAINS,
                )
            save_run("markets", sig_key, mk)
            st.session_state["markets"] = {"key": sig_key, **mk}
        if not live:
            saved_mk = load_runs("markets")
            safe = "".join(c if c.isalnum() else "_" for c in sig_key)[:60]
            if safe in saved_mk:
                st.session_state["markets"] = {"key": sig_key, **saved_mk[safe]}
        mk = st.session_state.get("markets")
        if mk and mk.get("key") == sig_key:
            rows = mk["output"].get("markets", [])
            if rows:
                for r in rows:
                    try:
                        r["gap vs lean (pts)"] = round((p - float(r["probability"])) * 100)
                    except (TypeError, ValueError):
                        pass
                st.dataframe(rows, hide_index=True)
                st.caption("A big gap is the interesting part: either the market is missing evidence, or the scan is.")
            else:
                st.info("No prediction market or public forecast found for this decision.")
