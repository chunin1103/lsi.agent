# Keyword Burst Buddy – ultra‑minimal black‑&‑white UI
# ---------------------------------------------------
# One‑file Streamlit front‑end for lsi_keyword_api.py
# ---------------------------------------------------

import time
import pandas as pd
import requests
import streamlit as st

# ── Page & global config ───────────────────────────────────────────────────
st.set_page_config(page_title="Keyword Burst Buddy", layout="wide")

API_BASE  = "http://localhost:8000"   # ← change to your API host/port
PAGE_SIZE = 50
TIMEOUT   = 120                      # seconds to wait for crawler

# ── Pure black‑&‑white theme (no greys) ────────────────────────────────────
st.markdown(
    """
    <style>
        html, body, .stApp { background:#ffffff ; color:#000000 ; }
        /* hide Streamlit’s chrome */
        header, footer, #MainMenu { visibility:hidden; }

        /* buttons – white background, black border, invert on hover */
        div.stButton > button {
            background:#ffffff ;
            color:#000000 ;
            border:1px solid #000000 ;
            border-radius:4px ;
            padding:0.4rem 1.2rem ;
            font-weight:600 ;
        }
        div.stButton > button:hover {
            background:#000000 ;
            color:#ffffff ;
        }

        /* text inputs / textareas */
        input[type="text"], textarea {
            background:#ffffff ;
            color:#000000 ;
            border:1px solid #000000 ;
            border-radius:4px ;
            padding:0.45rem 0.6rem ;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Title ──────────────────────────────────────────────────────────────────
st.title("Keyword Burst Buddy")

# ── User controls ──────────────────────────────────────────────────────────
seed       = st.text_input("Seed keyword", placeholder="e.g. nước mắm")
order_top  = st.checkbox("Show newest keywords on top", value=True, key="order_top")
start_btn  = st.button("Generate")

# ── Tabs (only LSI is wired for now) ───────────────────────────────────────
tab_lsi, tab_cluster, tab_vis = st.tabs(["LSI Keywords", "Topic Clusters", "Visualization"])

# ── Streamlit session state ────────────────────────────────────────────────
state = st.session_state
state.setdefault("all_kw", [])        # raw order from API (oldest → newest)

# ── Helper functions ───────────────────────────────────────────────────────

def fetch_page(seed_kw: str, page: int):
    r = requests.get(
        f"{API_BASE}/keywords",
        params={
            "seed": seed_kw,
            "page": page,
            "page_size": PAGE_SIZE,
            "planner": "true",
        },
    )
    r.raise_for_status()
    return r.json()["keywords"]

def wait_status(seed_kw: str):
    total = 0
    """Block until /status says finished, but stream totals meanwhile."""
    while True:
        r = requests.get(f"{API_BASE}/status", params={"seed": seed_kw})
        r.raise_for_status()
        body = r.json()
        st.session_state.total = r.json()["total"]      # live progress bar

        # ── start paging as soon as something exists ──
        if body["total"] and not body["finished"]:
            return body["total"], False             # <─ NEW
        if body["finished"]:
            return body["total"], True
        time.sleep(1)

def get_status(seed_kw: str) -> dict:
    r = requests.get(f"{API_BASE}/status", params={"seed": seed_kw})
    r.raise_for_status()
    return r.json()

def stream_results(seed_kw: str):
    """Fetch all pages and update UI live while streaming."""
    total, ready = wait_status(seed_kw)

    # placeholders we repeatedly overwrite
    ph_summary = tab_lsi.empty()
    ph_table   = tab_lsi.empty()
    prog       = st.progress(0.0, text="Fetching keywords…")

    collected_raw = []                # chronological (oldest → newest)
    page = 1
    while True:
        chunk = fetch_page(seed_kw, page)
        if not chunk:
            if ready:               # finished AND empty page → really done
                break
            time.sleep(1)           # not finished yet → wait & retry
            continue

        collected_raw.extend(chunk)

        # adjust display order according to current checkbox
        display = (list(reversed(collected_raw))
                   if st.session_state.order_top else collected_raw)

        # live counter & table
        ph_summary.markdown(f"**Fetched {len(collected_raw):,} / {total:,} keywords**")
        ph_table.dataframe(
            pd.DataFrame({"Keyword": display}),
            use_container_width=True,
            hide_index=True,
        )

        prog.progress(min(len(collected_raw) / total, 1.0))
        page += 1
        time.sleep(0.2)

    prog.empty()
    state.all_kw = collected_raw      # store raw order for later use

# ── Main generate logic ────────────────────────────────────────────────────
if start_btn:
    if not seed.strip():
        st.warning("Please enter a seed keyword first.")
        st.stop()
    state.all_kw = []                 # reset previous run
    stream_results(seed.strip())

# ── Show final results (reacts to checkbox toggling) ───────────────────────
if state.all_kw:
    with tab_lsi:
        final_display = (list(reversed(state.all_kw))
                         if st.session_state.order_top else state.all_kw)

        st.dataframe(
            pd.DataFrame({"Keyword": final_display}),
            use_container_width=True,
            hide_index=True,
        )

        # Copy / CSV export buttons
        col_spacer, col_copy, col_csv = st.columns([6, 1, 1])
        with col_copy:
            if st.button("Copy All"):
                joined = "\n".join(final_display)
                st.text_input("_clip", value=joined, label_visibility="hidden")
                st.markdown(
                    "<script>"
                    "navigator.clipboard.writeText("
                    "document.getElementById('_clip').value);"
                    "</script>",
                    unsafe_allow_html=True,
                )
                st.toast("Copied ✔️")
        with col_csv:
            csv_bytes = ("\ufeff" + "\n".join(final_display)).encode("utf-8")
            st.download_button(
                "Export CSV",
                csv_bytes,
                file_name=f"{seed}_lsi_keywords.csv",
                mime="text/csv;charset=utf-8",
            )

# ── Footer ─────────────────────────────────────────────────────────────────
st.markdown(
    "<div style='text-align:center; font-size:0.8rem;'>⚡ Powered by LSI‑Agent API | Built with Streamlit</div>",
    unsafe_allow_html=True,
)
