import json
import os
import requests
import pandas as pd
import streamlit as st
from supabase import create_client, Client
from google import genai
from google.genai import types


# ==========================================
# 1. PAGE CONFIGURATION & PASSCODE AUTH
# ==========================================

st.set_page_config(
    page_title="Personal Command Center",
    page_icon="⚡",
    layout="wide",
)


def check_password():
    """Return True after the user enters the correct password."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if st.session_state.authenticated:
        return True

    st.title("🔒 Passcode Required")
    user_password = st.text_input("Enter Access Passcode:", type="password")

    if st.button("Unlock Dashboard"):
        expected_password = st.secrets.get("APP_PASSWORD")

        if expected_password and user_password == expected_password:
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
    api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing from Streamlit secrets.")
    return genai.Client(api_key=api_key)


gemini_client = init_gemini()


# ==========================================
# 3. CONSTANTS
# ==========================================

HEVY_BASE_URL = "https://api.hevyapp.com/v1"
HEVY_TIMEOUT = 30

# You can override this in Streamlit secrets with:
# GEMINI_MODEL = "gemini-2.5-flash"
PREFERRED_GEMINI_MODEL = st.secrets.get("GEMINI_MODEL", "gemini-3.6-flash")


# ==========================================
# 4. GEMINI HELPERS
# ==========================================

@st.cache_resource
def get_available_gemini_models():
    """Return models available to this Gemini API key."""
    try:
        models = list(gemini_client.models.list())
        available = []

        for model in models:
            name = getattr(model, "name", "")
            supported = getattr(model, "supported_actions", []) or []

            # API model names are often returned as models/xyz.
            clean_name = name.replace("models/", "", 1)

            if (
                "generateContent" in supported
                or not supported
            ):
                if clean_name:
                    available.append(clean_name)

        return available
    except Exception:
        return []


def choose_gemini_model():
    """
    Pick a working model without hard-coding a long list of obsolete models.
    The preferred model is tried first.
    """
    available = get_available_gemini_models()

    if not available:
        return PREFERRED_GEMINI_MODEL

    if PREFERRED_GEMINI_MODEL in available:
        return PREFERRED_GEMINI_MODEL

    # Prefer Flash models for this app because the tasks are structured parsing.
    flash_models = [
        m for m in available
        if "flash" in m.lower() and "embedding" not in m.lower()
    ]

    if flash_models:
        return sorted(flash_models)[0]

    return available[0]


def call_gemini_safe(prompt_text, json_mode=False):
    """Call Gemini using the currently available model."""
    model = choose_gemini_model()

    config = None

    if json_mode:
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
        )

    try:
        response = gemini_client.models.generate_content(
            model=model,
            contents=prompt_text,
            config=config,
        )
    except Exception as first_error:
        # If model discovery selected something unavailable, try the configured
        # model once more. This also gives a useful error if the key is bad.
        raise RuntimeError(
            f"Gemini request failed using {model}: {first_error}. "
            f"Set GEMINI_MODEL to a currently available model such as "
            f"gemini-3.6-flash if your API project supports it."
        ) from first_error

    text = getattr(response, "text", None)

    if not text:
        raise RuntimeError("Gemini returned an empty response.")

    return text


def clean_json_response(text):
    """Remove accidental Markdown code fences around JSON."""
    cleaned = (text or "").strip()

    if cleaned.startswith("```json"):
        cleaned = cleaned[7:].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:].strip()

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    return cleaned


def parse_json_response(text):
    """Parse Gemini JSON and give a useful error if it is invalid."""
    cleaned = clean_json_response(text)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        preview = cleaned[:1500]
        raise ValueError(
            "Gemini did not return valid JSON.\n\n"
            f"Response received:\n{preview}"
        ) from exc


# ==========================================
# 5. HEVY API HELPERS
# ==========================================

def get_hevy_headers():
    api_key = st.secrets.get("HEVY_API_KEY")

    if not api_key:
        raise RuntimeError("HEVY_API_KEY is missing from Streamlit secrets.")

    return {
        "api-key": api_key,
        "accept": "application/json",
        "Content-Type": "application/json",
    }


def hevy_request(method, endpoint, **kwargs):
    """Centralised Hevy request handler with timeout and useful errors."""
    url = f"{HEVY_BASE_URL}/{endpoint.lstrip('/')}"

    response = requests.request(
        method=method,
        url=url,
        headers=get_hevy_headers(),
        timeout=HEVY_TIMEOUT,
        **kwargs,
    )

    if not response.ok:
        raise RuntimeError(
            f"Hevy API error {response.status_code} for {method} {url}\n"
            f"{response.text[:2000]}"
        )

    return response


def fetch_hevy_workouts():
    """Fetch the five most recent workouts."""
    try:
        response = hevy_request(
            "GET",
            "/workouts?page=1&pageSize=5",
        )
        data = response.json()
        return data.get("workouts", [])
    except Exception as exc:
        st.error(f"Error fetching Hevy workouts: {exc}")
        return []


def fetch_exercise_templates():
    """Fetch exercise templates from the user's Hevy account."""
    try:
        response = hevy_request(
            "GET",
            "/exercise_templates?page=1&pageSize=100",
        )
        data = response.json()
        return data.get("exercise_templates", [])
    except Exception as exc:
        st.error(f"Error fetching Hevy exercise templates: {exc}")
        return []


