# app.py — Life RPG (Local Rules, No LLM)
# Run:  streamlit run app.py
# Requires: pip install streamlit

import math
import sqlite3
import json
import datetime as dt
from pathlib import Path
import streamlit as st

DB_PATH = Path("rpg_local.db")
TODAY = dt.date.today()

# ------------------------- Utilities -------------------------
@st.cache_resource
def get_conn():
    conn = sqlite3.connect(DB_PATH.as_posix(), check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

conn = get_conn()

# ------------------------- Schema ----------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS Skill(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  category TEXT
);

CREATE TABLE IF NOT EXISTS Task(
  id INTEGER PRIMARY KEY,
  skill_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  base_xp INTEGER NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(skill_id) REFERENCES Skill(id)
);

CREATE TABLE IF NOT EXISTS TaskLog(
  id INTEGER PRIMARY KEY,
  task_id INTEGER,
  skill_id INTEGER,
  label TEXT,
  completed_at TEXT,
  minutes INTEGER,
  intensity TEXT,
  notes TEXT,
  xp_awarded INTEGER,
  FOREIGN KEY(task_id) REFERENCES Task(id),
  FOREIGN KEY(skill_id) REFERENCES Skill(id)
);

CREATE TABLE IF NOT EXISTS Streak(
  id INTEGER PRIMARY KEY,
  key TEXT UNIQUE,          -- e.g., skill name or quest id
  current_streak_days INTEGER DEFAULT 0,
  last_completed_date TEXT
);

CREATE TABLE IF NOT EXISTS Badge(
  id INTEGER PRIMARY KEY,
  code TEXT UNIQUE,
  name TEXT,
  description TEXT
);

CREATE TABLE IF NOT EXISTS UserBadge(
  id INTEGER PRIMARY KEY,
  badge_code TEXT,
  awarded_at TEXT
);

CREATE TABLE IF NOT EXISTS Penalty(
  id INTEGER PRIMARY KEY,
  date TEXT,
  code TEXT,
  amount INTEGER,
  note TEXT
);

