# app.py — Life RPG (Local Rules, No LLM)
# Run:  streamlit run app.py
# Requires: pip install streamlit requests

import math
import sqlite3
import json
import datetime as dt
from pathlib import Path
import streamlit as st
import os
import requests
from io import StringIO

# Create data directory if it doesn't exist
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "rpg_local.db"
TODAY = dt.date.today()

# ------------------------- Utilities -------------------------
@st.cache_resource
def get_conn():
    try:
        # Ensure data directory exists and is writable
        if not DATA_DIR.exists():
            DATA_DIR.mkdir(parents=True, exist_ok=True)
        
        # Check if we can write to the data directory
        test_file = DATA_DIR / "test_write.tmp"
        try:
            test_file.write_text("test")
            test_file.unlink()
        except Exception as e:
            st.error(f"Cannot write to data directory: {e}")
            st.stop()
        
        conn = sqlite3.connect(DB_PATH.as_posix(), check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON;")
        
        # Test the connection
        conn.execute("SELECT 1").fetchone()
        
        return conn
    except Exception as e:
        st.error(f"Database connection error: {e}")
        st.error(f"Database path: {DB_PATH}")
        st.error(f"Current working directory: {os.getcwd()}")
        st.stop()

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
  sublabel TEXT,
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

CREATE TABLE IF NOT EXISTS WeeklyMission(
  id INTEGER PRIMARY KEY,
  week_start TEXT,
  mission_type TEXT,
  target_value INTEGER,
  current_progress INTEGER DEFAULT 0,
  completed BOOLEAN DEFAULT FALSE,
  reward_xp INTEGER DEFAULT 100
);

CREATE TABLE IF NOT EXISTS Badge(
  id INTEGER PRIMARY KEY,
  code TEXT UNIQUE,
  name TEXT,
  description TEXT,
  icon TEXT,
  unlock_condition TEXT
);

CREATE TABLE IF NOT EXISTS UserBadge(
  id INTEGER PRIMARY KEY,
  badge_code TEXT,
  awarded_at TEXT,
  FOREIGN KEY(badge_code) REFERENCES Badge(code)
);

CREATE TABLE IF NOT EXISTS GraceToken(
  id INTEGER PRIMARY KEY,
  awarded_date TEXT,
  used_date TEXT,
  streak_key TEXT,
  reason TEXT
);
"""

with conn:
    conn.executescript(SCHEMA)

# ------------------------- Seed Data -------------------------
# Initialize badges
SEED_BADGES = [
    ("first_quest", "🎯 First Quest", "Complete your first daily quest", "🎯", "Complete any daily quest"),
    ("week_warrior", "⚔️ Week Warrior", "Complete 7 days in a row", "⚔️", "7-day streak"),
    ("squash_master", "🏸 Squash Master", "Reach Level 5 in any squash skill", "🏸", "Squash skill level 5"),
    ("fitness_beast", "💪 Fitness Beast", "Earn 1000 XP from fitness skills", "💪", "1000 fitness XP"),
    ("perfectionist", "✨ Perfectionist", "Complete 100% of required quests for 3 days", "✨", "3 perfect days"),
    ("boss_slayer", "👹 Boss Slayer", "Reach 90% boss readiness", "👹", "90% boss readiness"),
    ("streak_master", "🔥 Streak Master", "Maintain a 14-day streak", "🔥", "14-day streak"),
    ("grace_guardian", "🛡️ Grace Guardian", "Earn your first grace token", "🛡️", "First grace token")
]

with conn:
    for code, name, desc, icon, condition in SEED_BADGES:
        conn.execute("INSERT OR IGNORE INTO Badge(code, name, description, icon, unlock_condition) VALUES(?,?,?,?,?)", 
                     (code, name, desc, icon, condition))

# Initialize campaign settings
with conn:
    conn.execute("INSERT OR IGNORE INTO Settings(key, value) VALUES('campaign_length', '42')")
    conn.execute("INSERT OR IGNORE INTO Settings(key, value) VALUES('boss_date', ?)", 
                 ((dt.date.today() + dt.timedelta(days=42)).isoformat(),))

# Weekly grace token award (1 per week)
def award_weekly_grace_token():
    # Check if already awarded this week
    week_start = TODAY - dt.timedelta(days=TODAY.weekday())
    existing = conn.execute("SELECT id FROM GraceToken WHERE awarded_date >= ? AND used_date IS NULL", 
                          (week_start.isoformat(),)).fetchone()
    if not existing:
        with conn:
            conn.execute("INSERT INTO GraceToken(awarded_date, reason) VALUES(?, ?)",
                       (TODAY.isoformat(), "Weekly grace token"))

award_weekly_grace_token()

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
    ("Language", "Study"),
    ("Study", "Study")  # General study category
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
        conn.execute("""
    INSERT INTO Streak(key, current_streak_days, last_completed_date)
    VALUES(?,?,?)
    ON CONFLICT(key) DO UPDATE SET
      current_streak_days=excluded.current_streak_days,
      last_completed_date=excluded.last_completed_date
""", (key, streak, completed_date.isoformat()))
    return streak

def streak_multiplier(streak_days:int)->float:
    return 1.0 + 0.1 * min(streak_days, STREAK_CAP)

# ------------------------- ICS Calendar Export ----------------
def generate_ics_events(daily_quests, target_date=None):
    """Generate ICS calendar events from daily quests with 10-min reminders"""
    if target_date is None:
        target_date = TODAY
    
    ics_content = StringIO()
    ics_content.write("BEGIN:VCALENDAR\n")
    ics_content.write("VERSION:2.0\n")
    ics_content.write("PRODID:-//Life RPG//Quest Scheduler//EN\n")
    ics_content.write("CALSCALE:GREGORIAN\n")
    
    # Default times for different quest types
    quest_times = {
        "roadwork_daily": {"hour": 16, "minute": 30, "duration": 45},  # 4:30 PM
        "jumprope_daily": {"hour": 18, "minute": 10, "duration": 12},  # 6:10 PM  
        "volt_lifts": {"hour": 19, "minute": 0, "duration": 60},       # 7:00 PM
        "squash_rotation": {"hour": 16, "minute": 30, "duration": 60}, # 4:30 PM
        "mind_rotation": {"hour": 20, "minute": 0, "duration": 30},    # 8:00 PM
        "creative_rotation": {"hour": 19, "minute": 30, "duration": 25}, # 7:30 PM
        "quiet_time": {"hour": 7, "minute": 0, "duration": 15},        # 7:00 AM
        "stretch_daily": {"hour": 21, "minute": 30, "duration": 15},   # 9:30 PM
        "reading_daily": {"hour": 22, "minute": 0, "duration": 20},    # 10:00 PM
        "clean_room": {"hour": 8, "minute": 0, "duration": 5},         # 8:00 AM
        "sleep_8h": {"hour": 23, "minute": 0, "duration": 480}         # 11:00 PM (8 hours)
    }
    
    event_counter = 1
    
    for category in ["mandatory", "rotating"]:
        for quest in daily_quests[category]:
            quest_id = quest["id"]
            quest_label = quest["label"]
            
            # Get timing info
            timing = quest_times.get(quest_id, {"hour": 12, "minute": 0, "duration": 30})
            
            # Create start datetime
            start_dt = dt.datetime.combine(target_date, dt.time(timing["hour"], timing["minute"]))
            end_dt = start_dt + dt.timedelta(minutes=timing["duration"])
            
            # Create reminder time (10 minutes before)
            reminder_dt = start_dt - dt.timedelta(minutes=10)
            
            # Format for ICS
            start_str = start_dt.strftime("%Y%m%dT%H%M%S")
            end_str = end_dt.strftime("%Y%m%dT%H%M%S")
            reminder_str = reminder_dt.strftime("%Y%m%dT%H%M%S")
            now_str = dt.datetime.now().strftime("%Y%m%dT%H%M%SZ")
            
            # Write event
            ics_content.write("BEGIN:VEVENT\n")
            ics_content.write(f"UID:quest-{quest_id}-{target_date.strftime('%Y%m%d')}@liferpg.local\n")
            ics_content.write(f"DTSTAMP:{now_str}\n")
            ics_content.write(f"DTSTART:{start_str}\n")
            ics_content.write(f"DTEND:{end_str}\n")
            ics_content.write(f"SUMMARY:🎮 {quest_label}\n")
            ics_content.write(f"DESCRIPTION:Life RPG Quest - {category.title()}\n")
            ics_content.write("CATEGORIES:LifeRPG,Quest\n")
            
            # Add 10-minute reminder
            ics_content.write("BEGIN:VALARM\n")
            ics_content.write("TRIGGER:-PT10M\n")
            ics_content.write("ACTION:DISPLAY\n")
            ics_content.write(f"DESCRIPTION:🎮 Quest starting in 10 minutes: {quest_label}\n")
            ics_content.write("END:VALARM\n")
            
            ics_content.write("END:VEVENT\n")
            event_counter += 1
    
    ics_content.write("END:VCALENDAR\n")
    return ics_content.getvalue()

# ------------------------- ntfy Push Notifications -----------
def send_ntfy_notification(title, body, topic="life-rpg", priority=3):
    """Send push notification via ntfy.sh"""
    try:
        url = f"https://ntfy.sh/{topic}"
        headers = {
            "Title": title,
            "Priority": str(priority),
            "Tags": "video_game,calendar"
        }
        
        response = requests.post(url, data=body.encode('utf-8'), headers=headers, timeout=10)
        return response.status_code == 200
    except Exception as e:
        st.error(f"Failed to send notification: {e}")
        return False

def generate_daily_schedule_text(daily_quests):
    """Generate a concise schedule text for notifications"""
    schedule_items = []
    
    # Default times for different quest types
    quest_times = {
        "roadwork_daily": "4:30p",
        "jumprope_daily": "6:10p", 
        "volt_lifts": "7:00p",
        "squash_rotation": "4:30p",
        "mind_rotation": "8:00p",
        "creative_rotation": "7:30p",
        "quiet_time": "7:00a",
        "stretch_daily": "9:30p",
        "reading_daily": "10:00p",
        "clean_room": "8:00a",
        "sleep_8h": "11:00p"
    }
    
    # Collect timed events
    timed_events = []
    for category in ["mandatory", "rotating"]:
        for quest in daily_quests[category]:
            quest_id = quest["id"]
            time_str = quest_times.get(quest_id, "12:00p")
            
            # Shorten labels for notification
            label = quest["label"]
            if "Roadwork:" in label:
                short_label = f"Run {label.split()[1]}"
            elif "Jump Rope:" in label:
                short_label = "Rope 10'"
            elif "Volt Lifts" in label:
                short_label = f"Lifts ({label.split('(')[1].split(')')[0]})"
            elif "Squash Solo" in label:
                short_label = "Squash Solo"
            elif "Study" in label or "LeetCode" in label or "AOPS" in label:
                short_label = "Study 30'"
            elif "Piano" in label:
                short_label = "Piano 20'"
            elif "Drawing" in label:
                short_label = "Art 20'"
            else:
                short_label = label[:15] + "..." if len(label) > 15 else label
            
            timed_events.append((time_str, short_label))
    
    # Sort by time and format
    time_sort_order = ["7:00a", "8:00a", "4:30p", "6:10p", "7:00p", "7:30p", "8:00p", "9:30p", "10:00p", "11:00p"]
    timed_events.sort(key=lambda x: time_sort_order.index(x[0]) if x[0] in time_sort_order else 99)
    
    # Create schedule text (limit to 3 main items)
    main_events = [f"{time} {event}" for time, event in timed_events[:3]]
    return ", ".join(main_events)

# ------------------------- Auto-Generated Quest Framework -----
import hashlib
import random

# Quest pools for rotation
SQUASH_DRILL_POOL = [
    {"code":"fh_drives","label":"50 forehand drives","target":50,"unit":"reps","base_xp":25},
    {"code":"bh_drives","label":"50 backhand drives","target":50,"unit":"reps","base_xp":25},
    {"code":"cross_volleys","label":"20 cross-court volleys","target":20,"unit":"reps","base_xp":20},
    {"code":"volley_drops","label":"20 volley drops","target":20,"unit":"reps","base_xp":20},
    {"code":"fig8_drills","label":"50 figure-8 drills","target":50,"unit":"reps","base_xp":20},
    {"code":"boast_drive","label":"30 boast→drive chains","target":30,"unit":"sequences","base_xp":22},
    {"code":"drop_kill","label":"15 drop→kill patterns","target":15,"unit":"sequences","base_xp":20},
    {"code":"serve_return","label":"25 serve & return variations","target":25,"unit":"serves","base_xp":18},
    {"code":"length_game","label":"10 min length game practice","target":10,"unit":"minutes","base_xp":18},
    {"code":"ghost_star","label":"Ghosting star pattern","target":5,"unit":"sets","base_xp":20}
]

MIND_QUEST_POOL = [
    {"code":"aops","label":"AOPS: 1 problem","skill":"Studying / Schoolwork","base_xp":40,"target":1,"unit":"problem"},
    {"code":"leetcode","label":"LeetCode: 1 easy problem","skill":"Coding / Projects","base_xp":45,"target":1,"unit":"problem"},
    {"code":"financial","label":"Financial modeling practice","skill":"Studying / Schoolwork","base_xp":50,"target":30,"unit":"minutes"},
    {"code":"coding_proj","label":"30 min coding project","skill":"Coding / Projects","base_xp":60,"target":30,"unit":"minutes"},
    {"code":"math_drill","label":"Math practice (calculus/stats)","skill":"Studying / Schoolwork","base_xp":35,"target":25,"unit":"minutes"}
]

CREATIVE_QUEST_POOL = [
    {"code":"piano_scales","label":"Piano: scales + 1 piece","skill":"Piano","base_xp":45,"target":20,"unit":"minutes"},
    {"code":"piano_improv","label":"Piano: improvisation session","skill":"Piano","base_xp":40,"target":15,"unit":"minutes"},
    {"code":"piano_hanon","label":"Piano: Hanon + repertoire","skill":"Piano","base_xp":50,"target":25,"unit":"minutes"},
    {"code":"procreate_sketch","label":"Drawing: 1 sketch exercise","skill":"Procreate","base_xp":40,"target":20,"unit":"minutes"},
    {"code":"procreate_study","label":"Drawing: character study","skill":"Procreate","base_xp":45,"target":25,"unit":"minutes"},
    {"code":"procreate_color","label":"Drawing: color theory practice","skill":"Procreate","base_xp":35,"target":20,"unit":"minutes"}
]

LIFE_HABITS_POOL = [
    {"code":"james_1","label":"Quiet time (James 1)","base_xp":20,"target":15,"unit":"minutes"},
    {"code":"james_2","label":"Quiet time (James 2)","base_xp":20,"target":15,"unit":"minutes"},
    {"code":"james_3","label":"Quiet time (James 3)","base_xp":20,"target":15,"unit":"minutes"},
    {"code":"james_4","label":"Quiet time (James 4)","base_xp":20,"target":15,"unit":"minutes"},
    {"code":"proverbs","label":"Quiet time (Proverbs daily)","base_xp":20,"target":10,"unit":"minutes"}
]

STRETCH_POOL = [
    {"code":"hip_stretch","label":"Stretch (hips focus)","base_xp":15,"target":10,"unit":"minutes"},
    {"code":"shoulder_stretch","label":"Stretch (shoulders focus)","base_xp":15,"target":10,"unit":"minutes"},
    {"code":"leg_stretch","label":"Stretch (legs focus)","base_xp":15,"target":10,"unit":"minutes"},
    {"code":"full_body","label":"Stretch (full body flow)","base_xp":20,"target":15,"unit":"minutes"},
    {"code":"yoga_flow","label":"Yoga flow sequence","base_xp":25,"target":20,"unit":"minutes"}
]

READING_POOL = [
    {"code":"tech_book","label":"Read 10 pages (tech book)","base_xp":15,"target":10,"unit":"pages"},
    {"code":"biography","label":"Read 15 pages (biography)","base_xp":15,"target":15,"unit":"pages"},
    {"code":"fiction","label":"Read 20 pages (fiction)","base_xp":10,"target":20,"unit":"pages"},
    {"code":"finance_book","label":"Read 12 pages (finance)","base_xp":20,"target":12,"unit":"pages"}
]

def generate_daily_quests(date: dt.date):
    """Generate rotating quests based on date for consistency"""
    # Use date as seed for consistent daily generation
    day_seed = int(date.strftime('%Y%m%d'))
    random.seed(day_seed)
    
    weekday = date.weekday()  # Mon=0
    
    # Build quest structure
    quests = {
        "mandatory": [],
        "rotating": []
    }
    
    # MANDATORY QUESTS (always appear)
    # Roadwork - daily
    miles = 4 + (1 if weekday in [2, 4] else 0)  # Extra mile Wed/Fri
    quests["mandatory"].append({
        "id": "roadwork_daily",
        "label": f"Roadwork: {miles} miles",
        "skill": "Roadwork",
        "base_xp": 50 + (miles-4)*10,
        "penalty": 20,
        "streak_key": "roadwork_daily"
    })
    
    # Jump Rope - daily
    quests["mandatory"].append({
        "id": "jumprope_daily", 
        "label": "Jump Rope: 10-12 min skill set",
        "skill": "Jump Rope",
        "base_xp": 40,
        "penalty": 15,
        "streak_key": "jumprope_daily"
    })
    
    # Volt Lifts - Mon/Wed/Fri
    if weekday in [0, 2, 4]:
        workout_type = "full body" if weekday == 0 else "upper" if weekday == 2 else "lower"
        quests["mandatory"].append({
            "id": "volt_lifts",
            "label": f"Volt Lifts ({workout_type})",
            "skill": "Strength / Lifts", 
            "base_xp": 80,
            "penalty": 30,
            "streak_key": "strength_week"
        })
    
    # ROTATING QUESTS
    # Squash: Pick 2-3 drills
    squash_drills = random.sample(SQUASH_DRILL_POOL, 3)
    quests["rotating"].append({
        "id": "squash_rotation",
        "label": "🎾 Squash Solo Drills",
        "skill": "Squash - Solo",
        "penalty": 20,
        "streak_key": "squash_daily",
        "subquests": squash_drills
    })
    
    # Mind Quest: Pick 1
    mind_quest = random.choice(MIND_QUEST_POOL)
    quests["rotating"].append({
        "id": "mind_rotation",
        "label": f"🧠 {mind_quest['label']}", 
        "skill": mind_quest["skill"],
        "base_xp": mind_quest["base_xp"],
        "penalty": 15,
        "streak_key": "mind_daily"
    })
    
    # Creative: Pick 1
    creative_quest = random.choice(CREATIVE_QUEST_POOL)
    quests["rotating"].append({
        "id": "creative_rotation",
        "label": f"🎵 {creative_quest['label']}",
        "skill": creative_quest["skill"], 
        "base_xp": creative_quest["base_xp"],
        "penalty": 10,
        "streak_key": "creative_daily"
    })
    
    # Life Habits: Pick 1 from each category
    quiet_time = random.choice(LIFE_HABITS_POOL)
    stretch = random.choice(STRETCH_POOL)
    reading = random.choice(READING_POOL)
    
    quests["rotating"].extend([
        {
            "id": "quiet_time",
            "label": f"🛡️ {quiet_time['label']}",
            "skill": "Writing / Reflection",
            "base_xp": quiet_time["base_xp"],
            "penalty": 10,
            "streak_key": "quiet_daily"
        },
        {
            "id": "stretch_daily", 
            "label": f"🛡️ {stretch['label']}",
            "skill": "Flexibility",
            "base_xp": stretch["base_xp"],
            "penalty": 8,
            "streak_key": "stretch_daily"
        },
        {
            "id": "reading_daily",
            "label": f"🛡️ {reading['label']}", 
            "skill": "Study",
            "base_xp": reading["base_xp"],
            "penalty": 5,
            "streak_key": "reading_daily"
        },
        {
            "id": "clean_room",
            "label": "🛡️ Clean room (5 min tidy)",
            "skill": "Self-care",
            "base_xp": 15,
            "penalty": 5, 
            "streak_key": "clean_daily"
        }
    ])
    
    # Sleep - always required
    quests["rotating"].append({
        "id": "sleep_8h",
        "label": "Sleep: 8 hours",
        "skill": "Sleep",
        "base_xp": 40,
        "bedtime_bonus": 20,
        "penalty": 15,
        "streak_key": "sleep_daily"
    })
    
    return quests

# Legacy config for backwards compatibility 
DAILY_CFG = {
    "tiers": {
        "1": {"required": True, "quests": []},
        "2": {"required": True, "quests": []}, 
        "3": {"required": True, "quests": []},
        "4": {"required": False, "quests": []}
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

# Retro Game Boy CSS styling
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap');
    
    .main-title {
        font-family: 'Press Start 2P', monospace;
        color: #0f380f;
        text-align: center;
        background: linear-gradient(135deg, #9bbc0f, #8bac0f);
        padding: 20px;
        border: 4px solid #1e1e1e;
        border-radius: 0px;
        margin-bottom: 20px;
        text-shadow: 2px 2px 0px #306230;
    }
    
    .retro-box {
        background: linear-gradient(135deg, #9bbc0f, #8bac0f);
        border: 3px solid #1e1e1e;
        padding: 15px;
        margin: 10px 0;
        font-family: 'Press Start 2P', monospace;
        font-size: 12px;
        color: #0f380f;
    }
    
    .quest-container {
        background: #8bac0f;
        border: 2px solid #306230;
        padding: 10px;
        margin: 5px 0;
        font-family: 'Press Start 2P', monospace;
        font-size: 10px;
    }
    
    .tier-header {
        background: linear-gradient(90deg, #0f380f, #306230);
        color: #9bbc0f;
        padding: 10px;
        border: 2px solid #1e1e1e;
        font-family: 'Press Start 2P', monospace;
        text-align: center;
        margin: 15px 0;
    }
    
    .pixel-button {
        background: #9bbc0f;
        border: 3px solid #0f380f;
        color: #0f380f;
        font-family: 'Press Start 2P', monospace;
        padding: 10px 15px;
        font-size: 10px;
    }
    
    .map-node {
        width: 40px;
        height: 40px;
        border: 2px solid #1e1e1e;
        display: inline-block;
        margin: 2px;
        text-align: center;
        line-height: 36px;
        font-family: 'Press Start 2P', monospace;
        font-size: 8px;
    }
    
    .node-completed {
        background: #9bbc0f;
        color: #0f380f;
    }
    
    .node-current {
        background: #8bac0f;
        color: #306230;
        animation: blink 1s infinite;
    }
    
    .node-locked {
        background: #1e1e1e;
        color: #666;
    }
    
    @keyframes blink {
        0%, 50% { opacity: 1; }
        51%, 100% { opacity: 0.5; }
    }
    
    .calendar-day {
        width: 30px;
        height: 30px;
        border: 1px solid #1e1e1e;
        display: inline-block;
        margin: 1px;
        text-align: center;
        line-height: 28px;
        font-family: 'Press Start 2P', monospace;
        font-size: 6px;
    }
    
    .day-miss { background: #8b0000; color: white; }
    .day-okay { background: #ffd700; color: #1e1e1e; }
    .day-great { background: #228b22; color: white; }
    .day-empty { background: #d3d3d3; color: #666; }
    
    .boss-bar {
        width: 100%;
        height: 30px;
        background: #1e1e1e;
        border: 3px solid #0f380f;
        position: relative;
        margin: 20px 0;
    }
    
    .boss-fill {
        height: 100%;
        background: linear-gradient(90deg, #8b0000, #ffd700, #228b22);
        transition: width 0.5s ease;
    }
    
    .boss-text {
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        font-family: 'Press Start 2P', monospace;
        font-size: 10px;
        color: white;
        text-shadow: 1px 1px 0px black;
    }
    
    .weekly-mission {
        background: #306230;
        border: 2px solid #9bbc0f;
        padding: 10px;
        margin: 5px 0;
        font-family: 'Press Start 2P', monospace;
        font-size: 8px;
        color: #9bbc0f;
    }
    
    .progress-bar {
        width: 100%;
        height: 16px;
        background: #1e1e1e;
        border: 2px solid #9bbc0f;
        position: relative;
        margin: 5px 0;
    }
    
    .progress-fill {
        height: 100%;
        background: linear-gradient(90deg, #9bbc0f, #8bac0f);
        transition: width 0.3s ease;
    }
    
    .progress-text {
        position: absolute;
        top: 0;
        left: 50%;
        transform: translateX(-50%);
        font-family: 'Press Start 2P', monospace;
        font-size: 6px;
        color: white;
        text-shadow: 1px 1px 0px black;
        line-height: 16px;
    }
    
    .badge-item {
        display: inline-block;
        background: #8bac0f;
        border: 2px solid #0f380f;
        padding: 8px;
        margin: 3px;
        font-family: 'Press Start 2P', monospace;
        font-size: 8px;
        text-align: center;
        width: 80px;
        height: 60px;
        vertical-align: top;
    }
    
    .badge-locked {
        background: #1e1e1e;
        color: #666;
        border-color: #666;
    }
    
    .grace-token {
        display: inline-block;
        background: radial-gradient(circle, #ffd700, #ffed4e);
        border: 3px solid #b8860b;
        border-radius: 50%;
        width: 40px;
        height: 40px;
        margin: 5px;
        text-align: center;
        line-height: 34px;
        font-family: 'Press Start 2P', monospace;
        font-size: 12px;
        color: #8b4513;
        animation: shine 2s infinite;
    }
    
    @keyframes shine {
        0%, 100% { box-shadow: 0 0 5px #ffd700; }
        50% { box-shadow: 0 0 15px #ffd700, 0 0 25px #ffd700; }
    }
    
    .inventory-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(80px, 1fr));
        gap: 5px;
        padding: 10px;
        background: #306230;
        border: 2px solid #9bbc0f;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-title">🎮 LIFE RPG - RETRO QUEST 🎮</div>', unsafe_allow_html=True)

with st.sidebar:
    st.markdown('<div class="retro-box">📅 TODAY<br>' + TODAY.isoformat() + '</div>', unsafe_allow_html=True)
    st.markdown('<div class="retro-box">⚡ STREAK RULE<br>+10% per day up to +50%</div>', unsafe_allow_html=True)
    st.markdown('<div class="retro-box">⚠️ PENALTIES<br>Apply at day end for required quests left unchecked</div>', unsafe_allow_html=True)
    
    # Grace Tokens display
    grace_tokens = conn.execute("SELECT COUNT(*) FROM GraceToken WHERE used_date IS NULL").fetchone()[0]
    st.markdown(f'<div class="retro-box">🛡️ GRACE TOKENS<br>Available: {grace_tokens}</div>', unsafe_allow_html=True)
    if grace_tokens > 0:
        for i in range(min(grace_tokens, 3)):  # Show max 3 tokens visually
            st.markdown('<div class="grace-token">🛡️</div>', unsafe_allow_html=True)

# Tabs
board_tab, missions_tab, map_tab, calendar_tab, badges_tab, boss_tab, progress_tab, config_tab = st.tabs([
    "🎯 Daily Board", "📋 Missions", "🗺️ Map", "📅 Calendar", "🏆 Badges", "👹 Boss", "📊 Progress", "⚙️ Config"
]) 

# ------------------------- Daily Board -----------------------
with board_tab:
    st.markdown('<div class="retro-box">🎯 AUTO-GENERATED DAILY QUEST BOARD</div>', unsafe_allow_html=True)
    
    # Generate today's quests
    daily_quests = generate_daily_quests(TODAY)
    
    total_gain, pending_penalty = 0, 0
    logs_to_commit = []  # (skill_id, label, sublabel, minutes, intensity, xp)
    streak_updates = []  # streak keys completed today

    weekday = TODAY.weekday()  # Mon=0
    
    # Display date info
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    st.markdown(f'<div class="retro-box">📅 {day_names[weekday]} - {TODAY.strftime("%B %d, %Y")}</div>', unsafe_allow_html=True)

    # MANDATORY QUESTS SECTION
    st.markdown('<div class="tier-header">⚔️ MANDATORY QUESTS - CORE TRAINING</div>', unsafe_allow_html=True)
    
    for q in daily_quests["mandatory"]:
        with st.container():
            st.markdown(f'<div class="quest-container">✅ {q["label"]}</div>', unsafe_allow_html=True)
            cols_top = st.columns([1.4,1,1,1,1.2])
            intensity = cols_top[2].selectbox("Intensity", ["easy","standard","hard","max"], index=1, key=f"int_{q['id']}")
            quest_gain = 0
            quest_any_done = False

            # Simple quest logic
            row = st.columns([2.6,0.9,0.9,1.2,1.2])
            done = row[0].checkbox("✅ Mark Complete", key=f"done_{q['id']}")
            minutes = row[1].number_input("Minutes", 0, 240, 0, key=f"min_{q['id']}")
            bonus = 0
            
            # Special bonuses
            if q["id"] == "roadwork_daily":
                bonus += max(0, minutes - 30) * 1  # 1 XP per extra minute
            if q["id"] == "volt_lifts":
                pr_hit = row[2].checkbox("PR Hit (+40 XP)", key=f"pr_{q['id']}")
                if pr_hit: bonus += 40

            base_xp = q.get("base_xp", 0)

            if done:
                quest_any_done = True
                s_days, _ = get_streak(conn, q.get("streak_key", q["id"]))
                s_mult = streak_multiplier(s_days)
                xm = INT_MULT[intensity]
                dur_mult = 1 + max(0, minutes - 20) / 60
                gain = round((base_xp + bonus) * xm * s_mult * dur_mult)
                quest_gain += gain
                sid = skill_id_by_name(q.get("skill")) or 1
                logs_to_commit.append((sid, q["label"], None, minutes, intensity, gain))

            # Quest footer: streak update + penalties
            total_gain += quest_gain
            if quest_any_done:
                streak_updates.append(q.get("streak_key", q["id"]))
            else:
                pending_penalty += q.get("penalty", 0)

    # ROTATING QUESTS SECTION
    st.markdown('<div class="tier-header">🔄 ROTATING QUESTS - TODAY\'S SELECTION</div>', unsafe_allow_html=True)
    
    for q in daily_quests["rotating"]:
        with st.container():
            st.markdown(f'<div class="quest-container">{q["label"]}</div>', unsafe_allow_html=True)
            cols_top = st.columns([1.4,1,1,1,1.2])
            intensity = cols_top[2].selectbox("Intensity", ["easy","standard","hard","max"], index=1, key=f"int_{q['id']}")
            quest_gain = 0
            quest_any_done = False

            # Check if this quest has subquests (squash drills)
            if "subquests" in q:
                for sq in q["subquests"]:
                    row = st.columns([3.4,0.9,0.9,1.2,1.2])
                    done = row[0].checkbox(f"🎯 {sq['label']}", key=f"done_{q['id']}_{sq['code']}")
                    achieved = row[1].number_input("Amount", 0, 500, sq.get("target",0), key=f"ach_{q['id']}_{sq['code']}")
                    minutes = row[2].number_input("Minutes", 0, 240, 0, key=f"min_{q['id']}_{sq['code']}")
                    base_xp = sq.get("base_xp", 0)
                    
                    if done:
                        quest_any_done = True
                        s_days, _ = get_streak(conn, q.get("streak_key", q["id"]))
                        s_mult = streak_multiplier(s_days)
                        xm = INT_MULT[intensity]
                        dur_mult = 1 + max(0, minutes - 10) / 60
                        gain = round(base_xp * xm * s_mult * dur_mult)
                        quest_gain += gain
                        sid = skill_id_by_name(q.get("skill")) or 1
                        logs_to_commit.append((sid, q["label"], sq["label"], minutes, intensity, gain))
                    
                    row[4].markdown(f"**Target:** {sq.get('target','–')} {sq.get('unit','')} | **Base:** {base_xp} XP")
            else:
                # Simple quest
                row = st.columns([2.6,0.9,0.9,1.2,1.2])
                done = row[0].checkbox("✅ Mark Complete", key=f"done_{q['id']}")
                minutes = row[1].number_input("Minutes", 0, 240, 0, key=f"min_{q['id']}")
                bonus = 0
                
                # Special bonuses for sleep
                if q["id"] == "sleep_8h":
                    bedtime = row[2].checkbox("Bedtime (+20 XP)", key=f"bed_{q['id']}")
                    if bedtime: bonus += q.get("bedtime_bonus", 0)

                base_xp = q.get("base_xp", 0)

                if done:
                    quest_any_done = True
                    s_days, _ = get_streak(conn, q.get("streak_key", q["id"]))
                    s_mult = streak_multiplier(s_days)
                    xm = INT_MULT[intensity]
                    dur_mult = 1 + max(0, minutes - 15) / 60
                    gain = round((base_xp + bonus) * xm * s_mult * dur_mult)
                    quest_gain += gain
                    sid = skill_id_by_name(q.get("skill")) or 1
                    logs_to_commit.append((sid, q["label"], None, minutes, intensity, gain))

            # Quest footer
            total_gain += quest_gain
            if quest_any_done:
                streak_updates.append(q.get("streak_key", q["id"]))
            else:
                pending_penalty += q.get("penalty", 0)

    st.markdown("---")
    
    # PHASE 1: ICS Export & Notifications
    st.markdown('<div class="tier-header">📱 PHASE 1: INSTANT NOTIFICATIONS</div>', unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("### 📅 Calendar Export")
        if st.button("📅 Generate Today's Calendar (.ics)", help="Download calendar file with 10-min reminders"):
            ics_content = generate_ics_events(daily_quests, TODAY)
            st.download_button(
                label="📲 Download Calendar Events",
                data=ics_content,
                file_name=f"life-rpg-{TODAY.isoformat()}.ics",
                mime="text/calendar"
            )
            st.success("📅 Calendar generated! Import into your phone's calendar app.")
        
        if st.button("📅 Generate Tomorrow's Calendar", help="Preview and download tomorrow's schedule"):
            tomorrow = TODAY + dt.timedelta(days=1)
            tomorrow_quests = generate_daily_quests(tomorrow)
            ics_content = generate_ics_events(tomorrow_quests, tomorrow)
            st.download_button(
                label="📲 Download Tomorrow's Events",
                data=ics_content,
                file_name=f"life-rpg-{tomorrow.isoformat()}.ics", 
                mime="text/calendar"
            )
    
    with col2:
        st.markdown("### 📱 Push Notifications")
        
        # ntfy topic configuration
        ntfy_topic = st.text_input("ntfy Topic", value="life-rpg-personal", help="Your unique notification channel")
        
        if st.button("🚀 Send Today's Schedule", help="Push notification to your phone"):
            schedule_text = generate_daily_schedule_text(daily_quests)
            success = send_ntfy_notification(
                title="Life RPG — Today",
                body=schedule_text,
                topic=ntfy_topic
            )
            if success:
                st.success(f"📱 Notification sent to topic: {ntfy_topic}")
                st.info("💡 Install ntfy app on your phone and subscribe to this topic!")
            else:
                st.error("❌ Failed to send notification")
        
        # Show what the notification would look like
        with st.expander("👀 Preview Notification"):
            preview_schedule = generate_daily_schedule_text(daily_quests)
            st.markdown(f"**Title:** Life RPG — Today")
            st.markdown(f"**Body:** {preview_schedule}")
    
    st.markdown("---")
    
    # Setup instructions
    with st.expander("📋 Phase 1 Setup Instructions"):
        st.markdown("""
        ### 📱 Phone Setup (2 minutes):
        1. **Install ntfy app** from App Store/Play Store
        2. **Subscribe to your topic** (e.g., `life-rpg-personal`)
        3. **Enable notifications** in ntfy app settings
        
        ### 📅 Calendar Setup:
        1. **Click "Generate Today's Calendar"** above
        2. **Download the .ics file**
        3. **Import into your phone's calendar** (iOS Calendar, Google Calendar, etc.)
        4. **Enable calendar notifications** (you'll get 10-min reminders)
        
        ### 🤖 Optional: GitHub Action (Advanced):
        - Set up a GitHub Action to run at 7am ET
        - Fetch WHOOP recovery data
        - Generate daily plan automatically  
        - Push notifications without manual intervention
        """)

    st.markdown("---")
    
    # Summary section
    col1, col2, col3 = st.columns(3)
    col1.metric("🎯 Potential XP", total_gain)
    col2.metric("💀 Pending Penalty", pending_penalty)
    
    # Count completed mandatory quests
    mandatory_completed = sum(1 for q in daily_quests['mandatory'] if st.session_state.get(f'done_{q["id"]}', False))
    col3.metric("⚔️ Mandatory Complete", f"{mandatory_completed}/{len(daily_quests['mandatory'])}")

    # Quest regeneration button
    if st.button("🔄 PREVIEW TOMORROW'S QUESTS", help="See what tomorrow's rotation will bring!"):
        tomorrow = TODAY + dt.timedelta(days=1)
        tomorrow_quests = generate_daily_quests(tomorrow)
        
        st.markdown("### 🔮 Tomorrow's Quest Preview")
        st.markdown("**Mandatory:**")
        for q in tomorrow_quests["mandatory"]:
            st.write(f"✅ {q['label']}")
        
        st.markdown("**Rotating Selection:**")
        for q in tomorrow_quests["rotating"]:
            if "subquests" in q:
                st.write(f"{q['label']}:")
                for sq in q["subquests"]:
                    st.write(f"   • {sq['label']}")
            else:
                st.write(f"• {q['label']}")

    if st.button("✅ FINALIZE TODAY", help="Lock in your daily progress!"):
        with conn:
            for sid, label, sublabel, minutes, intensity, gain in logs_to_commit:
                conn.execute(
                    """INSERT INTO TaskLog(task_id, skill_id, label, sublabel, completed_at, minutes, intensity, notes, xp_awarded)
                       VALUES(NULL,?,?,?,?,?,?,?,?)""",
                    (sid, label, sublabel or "", dt.datetime.now().isoformat(), minutes, intensity, "", gain)
                )
            # Apply penalties once
            if pending_penalty > 0:
                conn.execute("INSERT INTO Penalty(date, code, amount, note) VALUES(?,?,?,?)",
                             (TODAY.isoformat(), "daily_miss", pending_penalty, "Required quests missed"))
            # Update streaks for completed quests
            for sk in set(streak_updates):
                update_streak(conn, sk, TODAY)
        st.success(f"🎮 Day finalized! +{total_gain} XP earned, -{pending_penalty} penalty applied.")

# ------------------------- Weekly Missions Tab ---------------
with missions_tab:
    st.markdown('<div class="retro-box">📋 WEEKLY MISSIONS COMMAND CENTER</div>', unsafe_allow_html=True)
    
    # Get current week start (Monday)
    week_start = TODAY - dt.timedelta(days=TODAY.weekday())
    week_end = week_start + dt.timedelta(days=6)
    
    # Define weekly missions
    weekly_missions = [
        {
            "type": "total_xp",
            "name": "XP WARRIOR",
            "description": "Earn 500 total XP this week",
            "target": 500,
            "icon": "⚔️",
            "reward": 150
        },
        {
            "type": "perfect_days",
            "name": "PERFECTIONIST",
            "description": "Complete 100% of required quests for 3 days",
            "target": 3,
            "icon": "✨", 
            "reward": 200
        },
        {
            "type": "streak_maintenance",
            "name": "STREAK KEEPER",
            "description": "Maintain any streak for 7 days",
            "target": 7,
            "icon": "🔥",
            "reward": 100
        },
        {
            "type": "squash_master",
            "name": "COURT COMMANDER",
            "description": "Complete 5 squash sessions (any type)",
            "target": 5,
            "icon": "🏸",
            "reward": 120
        }
    ]
    
    for mission in weekly_missions:
        # Calculate current progress
        if mission["type"] == "total_xp":
            progress = conn.execute("""
                SELECT COALESCE(SUM(xp_awarded), 0) 
                FROM TaskLog 
                WHERE date(completed_at) >= ? AND date(completed_at) <= ?
            """, (week_start.isoformat(), week_end.isoformat())).fetchone()[0]
            
        elif mission["type"] == "perfect_days":
            # Count days with 100% required quest completion
            progress = 0
            for day_offset in range(7):
                check_date = week_start + dt.timedelta(days=day_offset)
                if check_date > TODAY:
                    continue
                
                # Count required vs completed
                weekday_check = check_date.weekday()
                total_req = sum(1 for tier in ["1","2","3"] for q in DAILY_CFG["tiers"][tier]["quests"] 
                              if DAILY_CFG["tiers"][tier]["required"] and 
                              (q.get("id") != "strength_day" or weekday_check in q.get("scheduled_days", [])))
                
                completed = conn.execute("""
                    SELECT COUNT(DISTINCT label) FROM TaskLog 
                    WHERE date(completed_at) = ?
                """, (check_date.isoformat(),)).fetchone()[0] or 0
                
                if completed >= total_req and total_req > 0:
                    progress += 1
                    
        elif mission["type"] == "streak_maintenance":
            # Find longest current streak
            streaks = conn.execute("SELECT MAX(current_streak_days) FROM Streak").fetchone()[0] or 0
            progress = min(streaks, mission["target"])
            
        elif mission["type"] == "squash_master":
            progress = conn.execute("""
                SELECT COUNT(*) FROM TaskLog 
                WHERE date(completed_at) >= ? AND date(completed_at) <= ?
                AND (label LIKE '%Squash%' OR label LIKE '%squash%')
            """, (week_start.isoformat(), week_end.isoformat())).fetchone()[0] or 0
        
        # Display mission with progress bar
        progress_pct = min(100, (progress / mission["target"]) * 100)
        completed = progress >= mission["target"]
        
        st.markdown(f'''
            <div class="weekly-mission">
                {mission["icon"]} {mission["name"]}<br>
                {mission["description"]}<br>
                Reward: +{mission["reward"]} XP
            </div>
        ''', unsafe_allow_html=True)
        
        st.markdown(f'''
            <div class="progress-bar">
                <div class="progress-fill" style="width: {progress_pct}%;"></div>
                <div class="progress-text">{progress}/{mission["target"]}</div>
            </div>
        ''', unsafe_allow_html=True)
        
        if completed:
            st.success(f"🎉 {mission['name']} COMPLETED! +{mission['reward']} XP")
        
        st.markdown("---")
    
    # Weekly summary
    st.markdown(f"**Week of {week_start.strftime('%b %d')} - {week_end.strftime('%b %d')}**")
    week_xp = conn.execute("""
        SELECT COALESCE(SUM(xp_awarded), 0) FROM TaskLog 
        WHERE date(completed_at) >= ? AND date(completed_at) <= ?
    """, (week_start.isoformat(), week_end.isoformat())).fetchone()[0]
    st.metric("📊 Week Total XP", week_xp)

# ------------------------- Map Tab ---------------------------
with map_tab:
    st.markdown('<div class="retro-box">🗺️ CAMPAIGN MAP TO SEASON BOSS</div>', unsafe_allow_html=True)
    
    # Get campaign settings
    campaign_length = int(conn.execute("SELECT value FROM Settings WHERE key='campaign_length'").fetchone()[0])
    boss_date_str = conn.execute("SELECT value FROM Settings WHERE key='boss_date'").fetchone()[0]
    boss_date = dt.date.fromisoformat(boss_date_str)
    
    # Calculate map based on boss date
    map_start = boss_date - dt.timedelta(days=campaign_length-1)
    
    # Get XP data for the campaign period
    map_logs = conn.execute("""
        SELECT date(completed_at) as day, SUM(xp_awarded) as daily_xp 
        FROM TaskLog 
        WHERE date(completed_at) >= ? AND date(completed_at) <= ?
        GROUP BY date(completed_at)
        ORDER BY day
    """, (map_start.isoformat(), boss_date.isoformat())).fetchall()
    
    # Create a dict for easy lookup
    xp_by_day = {day: xp for day, xp in map_logs}
    
    st.markdown(f"Navigate the {campaign_length}-day path to face the Season Boss on **{boss_date.strftime('%B %d, %Y')}**!")
    
    # Calculate grid dimensions
    cols_per_row = 7
    rows_needed = (campaign_length + cols_per_row - 1) // cols_per_row
    
    # Create grid
    for week in range(rows_needed):
        cols = st.columns(cols_per_row)
        for day_offset in range(cols_per_row):
            node_index = week * cols_per_row + day_offset
            if node_index < campaign_length:
                current_date = map_start + dt.timedelta(days=node_index)
                day_xp = xp_by_day.get(current_date.isoformat(), 0)
                
                # Determine node state
                if current_date > TODAY:
                    node_class = "node-locked"
                    symbol = "🔒"
                elif current_date == TODAY:
                    node_class = "node-current"
                    symbol = "📍"
                elif day_xp > 0:
                    node_class = "node-completed"
                    symbol = "⭐"
                else:
                    node_class = "node-locked"
                    symbol = "💀"
                
                with cols[day_offset]:
                    st.markdown(f'''
                        <div class="map-node {node_class}">
                            {symbol}<br>{current_date.day}
                        </div>
                    ''', unsafe_allow_html=True)
                    if day_xp > 0:
                        st.caption(f"{day_xp} XP")
    
    # Boss node and progress
    completed_days = len([d for d in xp_by_day.keys() if d <= TODAY.isoformat()])
    days_until_boss = (boss_date - TODAY).days
    
    st.markdown("---")
    st.markdown(f'<div class="retro-box">👹 SEASON BOSS BATTLE<br>Progress: {completed_days}/{campaign_length} days<br>Days until boss: {days_until_boss}</div>', unsafe_allow_html=True)

# ------------------------- Calendar Tab ----------------------
with calendar_tab:
    st.markdown('<div class="retro-box">📅 6-WEEK COMPLETION HEATMAP</div>', unsafe_allow_html=True)
    
    # Grace tokens section
    grace_tokens = conn.execute("SELECT COUNT(*) FROM GraceToken WHERE used_date IS NULL").fetchone()[0]
    used_tokens = conn.execute("SELECT COUNT(*) FROM GraceToken WHERE used_date IS NOT NULL").fetchone()[0]
    
    col1, col2 = st.columns([1, 3])
    with col1:
        st.markdown("### 🛡️ Grace Tokens")
        st.write(f"Available: **{grace_tokens}**")
        st.write(f"Used: **{used_tokens}**")
        
        if grace_tokens > 0:
            st.markdown("**Use Grace Token:**")
            streak_keys = [row[0] for row in conn.execute("SELECT DISTINCT key FROM Streak WHERE current_streak_days > 0").fetchall()]
            if streak_keys:
                selected_streak = st.selectbox("Save which streak?", streak_keys, key="grace_streak_select")
                if st.button("🛡️ Use Grace Token", key="use_grace"):
                    # Use grace token to save streak
                    with conn:
                        conn.execute("""
                            UPDATE GraceToken 
                            SET used_date = ?, streak_key = ?, reason = ? 
                            WHERE used_date IS NULL 
                            LIMIT 1
                        """, (TODAY.isoformat(), selected_streak, f"Saved {selected_streak} streak"))
                    st.success(f"🛡️ Grace token used to save {selected_streak} streak!")
                    st.rerun()
    
    with col2:
        # Get completion percentages for the last 42 days
        cal_start = TODAY - dt.timedelta(days=41)
        
        # Calculate required quest completion for each day
        daily_completion = {}
        
        for day_offset in range(42):
            check_date = cal_start + dt.timedelta(days=day_offset)
            if check_date > TODAY:
                continue
                
            # Get total possible required quests for that day
            weekday_check = check_date.weekday()
            total_required = 0
            completed_required = 0
            
            # Count required quests based on day configuration
            for tier_key in ["1", "2", "3"]:  # Only required tiers
                tier = DAILY_CFG["tiers"][tier_key]
                if tier["required"]:
                    for q in tier["quests"]:
                        # Check if quest was scheduled for that day
                        if q.get("id") == "strength_day":
                            if weekday_check in q.get("scheduled_days", []):
                                total_required += 1
                        else:
                            total_required += 1
            
            # Check how many were actually completed
            day_logs = conn.execute("""
                SELECT COUNT(DISTINCT label) as completed_count
                FROM TaskLog 
                WHERE date(completed_at) = ?
            """, (check_date.isoformat(),)).fetchone()
            
            if day_logs and day_logs[0]:
                completed_required = min(day_logs[0], total_required)
            
            if total_required > 0:
                completion_pct = (completed_required / total_required) * 100
                daily_completion[check_date.isoformat()] = completion_pct
        
        st.markdown("Red = miss, Yellow = okay (≥50%), Green = great (≥85%)")
        
        # Display calendar grid
        for week in range(6):
            cols = st.columns(7)
            for day_offset in range(7):
                date_index = week * 7 + day_offset
                if date_index < 42:
                    cal_date = cal_start + dt.timedelta(days=date_index)
                    completion = daily_completion.get(cal_date.isoformat(), -1)
                    
                    if cal_date > TODAY:
                        day_class = "day-empty"
                        display_text = str(cal_date.day)
                    elif completion < 0:
                        day_class = "day-empty"
                        display_text = str(cal_date.day)
                    elif completion < 50:
                        day_class = "day-miss"
                        display_text = str(cal_date.day)
                    elif completion < 85:
                        day_class = "day-okay"
                        display_text = str(cal_date.day)
                    else:
                        day_class = "day-great"
                        display_text = str(cal_date.day)
                    
                    with cols[day_offset]:
                        st.markdown(f'''
                            <div class="calendar-day {day_class}">
                                {display_text}
                            </div>
                        ''', unsafe_allow_html=True)
                        if completion >= 0:
                            st.caption(f"{completion:.0f}%")

# ------------------------- Badges/Inventory Tab --------------
with badges_tab:
    st.markdown('<div class="retro-box">🏆 BADGES & INVENTORY</div>', unsafe_allow_html=True)
    
    # Get user's earned badges
    user_badges = conn.execute("""
        SELECT ub.badge_code, ub.awarded_at, b.name, b.description, b.icon
        FROM UserBadge ub
        JOIN Badge b ON ub.badge_code = b.code
        ORDER BY ub.awarded_at DESC
    """).fetchall()
    
    earned_badge_codes = [badge[0] for badge in user_badges]
    
    # Check for new badge unlocks
    def check_badge_unlocks():
        new_badges = []
        
        # Get current stats for badge checking
        total_xp = conn.execute("SELECT COALESCE(SUM(xp_awarded),0) FROM TaskLog").fetchone()[0]
        fitness_xp = conn.execute("""
            SELECT COALESCE(SUM(TaskLog.xp_awarded), 0)
            FROM TaskLog JOIN Skill ON TaskLog.skill_id = Skill.id
            WHERE Skill.category = 'Fitness'
        """).fetchone()[0]
        max_streak = conn.execute("SELECT COALESCE(MAX(current_streak_days), 0) FROM Streak").fetchone()[0]
        quest_count = conn.execute("SELECT COUNT(*) FROM TaskLog").fetchone()[0]
        
        # Check each badge condition
        badge_checks = [
            ("first_quest", quest_count > 0),
            ("week_warrior", max_streak >= 7),
            ("fitness_beast", fitness_xp >= 1000),
            ("streak_master", max_streak >= 14),
            ("grace_guardian", conn.execute("SELECT COUNT(*) FROM GraceToken").fetchone()[0] > 0)
        ]
        
        for badge_code, condition in badge_checks:
            if condition and badge_code not in earned_badge_codes:
                # Award badge
                with conn:
                    conn.execute("INSERT INTO UserBadge(badge_code, awarded_at) VALUES(?,?)",
                               (badge_code, dt.datetime.now().isoformat()))
                new_badges.append(badge_code)
        
        return new_badges
    
    new_badges = check_badge_unlocks()
    if new_badges:
        st.success(f"🎉 New badge(s) unlocked: {', '.join(new_badges)}")
    
    # Display badge collection
    st.markdown("### 🏆 Badge Collection")
    all_badges = conn.execute("SELECT code, name, description, icon FROM Badge ORDER BY code").fetchall()
    
    st.markdown('<div class="inventory-grid">', unsafe_allow_html=True)
    for code, name, desc, icon in all_badges:
        is_earned = code in earned_badge_codes
        badge_class = "badge-item" if is_earned else "badge-item badge-locked"
        display_icon = icon if is_earned else "🔒"
        
        st.markdown(f'''
            <div class="{badge_class}">
                <div style="font-size: 16px;">{display_icon}</div>
                <div style="font-size: 6px; margin-top: 4px;">{name if is_earned else "LOCKED"}</div>
            </div>
        ''', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
    
    # Recently earned badges
    if user_badges:
        st.markdown("### 🆕 Recently Earned")
        for badge_code, awarded_at, name, desc, icon in user_badges[:5]:
            award_date = dt.datetime.fromisoformat(awarded_at).strftime("%b %d")
            st.write(f"{icon} **{name}** - {desc} (Earned: {award_date})")
    
    # Pixel items inventory (cosmetic)
    st.markdown("### 📦 Pixel Items")
    items = [
        ("⚔️", "Rusty Sword", "Your starter weapon"),
        ("🛡️", "Wooden Shield", "Basic protection"), 
        ("🎒", "Adventurer's Pack", "Holds all your stuff"),
        ("📜", "Quest Scroll", "Contains your daily missions"),
        ("🧪", "Health Potion", "Restores energy"),
        ("🔑", "Mysterious Key", "What does it unlock?")
    ]
    
    st.markdown('<div class="inventory-grid">', unsafe_allow_html=True)
    for icon, name, desc in items:
        st.markdown(f'''
            <div class="badge-item">
                <div style="font-size: 16px;">{icon}</div>
                <div style="font-size: 6px; margin-top: 4px;">{name}</div>
            </div>
        ''', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

# ------------------------- Boss Tab --------------------------
with boss_tab:
    st.markdown('<div class="retro-box">👹 BOSS BATTLE READINESS</div>', unsafe_allow_html=True)
    
    # Calculate readiness based on Tier 1 XP vs penalties
    tier1_xp = 0
    tier1_skills = []
    
    # Get Tier 1 skills
    for q in DAILY_CFG["tiers"]["1"]["quests"]:
        skill_name = q.get("skill")
        if skill_name:
            tier1_skills.append(skill_name)
    
    # Calculate total XP from Tier 1 skills
    for skill_name in tier1_skills:
        skill_xp = conn.execute("""
            SELECT COALESCE(SUM(TaskLog.xp_awarded), 0)
            FROM TaskLog 
            JOIN Skill ON TaskLog.skill_id = Skill.id
            WHERE Skill.name = ?
        """, (skill_name,)).fetchone()[0]
        tier1_xp += skill_xp
    
    # Get total penalties
    total_penalties = conn.execute("SELECT COALESCE(SUM(amount),0) FROM Penalty").fetchone()[0]
    
    # Calculate readiness percentage
    net_tier1 = max(0, tier1_xp - total_penalties)
    max_possible = tier1_xp + 1000  # Arbitrary ceiling for percentage calculation
    readiness_pct = min(100, (net_tier1 / max_possible) * 100) if max_possible > 0 else 0
    
    # Determine readiness level
    if readiness_pct >= 80:
        readiness_text = "BOSS SLAYER 👹⚔️"
        readiness_color = "#228b22"
    elif readiness_pct >= 60:
        readiness_text = "BATTLE READY ⚔️"
        readiness_color = "#ffd700"
    elif readiness_pct >= 40:
        readiness_text = "TRAINING HARD 💪"
        readiness_color = "#ff8c00"
    elif readiness_pct >= 20:
        readiness_text = "NOVICE WARRIOR 🗡️"
        readiness_color = "#ff6347"
    else:
        readiness_text = "NEEDS TRAINING 😰"
        readiness_color = "#8b0000"
    
    # Display readiness bar
    st.markdown(f'''
        <div class="boss-bar">
            <div class="boss-fill" style="width: {readiness_pct}%; background: {readiness_color};"></div>
            <div class="boss-text">{readiness_pct:.1f}% - {readiness_text}</div>
        </div>
    ''', unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns(3)
    col1.metric("🏆 Tier 1 XP", tier1_xp)
    col2.metric("💀 Total Penalties", total_penalties)
    col3.metric("⚔️ Net Combat Power", net_tier1)
    
    st.markdown("---")
    st.markdown("**Boss Battle Tips:**")
    st.markdown("• Focus on Tier 1 quests (Squash, Roadwork, Jump Rope, Strength, Flexibility)")
    st.markdown("• Avoid penalties by completing required daily quests")
    st.markdown("• Maintain streaks for XP multipliers")
    st.markdown("• 80%+ readiness = You're ready to face any challenge! 👹⚔️")

# ------------------------- Progress --------------------------
with progress_tab:
    st.markdown('<div class="retro-box">📊 CHARACTER PROGRESSION</div>', unsafe_allow_html=True)

    # Total XP minus penalties
    total_xp = conn.execute("SELECT COALESCE(SUM(xp_awarded),0) FROM TaskLog").fetchone()[0]
    total_pen = conn.execute("SELECT COALESCE(SUM(amount),0) FROM Penalty").fetchone()[0]
    net_xp = max(0, total_xp - total_pen)
    lvl = level_for_xp(net_xp)
    nxt = xp_for_level(lvl+1) - net_xp

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🎯 Total XP", net_xp)
    c2.metric("📈 Character Level", lvl)
    c3.metric("⭐ XP to Next Level", nxt)
    c4.metric("💀 Lifetime Penalties", total_pen)

    st.markdown('<div class="retro-box">🏆 SKILL MASTERY LEVELS</div>', unsafe_allow_html=True)
    cur = conn.execute("""
        SELECT Skill.name, COALESCE(SUM(TaskLog.xp_awarded),0) as sxp
        FROM Skill LEFT JOIN TaskLog ON TaskLog.skill_id = Skill.id
        GROUP BY Skill.id
        ORDER BY sxp DESC
    """)
    rows = cur.fetchall()
    for name, sxp in rows:
        slvl = level_for_xp(sxp)
        if sxp > 0:
            st.write(f"⚔️ **{name}** — Level {slvl} ({sxp} XP)")
        else:
            st.write(f"⚪ **{name}** — Level {slvl} ({sxp} XP)")

    st.markdown('<div class="retro-box">🔥 ACTIVE STREAKS</div>', unsafe_allow_html=True)
    srows = conn.execute("SELECT key, current_streak_days, last_completed_date FROM Streak ORDER BY current_streak_days DESC").fetchall()
    if not srows:
        st.info("🌱 No streaks yet — complete some daily quests to start building momentum!")
    else:
        for key, days, last in srows:
            if days > 0:
                streak_emoji = "🔥" if days >= 5 else "⚡" if days >= 3 else "✨"
                st.write(f"{streak_emoji} **{key}**: {days} day streak (last: {last})")

    st.markdown('<div class="retro-box">📜 RECENT QUEST LOG (7 days)</div>', unsafe_allow_html=True)
    since = (TODAY - dt.timedelta(days=7)).isoformat()
    logs = conn.execute("SELECT completed_at, label, sublabel, xp_awarded FROM TaskLog WHERE date(completed_at) >= ? ORDER BY completed_at DESC", (since,)).fetchall()
    if logs:
        for ts, label, sublabel, xp in logs:
            sub = f" — {sublabel}" if sublabel else ""
            timestamp = ts[:16].replace('T', ' ')
            st.write(f"⚔️ {timestamp} — {label}{sub} (+{xp} XP)")
    else:
        st.info("🎯 No recent activity — start completing quests to see your progress!")

# ------------------------- Config ----------------------------
with config_tab:
    st.markdown('<div class="retro-box">⚙️ GAME CONFIGURATION</div>', unsafe_allow_html=True)
    st.write("🎮 This retro RPG runs **entirely local** (SQLite) with deterministic rules.")
    st.write("📝 Edit DAILY_CFG in the code to customize subquests, targets, and XP rewards.")
    st.write("🎨 Pixel art styling inspired by classic Game Boy aesthetics.")

    st.markdown("### ⚙️ Game Settings")
    st.write("**XP Curve:** 100 × level²")
    st.write("**Streak Bonus:** +10% per day (max +50%)")
    st.write("**Intensity Multipliers:** Easy (0.8×), Standard (1.0×), Hard (1.25×), Max (1.5×)")
    
    st.markdown("### 🗺️ Campaign Configuration")
    
    # Get current settings
    current_length = int(conn.execute("SELECT value FROM Settings WHERE key='campaign_length'").fetchone()[0])
    current_boss_date = conn.execute("SELECT value FROM Settings WHERE key='boss_date'").fetchone()[0]
    
    col1, col2 = st.columns(2)
    
    with col1:
        new_length = st.number_input("Campaign Length (days)", min_value=7, max_value=365, value=current_length)
        if st.button("Update Campaign Length"):
            with conn:
                conn.execute("UPDATE Settings SET value = ? WHERE key = 'campaign_length'", (str(new_length),))
            st.success(f"Campaign length updated to {new_length} days!")
            st.rerun()
    
    with col2:
        new_boss_date = st.date_input("Boss Battle Date", value=dt.date.fromisoformat(current_boss_date))
        if st.button("Update Boss Date"):
            with conn:
                conn.execute("UPDATE Settings SET value = ? WHERE key = 'boss_date'", (new_boss_date.isoformat(),))
            st.success(f"Boss date updated to {new_boss_date.strftime('%B %d, %Y')}!")
            st.rerun()
    
    st.markdown("### 🛡️ Grace Token Management")
    grace_count = conn.execute("SELECT COUNT(*) FROM GraceToken WHERE used_date IS NULL").fetchone()[0]
    st.write(f"Current available tokens: **{grace_count}**")
    
    if st.button("🛡️ Award Grace Token (Admin)"):
        with conn:
            conn.execute("INSERT INTO GraceToken(awarded_date, reason) VALUES(?, ?)",
                       (TODAY.isoformat(), "Admin awarded"))
        st.success("Grace token awarded!")
        st.rerun()

    if st.button("🗑️ RESET GAME DATA (DANGER ZONE)", help="This will delete all progress!"):
        try:
            DB_PATH.unlink(missing_ok=True)
            st.success("🎮 Game data reset! Refresh the page to start fresh.")
            st.rerun()
        except Exception as e:
            st.error(f"Could not reset database: {e}")