def build_template_lookup(templates):
    """Create case-insensitive title -> template data lookup."""
    lookup = {}

    for template in templates:
        title = str(template.get("title", "")).strip()
        template_id = template.get("id")

        if title and template_id:
            lookup[title.lower()] = template

    return lookup


def validate_exercise_ids(payload, valid_ids):
    """
    Make sure Gemini only sends exercise template IDs that actually exist
    in the user's Hevy exercise library.
    """
    invalid = []

    if "routine" in payload:
        exercises = payload["routine"].get("exercises", [])
    elif "workout" in payload:
        exercises = payload["workout"].get("exercises", [])
    else:
        raise ValueError("Gemini response is missing 'routine' or 'workout'.")

    for exercise in exercises:
        exercise_id = exercise.get("exercise_template_id")

        if not exercise_id:
            invalid.append("(missing exercise_template_id)")
        elif str(exercise_id) not in valid_ids:
            invalid.append(str(exercise_id))

    if invalid:
        raise ValueError(
            "Gemini returned exercise template IDs that are not in your "
            f"Hevy library: {', '.join(invalid)}"
        )


def validate_sets(payload):
    """Basic validation before sending data to Hevy."""
    container = payload.get("routine") or payload.get("workout")

    if not isinstance(container, dict):
        raise ValueError("Payload must contain a routine or workout object.")

    exercises = container.get("exercises")

    if not isinstance(exercises, list) or not exercises:
        raise ValueError("No exercises were found in the AI response.")

    for exercise in exercises:
        if not exercise.get("exercise_template_id"):
            raise ValueError("An exercise is missing exercise_template_id.")

        sets = exercise.get("sets", [])

        if not isinstance(sets, list) or not sets:
            raise ValueError(
                f"Exercise '{exercise.get('title', 'Unknown')}' has no sets."
            )

        for workout_set in sets:
            if not isinstance(workout_set, dict):
                raise ValueError("Invalid set format returned by Gemini.")

            if "type" not in workout_set:
                workout_set["type"] = "normal"

            # Hevy expects a numeric weight/reps value when supplied.
            if "weight_kg" in workout_set and workout_set["weight_kg"] is not None:
                workout_set["weight_kg"] = float(workout_set["weight_kg"])

            if "reps" in workout_set and workout_set["reps"] is not None:
                workout_set["reps"] = int(workout_set["reps"])


def post_routine(routine_payload):
    """Create a Hevy routine."""
    return hevy_request(
        "POST",
        "/routines",
        json=routine_payload,
    )


def post_workout(workout_payload):
    """Create a completed Hevy workout."""
    return hevy_request(
        "POST",
        "/workouts",
        json=workout_payload,
    )


# ==========================================
# 6. DASHBOARD INTERFACE
# ==========================================

st.title("⚡ Personal Command Center")
st.caption("Welcome back! Your OS is live and synced.")

tab_overview, tab_fitness, tab_ai = st.tabs(
    ["📊 Overview", "🏋️ Hevy Manager", "🤖 AI Assistant"]
)


# ==========================================
# TAB 1: OVERVIEW
# ==========================================

