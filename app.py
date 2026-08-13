import os
import requests
import pandas as pd
import streamlit as st
from supabase import create_client, Client
from google import genai

# ==========================================
# 1. PAGE CONFIGURATION & PASSCODE AUTH
# ==========================================
st.set_page_config(
    page_title="Personal Command Center",
    page_icon="⚡",
    layout="wide"
)

def check_password():
    """Returns `True` if the user enters the correct password."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if st.session_state.authenticated:
        return True

    st.title("🔒 Passcode Required")
    user_password = st.text_input("Enter Access Passcode:", type="password")
    
    if st.button("Unlock Dashboard"):
        if user_password == st.secrets.get("APP_PASSWORD"):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("❌ Incorrect passcode. Access denied.")
            
    return False

if not check_password():
    st.stop()  # Stop executing script until authenticated

# ==========================================
# 2. INITIALIZE CONNECTIONS & CLIENTS
# ==========================================
# Supabase Client
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase: Client = init_supabase()

# Gemini AI Client
@st.cache_resource
def init_gemini():
    return genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

gemini_client = init_gemini()

# ==========================================
# 3. HELPER FUNCTIONS
# ==========================================
def fetch_hevy_workouts():
    """Fetch recent workouts from Hevy API."""
    api_key = st.secrets.get("HEVY_API_KEY")
    if not api_key:
        return None
    
    headers = {"api-key": api_key, "accept": "application/json"}
    try:
        response = requests.get("https://api.hevyapp.com/v1/workouts?page=1&pageSize=5", headers=headers)
        if response.status_code == 200:
            return response.json().get("workouts", [])
    except Exception as e:
        st.error(f"Error fetching Hevy data: {e}")
    return None

# ==========================================
# 4. DASHBOARD INTERFACE
# ==========================================
st.title("⚡ Personal Command Center")
st.caption("Welcome back! Your OS is live and synced.")

# Navigation Tabs
tab_overview, tab_fitness, tab_ai = st.tabs(["📊 Overview", "🏋️ Fitness (Hevy)", "🤖 AI Assistant"])

# --- TAB 1: OVERVIEW ---
with tab_overview:
    st.subheader("System Status")
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric(label="Database Status", value="Connected 🟢")
    with col2:
        st.metric(label="Domain", value="67whatever.au")
    with col3:
        st.metric(label="AI Model", value="Gemini 2.5 Flash")

    st.divider()
    st.info("💡 Next feature: Syncing daily task logs to Supabase.")

# --- TAB 2: FITNESS ---
with tab_fitness:
    st.subheader("🏋️ Recent Workouts (Hevy)")
    if st.button("Sync Hevy Workouts"):
        workouts = fetch_hevy_workouts()
        if workouts:
            for w in workouts:
                with st.expander(f"Workout: {w.get('title', 'Untitled')} ({w.get('start_time', '')[:10]})"):
                    st.write(f"**Duration:** {w.get('duration_seconds', 0) // 60} mins")
                    st.write(f"**Exercises Logged:** {len(w.get('exercises', []))}")
        else:
            st.warning("Could not fetch workouts. Check your HEVY_API_KEY in Secrets.")

# --- TAB 3: AI ASSISTANT ---
with tab_ai:
    st.subheader("🤖 Gemini Assistant")
    user_prompt = st.text_area("Ask your personal OS anything:", placeholder="Summarize my day or suggest a training tweak...")
    
    if st.button("Ask Gemini"):
        if user_prompt:
            with st.spinner("Thinking..."):
                try:
                    response = gemini_client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=user_prompt
                    )
                    st.markdown("### Response:")
                    st.write(response.text)
                except Exception as e:
                    st.error(f"Gemini API Error: {e}")
        else:
            st.warning("Please enter a prompt first.")