CREATE TABLE IF NOT EXISTS Settings(
  key TEXT PRIMARY KEY,
  value TEXT
);
"""

with conn:
    conn.executescript(SCHEMA)

# ------------------------- Seed Skills -----------------------
SEED_SKILLS = [
    ("Rope Flow", "Fitness"),
    ("Jump Rope", "Fitness"),
    ("Roadwork", "Fitness"),
    ("Squash - Solo", "Squash"),
    ("Squash - Footwork", "Squash"),
    ("Squash - Match", "Squash"),
    ("Squash - Tactics", "Squash"),
    ("Strength / Lifts", "Fitness"),
    ("Flexibility", "Recovery"),
    ("Writing / Reflection", "Study"),
    ("Studying / Schoolwork", "Study"),
    ("Sleep", "Recovery"),
    ("Self-care", "Recovery"),
    ("Piano", "Creative"),
    ("Procreate", "Creative"),
    ("Coding / Projects", "Study"),
    ("Language", "Study")
]

with conn:
    for name, cat in SEED_SKILLS:
        conn.execute("INSERT OR IGNORE INTO Skill(name, category) VALUES(?,?)", (name, cat))

# ------------------------- XP Curve --------------------------
INT_MULT = {"easy": 0.8, "standard": 1.0, "hard": 1.25, "max": 1.5}

def xp_for_level(level:int)->int:
    return 100 * (level ** 2)

def level_for_xp(xp:int)->int:
    return int(math.sqrt(max(0, xp)/100))

# Streak multiplier for a given key (e.g., skill or quest)
STREAK_CAP = 5  # days

def get_streak(conn, key:str):
    cur = conn.execute("SELECT current_streak_days, last_completed_date FROM Streak WHERE key=?", (key,))
    row = cur.fetchone()
    if not row:
        return 0, None
    return row[0], dt.date.fromisoformat(row[1]) if row[1] else None

def update_streak(conn, key:str, completed_date:dt.date):
    streak, last = get_streak(conn, key)
    if last is None:
        streak = 1
    else:
        delta = (completed_date - last).days
        if delta == 0:
            pass  # already counted today
        elif delta == 1:
            streak += 1
        else:
            streak = 1
    with conn:
        conn.execute("INSERT INTO Streak(key, current_streak_days, last_completed_date) VALUES(?,?,?)\n                      ON CONFLICT(key) DO UPDATE SET current_streak_days=excluded.current_streak_days, last_completed_date=excluded.last_completed_date",
                     (key, streak, completed_date.isoformat()))
    return streak

def streak_multiplier(streak_days:int)->float:
    return 1.0 + 0.1 * min(streak_days, STREAK_CAP)

# ------------------------- Daily Config ----------------------
# Pure local rules (no LLM). Required quests enforce penalties when unfinished at day-end.
DAILY_CFG = {
    "tiers": {
        "1": {
            "required": True,
            "quests": [
                {"id":"squash_50","label":"Squash: 50 straight drives","skill":"Squash - Solo","base_xp":50,
                 "achievements":[{"t":100,"xp":25},{"t":150,"xp":50},{"t":200,"xp":100}],
                 "penalty":20,"streak_key":"squash_daily"},
                {"id":"roadwork_20","label":"Roadwork: 20+ minutes","skill":"Roadwork","base_xp":50,
                 "per_min_bonus":1, "penalty":15, "streak_key":"roadwork_daily"},
                {"id":"rope_10","label":"Jump Rope: 10 min (southpaw + 1 skill)","skill":"Jump Rope","base_xp":40,
                 "combo_bonus":10, "penalty":10, "streak_key":"jumprope_daily"},
                {"id":"strength_day","label":"Strength / Lifts (if scheduled)","skill":"Strength / Lifts","base_xp":80,
                 "pr_bonus":40, "penalty":30, "scheduled_days":[0,2,4], "streak_key":"strength_week"}, # Mon=0
                {"id":"flex_10","label":"Flexibility: 10+ minutes","skill":"Flexibility","base_xp":20,
                 "long_bonus":10, "penalty":10, "streak_key":"flex_daily"}
            ]
        },
        "2": {
            "required": True,
            "quests": [
                {"id":"writing","label":"Writing / Reflection: 5+ sentences","skill":"Writing / Reflection","base_xp":40,
                 "deep_bonus":20, "penalty":10, "streak_key":"writing_daily"},
                {"id":"study","label":"Studying: 30+ minutes","skill":"Studying / Schoolwork","base_xp":50,
                 "inbox_zero_bonus":20, "penalty":10, "streak_key":"study_daily"}
            ]
        },
        "3": {
            "required": True,
            "quests": [
                {"id":"sleep_8h","label":"Sleep: 8 hours","skill":"Sleep","base_xp":40,
                 "bedtime_bonus":20, "streak_mult_drop":0.2, "streak_key":"sleep_daily"},
                {"id":"selfcare","label":"Self-care: skincare/healthcare","skill":"Self-care","base_xp":20,
                 "am_pm_bonus":10, "streak_mult_drop":0.1, "streak_key":"selfcare_daily"}
            ]
        },
        "4": {
            "required": False,
            "quests": [
                {"id":"piano","label":"Piano 20+ min","skill":"Piano","base_xp":50},
                {"id":"procreate","label":"Procreate 20+ min","skill":"Procreate","base_xp":50},
                {"id":"coding","label":"Coding / Project 30+ min","skill":"Coding / Projects","base_xp":60},
                {"id":"language","label":"Language 15+ min","skill":"Language","base_xp":40}
            ]
        }
    },
    "streak": {"per_day": 0.10, "cap": 0.50}
}

# Helper: get skill id by name
@st.cache_data
def skill_id_by_name(name:str):
    row = conn.execute("SELECT id FROM Skill WHERE name=?", (name,)).fetchone()
    return row[0] if row else None

# ------------------------- UI -------------------------------
st.set_page_config(page_title="Life RPG — Local", page_icon="🎮", layout="wide")
st.title("🎮 Life RPG — Local Rules Prototype")

with st.sidebar:
    st.header("Today")
    st.write(TODAY.isoformat())
    st.markdown("**Streak rule:** +10% per day up to +50%.")
    st.markdown("**Note:** Penalties apply at day end for *required* quests left unchecked.")

# Tabs
board_tab, progress_tab, config_tab = st.tabs(["Daily Board","Progress","Config"]) 

# ------------------------- Daily Board -----------------------
with board_tab:
    st.subheader("Daily Quest Board")
    total_gain, pending_penalty = 0, 0
    logs_to_commit = []  # (skill_id, label, minutes, intensity, xp)
    streak_updates = []  # streak keys completed today

    weekday = TODAY.weekday()  # Mon=0

    for tier_key in ["1","2","3","4"]:
        tier = DAILY_CFG["tiers"][tier_key]
        st.markdown(f"### Tier {tier_key} {'(required)' if tier['required'] else '(optional)'}")
        for q in tier["quests"]:
            cols = st.columns([5,1.2,1.2,1.2,2])
            done = cols[0].checkbox(q["label"], key=f"done_{q['id']}")
            minutes = cols[1].number_input("min", 0, 240, 0, key=f"min_{q['id']}")
            intensity = cols[2].selectbox("int", ["easy","standard","hard","max"], index=1, key=f"int_{q['id']}")
            bonus = 0

            # Scheduled lift days only grant XP if scheduled, but you can still check for discipline
            if q["id"] == "strength_day":
                scheduled = weekday in q.get("scheduled_days", [])
                cols[4].markdown("Scheduled: **{}**".format("Yes" if scheduled else "No"))
                pr_hit = cols[3].checkbox("PR +40", key=f"pr_{q['id']}")
                if pr_hit:
                    bonus += q.get("pr_bonus", 0)
                if not scheduled:
                    # If not scheduled, reduce base XP to 0 but allow bonus for extra credit
                    base_xp = 0
                else:
                    base_xp = q["base_xp"]
            else:
                base_xp = q["base_xp"]

            # Quest-specific bonuses
            if q["id"] == "roadwork_20":
                bonus += max(0, minutes - 20) * q.get("per_min_bonus", 0)
            if q["id"] == "rope_10":
                combo = cols[3].checkbox("Combo +10", key=f"combo_{q['id']}")
                if combo: bonus += q.get("combo_bonus", 0)
            if q["id"] == "flex_10":
                long = cols[3].checkbox("20+ min +10", key=f"long_{q['id']}")
                if long: bonus += q.get("long_bonus", 0)
            if q["id"] == "writing":
                deep = cols[3].checkbox("Deep +20", key=f"deep_{q['id']}")
                if deep: bonus += q.get("deep_bonus", 0)
            if q["id"] == "study":
                inbox_zero = cols[3].checkbox("Inbox zero +20", key=f"inb_{q['id']}")
                if inbox_zero: bonus += q.get("inbox_zero_bonus", 0)
            if q["id"] == "sleep_8h":
                bedtime = cols[3].checkbox("Bedtime +20", key=f"bed_{q['id']}")
                if bedtime: bonus += q.get("bedtime_bonus", 0)
            if q["id"] == "selfcare":
                ampm = cols[3].checkbox("AM+PM +10", key=f"ampm_{q['id']}")
                if ampm: bonus += q.get("am_pm_bonus", 0)

            # Achievements for squash drives (enter achieved run length)
            if q["id"] == "squash_50":
                achieved = cols[4].number_input("max in-row", 0, 400, 50, key=f"ach_{q['id']}")
                for a in q.get("achievements", []):
                    if achieved >= a["t"]:
                        bonus += a["xp"]

            # Compute XP if done
            if done:
                # Streak multiplier for the quest key
                s_days, s_last = get_streak(conn, q.get("streak_key", q["id"]))
                s_mult = streak_multiplier(s_days)
                xm = INT_MULT[intensity]
                # Duration factor: reward beyond a default baseline of 30 min for physical, 10–20 for others
                if q["id"] in ("roadwork_20", "strength_day", "flex_10"):
                    dur_mult = 1 + max(0, minutes - 20) / 60
                elif q["id"] in ("rope_10", "squash_50"):
                    dur_mult = 1 + max(0, minutes - 10) / 60
                else:
                    dur_mult = 1 + max(0, minutes - 30) / 60

                gain = round((base_xp + bonus) * xm * s_mult * dur_mult)
                total_gain += gain
                streak_updates.append(q.get("streak_key", q["id"]))
                sid = skill_id_by_name(q["skill"]) or 1
                logs_to_commit.append((sid, q["label"], minutes, intensity, gain))
            else:
                if tier["required"]:
                    # Penalty only considered at finalize
                    pending_penalty += q.get("penalty", 0)

    st.markdown("---")
    c1, c2, c3 = st.columns(3)
    c1.metric("Potential XP (checked)", total_gain)
    c2.metric("Pending Penalty (required unchecked)", pending_penalty)

    if st.button("✅ Finalize Today"):
        with conn:
            for sid, label, minutes, intensity, gain in logs_to_commit:
                conn.execute(
                    "INSERT INTO TaskLog(task_id, skill_id, label, completed_at, minutes, intensity, notes, xp_awarded)\n                     VALUES(NULL,?,?,?,?,?,?,?)",
                    (sid, sid, label, dt.datetime.now().isoformat(), minutes, intensity, "", gain)
                )
            # Apply penalties once
            if pending_penalty > 0:
                conn.execute("INSERT INTO Penalty(date, code, amount, note) VALUES(?,?,?,?)",
                             (TODAY.isoformat(), "daily_miss", pending_penalty, "Required quests missed"))
            # Update streaks for completed quests
            for sk in set(streak_updates):
                update_streak(conn, sk, TODAY)
        st.success(f"Day finalized. +{total_gain} XP, -{pending_penalty} penalty applied.")

# ------------------------- Progress --------------------------
with progress_tab:
    st.subheader("Progress & Levels")

    # Total XP minus penalties
    total_xp = conn.execute("SELECT COALESCE(SUM(xp_awarded),0) FROM TaskLog").fetchone()[0]
    total_pen = conn.execute("SELECT COALESCE(SUM(amount),0) FROM Penalty").fetchone()[0]
    net_xp = max(0, total_xp - total_pen)
    lvl = level_for_xp(net_xp)
    nxt = xp_for_level(lvl+1) - net_xp

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total XP", net_xp)
    c2.metric("Level", lvl)
    c3.metric("XP to next", nxt)
    c4.metric("Penalties (lifetime)", total_pen)

    st.markdown("### Skill Levels")
    cur = conn.execute("""
        SELECT Skill.name, COALESCE(SUM(TaskLog.xp_awarded),0) as sxp
        FROM Skill LEFT JOIN TaskLog ON TaskLog.skill_id = Skill.id
        GROUP BY Skill.id
        ORDER BY sxp DESC
    """)
    rows = cur.fetchall()
    for name, sxp in rows:
        slvl = level_for_xp(sxp)
        st.write(f"**{name}** — LV {slvl} ({sxp} XP)")

    st.markdown("### Streaks")
    srows = conn.execute("SELECT key, current_streak_days, last_completed_date FROM Streak ORDER BY key").fetchall()
    if not srows:
        st.info("No streaks yet — complete some daily quests.")
    else:
        for key, days, last in srows:
            st.write(f"{key}: {days} day streak (last: {last})")

    st.markdown("### Recent Logs (7 days)")
    since = (TODAY - dt.timedelta(days=7)).isoformat()
    logs = conn.execute("SELECT completed_at, label, xp_awarded FROM TaskLog WHERE date(completed_at) >= ? ORDER BY completed_at DESC", (since,)).fetchall()
    for ts, label, xp in logs:
        st.write(f"{ts[:16]} — {label} (+{xp} XP)")

# ------------------------- Config ----------------------------
with config_tab:
    st.subheader("Config")
    st.write("This prototype runs **entirely local** (SQLite) and uses deterministic rules.")
    st.write("You can edit the DAILY_CFG in the code to change XP, penalties, and scheduled lift days.")

    if st.button("Reset DB (danger)"):
        DB_PATH.unlink(missing_ok=True)
        st.experimental_rerun()
