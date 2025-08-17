#!/bin/bash

# Phase 1 Setup Script for Life RPG
echo "🎮 Setting up Life RPG Phase 1..."

# 1. Install dependencies
echo "📦 Installing Python dependencies..."
pip install -r requirements.txt

# 2. Create data directory
mkdir -p data

# 3. Instructions
echo ""
echo "✅ Phase 1 Setup Complete!"
echo ""
echo "📱 Next Steps:"
echo "1. Run: streamlit run streamlit_app.py"
echo "2. Open the app and go to '🎯 Daily Board' tab"
echo "3. Scroll to 'PHASE 1: INSTANT NOTIFICATIONS'"
echo "4. Download calendar events and set up ntfy notifications"
echo ""
echo "📋 Phone Setup:"
echo "• Install 'ntfy' app from App Store/Play Store"
echo "• Subscribe to topic: life-rpg-personal"
echo "• Import .ics calendar file to get 10-min reminders"
echo ""
echo "🚀 You'll get notifications like:"
echo "   Title: Life RPG — Today"
echo "   Body: 4:30p Run 4mi, 6:10p Rope 10', 8:00p Study 30'"
echo ""
