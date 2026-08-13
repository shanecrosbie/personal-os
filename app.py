import json
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
    st.stop()

# ==========================================
# 2. INITIALIZE CONNECTIONS & CLIENTS
# ==========================================
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase: Client = init_supabase()

@st.cache_resource
def init_gemini():
    return genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

gemini_client = init_gemini()

# ==========================================
# 3. HELPER FUNCTIONS
# ==========================================
def fetch_hevy_workouts():
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

def fetch_exercise_templates():
    """Fetch user's exercise templates from Hevy to match titles to IDs."""
    api_key = st.secrets.get("HEVY_API_KEY")
    headers = {"api-key": api_key, "accept": "application/json"}
    try:
        res = requests.get("https://api.hevyapp.com/v1/exercise_templates?pageSize=100", headers=headers)
        if res.status_code == 200:
            return res.json().get("exercise_templates", [])
    except Exception as e:
        st.error(f"Error fetching templates: {e}")
    return []

def post_workout_to_hevy(workout_payload):
    """Post structured JSON workout to Hevy API."""
    api_key = st.secrets.get("HEVY_API_KEY")
    headers = {
        "api-key": api_key,
        "accept": "application/json",
        "Content-Type": "application/json"
    }
    res = requests.post("https://api.hevyapp.com/v1/workouts", headers=headers, json=workout_payload)
    return res

# ==========================================
# 4. DASHBOARD INTERFACE
# ==========================================
st.title("⚡ Personal Command Center")
st.caption("Welcome back! Your OS is live and synced.")

tab_overview, tab_fitness, tab_ai = st.tabs(["📊 Overview", "🏋️ Fitness & Hevy Creator", "🤖 AI Assistant"])

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

# --- TAB 2: FITNESS & HEVY CREATOR ---
with tab_fitness:
    st.subheader("📝 Text-to-Hevy Workout Builder")
    st.write("Paste raw text describing your workout below. Gemini will format it and push it straight into Hevy!")
    
    raw_workout_text = st.text_area(
        "Paste workout text:",
        placeholder="Chest Day:\nBench Press 3x8 @ 80kg\nIncline Dumbbell Press 3x10 @ 28kg\nCable Flyes 3x12 @ 15kg",
        height=150
    )
    
    if st.button("🚀 Convert & Push to Hevy"):
        if not raw_workout_text.strip():
            st.warning("Please paste some workout text first.")
        else:
            with st.spinner("Fetching Hevy exercise library..."):
                templates = fetch_exercise_templates()
                # Create simple lookup mapping name -> template_id
                template_summary = [{"id": t["id"], "title": t["title"]} for t in templates] if templates else []

            with st.spinner("Gemini is parsing your workout..."):
                prompt = f"""
                You are a Hevy API data builder. Convert the following workout text into a valid JSON object matching the Hevy POST /v1/workouts schema.
                
                Available exercise templates from user account:
                {json.dumps(template_summary[:50])}
                
                User input:
                {raw_workout_text}
                
                Respond ONLY with a raw JSON object (no markdown formatting, no code blocks) matching this layout:
                {{
                  "workout": {{
                    "title": "Workout Title",
                    "exercises": [
                      {{
                        "exercise_template_id": "template_id_string",
                        "sets": [
                          {{
                            "type": "normal",
                            "weight_kg": 80.0,
                            "reps": 8
                          }}
                        ]
                      }}
                    ]
                  }}
                }}
                """
                try:
                    response = gemini_client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=prompt
                    )
                    cleaned_json = response.text.replace("```json", "").replace("```", "").strip()
                    workout_data = json.loads(cleaned_json)
                    
                    st.success("Successfully parsed by Gemini!")
                    st.json(workout_data)
                    
                    # Push to Hevy API
                    with st.spinner("Pushing to Hevy..."):
                        res = post_workout_to_hevy(workout_data)
                        if res.status_code in [200, 201]:
                            st.balloons()
                            st.success("🎉 Workout successfully added to your Hevy account!")
                        else:
                            st.error(f"Hevy API Error ({res.status_code}): {res.text}")

                except Exception as e:
                    st.error(f"Processing error: {e}")

    st.divider()
    st.subheader("🏋️ Recent Workouts")
    if st.button("Sync Recent Workouts"):
        workouts = fetch_hevy_workouts()
        if workouts:
            for w in workouts:
                with st.expander(f"Workout: {w.get('title', 'Untitled')} ({w.get('start_time', '')[:10]})"):
                    st.write(f"**Duration:** {w.get('duration_seconds', 0) // 60} mins")
                    st.write(f"**Exercises Logged:** {len(w.get('exercises', []))}")

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
