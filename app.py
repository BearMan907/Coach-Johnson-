import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from streamlit_calendar import calendar
import json
import re
import html
import string
import copy
from pathlib import Path
from datetime import date, datetime, timedelta

DATA_DIR = Path(__file__).parent / "data" / "clients"
DATA_DIR.mkdir(parents=True, exist_ok=True)

st.set_page_config(page_title="CoachJohnson Dashboard", layout="wide", page_icon="\U0001F3D4")


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
    # Pinned so the clicked grid cell and its serialized ISO date always agree —
    # without this, dateClick's date.toISOString() converts through the
    # browser's local timezone and can land on the wrong calendar day.
    "timeZone": "UTC",
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
TEMPLATES_PATH = Path(__file__).parent / "data" / "workout_templates.json"

DEFAULT_BLOCK_COUNT = 3
DEFAULT_SLOTS_PER_BLOCK = 2
DEFAULT_FINISHER_SLOTS = 2

TEMPLATE_CATEGORIES = [
    "Landmine/Kettlebell", "Hypertrophy", "Glute Focus", "Strength", "Conditioning", "Full Body", "Custom",
]


def slot_letter(idx):
    return string.ascii_uppercase[idx] if idx < 26 else str(idx + 1)


def slot_label_for(block_num, idx):
    # Number = exercise's position within the block, letter = which block —
    # matches Tyler's real programming notation (1A/2A in Block 1, then
    # 1B/2B in Block 2), not a running "1A,1B,1C" count within one block.
    return f"{idx + 1}{slot_letter(block_num - 1)}"


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


def client_options(clients):
    """(display_label, client_id) pairs. If two clients share a name, a plain
    name-keyed dict would silently collapse to one of them — disambiguate so
    every client stays selectable everywhere."""
    name_counts = {}
    for c in clients:
        name_counts[c["name"]] = name_counts.get(c["name"], 0) + 1
    options = []
    for c in clients:
        label = c["name"]
        if name_counts[c["name"]] > 1:
            label = f"{c['name']} ({c.get('start_date') or c['id']})"
        options.append((label, c["id"]))
    return options


def status_flag(client):
    last_dates = [e.get("date", "") for e in client.get("checkins", [])] + \
                 [e.get("date", "") for e in client.get("lift_entries", [])] + \
                 [d for d, w in client.get("workouts", {}).items() if w.get("status") == "completed"]
    last_dates = [d for d in last_dates if d]
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
    if not client or not exercise_name:
        return None
    entries = [e for e in client.get("lift_entries", [])
               if e.get("exercise", "").strip().lower() == exercise_name.strip().lower()]
    if before_date:
        entries = [e for e in entries if e.get("date", "") < str(before_date)]
    if not entries:
        return None
    return max(entries, key=lambda e: e.get("date", ""))


def empty_slot(label):
    return {"slot": label, "exercise": "", "sets": 0, "reps": "", "weight": "", "rest_sec": 0, "notes": ""}


def blank_program():
    return {
        "id": "",
        "name": "",
        "category": TEMPLATE_CATEGORIES[0],
        "warmup": {"duration_min": 10, "notes": ""},
        "blocks": [
            {"label": f"Block {i + 1}", "notes": "",
             "slots": [empty_slot(slot_label_for(i + 1, j)) for j in range(DEFAULT_SLOTS_PER_BLOCK)]}
            for i in range(DEFAULT_BLOCK_COUNT)
        ],
        "finisher": {"slots": [empty_slot(f"Finisher {slot_letter(j)}") for j in range(DEFAULT_FINISHER_SLOTS)]},
        "day_notes": "",
    }


def load_templates():
    if not TEMPLATES_PATH.exists():
        return []
    return json.loads(TEMPLATES_PATH.read_text())


def save_templates(templates):
    TEMPLATES_PATH.write_text(json.dumps(templates, indent=2))


