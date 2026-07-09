import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from streamlit_calendar import calendar
import json
import re
import html
from pathlib import Path
from datetime import date, datetime

DATA_DIR = Path(__file__).parent / "data" / "clients"
DATA_DIR.mkdir(parents=True, exist_ok=True)

st.set_page_config(page_title="HotshotHunterCoach Dashboard", layout="wide", page_icon="\U0001F3D4")


# ---------- palette (dataviz skill, dark-mode validated set) ----------

CHROME = {
    "surface": "#1a1a19",
    "page": "#0d0d0d",
    "text_primary": "#ffffff",
    "text_secondary": "#c3c2b7",
    "muted": "#898781",
    "grid": "#2c2c2a",
    "baseline": "#383835",
}

METRIC_COLOR = {
    "weight": "#3987e5",
    "body_fat": "#199e70",
    "strength": "#c98500",
    "vo2max": "#008300",
    "muscle": "#9085e9",
    "waist": "#d55181",
    "other": "#d95926",
}

STATUS_COLOR = {"good": "#0ca30c", "warning": "#fab219", "critical": "#d03b3b"}

BODY_METRICS = [
    ("weight_lb", "Weight (lb)", METRIC_COLOR["weight"]),
    ("body_fat_pct", "Body Fat %", METRIC_COLOR["body_fat"]),
    ("vo2_max", "VO2 Max (ml/kg/min)", METRIC_COLOR["vo2max"]),
    ("skeletal_muscle_lb", "Skeletal Muscle (lb)", METRIC_COLOR["muscle"]),
    ("waist_in", "Waist (in)", METRIC_COLOR["waist"]),
    ("bmi", "BMI", METRIC_COLOR["other"]),
]

CAL_OPTIONS = {
    "initialView": "dayGridMonth",
    "height": 600,
    "headerToolbar": {"left": "prev,next today", "center": "title", "right": ""},
    "editable": False,
}

CAL_CSS = """
.fc { background-color: #1a1a19; color: #c3c2b7; }
.fc-theme-standard td, .fc-theme-standard th { border-color: #2c2c2a; }
.fc-col-header-cell-cushion { color: #898781; }
.fc-daygrid-day-number { color: #c3c2b7; }
.fc-daygrid-day.fc-day-today { background-color: #2c2c2a; }
.fc-toolbar-title { color: #ffffff; }
.fc-button-primary { background-color: #c98500 !important; border-color: #c98500 !important; }
.fc-button-primary:hover { background-color: #d95926 !important; border-color: #d95926 !important; }
.fc-event { border: none; }
"""

EXERCISE_LIBRARY_PATH = Path(__file__).parent / "data" / "exercise_library.json"

BLOCK_SPEC = [("Block 1", ["1A", "1B", "1C"]), ("Block 2", ["2A", "2B"])]
FINISHER_SLOTS = ["Finisher A", "Finisher B"]


# ---------- storage ----------

def list_clients():
    clients = []
    for f in sorted(DATA_DIR.glob("*.json")):
        clients.append(json.loads(f.read_text()))
    return clients


def load_client(client_id):
    path = DATA_DIR / f"{client_id}.json"
    return json.loads(path.read_text())


def save_client(client):
    path = DATA_DIR / f"{client['id']}.json"
    path.write_text(json.dumps(client, indent=2))


