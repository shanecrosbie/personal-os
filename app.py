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

def clean_json_response(text):
    """Safely remove markdown code blocks from AI response."""
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()

# ==========================================
# 4. DASHBOARD INTERFACE
# ==========================================
st.title("⚡ Personal Command Center")
st.caption("Welcome back! Your OS is live and synced.")

tab_overview, tab_fitness, tab_ai = st.tabs(["📊 Overview", "🏋️ Hevy Manager", "🤖 AI Assistant"])

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

# --- TAB 2: HEVY MANAGER ---
with tab_fitness:
    st.subheader("🏋️ Hevy Manager")
    mode = st.radio("Choose Action:", ["Save Program as Hevy Routines", "Log Today's Workout"])
    
    # MODE 1: BUILD ROUTINE TEMPLATES
    if mode == "Save Program as Hevy Routines":
        st.write("Paste your raw multi-day program text below. Gemini will parse out unique workout templates and save them directly into Hevy as reusable Routines.")
        program_text = st.text_area("Paste Full Program Text:", height=250)
        
        if st.button("🚀 Parse & Create Hevy Routines"):
            if not program_text.strip():
                st.warning("Please paste program text first.")
            else:
                with st.spinner("Fetching Hevy exercise library..."):
                    templates = fetch_exercise_templates()
                    template_summary = [{"id": t["id"], "title": t["title"]} for t in templates] if templates else []

                with st.spinner("Gemini is parsing unique workout templates..."):
                    prompt = f"""
                    You are a Hevy API expert. Extract the distinct, unique workout split routines from this multi-week program text.
                    For example, identify unique sessions like 'Old School Iron - Total Body A', 'Old School Iron - Abs & Correctives', 'Total Body B', etc. Do not create duplicates for repeated days.
                    
                    Available exercise templates from user account:
                    {json.dumps(template_summary[:80])}
                    
                    Program Text:
                    {program_text}
                    
                    Respond ONLY with a valid JSON array of routine objects matching this exact structure:
                    [
                      {{
                        "routine": {{
                          "title": "Routine Title",
                          "notes": "Rest and progression instructions",
                          "exercises": [
                            {{
                              "exercise_template_id": "template_id_string",
                              "notes": "3 x 5 @ 75-80% 1RM",
                              "sets": [
                                {{"type": "normal", "weight_kg": 0, "reps": 5}}
                              ]
                            }}
                          ]
                        }}
                      }}
                    ]
                    """
                    try:
                        response = gemini_client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=prompt
                        )
                        cleaned_json = clean_json_response(response.text)
                        routines_list = json.loads(cleaned_json)
                        
                        st.write("### Parsed Routines Preview:")
                        st.json(routines_list)
                        
                        api_key = st.secrets.get("HEVY_API_KEY")
                        headers = {"api-key": api_key, "accept": "application/json", "Content-Type": "application/json"}
                        
                        success_count = 0
                        with st.spinner("Pushing routine templates to Hevy..."):
                            for r_item in routines_list:
                                res = requests.post("[https://api.hevyapp.com/v1/routines](https://api.hevyapp.com/v1/routines)", headers=headers, json=r_item)
                                if res.status_code in [200, 201]:
                                    success_count += 1
                                else:
                                    st.warning(f"Failed to post {r_item.get('routine', {}).get('title')}: {res.text}")
                        
                        if success_count > 0:
                            st.balloons()
                            st.success(f"🎉 Successfully created {success_count} Routine Templates in Hevy!")

                    except Exception as e:
                        st.error(f"Error processing program: {e}")

    # MODE 2: LOG DAILY COMPLETED WORKOUT
    elif mode == "Log Today's Workout":
        st.write("Paste raw text describing what you lifted today. Gemini will match your exercises and post the completed workout to Hevy.")
        raw_workout_text = st.text_area(
            "Paste today's log text:",
            placeholder="Day 1 Total Body A:\nSquats 100kg 5,5,5\nBench Press 80kg 5,5,5\nChin ups 8,8,8",
            height=150
        )
        
        if st.button("🚀 Push Workout to Hevy"):
            if not raw_workout_text.strip():
                st.warning("Please paste some workout text first.")
            else:
                with st.spinner("Fetching Hevy exercise library..."):
                    templates = fetch_exercise_templates()
                    template_summary = [{"id": t["id"], "title": t["title"]} for t in templates] if templates else []

                with st.spinner("Gemini is parsing your daily log..."):
                    prompt = f"""
                    Convert the following completed workout notes into a valid JSON object matching the Hevy POST /v1/workouts schema.
                    
                    Available exercise templates:
                    {json.dumps(template_summary[:80])}
                    
                    User input:
                    {raw_workout_text}
                    
                    Respond ONLY with a raw JSON object:
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
                        cleaned_json = clean_json_response(response.text)
                        workout_data = json.loads(cleaned_json)
                        
                        api_key = st.secrets.get("HEVY_API_KEY")
                        headers = {"api-key": api_key, "accept": "application/json", "Content-Type": "application/json"}
                        
                        res = requests.post("[https://api.hevyapp.com/v1/workouts](https://api.hevyapp.com/v1/workouts)", headers=headers, json=workout_data)
                        if res.status_code in [200, 201]:
                            st.balloons()
                            st.success("🎉 Workout logged to Hevy!")
                        else:
                            st.error(f"Hevy API Error ({res.status_code}): {res.text}")

                    except Exception as e:
                        st.error(f"Error logging workout: {e}")

    st.divider()
    st.subheader("📋 Recent Hevy History")
    if st.button("Sync Recent History"):
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
