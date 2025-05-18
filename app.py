# app.py (Refactored Streamlit Client)
import streamlit as st
import pandas as pd
import pathlib

# Import functions from the new api_client.py
try:
    from api_client import get_api_status, stream_sse_keywords
except ImportError:
    st.error("Failed to import api_client.py. Make sure it's in the same directory or Python path.")
    # Fallback or stop execution if critical
    def get_api_status(seed_kw, planner_active): return {"total_cached_keywords":0} 
    def stream_sse_keywords(*args, **kwargs): st.error("API client not available."); return


# ── Page & global config ───────────────────────────────────────────────────
st.set_page_config(page_title="Keyword Burst Buddy", layout="wide")

# Function to load external CSS
def load_css(file_name):
    """Loads an external CSS file into the Streamlit app."""
    try:
        css_path = pathlib.Path(__file__).parent / file_name
        with open(css_path) as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
        print(f"APP: Loaded CSS from {css_path}")
    except FileNotFoundError:
        st.error(f"CSS file not found: {file_name} at {css_path}")
        print(f"APP: ERROR - CSS file not found: {file_name} at {css_path}")


# Load the external CSS file
load_css("./templates/style.css")

# ── Title ──────────────────────────────────────────────────────────────────
st.title("Keyword Burst Buddy")
st.caption("Real-time LSI Keyword Generation with Server-Sent Events")

# ── User controls ──────────────────────────────────────────────────────────
# Use a form to group inputs and the button, which can sometimes improve interaction consistency
with st.form(key="keyword_form"):
    seed_input_val = st.text_input("Seed keyword", placeholder="e.g. nước mắm", key="seed_input")
    use_planner_val = st.checkbox("Use Google Ads Planner", value=True, key="use_planner_checkbox")
    order_top_val = st.checkbox("Show newest keywords on top", value=True, key="order_top_checkbox")
    start_button_val = st.form_submit_button(label="Generate Keywords")


# ── Tabs (only LSI is wired for now) ───────────────────────────────────────
tab_lsi, tab_cluster, tab_vis = st.tabs(["LSI Keywords", "Topic Clusters", "Visualization"])

# ── Streamlit session state ────────────────────────────────────────────────
# Initialize session state variables if they don't exist
if "all_kw" not in st.session_state:
    st.session_state.all_kw = []  # Stores keywords as they arrive
if "stream_active" not in st.session_state:
    st.session_state.stream_active = False # Tracks if a stream is currently active
if "last_seed" not in st.session_state:
    st.session_state.last_seed = "" # Stores the last seed used for generation
if "last_planner_option" not in st.session_state:
    st.session_state.last_planner_option = True # Stores the last planner option


# ── Main generate logic (triggered by form submission) ─────────────────────
if start_button_val: # This is True when the form's submit button is clicked
    current_seed = seed_input_val.strip()
    current_planner_option = use_planner_val
    current_order_top = order_top_val # Get the current sort order preference

    if not current_seed:
        st.warning("Please enter a seed keyword first.")
    else:
        print(f"APP: Start button clicked. Seed: '{current_seed}', Planner: {current_planner_option}")
        # Reset keywords if the seed or planner option has changed, or if no keywords from last run
        if (current_seed != st.session_state.last_seed or 
            current_planner_option != st.session_state.last_planner_option or 
            not st.session_state.all_kw):
            st.session_state.all_kw = [] 
            print("APP: Resetting all_kw due to new seed/options or empty previous run.")
        
        st.session_state.last_seed = current_seed
        st.session_state.last_planner_option = current_planner_option

        # Define placeholders within the LSI tab for dynamic content
        with tab_lsi:
            summary_placeholder = st.empty()
            table_placeholder = st.empty()
        
        # Progress bar will be outside the tab, globally visible during generation
        progress_bar = st.progress(0.0, text="Initializing stream...")
        
        with st.spinner(f"Initiating keyword generation for '{current_seed}'..."):
            # Call the keyword streaming function from api_client.py
            stream_sse_keywords(
                current_seed, 
                current_planner_option,
                current_order_top, # Pass the sort order
                summary_placeholder,
                table_placeholder,
                progress_bar,
                st.session_state # Pass the whole session state object
            )

# ── Display final/current results (reacts to checkbox toggling or if data exists) ──
# This section ensures the UI updates if the sort order checkbox is changed after a stream.
if st.session_state.all_kw:
    with tab_lsi:
        # Determine the display order based on the current checkbox state
        # This uses the 'order_top_val' directly from the checkbox, not a stored state,
        # so it reacts immediately to checkbox changes if this part of the script reruns.
        final_display_list = list(reversed(st.session_state.all_kw)) if order_top_val else st.session_state.all_kw
        
        # Only redraw this "final" display if the stream is not active.
        # The live updates happen within stream_sse_keywords.
        if not st.session_state.stream_active:
            st.markdown(f"**Displaying {len(st.session_state.all_kw):,} keywords for '{st.session_state.last_seed}'**")
            st.dataframe(
                pd.DataFrame({"Keyword": final_display_list}),
                use_container_width=True, 
                hide_index=True,
            )

        # Copy / CSV export buttons - always available if there are keywords
        col_spacer, col_copy, col_csv = st.columns([6, 1, 1])
        with col_copy:
            if st.button("Prepare for Copy", key="copy_button"):
                # Use the current display order for copying
                copy_display_list = list(reversed(st.session_state.all_kw)) if order_top_val else st.session_state.all_kw
                joined_keywords = "\n".join(copy_display_list)
                st.text_area("Copy these keywords:", joined_keywords, height=150, key="copy_text_area")
                st.info("Keywords ready in the text area above for you to copy.")

        with col_csv:
            # Use the current display order for CSV export
            csv_display_list = list(reversed(st.session_state.all_kw)) if order_top_val else st.session_state.all_kw
            csv_bytes = ("\ufeff" + "\n".join(csv_display_list)).encode("utf-8") # Add BOM for Excel
            st.download_button(
                "Export CSV", 
                csv_bytes,
                file_name=f"{st.session_state.last_seed}_lsi_keywords.csv",
                mime="text/csv;charset=utf-8", 
                key="download_csv_button"
            )
elif st.session_state.last_seed and not st.session_state.stream_active and not st.session_state.all_kw and start_button_val:
    # This handles the case where a search was attempted but yielded no results
    # and the start button was the last interaction.
    with tab_lsi:
        st.info(f"No keywords were found for '{st.session_state.last_seed}' with the selected options, or the stream was interrupted.")


# ── Footer ─────────────────────────────────────────────────────────────────
st.markdown(
    "<div class='footer-text'>⚡ LSI‑Agent API | Streamlit UI</div>", # Use class for styling
    unsafe_allow_html=True,
)
