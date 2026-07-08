import streamlit as st
import pandas as pd
import json
import re
from pathlib import Path
from datetime import date, datetime

DATA_DIR = Path(__file__).parent / "data" / "clients"
DATA_DIR.mkdir(parents=True, exist_ok=True)

st.set_page_config(page_title="HotshotHunterCoach Dashboard", layout="wide")


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


# ---------- pages ----------

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
            "Sessions Logged": len(c.get("lift_entries", [])) ,
            "Check-ins": len(c.get("checkins", [])),
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    names = {c["name"]: c["id"] for c in clients}
    picked = st.selectbox("Open a client profile", options=list(names.keys()))
    if st.button("Go to profile"):
        st.session_state["selected_client_id"] = names[picked]
        st.session_state["nav"] = "Client Profile"
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
                    "status": "active",
                }
                save_client(client)
                st.success(f"Saved {name}.")
                st.session_state["selected_client_id"] = client_id
                st.session_state["nav"] = "Client Profile"
                st.rerun()


def page_client_profile():
    st.title("Client Profile")
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

    tabs = st.tabs(["Overview", "Log Entry", "Strength Charts", "Weight & Measurements", "Milestones"])

    with tabs[0]:
        tab_overview(client)
    with tabs[1]:
        tab_log_entry(client)
    with tabs[2]:
        tab_strength_charts(client)
    with tabs[3]:
        tab_weight_charts(client)
    with tabs[4]:
        tab_milestones(client)


def tab_overview(client):
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Goals")
        st.write(f"**Primary:** {client['goals'].get('primary') or '—'}")
        st.write(f"**Why:** {client['goals'].get('why') or '—'}")
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
            injuries_text = st.text_input("Injuries / limitations (comma separated)",
                                           value=", ".join(client["health_flags"].get("injuries", [])))
            status = st.selectbox("Status", options=["active", "paused", "ended"],
                                   index=["active", "paused", "ended"].index(client.get("status", "active")))
            if st.form_submit_button("Save changes"):
                client["name"] = name.strip()
                client["contact"] = {"phone": phone, "email": email}
                client["goals"] = {"primary": primary_goal, "why": goal_why}
                client["health_flags"] = {"injuries": [i.strip() for i in injuries_text.split(",") if i.strip()]}
                client["status"] = status
                save_client(client)
                st.success("Updated.")
                st.rerun()


def tab_log_entry(client):
    st.subheader("Log a check-in (weight / measurements)")
    with st.form("checkin_form", clear_on_submit=True):
        c_date = st.date_input("Date", value=date.today(), key="checkin_date")
        col1, col2 = st.columns(2)
        with col1:
            weight = st.number_input("Weight (lb)", min_value=0.0, step=0.5)
        with col2:
            waist = st.number_input("Waist (in, optional)", min_value=0.0, step=0.5)
        notes = st.text_area("Notes (energy, sleep, wins, struggles)")
        if st.form_submit_button("Save check-in"):
            client.setdefault("checkins", []).append({
                "date": str(c_date),
                "weight_lb": weight or None,
                "waist_in": waist or None,
                "notes": notes,
            })
            save_client(client)
            st.success("Check-in saved.")
            st.rerun()

    st.divider()
    st.subheader("Log a lift")
    with st.form("lift_form", clear_on_submit=True):
        l_date = st.date_input("Date", value=date.today(), key="lift_date")
        exercise = st.text_input("Exercise (e.g. Back Squat, Bench Press)")
        col1, col2, col3 = st.columns(3)
        with col1:
            load_lb = st.number_input("Load (lb)", min_value=0.0, step=2.5)
        with col2:
            reps = st.number_input("Reps", min_value=1, step=1, value=5)
        with col3:
            rpe = st.number_input("RPE (optional)", min_value=0.0, max_value=10.0, step=0.5)
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


def tab_strength_charts(client):
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
    st.line_chart(edf.set_index("date")["est_1rm"], y_label="Estimated 1RM (lb)")
    st.dataframe(edf[["date", "load_lb", "reps", "rpe", "est_1rm"]].sort_values("date", ascending=False),
                 use_container_width=True, hide_index=True)


def tab_weight_charts(client):
    checkins = client.get("checkins", [])
    baseline = client.get("baseline", {})
    rows = []
    if baseline.get("weight_lb"):
        rows.append({"date": baseline["date"], "weight_lb": baseline["weight_lb"], "waist_in": baseline.get("waist_in")})
    for c in checkins:
        if c.get("weight_lb"):
            rows.append({"date": c["date"], "weight_lb": c["weight_lb"], "waist_in": c.get("waist_in")})

    if not rows:
        st.info("No weight data logged yet.")
        return

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates(subset="date", keep="last")
    st.line_chart(df.set_index("date")["weight_lb"], y_label="Weight (lb)")

    if df["waist_in"].notna().any():
        st.line_chart(df.set_index("date")["waist_in"], y_label="Waist (in)")

    st.dataframe(df.sort_values("date", ascending=False), use_container_width=True, hide_index=True)


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


# ---------- nav ----------

st.sidebar.title("HotshotHunterCoach")
if "nav" not in st.session_state:
    st.session_state["nav"] = "Roster"

nav = st.sidebar.radio(
    "Navigate",
    ["Roster", "Add Client", "Client Profile"],
    index=["Roster", "Add Client", "Client Profile"].index(st.session_state["nav"]),
)
st.session_state["nav"] = nav

if nav == "Roster":
    page_roster()
elif nav == "Add Client":
    page_add_client()
elif nav == "Client Profile":
    page_client_profile()