def new_client_id(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    existing = {f.stem for f in DATA_DIR.glob("*.json")}
    candidate = slug
    n = 2
    while candidate in existing:
        candidate = f"{slug}-{n}"
        n += 1
    return candidate


def epley_1rm(load_lb, reps):
    if reps <= 1:
        return load_lb
    return round(load_lb * (1 + reps / 30), 1)


def days_since(date_str):
    if not date_str:
        return None
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (date.today() - d).days


def status_flag(client):
    last_dates = [e["date"] for e in client.get("checkins", [])] + \
                 [e["date"] for e in client.get("lift_entries", [])]
    if not last_dates:
        return "New", "\U0001F7E2"
    most_recent = max(last_dates)
    gap = days_since(most_recent)
    if gap is None:
        return "Unknown", "⚪"
    if gap <= 7:
        return f"Active ({gap}d ago)", "\U0001F7E2"
    elif gap <= 14:
        return f"Watch ({gap}d ago)", "\U0001F7E1"
    else:
        return f"At risk ({gap}d ago)", "\U0001F534"


def build_body_df(client):
    rows = []
    b = client.get("baseline", {})
    if b.get("weight_lb") or b.get("waist_in"):
        rows.append({"date": b.get("date"), "weight_lb": b.get("weight_lb"), "waist_in": b.get("waist_in")})
    for c in client.get("checkins", []):
        rows.append(c)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    return df


def load_exercise_library():
    return json.loads(EXERCISE_LIBRARY_PATH.read_text())


def exercise_options():
    lib = load_exercise_library()
    return [""] + sorted(e["name"] for e in lib)


def last_performance(client, exercise_name, before_date=None):
    if not exercise_name:
        return None
    entries = [e for e in client.get("lift_entries", [])
               if e["exercise"].strip().lower() == exercise_name.strip().lower()]
    if before_date:
        entries = [e for e in entries if e["date"] < str(before_date)]
    if not entries:
        return None
    return max(entries, key=lambda e: e["date"])


def empty_slot(label):
    return {"slot": label, "exercise": "", "sets": 0, "reps": "", "weight": "", "rest_sec": 0}


def get_workout(client, key_date):
    w = client.get("workouts", {}).get(key_date)
    if w:
        return w
    return {
        "warmup": {"duration_min": 10, "notes": ""},
        "blocks": [{"label": label, "slots": [empty_slot(s) for s in slots]} for label, slots in BLOCK_SPEC],
        "finisher": {"slots": [empty_slot(s) for s in FINISHER_SLOTS]},
        "status": "planned",
        "day_notes": "",
    }


def workout_exercise_count(workout):
    count = sum(1 for b in workout.get("blocks", []) for s in b.get("slots", []) if s.get("exercise"))
    count += sum(1 for s in workout.get("finisher", {}).get("slots", []) if s.get("exercise"))
    return count


def build_calendar_events(client):
    events = []
    for d, w in client.get("workouts", {}).items():
        wstatus = w.get("status", "planned")
        color = {
            "planned": METRIC_COLOR["weight"],
            "completed": STATUS_COLOR["good"],
            "missed": STATUS_COLOR["critical"],
        }.get(wstatus, METRIC_COLOR["weight"])
        n = workout_exercise_count(w)
        title = f"{n} exercise{'s' if n != 1 else ''}" if n else (w.get("day_notes") or "Workout")
        events.append({"title": title, "start": d, "end": d, "allDay": True, "color": color})
    return events


# ---------- charts ----------

def interactive_line_chart(df, x_col, y_col, color, y_label):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df[x_col], y=df[y_col],
        mode="lines+markers",
        line=dict(color=color, width=2),
        marker=dict(size=8, color=color, line=dict(width=2, color=CHROME["surface"])),
        hovertemplate="%{x|%b %d, %Y}<br><b>%{y}</b> " + y_label + "<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor=CHROME["surface"],
        plot_bgcolor=CHROME["surface"],
        font=dict(color=CHROME["text_secondary"], family="system-ui, -apple-system, Segoe UI, sans-serif", size=13),
        xaxis=dict(gridcolor=CHROME["grid"], linecolor=CHROME["baseline"], tickfont=dict(color=CHROME["muted"])),
        yaxis=dict(title=y_label, gridcolor=CHROME["grid"], linecolor=CHROME["baseline"], tickfont=dict(color=CHROME["muted"])),
        margin=dict(l=10, r=10, t=30, b=10),
        height=320,
        hoverlabel=dict(bgcolor=CHROME["surface"], font_color=CHROME["text_primary"], bordercolor=color),
        showlegend=False,
    )
    return fig


