"""
EduTrack Pro v2 — Flask + SQLite Backend
Full multi-user, per-semester scoped academic system
"""
import sqlite3, bcrypt, jwt, os
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import Flask, request, jsonify, g, send_from_directory, send_file
from flask_cors import CORS

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
# Use /tmp for Render free plan, or custom path via env var
_default_db = os.path.join(BASE_DIR, 'database', 'edutrack.db')
DB_PATH = os.environ.get('DB_PATH', _default_db)
# Auto-create parent directory
os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else '.', exist_ok=True)
JWT_SECRET = os.environ.get('JWT_SECRET', 'edutrack-pro-secret-2025-xyz')
JWT_EXPIRY = 72

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, PATCH'
    response.headers['Access-Control-Max-Age'] = '86400'
    # Prevent browser from caching HTML page (avoids stale JS issues)
    if request.path == '/' or not request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
    return response

@app.route('/api/<path:path>', methods=['OPTIONS'])
def handle_options(path):
    return '', 204

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db: db.close()

def Q(sql, p=(), one=False):
    cur = get_db().execute(sql, p)
    r = cur.fetchone() if one else cur.fetchall()
    return (dict(r) if r else None) if one else [dict(x) for x in r]

def X(sql, p=()):
    db = get_db(); cur = db.execute(sql, p); db.commit(); return cur.lastrowid

def XM(sql, rows):
    db = get_db(); db.executemany(sql, rows); db.commit()