with tab_overview:
    st.subheader("System Status")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(label="Database Status", value="Connected 🟢")

    with col2:
        st.metric(label="Domain", value="67whatever.au")

    with col3:
        st.metric(label="AI Model", value=choose_gemini_model())

    st.divider()

    st.info(
        "Gemini is used for workout text parsing. Hevy is used as the workout "
        "and routine database."
    )


# ==========================================
# TAB 2: HEVY MANAGER
# ==========================================

with tab_fitness:
    st.subheader("🏋️ Hevy Manager")

    mode = st.radio(
        "Choose Action:",
        ["Save Program as Hevy Routines", "Log Today's Workout"],
    )

    # --------------------------------------
    # MODE 1: BUILD ROUTINE TEMPLATES
    # --------------------------------------

    if mode == "Save Program as Hevy Routines":
        st.write(
            "Paste your raw multi-day program text below. Gemini will identify "
            "unique workout routines and prepare them for Hevy."
        )

        program_text = st.text_area(
            "Paste Full Program Text:",
            height=250,
        )

        if st.button("🚀 Parse & Create Hevy Routines"):
            if not program_text.strip():
                st.warning("Please paste program text first.")
            else:
                try:
                    with st.spinner("Fetching Hevy exercise library..."):
                        templates = fetch_exercise_templates()

                    if not templates:
                        st.error(
                            "No Hevy exercise templates were returned. "
                            "Check your HEVY_API_KEY and Hevy Pro/API access."
                        )
                        st.stop()

                    template_summary = [
                        {
                            "id": t.get("id"),
                            "title": t.get("title"),
                        }
                        for t in templates
                        if t.get("id") and t.get("title")
                    ]

                    valid_ids = {
                        str(t["id"]) for t in template_summary
                    }

                    prompt = f"""
You are an expert Hevy workout-program parser.

Extract the distinct, unique workout routines from the program below.

IMPORTANT:
- Do NOT create duplicate routines for repeated days.
- Match every exercise to one of the supplied Hevy exercise templates.
- Use the exact exercise_template_id supplied in the exercise library.
- Never invent an exercise_template_id.
- If an exercise cannot be confidently matched, do not silently invent an ID.
- Preserve the user's exercise order.
- Preserve sets, reps, weights and notes when supplied.
- If a weight is not specified, use 0.
- Use "normal" for normal sets.
- Return ONLY valid JSON.

AVAILABLE HEVY EXERCISE TEMPLATES:
{json.dumps(template_summary, indent=2)}

PROGRAM TEXT:
{program_text}

Required JSON format:
[
  {{
    "routine": {{
      "title": "Routine Title",
      "notes": "Rest and progression instructions",
      "exercises": [
        {{
          "exercise_template_id": "REAL_TEMPLATE_ID",
          "notes": "Exercise notes",
          "sets": [
            {{
              "type": "normal",
              "weight_kg": 0,
              "reps": 5
            }}
          ]
        }}
      ]
    }}
  }}
]
"""

                    with st.spinner("Gemini is parsing the program..."):
                        raw_response = call_gemini_safe(
                            prompt,
                            json_mode=True,
                        )

                    routines_list = parse_json_response(raw_response)

                    if not isinstance(routines_list, list):
                        raise ValueError(
                            "Gemini returned something other than a JSON array."
                        )

                    st.write("### Parsed Routines Preview")
                    st.json(routines_list)

                    # Validate everything before creating anything in Hevy.
                    for routine_item in routines_list:
                        if not isinstance(routine_item, dict):
                            raise ValueError("Invalid routine object returned.")

                        validate_exercise_ids(routine_item, valid_ids)
                        validate_sets(routine_item)

                    if not routines_list:
                        raise ValueError("No unique routines were found.")

                    if st.button(
                        "✅ Confirm & Send These Routines to Hevy",
                        key="confirm_routines",
                    ):
                        success_count = 0

                        with st.spinner("Pushing routines to Hevy..."):
                            for routine_item in routines_list:
                                response = post_routine(routine_item)

                                if response.status_code in (200, 201):
                                    success_count += 1

                        if success_count:
                            st.balloons()
                            st.success(
                                f"🎉 Successfully created {success_count} "
                                "routine(s) in Hevy!"
                            )

                except Exception as exc:
                    st.error(f"Error processing program: {exc}")

    # --------------------------------------
    # MODE 2: LOG DAILY COMPLETED WORKOUT
    # --------------------------------------

    elif mode == "Log Today's Workout":
        st.write(
            "Paste what you actually completed today. Gemini will match the "
            "exercises and prepare the completed workout for Hevy."
        )

        raw_workout_text = st.text_area(
            "Paste today's log text:",
            placeholder=(
                "Day 1 Total Body A:\n"
                "Squats 100kg 5,5,5\n"
                "Bench Press 80kg 5,5,5\n"
                "Chin ups 8,8,8"
            ),
            height=150,
        )

        if st.button("🚀 Parse Workout"):
            if not raw_workout_text.strip():
                st.warning("Please paste some workout text first.")
            else:
                try:
                    with st.spinner("Fetching Hevy exercise library..."):
                        templates = fetch_exercise_templates()

                    if not templates:
                        st.error("No Hevy exercise templates were returned.")
                        st.stop()

                    template_summary = [
                        {
                            "id": t.get("id"),
                            "title": t.get("title"),
                        }
                        for t in templates
                        if t.get("id") and t.get("title")
                    ]

                    valid_ids = {
                        str(t["id"]) for t in template_summary
                    }

                    prompt = f"""
Convert this completed workout into a valid Hevy POST /v1/workouts payload.

IMPORTANT:
- Match exercises ONLY to the supplied Hevy exercise templates.
- Use the exact exercise_template_id from the supplied list.
- Never invent IDs.
- Preserve the exercise order.
- Preserve weights and reps exactly where supplied.
- Use weight_kg as a number.
- Use reps as an integer.
- Use "normal" for normal sets.
- Return ONLY valid JSON.

AVAILABLE HEVY EXERCISE TEMPLATES:
{json.dumps(template_summary, indent=2)}

USER WORKOUT:
{raw_workout_text}

Required JSON:
{{
  "workout": {{
    "title": "Workout Title",
    "exercises": [
      {{
        "exercise_template_id": "REAL_TEMPLATE_ID",
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

                    with st.spinner("Gemini is parsing your workout..."):
                        raw_response = call_gemini_safe(
                            prompt,
                            json_mode=True,
                        )

                    workout_data = parse_json_response(raw_response)

                    validate_exercise_ids(workout_data, valid_ids)
                    validate_sets(workout_data)

                    st.write("### Workout Preview")
                    st.json(workout_data)

                    if st.button(
                        "✅ Confirm & Log This Workout to Hevy",
                        key="confirm_workout",
                    ):
                        with st.spinner("Sending workout to Hevy..."):
                            response = post_workout(workout_data)

                        if response.status_code in (200, 201):
                            st.balloons()
                            st.success("🎉 Workout logged to Hevy!")

                except Exception as exc:
                    st.error(f"Error logging workout: {exc}")

    # --------------------------------------
    # RECENT HEVY HISTORY
    # --------------------------------------

    st.divider()
    st.subheader("📋 Recent Hevy History")

    if st.button("Sync Recent History"):
        workouts = fetch_hevy_workouts()

        if workouts:
            for workout in workouts:
                title = workout.get("title", "Untitled")
                date_str = str(workout.get("start_time", ""))[:10]

                duration_seconds = workout.get("duration_seconds", 0) or 0
                duration_mins = int(duration_seconds) // 60

                exercise_count = len(
                    workout.get("exercises", []) or []
                )

                with st.expander(
                    f"{title} ({date_str})"
                ):
                    st.write(f"**Duration:** {duration_mins} mins")
                    st.write(
                        f"**Exercises Logged:** {exercise_count}"
                    )
        else:
            st.info("No recent workouts were returned by Hevy.")


# ==========================================
# TAB 3: AI ASSISTANT
# ==========================================

with tab_ai:
    st.subheader("🤖 Gemini Assistant")

    user_prompt = st.text_area(
        "Ask your personal OS anything:",
        placeholder=(
            "Summarize my day or suggest a training tweak..."
        ),
    )

    if st.button("Ask Gemini"):
        if not user_prompt.strip():
            st.warning("Please enter a question.")
        else:
            with st.spinner("Thinking..."):
                try:
                    response_text = call_gemini_safe(user_prompt)
                    st.markdown("### Response:")
                    st.write(response_text)
                except Exception as exc:
                    st.error(f"Gemini API Error: {exc}")