def render_body_charts(client):
    df = build_body_df(client)
    if df.empty:
        st.info("No check-ins logged yet.")
        return

    available = [(col, label, color) for col, label, color in BODY_METRICS
                 if col in df.columns and df[col].notna().any()]
    if not available:
        st.info("No metrics logged yet.")
        return

    cols = st.columns(2)
    for i, (col, label, color) in enumerate(available):
        mdf = df[["date", col]].dropna()
        with cols[i % 2]:
            latest = mdf.iloc[-1][col]
            st.metric(label, f"{latest:g}")
            fig = interactive_line_chart(mdf, "date", col, color, label)
            st.plotly_chart(fig, use_container_width=True, key=f"chart_{client['id']}_{col}")

    with st.expander("Raw check-in data"):
        st.dataframe(df.sort_values("date", ascending=False), use_container_width=True, hide_index=True)


# ---------- workout calendar + form ----------

def render_slot_editor(client, prefix, existing_slots, slot_labels, options):
    rows = []
    for label in slot_labels:
        existing = next((s for s in existing_slots if s.get("slot") == label), empty_slot(label))
        st.markdown(f"**{label}**")
        c1, c2, c3, c4, c5 = st.columns([3, 1, 1, 1.3, 1])
        cur = existing.get("exercise", "")
        idx = options.index(cur) if cur in options else 0
        exercise = c1.selectbox("Exercise", options, index=idx, key=f"{prefix}_{label}_ex")
        sets = c2.number_input("Sets", min_value=0, step=1, value=int(existing.get("sets") or 0),
                                key=f"{prefix}_{label}_sets")
        reps = c3.text_input("Reps", value=existing.get("reps", ""), key=f"{prefix}_{label}_reps", placeholder="8-10")
        weight = c4.text_input("Weight", value=existing.get("weight", ""), key=f"{prefix}_{label}_weight",
                                placeholder="lb or RPE")
        rest_sec = c5.number_input("Rest (sec)", min_value=0, step=15, value=int(existing.get("rest_sec") or 0),
                                    key=f"{prefix}_{label}_rest")
        if exercise:
            last = last_performance(client, exercise)
            if last:
                st.caption(f"Last time: {last['load_lb']:g} lb × {last['reps']} (on {last['date']})")
            else:
                st.caption("No prior log for this exercise yet.")
        rows.append({"slot": label, "exercise": exercise, "sets": sets, "reps": reps,
                     "weight": weight, "rest_sec": rest_sec})
    return rows