def new_template_id(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "program"
    existing = {t["id"] for t in load_templates()}
    candidate = slug
    n = 2
    while candidate in existing:
        candidate = f"{slug}-{n}"
        n += 1
    return candidate


def normalize_workout(w):
    """Best-effort upgrade of a saved workout dict from any earlier schema
    version (e.g. the original flat "exercises" list, or a shape saved before
    a field like "notes" existed) into the current shape. Every place that
    reads a workout out of client["workouts"] must go through this — earlier
    schema versions were missing "warmup"/"blocks"/"finisher" entirely, which
    caused KeyError crashes (and calendar days silently showing as generic
    "Workout" instead of a real exercise count) once the current code assumed
    those keys always exist."""
    if not w:
        return None
    w = copy.deepcopy(w)
    old_exercises = w.pop("exercises", None)

    w.setdefault("warmup", {})
    w["warmup"].setdefault("duration_min", 0)
    w["warmup"].setdefault("notes", "")

    w.setdefault("blocks", [])
    for b in w["blocks"]:
        b.setdefault("label", "Block")
        b.setdefault("notes", "")
        b.setdefault("slots", [])
        for s in b["slots"]:
            s.setdefault("slot", "")
            s.setdefault("exercise", "")
            s.setdefault("sets", 0)
            s.setdefault("reps", "")
            s.setdefault("weight", "")
            s.setdefault("rest_sec", 0)
            s.setdefault("notes", "")

    w.setdefault("finisher", {})
    w["finisher"].setdefault("slots", [])
    for s in w["finisher"]["slots"]:
        s.setdefault("slot", "")
        s.setdefault("exercise", "")
        s.setdefault("sets", 0)
        s.setdefault("reps", "")
        s.setdefault("weight", "")
        s.setdefault("rest_sec", 0)
        s.setdefault("notes", "")

    w.setdefault("status", "planned")
    w.setdefault("day_notes", "")

    # migrate the original flat "exercises" schema into Block 1 if nothing
    # newer has already been saved over it
    if old_exercises and not w["blocks"]:
        slots = []
        for i, e in enumerate(old_exercises):
            slots.append({
                "slot": slot_label_for(1, i),
                "exercise": e.get("name", ""),
                "sets": e.get("sets", 0),
                "reps": e.get("reps", ""),
                "weight": e.get("load_note", ""),
                "rest_sec": 0,
                "notes": "",
            })
        w["blocks"] = [{"label": "Block 1", "slots": slots}]

    return w


def get_workout(client, key_date):
    w = client.get("workouts", {}).get(key_date)
    if w:
        return normalize_workout(w)
    return {
        "warmup": {"duration_min": 10, "notes": ""},
        "blocks": [
            {"label": f"Block {i + 1}", "notes": "",
             "slots": [empty_slot(slot_label_for(i + 1, j)) for j in range(DEFAULT_SLOTS_PER_BLOCK)]}
            for i in range(DEFAULT_BLOCK_COUNT)
        ],
        "finisher": {"slots": [empty_slot(f"Finisher {slot_letter(j)}") for j in range(DEFAULT_FINISHER_SLOTS)]},
        "status": "planned",
        "day_notes": "",
    }


def get_workout_state(client, key_date):
    """Mutable, session-scoped working copy of a day's workout so add/remove
    controls can edit block and slot lists in place across reruns without
    losing in-progress edits until the trainer explicitly saves."""
    state_key = f"workout_state_{client['id']}_{key_date}"
    if state_key not in st.session_state:
        st.session_state[state_key] = copy.deepcopy(get_workout(client, key_date))
    return st.session_state[state_key]


def workout_exercise_count(workout):
    count = sum(1 for b in workout.get("blocks", []) for s in b.get("slots", []) if s.get("exercise"))
    count += sum(1 for s in workout.get("finisher", {}).get("slots", []) if s.get("exercise"))
    return count


def extract_clicked_date(cal_state):
    """Clicking blank calendar space fires dateClick; clicking directly on an
    existing event pill fires eventClick instead (and dateClick never fires) —
    both need to resolve to the same selected date."""
    if not cal_state:
        return None
    cb = cal_state.get("callback")
    if cb == "dateClick":
        return cal_state["dateClick"]["date"][:10]
    if cb == "eventClick":
        return cal_state["eventClick"]["event"]["start"][:10]
    return None


def build_calendar_events(client):
    events = []
    for d, raw_w in client.get("workouts", {}).items():
        w = normalize_workout(raw_w)
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
            st.plotly_chart(fig, width='stretch', key=f"chart_{client['id']}_{col}")

    with st.expander("Raw check-in data"):
        st.dataframe(df.sort_values("date", ascending=False), width='stretch', hide_index=True)


# ---------- workout calendar + form ----------

def render_slot_row(client, options, prefix, slot):
    """Renders one exercise row, mutating `slot` in place. Returns True if
    this row's remove button was clicked."""
    st.caption(slot["slot"])
    c1, c2, c3, c4, c5, c6, c7 = st.columns([2.6, 0.8, 0.8, 1.1, 0.9, 1.8, 0.6])
    cur = slot.get("exercise", "")
    # A saved exercise name that predates the library (or was never in it)
    # must still show up, not silently blank out and get overwritten on save.
    row_options = options if (not cur or cur in options) else [cur] + options
    idx = row_options.index(cur) if cur in row_options else 0
    slot["exercise"] = c1.selectbox("Exercise", row_options, index=idx, key=f"{prefix}_ex")
    slot["sets"] = c2.number_input("Sets", min_value=0, step=1, value=int(slot.get("sets") or 0),
                                    key=f"{prefix}_sets")
    slot["reps"] = c3.text_input("Reps", value=slot.get("reps", ""), key=f"{prefix}_reps", placeholder="8-10")
    slot["weight"] = c4.text_input("Weight", value=slot.get("weight", ""), key=f"{prefix}_weight",
                                    placeholder="lb or RPE")
    slot["rest_sec"] = c5.number_input("Rest sec", min_value=0, step=15, value=int(slot.get("rest_sec") or 0),
                                        key=f"{prefix}_rest")
    slot["notes"] = c6.text_input("Notes", value=slot.get("notes", ""), key=f"{prefix}_notes", placeholder="cues")
    remove_clicked = c7.button("\U0001F5D1", key=f"{prefix}_del", help="Remove this exercise")
    if client and slot["exercise"]:
        last = last_performance(client, slot["exercise"])
        if last:
            st.caption(f"Last time: {last['load_lb']:g} lb × {last['reps']} (on {last['date']})")
        else:
            st.caption("No prior log for this exercise yet.")
    return remove_clicked


def render_slot_list(client, options, prefix, slots, add_label):
    """Renders every slot in `slots` (mutating in place), handles remove /
    add-exercise, and returns nothing — `slots` itself is the source of truth."""
    remove_idx = None
    for j, slot in enumerate(slots):
        if render_slot_row(client, options, f"{prefix}_{j}", slot):
            remove_idx = j
    if remove_idx is not None:
        slots.pop(remove_idx)
        st.rerun()
    if st.button(add_label, key=f"{prefix}_add"):
        slots.append(empty_slot(""))
        st.rerun()


def render_program_blocks_editor(state, base_id, client=None):
    """Warmup + dynamic Blocks + FINISHER editing UI, mutating `state` in
    place. Shared between per-client-day editing (client passed, so "last
    time" lookups work) and standalone Program Library template editing
    (client=None, no per-client history to show)."""
    options = exercise_options()

    st.subheader("Warmup / Stretch")
    c1, c2 = st.columns([1, 3])
    state["warmup"]["duration_min"] = c1.number_input(
        "Duration (min)", min_value=0, step=5,
        value=int(state.get("warmup", {}).get("duration_min") or 10), key=f"warmup_min_{base_id}")
    state["warmup"]["notes"] = c2.text_input(
        "Notes", value=state.get("warmup", {}).get("notes", ""), key=f"warmup_notes_{base_id}")

    blocks = state["blocks"]
    remove_block_idx = None
    for i, block in enumerate(blocks):
        block_num = i + 1
        if not block.get("label", "").strip():
            block["label"] = f"Block {block_num}"
        name_col, del_col = st.columns([6, 1])
        block["label"] = name_col.text_input(f"Block {block_num} name", value=block["label"],
                                              key=f"blockname_{base_id}_{i}")
        if del_col.button("\U0001F5D1 Remove block", key=f"delblock_{base_id}_{i}"):
            remove_block_idx = i
        block["notes"] = st.text_input(
            "Block notes (e.g. \"alternate 1A/2A, rest 60-90s, 3 rounds\")",
            value=block.get("notes", ""), key=f"blocknotes_{base_id}_{i}")
        for j, slot in enumerate(block["slots"]):
            slot["slot"] = slot_label_for(block_num, j)
        render_slot_list(client, options, f"blk_{base_id}_{i}", block["slots"],
                          add_label=f"+ Add exercise to {block['label']}")
    if remove_block_idx is not None:
        blocks.pop(remove_block_idx)
        st.rerun()

    if st.button("+ Add block", key=f"addblock_{base_id}"):
        blocks.append({"label": "", "notes": "", "slots": [empty_slot(""), empty_slot("")]})
        st.rerun()

    st.subheader("FINISHER")
    fin_slots = state["finisher"]["slots"]
    for j, slot in enumerate(fin_slots):
        slot["slot"] = f"Finisher {slot_letter(j)}"
    render_slot_list(client, options, f"fin_{base_id}", fin_slots, add_label="+ Add exercise to Finisher")


def render_workout_editor(client, target_date):
    key_date = str(target_date)
    workout = get_workout_state(client, key_date)
    st.markdown(f"### {target_date.strftime('%A, %b %d, %Y')}")

    base_id = f"{client['id']}_{key_date}"

    templates = load_templates()
    if templates:
        with st.expander("Load from a saved program"):
            cat = st.selectbox("Category", ["All"] + TEMPLATE_CATEGORIES, key=f"tplcat_{base_id}")
            candidates = templates if cat == "All" else [t for t in templates if t["category"] == cat]
            if candidates:
                tpl_names = [t["name"] for t in candidates]
                picked_tpl = st.selectbox("Program", tpl_names, key=f"tplpick_{base_id}")
                if st.button("Load into this day", key=f"tplload_{base_id}"):
                    chosen = next(t for t in candidates if t["name"] == picked_tpl)
                    workout["warmup"] = copy.deepcopy(chosen["warmup"])
                    workout["blocks"] = copy.deepcopy(chosen["blocks"])
                    workout["finisher"] = copy.deepcopy(chosen["finisher"])
                    workout["day_notes"] = chosen.get("day_notes", "")
                    st.success(f"Loaded \"{picked_tpl}\". Review/adjust weights below, then Save.")
                    st.rerun()
            else:
                st.caption("No programs in this category yet — build one in the Program Library tab.")

    render_program_blocks_editor(workout, base_id, client=client)

    wstatus = st.selectbox("Status", ["planned", "completed", "missed"],
                            index=["planned", "completed", "missed"].index(workout.get("status", "planned")),
                            key=f"wstatus_{base_id}")
    workout["status"] = wstatus
    workout["day_notes"] = st.text_area("Day notes", value=workout.get("day_notes", ""), key=f"daynotes_{base_id}")

    all_slots = [s for b in workout["blocks"] for s in b["slots"]] + workout["finisher"]["slots"]
    filled_slots = [s for s in all_slots if s.get("exercise")]

    actual_inputs = {}
    if wstatus == "completed" and filled_slots:
        st.info("Log what was actually performed — this feeds the strength chart and next time's reference.")
        for s in filled_slots:
            c1, c2, c3 = st.columns(3)
            aw = c1.number_input(f"Actual weight — {s['slot']} {s['exercise']}", min_value=0.0, step=2.5,
                                  key=f"actual_w_{base_id}_{s['slot']}")
            ar = c2.number_input(f"Actual reps — {s['slot']} {s['exercise']}", min_value=0, step=1,
                                  key=f"actual_r_{base_id}_{s['slot']}")
            arpe = c3.number_input(f"RPE (optional) — {s['slot']} {s['exercise']}", min_value=0.0, max_value=10.0,
                                    step=0.5, key=f"actual_rpe_{base_id}_{s['slot']}")
            actual_inputs[s["slot"]] = (s["exercise"], aw, ar, arpe)

    if st.button("Save workout", key=f"save_workout_{base_id}"):
        client.setdefault("workouts", {})[key_date] = copy.deepcopy(workout)
        for slot_label, (ex_name, aw, ar, arpe) in actual_inputs.items():
            if aw and ar:
                client.setdefault("lift_entries", []).append({
                    "date": key_date,
                    "exercise": ex_name,
                    "load_lb": aw,
                    "reps": int(ar),
                    "rpe": arpe or None,
                    "est_1rm": epley_1rm(aw, int(ar)),
                })
        save_client(client)
        st.success("Workout saved.")
        st.rerun()

    with st.expander("Duplicate this workout to another date"):
        dup_date = st.date_input("Copy to date", value=target_date + timedelta(days=7),
                                  key=f"dupdate_{base_id}")
        if st.button("Duplicate", key=f"dupbtn_{base_id}"):
            client.setdefault("workouts", {})[str(dup_date)] = copy.deepcopy(workout)
            save_client(client)
            st.success(f"Duplicated to {dup_date.strftime('%A, %b %d, %Y')}.")


def tab_program_calendar(client):
    st.subheader("Program Calendar")
    st.caption("Click a day on the calendar, or pick a date directly below, to add or edit that day's workout.")
    events = build_calendar_events(client)
    state_key = f"cal_selected_date_{client['id']}"

    selected_str = st.session_state.get(state_key, str(date.today()))
    selected = datetime.strptime(selected_str, "%Y-%m-%d").date()

    # A plain, always-reliable date picker — doesn't depend on the calendar
    # widget rendering correctly, and is the only way to jump to a date far
    # outside the calendar's currently-displayed month.
    picked_date = st.date_input("Jump to a specific date", value=selected, key=f"jumpdate_{client['id']}")
    if str(picked_date) != selected_str:
        st.session_state[state_key] = str(picked_date)
        st.rerun()

    cal_state = calendar(events=events, options=CAL_OPTIONS, custom_css=CAL_CSS,
                          callbacks=["dateClick", "eventClick"], key=f"trainer_cal_{client['id']}")
    clicked = extract_clicked_date(cal_state)
    if clicked:
        st.session_state[state_key] = clicked

    selected_str = st.session_state.get(state_key, str(date.today()))
    selected = datetime.strptime(selected_str, "%Y-%m-%d").date()

    st.divider()
    render_workout_editor(client, selected)


BLOCK_ACCENTS = [METRIC_COLOR["weight"], METRIC_COLOR["muscle"], METRIC_COLOR["body_fat"],
                 METRIC_COLOR["waist"], METRIC_COLOR["other"], METRIC_COLOR["vo2max"]]


def _wc_section_header(text, accent):
    return (f'<div style="background:{CHROME["page"]}; padding:10px 18px; '
            f'border-left:5px solid {accent}; margin-top:14px;">'
            f'<span style="color:{CHROME["text_primary"]}; font-weight:800; letter-spacing:0.03em; '
            f'font-size:0.92rem; text-transform:uppercase;">{html.escape(text)}</span></div>')


def _wc_callout(text, accent):
    return (f'<div style="background:{accent}26; padding:7px 18px;">'
            f'<span style="color:{CHROME["text_primary"]}; font-size:0.82rem; font-weight:600;">'
            f'{html.escape(text)}</span></div>')


def _wc_row(left_html, right_html, zebra, note_text=None):
    bg = CHROME["surface"] if zebra else CHROME["page"]
    out = (f'<div style="display:flex; justify-content:space-between; gap:14px; '
           f'padding:9px 18px; background:{bg};">'
           f'<span style="color:{CHROME["text_secondary"]}; font-size:0.88rem;">{left_html}</span>'
           f'<span style="color:{CHROME["text_primary"]}; font-weight:700; font-size:0.88rem; '
           f'white-space:nowrap;">{right_html}</span></div>')
    if note_text:
        out += (f'<div style="background:{bg}; padding:0 18px 8px 18px;">'
                f'<span style="color:{CHROME["muted"]}; font-size:0.76rem; font-style:italic;">'
                f'{html.escape(note_text)}</span></div>')
    return out


def _wc_exercise_row(client, s, zebra):
    left = f'<b>{html.escape(s["slot"])})</b> {html.escape(s["exercise"])}' if s.get("slot") else \
        f'<b>{html.escape(s["exercise"])}</b>'
    if s.get("weight"):
        left += f' &mdash; {html.escape(str(s["weight"]))}'
    right = f'{s.get("sets", "")} x {html.escape(str(s.get("reps", "")))}'
    note_bits = []
    if s.get("notes"):
        note_bits.append(s["notes"])
    if s.get("rest_sec"):
        note_bits.append(f'rest {s["rest_sec"]}s')
    if client:
        last = last_performance(client, s["exercise"])
        if last:
            note_bits.append(f'Last time: {last["load_lb"]:g} lb x {last["reps"]}')
    return _wc_row(left, right, zebra, " · ".join(note_bits) if note_bits else None)


def render_workout_card(client, workout, title=None):
    """Colorful, PDF-style client-facing workout display: dark section
    headers with a rotating accent per block, zebra-striped exercise rows
    with bold right-aligned prescription, a tinted callout for block-level
    superset/rest instructions, and a gold-accented FINISHER — modeled on
    Tyler's real branded program handouts."""
    parts = [f'<div style="border-radius:14px; overflow:hidden; border:1px solid {CHROME["grid"]}; '
             f'margin-bottom:10px;">']

    if title:
        parts.append(
            f'<div style="background:{CHROME["page"]}; padding:16px 18px 12px 18px;">'
            f'<div style="color:{CHROME["text_primary"]}; font-size:1.15rem; font-weight:800;">'
            f'{html.escape(title)}</div>'
            f'<div style="color:{METRIC_COLOR["strength"]}; font-size:0.78rem; font-weight:700; '
            f'letter-spacing:0.05em; margin-top:2px;">COACHJOHNSON &middot; KEEP CLIMBING</div></div>'
        )

    warmup = workout.get("warmup", {})
    if warmup.get("duration_min") or warmup.get("notes"):
        label = "WARM-UP / STRETCH"
        if warmup.get("duration_min"):
            label += f' ({warmup["duration_min"]} MIN)'
        parts.append(_wc_section_header(label, CHROME["baseline"]))
        if warmup.get("notes"):
            parts.append(_wc_row(html.escape(warmup["notes"]), "", True))

    for i, b in enumerate(workout.get("blocks", [])):
        filled = [s for s in b.get("slots", []) if s.get("exercise")]
        if not filled:
            continue
        accent = BLOCK_ACCENTS[i % len(BLOCK_ACCENTS)]
        parts.append(_wc_section_header(b.get("label", f"Block {i + 1}"), accent))
        if b.get("notes"):
            parts.append(_wc_callout(b["notes"], accent))
        for j, s in enumerate(filled):
            parts.append(_wc_exercise_row(client, s, j % 2 == 1))

    fin_filled = [s for s in workout.get("finisher", {}).get("slots", []) if s.get("exercise")]
    if fin_filled:
        parts.append(_wc_section_header("FINISHER", METRIC_COLOR["strength"]))
        for j, s in enumerate(fin_filled):
            parts.append(_wc_exercise_row(client, s, j % 2 == 1))

    if workout.get("day_notes"):
        parts.append(f'<div style="padding:10px 18px; background:{CHROME["surface"]};">'
                      f'<span style="color:{CHROME["muted"]}; font-size:0.8rem; font-style:italic;">'
                      f'{html.escape(workout["day_notes"])}</span></div>')

    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def render_readonly_calendar(client):
    st.subheader("Program Calendar")
    events = build_calendar_events(client)
    state_key = f"client_cal_selected_{client['id']}"
    cal_state = calendar(events=events, options=CAL_OPTIONS, custom_css=CAL_CSS,
                          callbacks=["dateClick", "eventClick"], key=f"client_cal_{client['id']}")
    clicked = extract_clicked_date(cal_state)
    if clicked:
        st.session_state[state_key] = clicked

    selected_str = st.session_state.get(state_key)
    if selected_str:
        workout = normalize_workout(client.get("workouts", {}).get(selected_str))
        selected = datetime.strptime(selected_str, "%Y-%m-%d").date()
        if not workout or not workout_exercise_count(workout):
            st.caption(f"No workout planned for {selected.strftime('%A, %b %d, %Y')}.")
        else:
            st.caption(f"Status: {workout.get('status', 'planned').title()}")
            render_workout_card(client, workout, title=selected.strftime("%A, %B %d, %Y"))


# ---------- trainer pages ----------

def page_roster():
    st.title("Roster")
    all_clients = list_clients()

    if not all_clients:
        st.info("No clients yet. Add your first client from the sidebar.")
        return

    show_inactive = st.checkbox("Show paused/ended clients", value=False)
    clients = all_clients if show_inactive else [c for c in all_clients if c.get("status", "active") == "active"]

    if not clients:
        st.info("No active clients. Check \"Show paused/ended clients\" above to see everyone.")
        return

    rows = []
    for c in clients:
        label, emoji = status_flag(c)
        session_dates = {e.get("date", "") for e in c.get("lift_entries", [])}
        session_dates |= {d for d, w in c.get("workouts", {}).items() if w.get("status") == "completed"}
        session_dates.discard("")
        rows.append({
            "": emoji,
            "Name": c["name"],
            "Primary Goal": c.get("goals", {}).get("primary", ""),
            "Status": label,
            "Sessions Logged": len(session_dates),
            "Check-ins": len(c.get("checkins", [])),
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, width='stretch', hide_index=True)

    st.divider()
    options = client_options(clients)
    labels = [label for label, _ in options]
    picked = st.selectbox("Open a client profile", options=labels)
    if st.button("Go to profile"):
        st.session_state["selected_client_id"] = dict(options)[picked]
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
    st.plotly_chart(fig, width='stretch', key=f"strength_chart_{client['id']}_{picked}")
    st.dataframe(edf[["date", "load_lb", "reps", "rpe", "est_1rm"]].sort_values("date", ascending=False),
                 width='stretch', hide_index=True)


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


def page_program_library():
    st.title("Program Library")
    st.caption("Pre-build reusable programs by category, then load them into any client's Program Calendar.")

    templates = load_templates()

    st.subheader("Saved Programs")
    cat_filter = st.selectbox("Filter by category", ["All"] + TEMPLATE_CATEGORIES, key="lib_cat_filter")
    filtered = templates if cat_filter == "All" else [t for t in templates if t["category"] == cat_filter]

    if not filtered:
        st.info("No programs yet in this category." if templates else
                "No programs yet — build your first one below.")
    else:
        for t in filtered:
            with st.expander(f"{t['name']} — {t['category']}"):
                render_workout_card(None, t, title=t["name"])
                c1, c2 = st.columns(2)
                if c1.button("Edit", key=f"edittpl_{t['id']}"):
                    st.session_state["editing_template_id"] = t["id"]
                    st.session_state.pop(f"template_state_{t['id']}", None)
                    st.rerun()
                if c2.button("Delete", key=f"deltpl_{t['id']}"):
                    remaining = [x for x in templates if x["id"] != t["id"]]
                    save_templates(remaining)
                    st.session_state.pop(f"template_state_{t['id']}", None)
                    st.success(f"Deleted \"{t['name']}\".")
                    st.rerun()

    st.divider()
    editing_id = st.session_state.get("editing_template_id")
    editing = next((t for t in templates if t["id"] == editing_id), None) if editing_id else None
    st.subheader(f"Editing: {editing['name']}" if editing else "Create a New Program")

    state_key = f"template_state_{editing_id or 'new'}"
    if state_key not in st.session_state:
        st.session_state[state_key] = copy.deepcopy(editing) if editing else blank_program()
    draft = st.session_state[state_key]

    c1, c2 = st.columns(2)
    draft["name"] = c1.text_input("Program name", value=draft.get("name", ""), key=f"{state_key}_name")
    cat_index = TEMPLATE_CATEGORIES.index(draft["category"]) if draft.get("category") in TEMPLATE_CATEGORIES else 0
    draft["category"] = c2.selectbox("Category", TEMPLATE_CATEGORIES, index=cat_index, key=f"{state_key}_cat")

    render_program_blocks_editor(draft, state_key, client=None)
    draft["day_notes"] = st.text_area("Program notes", value=draft.get("day_notes", ""), key=f"{state_key}_notes")

    save_col, cancel_col = st.columns(2)
    if save_col.button("Save Program", key=f"{state_key}_save"):
        if not draft["name"].strip():
            st.error("Give the program a name first.")
        else:
            all_templates = load_templates()
            if editing_id:
                all_templates = [draft if t["id"] == editing_id else t for t in all_templates]
            else:
                draft["id"] = new_template_id(draft["name"])
                all_templates.append(draft)
            save_templates(all_templates)
            st.session_state.pop(state_key, None)
            st.session_state.pop("editing_template_id", None)
            st.success(f"Saved \"{draft['name']}\".")
            st.rerun()
    if editing and cancel_col.button("Cancel edit", key=f"{state_key}_cancel"):
        st.session_state.pop(state_key, None)
        st.session_state.pop("editing_template_id", None)
        st.rerun()


def page_trainer_client():
    st.title("Client Workspace")
    clients = list_clients()
    if not clients:
        st.info("No clients yet. Add one from the sidebar first.")
        return

    options = client_options(clients)
    labels = [label for label, _ in options]
    default_id = st.session_state.get("selected_client_id")
    default_label = next((label for label, cid in options if cid == default_id), labels[0])
    picked_label = st.selectbox("Client", options=labels, index=labels.index(default_label))
    client_id = dict(options)[picked_label]
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
    options = client_options(clients)
    labels = [label for label, _ in options]
    picked_label = st.selectbox("Select client", options=labels)
    client = load_client(dict(options)[picked_label])

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

st.sidebar.title("\U0001F3D4 CoachJohnson")
mode = st.sidebar.radio("Mode", ["Trainer", "Client View"])

if mode == "Trainer":
    if "trainer_nav" not in st.session_state:
        st.session_state["trainer_nav"] = "Roster"
    trainer_nav = st.sidebar.radio(
        "Trainer Menu",
        ["Roster", "Add Client", "Client Workspace", "Program Library"],
        index=["Roster", "Add Client", "Client Workspace", "Program Library"].index(st.session_state["trainer_nav"]),
    )
    st.session_state["trainer_nav"] = trainer_nav

    if trainer_nav == "Roster":
        page_roster()
    elif trainer_nav == "Add Client":
        page_add_client()
    elif trainer_nav == "Client Workspace":
        page_trainer_client()
    elif trainer_nav == "Program Library":
        page_program_library()
else:
    page_client_view()
