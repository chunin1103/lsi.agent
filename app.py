# app.py (Refactored Streamlit Client with Progressive Clustering UI)
import streamlit as st
import pandas as pd
import pathlib
import time 
from typing import List, Optional, Dict, Any 

# Import functions from the api_client.py
try:
    from api_client import get_api_status, stream_sse_keywords, call_cluster_api, LLMConfigClientInput 
except ImportError:
    st.error("Failed to import from api_client.py. Make sure it's in the same directory or Python path.")
    def get_api_status(seed_kw, planner_active): return {"total_cached_keywords":0} 
    def stream_sse_keywords(*args, **kwargs): st.error("API client (stream) not available."); return False
    def call_cluster_api(*args, **kwargs): st.error("API client (cluster) not available."); return None
    class LLMConfigClientInput: pass 


# ── Page & global config ───────────────────────────────────────────────────
st.set_page_config(page_title="Keyword Burst Buddy", layout="wide")

# Function to load external CSS
def load_css(file_name):
    """Loads an external CSS file into the Streamlit app."""
    try:
        css_path = pathlib.Path(__file__).parent / "templates" / file_name
        with open(css_path) as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
        print(f"APP: Loaded CSS from {css_path}")
    except FileNotFoundError:
        st.error(f"CSS file not found: {file_name} at {css_path}. Please ensure 'templates/style.css' exists.")
        print(f"APP: ERROR - CSS file not found: {file_name} at {css_path}")

load_css("style.css") 

# ── Title & Global Summary Metrics ───────────────────────────────────────────
st.title("LSI Keyword Buddy") 
st.caption("Generate and cluster SEO keywords")

if "total_keywords_generated" not in st.session_state: st.session_state.total_keywords_generated = 0
if "total_topics_clustered" not in st.session_state: st.session_state.total_topics_clustered = 0

header_cols = st.columns([3, 1, 1, 3]) 
with header_cols[0]:
    seed_input_val = st.text_input("Enter seed keyword", placeholder="e.g., nước mắm", key="seed_input_main", label_visibility="collapsed")
with header_cols[1]:
    start_button_val = st.button("Generate & Cluster", key="generate_button_main", use_container_width=True)
with header_cols[2]:
    total_keywords_metric_placeholder = st.empty()
with header_cols[3]:
    total_topics_metric_placeholder = st.empty()
    
# Update metrics display based on session state (will update on rerun)
total_keywords_metric_placeholder.metric(label="Total Keywords", value=st.session_state.total_keywords_generated)
total_topics_metric_placeholder.metric(label="Total Topics", value=st.session_state.total_topics_clustered)


cols_options = st.columns([1,1,5]) 
with cols_options[0]:
    use_planner_val = st.checkbox("Use Google Ads Planner", value=True, key="use_planner_checkbox_main")

# ── Streamlit session state initialization ─────────────────────────────────
if "all_kw" not in st.session_state: st.session_state.all_kw = []  
if "stream_active" not in st.session_state: st.session_state.stream_active = False 
if "last_seed" not in st.session_state: st.session_state.last_seed = "" 
if "last_planner_option" not in st.session_state: st.session_state.last_planner_option = True 
if "topic_clusters" not in st.session_state: st.session_state.topic_clusters = [] 
if "existing_topic_names_session" not in st.session_state: st.session_state.existing_topic_names_session = []


# --- Function to display topic clusters ---
# This function is now self-contained and reads directly from session_state
def display_topic_clusters_in_ui(placeholder_element): # Definition
    """Renders the topic clusters from session_state into the provided placeholder."""
    with placeholder_element.container(): 
        clusters_list = st.session_state.get("topic_clusters", [])
        # Use last_seed from session_state for button key uniqueness
        current_display_seed_for_key = st.session_state.get("last_seed", "defaultseed").replace(' ','_')


        if clusters_list:
            st.markdown(f"Found **{len(clusters_list)}** topics for '{st.session_state.get('last_seed', 'your keywords')}'.")
            for i, cluster_data_obj in enumerate(clusters_list):
                topic_name = cluster_data_obj.topic_name 
                keywords_in_cluster = cluster_data_obj.keywords
                with st.container(): 
                    st.markdown(f"<h6>{topic_name}</h6>", unsafe_allow_html=True) 
                    st.caption(f"`{len(keywords_in_cluster)} keywords`")
                    
                    button_key = f"discover_btn_{topic_name.replace(' ','_')}_{i}_{current_display_seed_for_key}"
                    st.button("Discover", 
                              key=button_key, 
                              help=f"Explore '{topic_name}' further (not yet implemented)", 
                              use_container_width=False) 

                    displayed_kws = keywords_in_cluster[:3]
                    for kw_idx, kw in enumerate(displayed_kws):
                        st.markdown(f"<small style='color: #555; margin-left: 10px;'>• {kw}</small>", unsafe_allow_html=True)
                    
                    if len(keywords_in_cluster) > 3:
                        with st.expander(f"Show {len(keywords_in_cluster) - 3} more keywords..."):
                            for kw_rest_idx, kw_rest in enumerate(keywords_in_cluster[3:]):
                                st.markdown(f"<small style='color: #555; margin-left: 10px;'>• {kw_rest}</small>", unsafe_allow_html=True)
                    st.markdown("<hr style='margin-top: 0.5rem; margin-bottom: 0.5rem;'>", unsafe_allow_html=True)
        
        elif st.session_state.last_seed and not st.session_state.stream_active: 
            if not st.session_state.all_kw : 
                 st.info("No keywords were generated. Cannot perform clustering.")
            elif st.session_state.all_kw and not clusters_list: 
                st.info("Clustering is pending or did not produce any topics for the generated keywords.")