def render_workout_editor(client, target_date):
    key_date = str(target_date)
    workout = get_workout(client, key_date)
    st.markdown(f"### {target_date.strftime('%A, %b %d, %Y')}")

    options = exercise_options()

    st.subheader("Warmup / Stretch")
    c1, c2 = st.columns([1, 3])
    warmup_min = c1.number_input("Duration (min)", min_value=0, step=5,
                                  value=int(workout.get("warmup", {}).get("duration_min") or 10),
                                  key=f"warmup_min_{client['id']}_{key_date}")
    warmup_notes = c2.text_input("Notes", value=workout.get("warmup", {}).get("notes", ""),
                                  key=f"warmup_notes_{client['id']}_{key_date}")

    new_blocks = []
    for label, slot_labels in BLOCK_SPEC:
        st.subheader(label)
        existing_block = next((b for b in workout.get("blocks", []) if b.get("label") == label), {"slots": []})
        block_rows = render_slot_editor(client, f"blk_{client['id']}_{key_date}_{label}",
                                         existing_block.get("slots", []), slot_labels, options)
        new_blocks.append({"label": label, "slots": block_rows})

    st.subheader("FINISHER")
    finisher_rows = render_slot_editor(client, f"fin_{client['id']}_{key_date}",
                                        workout.get("finisher", {}).get("slots", []), FINISHER_SLOTS, options)

    wstatus = st.selectbox("Status", ["planned", "completed", "missed"],
                            index=["planned", "completed", "missed"].index(workout.get("status", "planned")),
                            key=f"wstatus_{client['id']}_{key_date}")
    day_notes = st.text_area("Day notes", value=workout.get("day_notes", ""), key=f"daynotes_{client['id']}_{key_date}")

    all_slots = [s for b in new_blocks for s in b["slots"]] + finisher_rows
    filled_slots = [s for s in all_slots if s["exercise"]]

    actual_inputs = {}
    if wstatus == "completed" and filled_slots:
        st.info("Log what was actually performed — this feeds the strength chart and next time's reference.")
        for s in filled_slots:
            c1, c2 = st.columns(2)
            aw = c1.number_input(f"Actual weight — {s['slot']} {s['exercise']}", min_value=0.0, step=2.5,
                                  key=f"actual_w_{client['id']}_{key_date}_{s['slot']}")
            ar = c2.number_input(f"Actual reps — {s['slot']} {s['exercise']}", min_value=0, step=1,
                                  key=f"actual_r_{client['id']}_{key_date}_{s['slot']}")
            actual_inputs[s["slot"]] = (s["exercise"], aw, ar)

    if st.button("Save workout", key=f"save_workout_{client['id']}_{key_date}"):
        client.setdefault("workouts", {})[key_date] = {
            "warmup": {"duration_min": warmup_min, "notes": warmup_notes},
            "blocks": new_blocks,
            "finisher": {"slots": finisher_rows},
            "status": wstatus,
            "day_notes": day_notes,
        }
        for slot_label, (ex_name, aw, ar) in actual_inputs.items():
            if aw and ar:
                client.setdefault("lift_entries", []).append({
                    "date": key_date,
                    "exercise": ex_name,
                    "load_lb": aw,
                    "reps": int(ar),
                    "rpe": None,
                    "est_1rm": epley_1rm(aw, int(ar)),
                })
        save_client(client)
        st.success("Workout saved.")
        st.rerun()


def tab_program_calendar(client):
    st.subheader("Program Calendar")
    st.caption("Click a day to add or edit that day's workout.")
    events = build_calendar_events(client)
    state_key = f"cal_selected_date_{client['id']}"
    cal_state = calendar(events=events, options=CAL_OPTIONS, custom_css=CAL_CSS,
                          callbacks=["dateClick"], key=f"trainer_cal_{client['id']}")
    if cal_state and cal_state.get("callback") == "dateClick":
        st.session_state[state_key] = cal_state["dateClick"]["date"][:10]

    selected_str = st.session_state.get(state_key, str(date.today()))
    selected = datetime.strptime(selected_str, "%Y-%m-%d").date()

    st.divider()
    render_workout_editor(client, selected)


def render_workout_readonly(client, workout):
    warmup = workout.get("warmup", {})
    if warmup.get("duration_min") or warmup.get("notes"):
        note = f" · {warmup['notes']}" if warmup.get("notes") else ""
        st.write(f"**Warmup / Stretch** — {warmup.get('duration_min', 0)} min{note}")

    for b in workout.get("blocks", []):
        filled = [s for s in b.get("slots", []) if s.get("exercise")]
        if not filled:
            continue
        st.write(f"**{b['label']}**")
        for s in filled:
            extra = f" · {s['weight']}" if s.get("weight") else ""
            rest = f" · rest {s['rest_sec']}s" if s.get("rest_sec") else ""
            st.write(f"- {s['slot']}) **{s['exercise']}** — {s.get('sets', '')} x {s.get('reps', '')}{extra}{rest}")
            last = last_performance(client, s["exercise"])
            if last:
                st.caption(f"Last time: {last['load_lb']:g} lb × {last['reps']}")

    fin_filled = [s for s in workout.get("finisher", {}).get("slots", []) if s.get("exercise")]
    if fin_filled:
        st.write("**FINISHER**")
        for s in fin_filled:
            extra = f" · {s['weight']}" if s.get("weight") else ""
            st.write(f"- **{s['exercise']}** — {s.get('sets', '')} x {s.get('reps', '')}{extra}")
            last = last_performance(client, s["exercise"])
            if last:
                st.caption(f"Last time: {last['load_lb']:g} lb × {last['reps']}")

    if workout.get("day_notes"):
        st.caption(workout["day_notes"])