def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        role TEXT NOT NULL CHECK(role IN ('admin','student')),
        username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        email TEXT DEFAULT '',
        student_id INTEGER,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS academic_years (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        year TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(user_id, year)
    );
    CREATE TABLE IF NOT EXISTS semesters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ay_id INTEGER NOT NULL REFERENCES academic_years(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL,
        label TEXT NOT NULL,
        is_active INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(ay_id, label)
    );
    CREATE TABLE IF NOT EXISTS sections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sem_id INTEGER NOT NULL REFERENCES semesters(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        subject_code TEXT NOT NULL DEFAULT '',
        subject_full TEXT DEFAULT '',
        program TEXT DEFAULT 'BSIT',
        year_level INTEGER DEFAULT 3,
        subject_type TEXT DEFAULT 'lec-lab',
        professor TEXT DEFAULT '',
        late_threshold INTEGER DEFAULT 15,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS schedule_slots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        section_id INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
        day TEXT NOT NULL,
        slot_type TEXT DEFAULT 'lecture',
        time_start TEXT DEFAULT '',
        time_end TEXT DEFAULT '',
        room TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        section_id INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
        student_no TEXT NOT NULL UNIQUE,
        full_name TEXT NOT NULL,
        gender TEXT DEFAULT 'M',
        year_level TEXT DEFAULT '3rd Year',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS rfid_tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL UNIQUE REFERENCES students(id) ON DELETE CASCADE,
        uid TEXT NOT NULL UNIQUE,
        self_registered INTEGER DEFAULT 0,
        registered_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        section_id INTEGER NOT NULL REFERENCES sections(id),
        slot_id INTEGER,
        term TEXT NOT NULL DEFAULT 'mt',
        sched_day TEXT DEFAULT '',
        slot_type TEXT DEFAULT 'lecture',
        time_start TEXT DEFAULT '',
        time_end TEXT DEFAULT '',
        room TEXT DEFAULT '',
        start_ts TEXT DEFAULT (datetime('now')),
        end_ts TEXT,
        is_open INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        status TEXT NOT NULL CHECK(status IN ('present','late','excuse','absent')),
        timestamp TEXT,
        note TEXT DEFAULT '',
        UNIQUE(session_id, student_id)
    );
    CREATE TABLE IF NOT EXISTS grade_configs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        section_id INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
        term TEXT NOT NULL,
        component TEXT NOT NULL,
        label TEXT NOT NULL,
        weight INTEGER NOT NULL DEFAULT 20,
        max_score REAL DEFAULT 0,
        col_order INTEGER DEFAULT 0,
        UNIQUE(section_id, term, component, col_order)
    );
    CREATE TABLE IF NOT EXISTS grade_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        section_id INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
        student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        term TEXT NOT NULL,
        component TEXT NOT NULL,
        col_order INTEGER NOT NULL DEFAULT 0,
        score REAL DEFAULT 0,
        UNIQUE(section_id, student_id, term, component, col_order)
    );
    """)
    row = db.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
    if not row:
        pw = bcrypt.hashpw(b'admin123', bcrypt.gensalt()).decode()
        db.execute("INSERT INTO users (role,username,password,name) VALUES ('admin','admin',?,'Administrator')", (pw,))
    # Auto-migrate: add missing user_id columns for existing DBs
    for _m in [
        "ALTER TABLE academic_years ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE semesters ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE sections ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1",
    ]:
        try: db.execute(_m)
        except: pass
    db.commit(); db.close()
    print("✅ DB ready:", DB_PATH)
    # Verify admin credentials on every startup
    _verify_admin_on_start()

def _verify_admin_on_start():
    """Ensure admin/admin123 always works. Resets if hash is broken."""
    import sqlite3 as _sq
    db2 = _sq.connect(DB_PATH)
    db2.row_factory = _sq.Row
    row = db2.execute("SELECT * FROM users WHERE username='admin' AND role='admin'").fetchone()
    if row:
        ok = False
        try: ok = bcrypt.checkpw(b'admin123', row['password'].encode())
        except: pass
        if not ok:
            new_pw = bcrypt.hashpw(b'admin123', bcrypt.gensalt()).decode()
            db2.execute("UPDATE users SET password=? WHERE username='admin'", (new_pw,))
            db2.commit()
            print("⚠️  Admin password was invalid — reset to admin123")
        else:
            print("✅ Admin password verified OK")
    db2.close()

def make_token(uid, role):
    exp = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY)
    return jwt.encode({'sub': str(uid), 'role': role, 'exp': exp}, JWT_SECRET, algorithm='HS256')

def verify_token(token):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        payload['sub'] = int(payload['sub'])  # convert back to int
        return payload
    except Exception as e:
        print(f"[VERIFY_TOKEN ERROR] {type(e).__name__}: {e}")
        return None

def get_token_from_request():
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        return auth[7:].strip()
    # Also check cookie fallback
    return request.cookies.get('et_token', '')

def auth_required(f):
    @wraps(f)
    def w(*a, **kw):
        tok = get_token_from_request()
        if not tok:
            print(f"[AUTH] No token for {request.method} {request.path}")
            return jsonify({'error':'Unauthorized - no token'}), 401
        p = verify_token(tok)
        if not p:
            print(f"[AUTH] Invalid token for {request.method} {request.path} — tok[:20]={tok[:20]}")
            return jsonify({'error':'Unauthorized - invalid token'}), 401
        g.user_id = int(p['sub']); g.role = p['role']
        return f(*a, **kw)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a, **kw):
        tok = get_token_from_request()
        if not tok:
            print(f"[ADMIN] No token for {request.method} {request.path}")
            return jsonify({'error':'Unauthorized - no token'}), 401
        p = verify_token(tok)
        if not p:
            print(f"[ADMIN] Invalid token for {request.method} {request.path} — tok[:20]={tok[:20]}")
            return jsonify({'error':'Unauthorized - invalid token'}), 401
        if p['role'] != 'admin':
            print(f"[ADMIN] Role mismatch: got {p['role']} for {request.path}")
            return jsonify({'error':'Admin only'}), 403
        g.user_id = int(p['sub']); g.role = 'admin'
        return f(*a, **kw)
    return w

# ── AUTH ──────────────────────────────────────────────────────────────────
@app.route('/api/auth/login', methods=['POST'])
def login():
    d = request.json or {}
    role = d.get('role', 'admin')
    if role == 'admin':
        u = Q("SELECT * FROM users WHERE role='admin' AND username=?", (d.get('username',''),), one=True)
        if not u or not bcrypt.checkpw(d.get('password','').encode(), u['password'].encode()):
            return jsonify({'error':'Invalid username or password'}), 401
        return jsonify({'token': make_token(u['id'], 'admin'), 'user': {k:v for k,v in u.items() if k!='password'}, 'role':'admin'})
    else:
        sno = d.get('student_no','').strip()
        st  = Q("SELECT * FROM students WHERE student_no=?", (sno,), one=True)
        if not st: return jsonify({'error':'Student number not found'}), 401
        u = Q("SELECT * FROM users WHERE student_id=?", (st['id'],), one=True)
        if not u:
            pw = bcrypt.hashpw(sno.encode(), bcrypt.gensalt()).decode()
            uid = X("INSERT OR IGNORE INTO users (role,username,password,name,student_id) VALUES ('student',?,?,?,?)", (sno, pw, st['full_name'], st['id']))
            u = Q("SELECT * FROM users WHERE id=?", (uid,), one=True) or Q("SELECT * FROM users WHERE student_id=?", (st['id'],), one=True)
        tag = Q("SELECT id FROM rfid_tags WHERE student_id=?", (st['id'],), one=True)
        return jsonify({'token': make_token(u['id'], 'student'), 'user': dict(st), 'role':'student', 'has_rfid': bool(tag), 'student_id': st['id']})

@app.route('/api/auth/me', methods=['GET'])
@auth_required
def get_me():
    u = Q("SELECT * FROM users WHERE id=?", (g.user_id,), one=True)
    if not u: return jsonify({'error':'Not found'}), 404
    return jsonify({k:v for k,v in u.items() if k!='password'})

@app.route('/api/auth/update-profile', methods=['PUT'])
@auth_required
def update_profile():
    d = request.json or {}
    if Q("SELECT id FROM users WHERE username=? AND id!=?", (d.get('username',''), g.user_id), one=True):
        return jsonify({'error':'Username taken'}), 400
    X("UPDATE users SET name=?, username=? WHERE id=?", (d.get('name'), d.get('username'), g.user_id))
    return jsonify({'ok': True})

@app.route('/api/auth/change-password', methods=['PUT'])
@auth_required
def change_password():
    d = request.json or {}
    u = Q("SELECT * FROM users WHERE id=?", (g.user_id,), one=True)
    if not bcrypt.checkpw(d.get('current','').encode(), u['password'].encode()):
        return jsonify({'error':'Current password incorrect'}), 400
    X("UPDATE users SET password=? WHERE id=?", (bcrypt.hashpw(d.get('new','').encode(), bcrypt.gensalt()).decode(), g.user_id))
    return jsonify({'ok': True})

# ── ACADEMIC YEARS (per admin user) ──────────────────────────────────────
@app.route('/api/academic-years', methods=['GET'])
@auth_required
def get_academic_years():
    if g.role == 'student':
        u = Q("SELECT student_id FROM users WHERE id=?", (g.user_id,), one=True)
        if not u or not u['student_id']: return jsonify([])
        st = Q("SELECT section_id FROM students WHERE id=?", (u['student_id'],), one=True)
        if not st: return jsonify([])
        sem = Q("SELECT s.*, a.year FROM semesters s JOIN academic_years a ON s.ay_id=a.id JOIN sections sec ON sec.sem_id=s.id WHERE sec.id=?", (st['section_id'],), one=True)
        return jsonify([sem] if sem else [])
    ays = Q("SELECT * FROM academic_years WHERE user_id=? ORDER BY year DESC", (g.user_id,))
    for ay in ays:
        ay['semesters'] = Q("SELECT * FROM semesters WHERE ay_id=? ORDER BY id", (ay['id'],))
    return jsonify(ays)

@app.route('/api/academic-years', methods=['POST'])
@admin_required
def create_academic_year():
    d = request.json or {}
    year = d.get('year','').strip()
    if not year: return jsonify({'error':'Year required'}), 400
    if Q("SELECT id FROM academic_years WHERE user_id=? AND year=?", (g.user_id, year), one=True):
        return jsonify({'error':'Year already exists'}), 400
    ay_id = X("INSERT INTO academic_years (user_id, year) VALUES (?,?)", (g.user_id, year))
    XM("INSERT INTO semesters (ay_id, user_id, label) VALUES (?,?,?)", [(ay_id, g.user_id, s) for s in ['1st Semester','2nd Semester','Summer']])
    return jsonify({'id': ay_id, 'year': year})

@app.route('/api/academic-years/<int:ay_id>', methods=['DELETE'])
@admin_required
def delete_academic_year(ay_id):
    if not Q("SELECT id FROM academic_years WHERE id=? AND user_id=?", (ay_id, g.user_id), one=True):
        return jsonify({'error':'Not found'}), 404
    X("DELETE FROM academic_years WHERE id=?", (ay_id,))
    return jsonify({'ok': True})

# ── SEMESTERS ─────────────────────────────────────────────────────────────
@app.route('/api/semesters', methods=['POST'])
@admin_required
def add_semester():
    d = request.json or {}
    ay_id = d.get('ay_id'); label = d.get('label','').strip()
    if not ay_id or not label: return jsonify({'error':'ay_id and label required'}), 400
    if not Q("SELECT id FROM academic_years WHERE id=? AND user_id=?", (ay_id, g.user_id), one=True):
        return jsonify({'error':'AY not found'}), 404
    if Q("SELECT id FROM semesters WHERE ay_id=? AND label=?", (ay_id, label), one=True):
        return jsonify({'error':'Label exists'}), 400
    sid = X("INSERT INTO semesters (ay_id, user_id, label) VALUES (?,?,?)", (ay_id, g.user_id, label))
    return jsonify({'id': sid, 'label': label, 'ay_id': ay_id, 'is_active': 0})

@app.route('/api/semesters/<int:sem_id>/activate', methods=['PUT'])
@admin_required
def activate_semester(sem_id):
    sem = Q("SELECT s.*, a.year FROM semesters s JOIN academic_years a ON s.ay_id=a.id WHERE s.id=? AND a.user_id=?", (sem_id, g.user_id), one=True)
    if not sem: return jsonify({'error':'Not found'}), 404
    X("UPDATE semesters SET is_active=0 WHERE user_id=?", (g.user_id,))
    X("UPDATE semesters SET is_active=1 WHERE id=?", (sem_id,))
    return jsonify({'ok': True, 'sem_id': sem_id, 'ay_id': sem['ay_id'], 'label': sem['label'], 'year': sem['year']})

@app.route('/api/semesters/active', methods=['GET'])
@auth_required
def get_active_semester():
    if g.role == 'student':
        u = Q("SELECT student_id FROM users WHERE id=?", (g.user_id,), one=True)
        if not u or not u['student_id']: return jsonify({}), 200
        st = Q("SELECT section_id FROM students WHERE id=?", (u['student_id'],), one=True)
        if not st: return jsonify({}), 200
        sem = Q("SELECT s.*, a.year, a.id AS ay_id FROM semesters s JOIN academic_years a ON s.ay_id=a.id JOIN sections sec ON sec.sem_id=s.id WHERE sec.id=?", (st['section_id'],), one=True)
        return jsonify(sem or {})
    sem = Q("SELECT s.*, a.year, a.id AS ay_id FROM semesters s JOIN academic_years a ON s.ay_id=a.id WHERE s.is_active=1 AND a.user_id=?", (g.user_id,), one=True)
    return jsonify(sem or {})

# ── SECTIONS ──────────────────────────────────────────────────────────────
def _sec_base_query():
    return "SELECT sec.*, sem.label AS sem_label, a.year AS ay_year FROM sections sec JOIN semesters sem ON sec.sem_id=sem.id JOIN academic_years a ON sem.ay_id=a.id"

def _attach_slots_count(secs):
    for s in secs:
        s['slots'] = Q("SELECT * FROM schedule_slots WHERE section_id=? ORDER BY id", (s['id'],))
        s['student_count'] = Q("SELECT COUNT(*) AS c FROM students WHERE section_id=?", (s['id'],), one=True)['c']
    return secs

@app.route('/api/sections', methods=['GET'])
@auth_required
def get_sections():
    sem_id = request.args.get('sem_id')
    if g.role == 'student':
        u = Q("SELECT student_id FROM users WHERE id=?", (g.user_id,), one=True)
        if not u or not u['student_id']: return jsonify([])
        st = Q("SELECT section_id FROM students WHERE id=?", (u['student_id'],), one=True)
        if not st: return jsonify([])
        secs = Q(_sec_base_query()+" WHERE sec.id=?", (st['section_id'],))
        return jsonify(_attach_slots_count(secs))
    if sem_id:
        ok = Q("SELECT s.id FROM semesters s JOIN academic_years a ON s.ay_id=a.id WHERE s.id=? AND a.user_id=?", (sem_id, g.user_id), one=True)
        if not ok: return jsonify([])
        secs = Q(_sec_base_query()+" WHERE sec.sem_id=?", (sem_id,))
    else:
        secs = Q(_sec_base_query()+" WHERE sec.user_id=? ORDER BY a.year DESC, sem.id", (g.user_id,))
    return jsonify(_attach_slots_count(secs))

@app.route('/api/sections', methods=['POST'])
@admin_required
def create_section():
    d = request.json or {}
    sem_id = d.get('sem_id')
    if not sem_id or not d.get('name'): return jsonify({'error':'sem_id and name required'}), 400
    ok = Q("SELECT s.id FROM semesters s JOIN academic_years a ON s.ay_id=a.id WHERE s.id=? AND a.user_id=?", (sem_id, g.user_id), one=True)
    if not ok: return jsonify({'error':'Semester not found'}), 404
    sec_id = X("INSERT INTO sections (sem_id,user_id,name,subject_code,subject_full,program,year_level,subject_type,professor,late_threshold) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (sem_id, g.user_id, d['name'], d.get('subject_code',''), d.get('subject_full',''), d.get('program','BSIT'), d.get('year_level',3), d.get('subject_type','lec-lab'), d.get('professor',''), d.get('late_threshold',15)))
    if d.get('slots'):
        XM("INSERT INTO schedule_slots (section_id,day,slot_type,time_start,time_end,room) VALUES (?,?,?,?,?,?)",
           [(sec_id, s['day'], s.get('type','lecture'), s.get('timeStart',''), s.get('timeEnd',''), s.get('room','')) for s in d['slots']])
    return jsonify({'id': sec_id})

@app.route('/api/sections/<int:sec_id>', methods=['PUT'])
@admin_required
def update_section(sec_id):
    d = request.json or {}
    if not Q("SELECT id FROM sections WHERE id=? AND user_id=?", (sec_id, g.user_id), one=True):
        return jsonify({'error':'Not found'}), 404
    X("UPDATE sections SET name=?,subject_code=?,subject_full=?,program=?,year_level=?,subject_type=?,professor=?,late_threshold=? WHERE id=?",
      (d.get('name'), d.get('subject_code'), d.get('subject_full'), d.get('program'), d.get('year_level'), d.get('subject_type'), d.get('professor'), d.get('late_threshold'), sec_id))
    X("DELETE FROM schedule_slots WHERE section_id=?", (sec_id,))
    if d.get('slots'):
        XM("INSERT INTO schedule_slots (section_id,day,slot_type,time_start,time_end,room) VALUES (?,?,?,?,?,?)",
           [(sec_id, s['day'], s.get('type','lecture'), s.get('timeStart',''), s.get('timeEnd',''), s.get('room','')) for s in d['slots']])
    return jsonify({'ok': True})

@app.route('/api/sections/<int:sec_id>', methods=['DELETE'])
@admin_required
def delete_section(sec_id):
    if not Q("SELECT id FROM sections WHERE id=? AND user_id=?", (sec_id, g.user_id), one=True):
        return jsonify({'error':'Not found'}), 404
    X("DELETE FROM sections WHERE id=?", (sec_id,))
    return jsonify({'ok': True})

# ── STUDENTS ──────────────────────────────────────────────────────────────
@app.route('/api/students', methods=['GET'])
@auth_required
def get_students():
    sec_id = request.args.get('section_id'); sem_id = request.args.get('sem_id'); q_str = request.args.get('q','')
    if g.role == 'student':
        u = Q("SELECT student_id FROM users WHERE id=?", (g.user_id,), one=True)
        if not u or not u['student_id']: return jsonify([])
        st = Q("SELECT s.*, r.uid, r.id AS tag_id, sec.name AS section_name FROM students s JOIN sections sec ON s.section_id=sec.id LEFT JOIN rfid_tags r ON s.id=r.student_id WHERE s.id=?", (u['student_id'],), one=True)
        return jsonify([st] if st else [])
    base = "SELECT s.*, r.uid, r.id AS tag_id, sec.name AS section_name FROM students s JOIN sections sec ON s.section_id=sec.id LEFT JOIN rfid_tags r ON s.id=r.student_id"
    if sec_id:
        if not Q("SELECT id FROM sections WHERE id=? AND user_id=?", (sec_id, g.user_id), one=True): return jsonify([])
        rows = Q(base+" WHERE s.section_id=? ORDER BY s.full_name", (sec_id,))
    elif sem_id:
        ok = Q("SELECT s.id FROM semesters s JOIN academic_years a ON s.ay_id=a.id WHERE s.id=? AND a.user_id=?", (sem_id, g.user_id), one=True)
        if not ok: return jsonify([])
        rows = Q(base+" WHERE sec.sem_id=? ORDER BY sec.name, s.full_name", (sem_id,))
    else:
        rows = Q(base+" WHERE sec.user_id=? ORDER BY sec.name, s.full_name", (g.user_id,))
    if q_str:
        ql = q_str.lower()
        rows = [r for r in rows if ql in r['full_name'].lower() or ql in r['student_no']]
    return jsonify(rows)

@app.route('/api/students', methods=['POST'])
@admin_required
def create_student():
    d = request.json or {}
    sno = d.get('student_no','').strip(); name = d.get('full_name','').strip(); sec_id = d.get('section_id')
    if not sno or not name or not sec_id: return jsonify({'error':'student_no, full_name, section_id required'}), 400
    if not Q("SELECT id FROM sections WHERE id=? AND user_id=?", (sec_id, g.user_id), one=True):
        return jsonify({'error':'Section not found or not yours'}), 404
    if Q("SELECT id FROM students WHERE student_no=?", (sno,), one=True):
        return jsonify({'error':'Duplicate student number'}), 400
    st_id = X("INSERT INTO students (section_id,student_no,full_name,gender,year_level) VALUES (?,?,?,?,?)",
              (sec_id, sno, name, d.get('gender','M'), d.get('year_level','3rd Year')))
    pw = bcrypt.hashpw(sno.encode(), bcrypt.gensalt()).decode()
    X("INSERT OR IGNORE INTO users (role,username,password,name,student_id) VALUES ('student',?,?,?,?)", (sno, pw, name, st_id))
    return jsonify({'id': st_id})

@app.route('/api/students/import', methods=['POST'])
@admin_required
def import_students():
    d = request.json or {}
    sec_id = d.get('section_id'); rows = d.get('rows',[])
    if not sec_id or not rows: return jsonify({'error':'section_id and rows required'}), 400
    if not Q("SELECT id FROM sections WHERE id=? AND user_id=?", (sec_id, g.user_id), one=True):
        return jsonify({'error':'Section not found'}), 404
    added = skipped = 0
    for r in rows:
        sno = str(r.get('student_no','')).strip(); name = str(r.get('full_name','')).strip()
        if not sno or not name: continue
        if Q("SELECT id FROM students WHERE student_no=?", (sno,), one=True): skipped += 1; continue
        st_id = X("INSERT INTO students (section_id,student_no,full_name,gender,year_level) VALUES (?,?,?,?,?)",
                  (sec_id, sno, name, r.get('gender','M'), r.get('year_level','3rd Year')))
        pw = bcrypt.hashpw(sno.encode(), bcrypt.gensalt()).decode()
        X("INSERT OR IGNORE INTO users (role,username,password,name,student_id) VALUES ('student',?,?,?,?)", (sno, pw, name, st_id))
        added += 1
    return jsonify({'added': added, 'skipped': skipped})

@app.route('/api/students/<int:st_id>', methods=['PUT'])
@admin_required
def update_student(st_id):
    d = request.json or {}
    st = Q("SELECT s.id, s.section_id FROM students s JOIN sections sec ON s.section_id=sec.id WHERE s.id=? AND sec.user_id=?", (st_id, g.user_id), one=True)
    if not st: return jsonify({'error':'Not found'}), 404
    new_sec = d.get('section_id', st['section_id'])
    if not Q("SELECT id FROM sections WHERE id=? AND user_id=?", (new_sec, g.user_id), one=True):
        return jsonify({'error':'Target section not found'}), 404
    X("UPDATE students SET full_name=?,gender=?,year_level=?,section_id=? WHERE id=?", (d.get('full_name'), d.get('gender'), d.get('year_level'), new_sec, st_id))
    return jsonify({'ok': True})

@app.route('/api/students/<int:st_id>', methods=['DELETE'])
@admin_required
def delete_student(st_id):
    st = Q("SELECT s.student_no FROM students s JOIN sections sec ON s.section_id=sec.id WHERE s.id=? AND sec.user_id=?", (st_id, g.user_id), one=True)
    if not st: return jsonify({'error':'Not found'}), 404
    X("DELETE FROM students WHERE id=?", (st_id,))
    X("DELETE FROM users WHERE username=? AND role='student'", (st['student_no'],))
    return jsonify({'ok': True})

# ── RFID ──────────────────────────────────────────────────────────────────
@app.route('/api/rfid', methods=['GET'])
@auth_required
def get_rfid():
    sem_id = request.args.get('sem_id')
    if sem_id:
        ok = Q("SELECT s.id FROM semesters s JOIN academic_years a ON s.ay_id=a.id WHERE s.id=? AND a.user_id=?", (sem_id, g.user_id), one=True)
        if not ok: return jsonify([])
        rows = Q("SELECT r.*, s.full_name, s.student_no, sec.name AS section_name FROM rfid_tags r JOIN students s ON r.student_id=s.id JOIN sections sec ON s.section_id=sec.id WHERE sec.sem_id=?", (sem_id,))
    else:
        rows = Q("SELECT r.*, s.full_name, s.student_no, sec.name AS section_name FROM rfid_tags r JOIN students s ON r.student_id=s.id JOIN sections sec ON s.section_id=sec.id WHERE sec.user_id=?", (g.user_id,))
    return jsonify(rows)

@app.route('/api/rfid', methods=['POST'])
@auth_required
def register_rfid():
    d = request.json or {}
    uid = d.get('uid','').strip().upper(); student_id = d.get('student_id')
    if not uid or not student_id: return jsonify({'error':'uid and student_id required'}), 400
    if Q("SELECT id FROM rfid_tags WHERE uid=?", (uid,), one=True):
        return jsonify({'error':'Card UID already registered to another student'}), 400
    if Q("SELECT id FROM rfid_tags WHERE student_id=?", (student_id,), one=True):
        return jsonify({'error':'Student already has a card'}), 400
    tag_id = X("INSERT INTO rfid_tags (student_id,uid,self_registered) VALUES (?,?,?)", (student_id, uid, 1 if d.get('self_registered') else 0))
    return jsonify({'id': tag_id, 'uid': uid})

@app.route('/api/rfid/<int:tag_id>', methods=['DELETE'])
@auth_required
def delete_rfid(tag_id):
    X("DELETE FROM rfid_tags WHERE id=?", (tag_id,))
    return jsonify({'ok': True})

@app.route('/api/rfid/lookup/<uid>', methods=['GET'])
@auth_required
def lookup_rfid(uid):
    tag = Q("SELECT r.*, s.id AS student_id, s.full_name, s.student_no, s.section_id, sec.name AS section_name FROM rfid_tags r JOIN students s ON r.student_id=s.id JOIN sections sec ON s.section_id=sec.id WHERE r.uid=?", (uid.upper(),), one=True)
    if not tag: return jsonify({'error':'Not found'}), 404
    return jsonify(tag)

# ── SESSIONS ──────────────────────────────────────────────────────────────
@app.route('/api/sessions', methods=['GET'])
@auth_required
def get_sessions():
    sem_id = request.args.get('sem_id'); sec_id = request.args.get('section_id')
    base = "SELECT s.*, sec.name AS section_name, sec.subject_code FROM sessions s JOIN sections sec ON s.section_id=sec.id"
    if sec_id:
        rows = Q(base+" WHERE s.section_id=? ORDER BY s.start_ts DESC", (sec_id,))
    elif sem_id:
        rows = Q(base+" WHERE sec.sem_id=? AND sec.user_id=? ORDER BY s.start_ts DESC", (sem_id, g.user_id))
    else:
        rows = Q(base+" WHERE sec.user_id=? ORDER BY s.start_ts DESC LIMIT 100", (g.user_id,))
    for row in rows:
        cts = Q("SELECT status, COUNT(*) AS c FROM attendance WHERE session_id=? GROUP BY status", (row['id'],))
        row['counts'] = {r['status']:r['c'] for r in cts}
    return jsonify(rows)

@app.route('/api/sessions', methods=['POST'])
@admin_required
def start_session():
    d = request.json or {}
    sec_id = d.get('section_id')
    if not sec_id: return jsonify({'error':'section_id required'}), 400
    X("UPDATE sessions SET is_open=0, end_ts=datetime('now') WHERE section_id=? AND is_open=1", (sec_id,))
    sid = X("INSERT INTO sessions (section_id,slot_id,term,sched_day,slot_type,time_start,time_end,room) VALUES (?,?,?,?,?,?,?,?)",
            (sec_id, d.get('slot_id'), d.get('term','mt'), d.get('sched_day',''), d.get('slot_type','lecture'), d.get('time_start',''), d.get('time_end',''), d.get('room','')))
    return jsonify({'id': sid})

@app.route('/api/sessions/<int:sid>/close', methods=['PUT'])
@admin_required
def close_session(sid):
    sess = Q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
    if not sess: return jsonify({'error':'Not found'}), 404
    students = Q("SELECT id FROM students WHERE section_id=?", (sess['section_id'],))
    recorded = {r['student_id'] for r in Q("SELECT student_id FROM attendance WHERE session_id=?", (sid,))}
    absent = [s['id'] for s in students if s['id'] not in recorded]
    if absent:
        XM("INSERT OR IGNORE INTO attendance (session_id,student_id,status) VALUES (?,?,'absent')", [(sid, s) for s in absent])
    X("UPDATE sessions SET is_open=0, end_ts=datetime('now') WHERE id=?", (sid,))
    return jsonify({'ok': True, 'absent_marked': len(absent)})

@app.route('/api/sessions/<int:sid>', methods=['DELETE'])
@admin_required
def delete_session(sid):
    X("DELETE FROM attendance WHERE session_id=?", (sid,))
    X("DELETE FROM sessions WHERE id=?", (sid,))
    return jsonify({'ok': True})

# ── ATTENDANCE ────────────────────────────────────────────────────────────
@app.route('/api/attendance', methods=['POST'])
@auth_required
def record_attendance():
    d = request.json or {}
    sid = d.get('session_id'); stid = d.get('student_id'); status = d.get('status','present')
    if not sid or not stid: return jsonify({'error':'session_id and student_id required'}), 400
    ts = datetime.now().isoformat()
    ex = Q("SELECT id FROM attendance WHERE session_id=? AND student_id=?", (sid, stid), one=True)
    if ex:
        X("UPDATE attendance SET status=?,note=?,timestamp=? WHERE id=?", (status, d.get('note',''), ts, ex['id']))
        return jsonify({'updated': True, 'id': ex['id']})
    aid = X("INSERT INTO attendance (session_id,student_id,status,note,timestamp) VALUES (?,?,?,?,?)", (sid, stid, status, d.get('note',''), ts))
    return jsonify({'id': aid, 'status': status})

@app.route('/api/attendance/<int:aid>', methods=['PUT'])
@auth_required
def update_attendance(aid):
    d = request.json or {}
    X("UPDATE attendance SET status=?,note=? WHERE id=?", (d.get('status'), d.get('note',''), aid))
    return jsonify({'ok': True})

@app.route('/api/attendance/session/<int:sid>', methods=['GET'])
@auth_required
def get_session_attendance(sid):
    return jsonify(Q("SELECT a.*, s.full_name, s.student_no FROM attendance a JOIN students s ON a.student_id=s.id WHERE a.session_id=? ORDER BY s.full_name", (sid,)))

@app.route('/api/attendance/student/<int:st_id>', methods=['GET'])
@auth_required
def get_student_attendance(st_id):
    return jsonify(Q("SELECT a.*, ss.start_ts, ss.sched_day, ss.slot_type, ss.term, ss.time_start FROM attendance a JOIN sessions ss ON a.session_id=ss.id WHERE a.student_id=? ORDER BY ss.start_ts DESC", (st_id,)))

@app.route('/api/attendance/summary/<int:sec_id>', methods=['GET'])
@auth_required
def attendance_summary(sec_id):
    term = request.args.get('term')
    if term:
        rows = Q("""SELECT s.id AS student_id, s.full_name, s.student_no,
                    COUNT(CASE WHEN a.status='present' THEN 1 END) AS present,
                    COUNT(CASE WHEN a.status='late' THEN 1 END) AS late,
                    COUNT(CASE WHEN a.status='excuse' THEN 1 END) AS excuse,
                    COUNT(CASE WHEN a.status='absent' THEN 1 END) AS absent,
                    COUNT(a.id) AS total
                    FROM students s LEFT JOIN attendance a ON s.id=a.student_id
                    LEFT JOIN sessions ss ON a.session_id=ss.id AND ss.term=?
                    WHERE s.section_id=? GROUP BY s.id ORDER BY s.full_name""", (term, sec_id))
    else:
        rows = Q("""SELECT s.id AS student_id, s.full_name, s.student_no,
                    COUNT(CASE WHEN a.status='present' THEN 1 END) AS present,
                    COUNT(CASE WHEN a.status='late' THEN 1 END) AS late,
                    COUNT(CASE WHEN a.status='excuse' THEN 1 END) AS excuse,
                    COUNT(CASE WHEN a.status='absent' THEN 1 END) AS absent,
                    COUNT(a.id) AS total
                    FROM students s LEFT JOIN attendance a ON s.id=a.student_id
                    WHERE s.section_id=? GROUP BY s.id ORDER BY s.full_name""", (sec_id,))
    return jsonify(rows)

# ── GRADES ────────────────────────────────────────────────────────────────
@app.route('/api/grades/config/<int:sec_id>/<term>', methods=['GET'])
@auth_required
def get_grade_config(sec_id, term):
    return jsonify(Q("SELECT * FROM grade_configs WHERE section_id=? AND term=? ORDER BY component, col_order", (sec_id, term)))

@app.route('/api/grades/config', methods=['POST'])
@admin_required
def save_grade_config():
    d = request.json or {}
    sec_id = d.get('section_id'); term = d.get('term'); cols = d.get('columns',[])
    if not sec_id or not term: return jsonify({'error':'section_id and term required'}), 400
    X("DELETE FROM grade_configs WHERE section_id=? AND term=?", (sec_id, term))
    if cols:
        XM("INSERT INTO grade_configs (section_id,term,component,label,weight,max_score,col_order) VALUES (?,?,?,?,?,?,?)",
           [(sec_id, term, c['component'], c['label'], c['weight'], c.get('max_score',0), c.get('col_order',0)) for c in cols])
    return jsonify({'ok': True})

@app.route('/api/grades/scores/<int:sec_id>/<term>', methods=['GET'])
@auth_required
def get_grade_scores(sec_id, term):
    return jsonify(Q("SELECT * FROM grade_scores WHERE section_id=? AND term=? ORDER BY student_id,component,col_order", (sec_id, term)))

@app.route('/api/grades/scores/bulk', methods=['POST'])
@admin_required
def save_grade_scores_bulk():
    d = request.json or {}
    rows = d.get('rows',[])
    if not rows: return jsonify({'error':'rows required'}), 400
    XM("""INSERT INTO grade_scores (section_id,student_id,term,component,col_order,score) VALUES (?,?,?,?,?,?)
          ON CONFLICT(section_id,student_id,term,component,col_order) DO UPDATE SET score=excluded.score""",
       [(r['section_id'],r['student_id'],r['term'],r['component'],r.get('col_order',0),r.get('score',0)) for r in rows])
    return jsonify({'ok': True, 'count': len(rows)})

@app.route('/api/grades/my-scores', methods=['GET'])
@auth_required
def get_my_scores():
    if g.role != 'student': return jsonify({'error':'Students only'}), 403
    u = Q("SELECT student_id FROM users WHERE id=?", (g.user_id,), one=True)
    if not u or not u['student_id']: return jsonify({'error':'No student record'}), 400
    st = Q("SELECT * FROM students WHERE id=?", (u['student_id'],), one=True)
    configs = Q("SELECT * FROM grade_configs WHERE section_id=? ORDER BY component,col_order", (st['section_id'],))
    scores  = Q("SELECT * FROM grade_scores WHERE section_id=? AND student_id=?", (st['section_id'], st['id']))
    att = {}
    for term in ('mt','ft'):
        total_sess = Q("SELECT COUNT(*) AS c FROM sessions WHERE section_id=? AND term=? AND is_open=0", (st['section_id'], term), one=True)['c']
        present    = Q("SELECT COUNT(*) AS c FROM attendance a JOIN sessions ss ON a.session_id=ss.id WHERE a.student_id=? AND ss.section_id=? AND ss.term=? AND a.status IN ('present','late','excuse')", (st['id'], st['section_id'], term), one=True)['c']
        att[term]  = {'present': present, 'total': total_sess}
    return jsonify({'student': st, 'configs': configs, 'scores': scores, 'att': att})

# ── REPORTS ───────────────────────────────────────────────────────────────
@app.route('/api/reports/absence-leaders/<int:sem_id>', methods=['GET'])
@auth_required
def absence_leaders(sem_id):
    rows = Q("""SELECT s.full_name, s.student_no, sec.name AS section_name, sec.subject_code,
                COUNT(CASE WHEN a.status='absent' THEN 1 END) AS absences
                FROM students s JOIN sections sec ON s.section_id=sec.id
                LEFT JOIN attendance a ON s.id=a.student_id
                WHERE sec.sem_id=? AND sec.user_id=?
                GROUP BY s.id HAVING absences>0 ORDER BY absences DESC LIMIT 20""", (sem_id, g.user_id))
    return jsonify(rows)

# ── ADMIN USERS ───────────────────────────────────────────────────────────
@app.route('/api/admin/users', methods=['GET'])
@admin_required
def list_admin_users():
    return jsonify(Q("SELECT id,name,username,email,created_at FROM users WHERE role='admin'"))

@app.route('/api/admin/users', methods=['POST'])
@admin_required
def create_admin_user():
    d = request.json or {}
    if Q("SELECT id FROM users WHERE username=?", (d.get('username',''),), one=True):
        return jsonify({'error':'Username taken'}), 400
    pw = bcrypt.hashpw(d.get('password','').encode(), bcrypt.gensalt()).decode()
    uid = X("INSERT INTO users (role,username,password,name,email) VALUES ('admin',?,?,?,?)",
            (d['username'], pw, d.get('name',''), d.get('email','')))
    return jsonify({'id': uid})

@app.route('/api/admin/users/<int:uid>', methods=['DELETE'])
@admin_required
def delete_admin_user(uid):
    if uid == g.user_id: return jsonify({'error':'Cannot delete yourself'}), 400
    X("DELETE FROM users WHERE id=? AND role='admin'", (uid,))
    return jsonify({'ok': True})

# ── SPA ───────────────────────────────────────────────────────────────────
@app.route('/', defaults={'path':''})
@app.route('/<path:path>')
def spa(path):
    if path and os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)
    return send_file(os.path.join(BASE_DIR, 'templates', 'index.html'))

if __name__ == '__main__':
    init_db()
    print("🚀 EduTrack Pro on http://localhost:5000")
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'
    app.run(debug=debug, host='0.0.0.0', port=port, use_reloader=False)
