# EduTrack Pro v2 — RFID/NFC Attendance & Grading System

## Tech Stack
- **Backend**: Python Flask + SQLite (server.py)
- **Frontend**: Single-page app (templates/index.html)
- **Database**: SQLite (database/edutrack.db — auto-created on first run)

## Quick Start

### 1. Install dependencies
```bash
pip install flask flask-cors bcrypt pyjwt
```

### 2. Run
```bash
python server.py
```

### 3. Open browser
```
http://localhost:5000
```

## Default Login
- **Admin**: username `admin`, password `admin123`
- **Students**: use their student number (no password)

## Key Features
- 🗓️ Academic Year → Semester hierarchy (everything scoped per active semester)
- 📡 RFID/NFC registration gate for students
- 📊 Full gradesheet with TSU formula (PRS → APRS → WPRS → RPG → transmutation)
- ⏱️ RFID-powered attendance with auto late detection
- 👨‍🎓 Student portal with full score transparency
- 🔐 Persistent login via JWT (auto re-login on return)
- 📱 Mobile-responsive UI

## Architecture
- Each admin has their own completely isolated data (AYs, sections, students)
- Students are scoped to a specific section → semester
- All pages filter by the currently active semester