def render_readonly_calendar(client):
    st.subheader("Program Calendar")
    events = build_calendar_events(client)
    state_key = f"client_cal_selected_{client['id']}"
    cal_state = calendar(events=events, options=CAL_OPTIONS, custom_css=CAL_CSS,
                          callbacks=["dateClick"], key=f"client_cal_{client['id']}")
    if cal_state and cal_state.get("callback") == "dateClick":
        st.session_state[state_key] = cal_state["dateClick"]["date"][:10]

    selected_str = st.session_state.get(state_key)
    if selected_str:
        workout = client.get("workouts", {}).get(selected_str)
        selected = datetime.strptime(selected_str, "%Y-%m-%d").date()
        st.markdown(f"**{selected.strftime('%A, %b %d, %Y')}**")
        if not workout or not workout_exercise_count(workout):
            st.caption("No workout planned for this day.")
        else:
            st.caption(f"Status: {workout.get('status', 'planned').title()}")
            render_workout_readonly(client, workout)


# ---------- trainer pages ----------

def page_roster():
    st.title("Roster")
    clients = list_clients()

    if not clients:
        st.info("No clients yet. Add your first client from the sidebar.")
        return

    rows = []
    for c in clients:
        label, emoji = status_flag(c)
        rows.append({
            "": emoji,
            "Name": c["name"],
            "Primary Goal": c.get("goals", {}).get("primary", ""),
            "Status": label,
            "Sessions Logged": len(c.get("lift_entries", [])),
            "Check-ins": len(c.get("checkins", [])),
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    names = {c["name"]: c["id"] for c in clients}
    picked = st.selectbox("Open a client profile", options=list(names.keys()))
    if st.button("Go to profile"):
        st.session_state["selected_client_id"] = names[picked]
        st.session_state["trainer_nav"] = "Client Workspace"
        st.rerun()


def page_add_client():
    st.title("Add New Client")
    with st.form("add_client_form", clear_on_submit=True):
        name = st.text_input("Name *")
        col1, col2 = st.columns(2)
        with col1:
            phone = st.text_input("Phone")
            start_date = st.date_input("Start date", value=date.today())
        with col2:
            email = st.text_input("Email")

        st.subheader("Goals")
        primary_goal = st.text_input("Primary goal (e.g. lose 20 lbs, deadlift 2x bodyweight)")
        goal_why = st.text_area("Why this matters to them (their words)")
        mantra = st.text_input("Mantra (shown at the top of their page)")

        st.subheader("Health flags")
        injuries = st.text_input("Injuries / limitations (comma separated)")

        st.subheader("Baseline")
        col3, col4 = st.columns(2)
        with col3:
            baseline_weight = st.number_input("Baseline weight (lb)", min_value=0.0, step=0.5)
        with col4:
            baseline_waist = st.number_input("Baseline waist (in, optional)", min_value=0.0, step=0.5)

        submitted = st.form_submit_button("Save Client")
        if submitted:
            if not name.strip():
                st.error("Name is required.")
            else:
                client_id = new_client_id(name)
                client = {
                    "id": client_id,
                    "name": name.strip(),
                    "start_date": str(start_date),
                    "contact": {"phone": phone, "email": email},
                    "goals": {"primary": primary_goal, "why": goal_why},
                    "mantra": mantra.strip(),
                    "health_flags": {
                        "injuries": [i.strip() for i in injuries.split(",") if i.strip()]
                    },
                    "baseline": {
                        "date": str(start_date),
                        "weight_lb": baseline_weight or None,
                        "waist_in": baseline_waist or None,
                    },
                    "checkins": [],
                    "lift_entries": [],
                    "milestones": [],
                    "workouts": {},
                    "status": "active",
                }
                save_client(client)
                st.success(f"Saved {name}.")
                st.session_state["selected_client_id"] = client_id
                st.session_state["trainer_nav"] = "Client Workspace"
                st.rerun()


def tab_overview(client):
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Goals")
        st.write(f"**Primary:** {client['goals'].get('primary') or '—'}")
        st.write(f"**Why:** {client['goals'].get('why') or '—'}")
        st.subheader("Mantra")
        st.write(f"“{client.get('mantra') or '—'}”")
        st.subheader("Contact")
        st.write(f"Phone: {client['contact'].get('phone') or '—'}")
        st.write(f"Email: {client['contact'].get('email') or '—'}")
    with col2:
        st.subheader("Health flags")
        injuries = client["health_flags"].get("injuries", [])
        st.write(", ".join(injuries) if injuries else "None on file")
        st.subheader("Baseline")
        b = client.get("baseline", {})
        st.write(f"Date: {b.get('date') or '—'}")
        st.write(f"Weight: {b.get('weight_lb') or '—'} lb")
        st.write(f"Waist: {b.get('waist_in') or '—'} in")

    with st.expander("Edit profile"):
        with st.form("edit_profile_form"):
            name = st.text_input("Name", value=client["name"])
            phone = st.text_input("Phone", value=client["contact"].get("phone", ""))
            email = st.text_input("Email", value=client["contact"].get("email", ""))
            primary_goal = st.text_input("Primary goal", value=client["goals"].get("primary", ""))
            goal_why = st.text_area("Why", value=client["goals"].get("why", ""))
            mantra = st.text_input("Mantra", value=client.get("mantra", ""))
            injuries_text = st.text_input("Injuries / limitations (comma separated)",
                                           value=", ".join(client["health_flags"].get("injuries", [])))
            status = st.selectbox("Status", options=["active", "paused", "ended"],
                                   index=["active", "paused", "ended"].index(client.get("status", "active")))
            if st.form_submit_button("Save changes"):
                client["name"] = name.strip()
                client["contact"] = {"phone": phone, "email": email}
                client["goals"] = {"primary": primary_goal, "why": goal_why}
                client["mantra"] = mantra.strip()
                client["health_flags"] = {"injuries": [i.strip() for i in injuries_text.split(",") if i.strip()]}
                client["status"] = status
                save_client(client)
                st.success("Updated.")
                st.rerun()


def tab_body_metrics(client):
    st.subheader("Log a body composition check-in")
    with st.form("checkin_form", clear_on_submit=True):
        c_date = st.date_input("Date", value=date.today(), key="checkin_date")
        c1, c2, c3 = st.columns(3)
        weight = c1.number_input("Weight (lb)", min_value=0.0, step=0.5)
        waist = c2.number_input("Waist (in)", min_value=0.0, step=0.5)
        vo2_max = c3.number_input("VO2 max (ml/kg/min)", min_value=0.0, step=0.1)

        st.caption("InBody scan fields (optional)")
        c4, c5, c6 = st.columns(3)
        body_fat_pct = c4.number_input("Body fat %", min_value=0.0, max_value=100.0, step=0.1)
        skeletal_muscle_lb = c5.number_input("Skeletal muscle mass (lb)", min_value=0.0, step=0.1)
        bmi = c6.number_input("BMI", min_value=0.0, step=0.1)
        c7, c8 = st.columns(2)
        visceral_fat_level = c7.number_input("Visceral fat level", min_value=0.0, step=1.0)
        body_water_pct = c8.number_input("Body water %", min_value=0.0, max_value=100.0, step=0.1)
        bmr = st.number_input("BMR (kcal/day)", min_value=0.0, step=10.0)

        notes = st.text_area("Notes (energy, sleep, wins, struggles)")
        if st.form_submit_button("Save check-in"):
            client.setdefault("checkins", []).append({
                "date": str(c_date),
                "weight_lb": weight or None,
                "waist_in": waist or None,
                "vo2_max": vo2_max or None,
                "body_fat_pct": body_fat_pct or None,
                "skeletal_muscle_lb": skeletal_muscle_lb or None,
                "bmi": bmi or None,
                "visceral_fat_level": visceral_fat_level or None,
                "body_water_pct": body_water_pct or None,
                "bmr": bmr or None,
                "notes": notes,
            })
            save_client(client)
            st.success("Check-in saved.")
            st.rerun()

    st.divider()
    render_body_charts(client)


def tab_strength(client):
    st.subheader("Log a lift")
    with st.form("lift_form", clear_on_submit=True):
        l_date = st.date_input("Date", value=date.today(), key="lift_date")
        exercise = st.text_input("Exercise (e.g. Back Squat, Bench Press)")
        col1, col2, col3 = st.columns(3)
        load_lb = col1.number_input("Load (lb)", min_value=0.0, step=2.5)
        reps = col2.number_input("Reps", min_value=1, step=1, value=5)
        rpe = col3.number_input("RPE (optional)", min_value=0.0, max_value=10.0, step=0.5)
        if st.form_submit_button("Save lift"):
            if not exercise.strip():
                st.error("Exercise name is required.")
            else:
                prior = [e for e in client.get("lift_entries", [])
                         if e["exercise"].strip().lower() == exercise.strip().lower()]
                prior_best = max((epley_1rm(e["load_lb"], e["reps"]) for e in prior), default=0)
                new_1rm = epley_1rm(load_lb, reps)

                client.setdefault("lift_entries", []).append({
                    "date": str(l_date),
                    "exercise": exercise.strip(),
                    "load_lb": load_lb,
                    "reps": int(reps),
                    "rpe": rpe or None,
                    "est_1rm": new_1rm,
                })
                save_client(client)

                if prior and new_1rm > prior_best:
                    st.balloons()
                    st.success(f"New PR! Estimated 1RM for {exercise}: {new_1rm} lb (was {prior_best} lb)")
                else:
                    st.success("Lift saved.")
                st.rerun()

    st.divider()
    entries = client.get("lift_entries", [])
    if not entries:
        st.info("No lifts logged yet.")
        return
    df = pd.DataFrame(entries)
    exercises = sorted(df["exercise"].unique())
    picked = st.selectbox("Exercise", options=exercises)
    edf = df[df["exercise"] == picked].copy()
    edf["date"] = pd.to_datetime(edf["date"])
    edf = edf.sort_values("date")

    st.metric("Best estimated 1RM", f"{edf['est_1rm'].max()} lb")
    fig = interactive_line_chart(edf, "date", "est_1rm", METRIC_COLOR["strength"], "Estimated 1RM (lb)")
    st.plotly_chart(fig, use_container_width=True, key=f"strength_chart_{client['id']}_{picked}")
    st.dataframe(edf[["date", "load_lb", "reps", "rpe", "est_1rm"]].sort_values("date", ascending=False),
                 use_container_width=True, hide_index=True)


def tab_milestones(client):
    milestones = client.get("milestones", [])
    with st.form("milestone_form", clear_on_submit=True):
        m_date = st.date_input("Date", value=date.today(), key="milestone_date")
        desc = st.text_input("Milestone (e.g. First session, 10-session streak, hit goal weight)")
        if st.form_submit_button("Add milestone"):
            if desc.strip():
                client.setdefault("milestones", []).append({"date": str(m_date), "description": desc.strip()})
                save_client(client)
                st.success("Milestone added.")
                st.rerun()

    st.divider()
    if not milestones:
        st.info("No milestones yet.")
    else:
        for m in sorted(milestones, key=lambda m: m["date"], reverse=True):
            st.write(f"**{m['date']}** — {m['description']}")


def page_trainer_client():
    st.title("Client Workspace")
    clients = list_clients()
    if not clients:
        st.info("No clients yet. Add one from the sidebar first.")
        return

    names = {c["name"]: c["id"] for c in clients}
    default_id = st.session_state.get("selected_client_id")
    default_name = next((n for n, i in names.items() if i == default_id), list(names.keys())[0])
    picked_name = st.selectbox("Client", options=list(names.keys()), index=list(names.keys()).index(default_name))
    client_id = names[picked_name]
    st.session_state["selected_client_id"] = client_id
    client = load_client(client_id)

    tabs = st.tabs(["Overview", "Body Metrics", "Strength", "Program Calendar", "Milestones"])
    with tabs[0]:
        tab_overview(client)
    with tabs[1]:
        tab_body_metrics(client)
    with tabs[2]:
        tab_strength(client)
    with tabs[3]:
        tab_program_calendar(client)
    with tabs[4]:
        tab_milestones(client)


# ---------- client-facing page ----------

def render_mantra_banner(client):
    mantra = client.get("mantra")
    if not mantra:
        return
    safe_name = html.escape(client["name"])
    safe_mantra = html.escape(mantra)
    st.markdown(f"""
<div style="background: linear-gradient(135deg, {METRIC_COLOR['strength']} 0%, {METRIC_COLOR['other']} 100%);
            border-radius: 14px; padding: 22px 28px; margin-bottom: 22px;">
  <div style="color: {CHROME['page']}; font-size: 0.75rem; font-weight: 700; letter-spacing: 0.08em;
              text-transform: uppercase; opacity: 0.7;">{safe_name}'s mantra</div>
  <div style="color: {CHROME['text_primary']}; font-size: 1.7rem; font-weight: 700; margin-top: 6px;">"{safe_mantra}"</div>
</div>
""", unsafe_allow_html=True)


def page_client_view():
    st.title("Client View")
    clients = list_clients()
    if not clients:
        st.info("No clients yet.")
        return
    names = {c["name"]: c["id"] for c in clients}
    picked_name = st.selectbox("Select client", options=list(names.keys()))
    client = load_client(names[picked_name])

    render_mantra_banner(client)

    col1, col2 = st.columns([2, 1])
    with col1:
        st.subheader(client["name"])
        st.write(f"**Goal:** {client['goals'].get('primary') or '—'}")
    with col2:
        b = client.get("baseline", {})
        df = build_body_df(client)
        if not df.empty and "weight_lb" in df.columns and df["weight_lb"].notna().any():
            current = df["weight_lb"].dropna().iloc[-1]
            start = b.get("weight_lb")
            delta = round(current - start, 1) if start else None
            st.metric("Weight", f"{current:g} lb", delta=f"{delta:+g} lb" if delta is not None else None,
                       delta_color="off")

    st.divider()
    render_body_charts(client)

    st.divider()
    render_readonly_calendar(client)

    st.divider()
    st.subheader("Milestones")
    milestones = client.get("milestones", [])
    if not milestones:
        st.caption("No milestones yet.")
    else:
        for m in sorted(milestones, key=lambda m: m["date"], reverse=True):
            st.write(f"**{m['date']}** — {m['description']}")


# ---------- nav ----------

st.sidebar.title("\U0001F3D4 HotshotHunterCoach")
mode = st.sidebar.radio("Mode", ["Trainer", "Client View"])

if mode == "Trainer":
    if "trainer_nav" not in st.session_state:
        st.session_state["trainer_nav"] = "Roster"
    trainer_nav = st.sidebar.radio(
        "Trainer Menu",
        ["Roster", "Add Client", "Client Workspace"],
        index=["Roster", "Add Client", "Client Workspace"].index(st.session_state["trainer_nav"]),
    )
    st.session_state["trainer_nav"] = trainer_nav

    if trainer_nav == "Roster":
        page_roster()
    elif trainer_nav == "Add Client":
        page_add_client()
    elif trainer_nav == "Client Workspace":
        page_trainer_client()
else:
    page_client_view()
