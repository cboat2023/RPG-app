# Life RPG - Phase 1: Instant Notifications 📱

Get immediate value from your Life RPG with calendar integration and push notifications!

## ⚡ Quick Start

1. **Run the app**: `streamlit run streamlit_app.py`
2. **Go to Daily Board tab** → scroll to "PHASE 1: INSTANT NOTIFICATIONS"
3. **Download calendar events** (.ics file with 10-min reminders)
4. **Set up ntfy notifications** for instant daily schedules

## 📱 Phone Setup (2 minutes)

### ntfy Push Notifications
1. Install **ntfy** app (App Store/Play Store)
2. Subscribe to topic: `life-rpg-personal` (or create your own)
3. Enable notifications in app settings

### Calendar Integration
1. Download the `.ics` file from the app
2. Import into your phone's calendar (iOS Calendar, Google Calendar, etc.)
3. Enable calendar notifications for 10-minute reminders

## 🎯 What You Get

### Morning Notification
```
Title: Life RPG — Today
Body: 4:30p Run 4mi, 6:10p Rope 10', 8:00p Study 30'
```

### Calendar Events
- **Roadwork: 4 miles** (4:30 PM, 45 min) + 10-min reminder
- **Jump Rope: 10-12 min skill set** (6:10 PM, 12 min) + 10-min reminder  
- **Study session** (8:00 PM, 30 min) + 10-min reminder
- And all other daily quests with optimal timing

## 🤖 Advanced: GitHub Action (Optional)

Set up automated daily notifications at 7am ET:

1. Fork this repo
2. Add GitHub secrets:
   - `NTFY_TOPIC`: Your notification topic
   - `WHOOP_TOKEN`: (Optional) For recovery data
3. Enable GitHub Actions
4. The workflow runs automatically every morning!

## 🔧 Customization

### Quest Timing
Edit `quest_times` in `streamlit_app.py` to adjust when each quest is scheduled:

```python
quest_times = {
    "roadwork_daily": {"hour": 16, "minute": 30, "duration": 45},  # 4:30 PM
    "jumprope_daily": {"hour": 18, "minute": 10, "duration": 12},  # 6:10 PM
    # ... customize your perfect schedule
}
```

### Notification Topic
Use a unique topic name like `life-rpg-yourname` to avoid conflicts.

## 📋 Phase 1 Benefits

✅ **No app store required** - uses standard calendar + ntfy  
✅ **10-minute reminders** - never miss a quest  
✅ **Morning overview** - see your whole day at a glance  
✅ **Cross-platform** - works on iOS, Android, web  
✅ **Immediate value** - feel the productivity boost today  

## 🚀 Next Phases

- **Phase 2**: WHOOP integration for recovery-based planning
- **Phase 3**: Smart rescheduling and habit optimization
- **Phase 4**: Social features and accountability

---

**You'll feel the jump immediately!** 🎮