# ── Main Content Area: Two Columns for Clusters and All Keywords ───────────
left_column, right_column = st.columns([2, 3]) 

with left_column:
    st.subheader("Topic Clusters")
    topic_clusters_display_placeholder = st.empty() 

with right_column:
    st.subheader("All Keywords")
    keywords_summary_placeholder = st.empty() 
    keywords_table_placeholder = st.empty()   
    all_keywords_buttons_placeholder = st.empty()


# Global progress bars
sse_progress_bar_placeholder = st.empty()
cluster_progress_bar_placeholder = st.empty() 


# ── Main generate and cluster logic ────────────────────────────────────────
if start_button_val: 
    current_seed = seed_input_val.strip() 
    current_planner_option = use_planner_val 
    current_order_top = True  

    if not current_seed:
        st.warning("Please enter a seed keyword first.")
    else:
        print(f"APP: Start button clicked. Seed: '{current_seed}', Planner: {current_planner_option}")
        st.session_state.all_kw = [] 
        st.session_state.topic_clusters = [] 
        topic_clusters_display_placeholder.empty() 
        keywords_table_placeholder.empty() 
        keywords_summary_placeholder.empty() 
        st.session_state.total_keywords_generated = 0 
        st.session_state.total_topics_clustered = 0
        total_keywords_metric_placeholder.metric(label="Total Keywords", value=0) 
        total_topics_metric_placeholder.metric(label="Total Topics", value=0) # Corrected line below
        st.session_state.existing_topic_names_session = [] 
        
        st.session_state.last_seed = current_seed
        st.session_state.last_planner_option = current_planner_option
        
        with st.spinner(f"Processing keywords and topics for '{current_seed}'..."):
            stream_successful = stream_sse_keywords(
                current_seed, 
                current_planner_option,
                current_order_top, 
                keywords_summary_placeholder, 
                keywords_table_placeholder,   
                sse_progress_bar_placeholder, 
                cluster_progress_bar_placeholder, 
                st.session_state 
            )
        
        total_keywords_metric_placeholder.metric(label="Total Keywords", value=st.session_state.total_keywords_generated)
        # Corrected SyntaxError here:
        total_topics_metric_placeholder.metric(label="Total Topics", value=st.session_state.total_topics_clustered)

        if not stream_successful:
            st.error("Keyword processing encountered an issue.")
        elif not st.session_state.all_kw:
            st.info("No keywords were generated from the stream.")
        
        sse_progress_bar_placeholder.empty()
        cluster_progress_bar_placeholder.empty()
        
# --- Persistent Display of Topic Clusters & All Keywords ---
# This section runs on every script rerun, drawing based on current session_state.

display_topic_clusters_in_ui(topic_clusters_display_placeholder) 

if st.session_state.all_kw: 
    if not st.session_state.stream_active: 
        keywords_summary_placeholder.markdown(f"**Total {len(st.session_state.all_kw):,} unique keywords fetched for '{st.session_state.last_seed}'.**")
        order_top = True 
        display_order = list(reversed(st.session_state.all_kw)) if order_top else st.session_state.all_kw
        df_all_kws = pd.DataFrame({"Keyword": display_order})
        keywords_table_placeholder.dataframe(df_all_kws, use_container_width=True, hide_index=True, height=400)

    with all_keywords_buttons_placeholder.container(): 
        kw_button_cols = st.columns(2)
        with kw_button_cols[0]:
            if st.button("Prepare for Copy (All Kws)", key="copy_all_kws_button_main_v3", use_container_width=True):
                order_top = True 
                copy_all_kws_list = list(reversed(st.session_state.all_kw)) if order_top else st.session_state.all_kw
                joined_all_keywords = "\n".join(copy_all_kws_list)  # Corrected: was copy_all_k_ws_list
                st.text_area("Copy all keywords:", joined_all_keywords, height=150, key="copy_all_kws_text_area_main_v3")
                st.info("All keywords ready in the text area above for you to copy.")
        with kw_button_cols[1]:
            order_top = True 
            csv_all_kws_list = list(reversed(st.session_state.all_kw)) if order_top else st.session_state.all_kw
            csv_all_bytes = ("\ufeff" + "\n".join(csv_all_kws_list)).encode("utf-8") 
            st.download_button(
                "Export CSV (All Kws)", csv_all_bytes,
                file_name=f"{st.session_state.last_seed}_all_lsi_keywords.csv",
                mime="text/csv;charset=utf-8", key="download_all_kws_csv_button_main_v3", use_container_width=True
            )
elif st.session_state.last_seed and not st.session_state.stream_active and start_button_val : 
     keywords_summary_placeholder.info("No keywords were generated from the stream.")


# ── Footer ─────────────────────────────────────────────────────────────────
st.markdown(
    "<div class='footer-text'>⚡ LSI‑Agent API | Streamlit UI</div>", 
    unsafe_allow_html=True,
)
