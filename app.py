import json
import os
import sqlite3
import urllib.request
import urllib.parse
import urllib.error
import ssl
import base64
import time
from collections import defaultdict
from functools import wraps
from flask import Flask, jsonify, request, render_template, redirect, url_for
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# ── Database backend seçimi ────────────────────────────────────────────────────
# Render'da DATABASE_URL set edilirse PostgreSQL, yoksa SQLite kullanılır.

DATABASE_URL = os.environ.get('DATABASE_URL', '')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

USE_PG = bool(DATABASE_URL)
LOCAL_DB = 'dashboard.db'

if USE_PG:
    import psycopg2
    import psycopg2.extras


class Db:
    """SQLite ve PostgreSQL için ortak arayüz."""

    def __init__(self):
        if USE_PG:
            self._conn = psycopg2.connect(DATABASE_URL)
            self._cur  = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            self._conn = sqlite3.connect(LOCAL_DB)
            self._conn.row_factory = sqlite3.Row
            self._cur  = self._conn.cursor()
        self._ph = '%s' if USE_PG else '?'

    def _sql(self, q):
        return q.replace('?', self._ph)

    def execute(self, query, params=()):
        self._cur.execute(self._sql(query), params)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()]

    def commit(self):
        self._conn.commit()
        return self

    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


SPOTIFY_CLIENT_ID     = os.environ.get('SPOTIFY_CLIENT_ID', '')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET', '')
SPOTIFY_REDIRECT_URI  = os.environ.get('SPOTIFY_REDIRECT_URI', 'https://dashboard-8rk5.onrender.com/callback')
SPOTIFY_SCOPES        = 'user-read-currently-playing user-read-playback-state'

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-before-deploying')

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

DEFAULT_CARD_ORDER = ['clock', 'weather', 'bookmarks', 'todos', 'notes', 'pomodoro', 'reminders', 'habits']

DEFAULT_DATA = {
    "todos": [],
    "notes": "",
    "bookmarks": [
        {"icon": "🔍", "name": "Google",  "url": "https://google.com"},
        {"icon": "📺", "name": "YouTube", "url": "https://youtube.com"},
        {"icon": "🐙", "name": "GitHub",  "url": "https://github.com"},
        {"icon": "📰", "name": "Reddit",  "url": "https://reddit.com"},
    ],
    "theme": "dark",
    "city": "Istanbul",
    "reminders": [],
    "habits": [],
    "card_order": DEFAULT_CARD_ORDER,
}


# ── Rate limiting ──────────────────────────────────────────────────────────────

_rate_store = defaultdict(list)

def rate_limit(max_requests=30, window=60):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            ip = request.remote_addr
            now = time.time()
            _rate_store[ip] = [t for t in _rate_store[ip] if now - t < window]
            if len(_rate_store[ip]) >= max_requests:
                return jsonify({'error': 'Çok fazla istek. Lütfen bekleyin.'}), 429
            _rate_store[ip].append(now)
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ── Database init ──────────────────────────────────────────────────────────────

def init_db():
    with Db() as db:
        if USE_PG:
            db.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id                    SERIAL PRIMARY KEY,
                    username              TEXT UNIQUE NOT NULL,
                    email                 TEXT UNIQUE NOT NULL,
                    password_hash         TEXT NOT NULL,
                    data                  TEXT NOT NULL DEFAULT '{}',
                    spotify_access_token  TEXT DEFAULT '',
                    spotify_refresh_token TEXT DEFAULT ''
                )
            ''')
        else:
            db.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    username              TEXT UNIQUE NOT NULL,
                    email                 TEXT UNIQUE NOT NULL,
                    password_hash         TEXT NOT NULL,
                    data                  TEXT NOT NULL DEFAULT '{}',
                    spotify_access_token  TEXT DEFAULT '',
                    spotify_refresh_token TEXT DEFAULT ''
                )
            ''')
        db.commit()


# ── Flask-Login ────────────────────────────────────────────────────────────────

class User(UserMixin):
    def __init__(self, id, username, email):
        self.id = id
        self.username = username
        self.email = email


@login_manager.user_loader
def load_user(user_id):
    with Db() as db:
        row = db.execute('SELECT id, username, email FROM users WHERE id = ?', (user_id,)).fetchone()
    if row:
        return User(row['id'], row['username'], row['email'])
    return None


# ── Per-user data helpers ──────────────────────────────────────────────────────

def load_data():
    with Db() as db:
        row = db.execute('SELECT data FROM users WHERE id = ?', (current_user.id,)).fetchone()
    if row and row['data']:
        try:
            stored = json.loads(row['data'])
            merged = dict(DEFAULT_DATA)
            merged.update(stored)
            if 'card_order' not in merged:
                merged['card_order'] = DEFAULT_CARD_ORDER[:]
            return merged
        except Exception:
            pass
    return dict(DEFAULT_DATA)


def save_data(data):
    with Db() as db:
        db.execute('UPDATE users SET data = ? WHERE id = ?',
                   (json.dumps(data, ensure_ascii=False), current_user.id))
        db.commit()


def get_spotify_tokens():
    with Db() as db:
        row = db.execute(
            'SELECT spotify_access_token, spotify_refresh_token FROM users WHERE id = ?',
            (current_user.id,)
        ).fetchone()
    return {
        'access_token':  (row or {}).get('spotify_access_token', ''),
        'refresh_token': (row or {}).get('spotify_refresh_token', ''),
    }


def set_spotify_tokens(access_token, refresh_token=None):
    with Db() as db:
        if refresh_token:
            db.execute(
                'UPDATE users SET spotify_access_token = ?, spotify_refresh_token = ? WHERE id = ?',
                (access_token, refresh_token, current_user.id)
            )
        else:
            db.execute(
                'UPDATE users SET spotify_access_token = ? WHERE id = ?',
                (access_token, current_user.id)
            )
        db.commit()


def clear_spotify_tokens():
    with Db() as db:
        db.execute(
            "UPDATE users SET spotify_access_token = '', spotify_refresh_token = '' WHERE id = ?",
            (current_user.id,)
        )
        db.commit()


# ── Auth routes ────────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    error = None
    registered = request.args.get('registered')
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        with Db() as db:
            row = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if row and check_password_hash(row['password_hash'], password):
            login_user(User(row['id'], row['username'], row['email']), remember=True)
            return redirect(url_for('index'))
        error = 'Kullanıcı adı veya şifre hatalı.'
    return render_template('login.html', error=error, registered=registered)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        if len(username) < 3:
            error = 'Kullanıcı adı en az 3 karakter olmalı.'
        elif len(password) < 6:
            error = 'Şifre en az 6 karakter olmalı.'
        else:
            try:
                with Db() as db:
                    db.execute(
                        'INSERT INTO users (username, email, password_hash, data) VALUES (?, ?, ?, ?)',
                        (username, email, generate_password_hash(password),
                         json.dumps(DEFAULT_DATA, ensure_ascii=False))
                    )
                    db.commit()
                return redirect(url_for('login') + '?registered=1')
            except Exception:
                error = 'Bu kullanıcı adı veya e-posta zaten kullanımda.'
    return render_template('register.html', error=error)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ── Main page ──────────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def index():
    return render_template('index.html', username=current_user.username)


# ── Data API ───────────────────────────────────────────────────────────────────

@app.route('/api/data')
@login_required
def get_data():
    return jsonify(load_data())


@app.route('/api/todos', methods=['POST'])
@login_required
@rate_limit(30, 60)
def add_todo():
    body = request.get_json(silent=True)
    if not body or not body.get('text', '').strip():
        return jsonify({'error': 'Görev metni boş olamaz.'}), 400
    data = load_data()
    new_id = max((t['id'] for t in data['todos']), default=0) + 1
    data['todos'].append({'id': new_id, 'text': body['text'].strip(), 'done': False})
    save_data(data)
    return jsonify(data['todos'])


@app.route('/api/todos/<int:todo_id>/toggle', methods=['POST'])
@login_required
def toggle_todo(todo_id):
    data = load_data()
    for t in data['todos']:
        if t['id'] == todo_id:
            t['done'] = not t['done']
            break
    save_data(data)
    return jsonify(data['todos'])


@app.route('/api/todos/<int:todo_id>', methods=['DELETE'])
@login_required
def delete_todo(todo_id):
    data = load_data()
    data['todos'] = [t for t in data['todos'] if t['id'] != todo_id]
    save_data(data)
    return jsonify(data['todos'])


@app.route('/api/notes', methods=['POST'])
@login_required
@rate_limit(60, 60)
def save_notes():
    body = request.get_json(silent=True)
    if body is None:
        return jsonify({'error': 'Geçersiz istek.'}), 400
    data = load_data()
    data['notes'] = body.get('notes', '')
    save_data(data)
    return jsonify({'ok': True})


@app.route('/api/bookmarks', methods=['POST'])
@login_required
@rate_limit(20, 60)
def add_bookmark():
    body = request.get_json(silent=True)
    if not body or not body.get('name') or not body.get('url'):
        return jsonify({'error': 'İsim ve URL zorunludur.'}), 400
    data = load_data()
    data['bookmarks'].append(body)
    save_data(data)
    return jsonify(data['bookmarks'])


@app.route('/api/bookmarks/<int:index>', methods=['DELETE'])
@login_required
def delete_bookmark(index):
    data = load_data()
    if 0 <= index < len(data['bookmarks']):
        data['bookmarks'].pop(index)
    save_data(data)
    return jsonify(data['bookmarks'])


@app.route('/api/theme', methods=['POST'])
@login_required
def toggle_theme():
    data = load_data()
    data['theme'] = 'light' if data['theme'] == 'dark' else 'dark'
    save_data(data)
    return jsonify({'theme': data['theme']})


@app.route('/api/city', methods=['POST'])
@login_required
def update_city():
    body = request.get_json(silent=True)
    if not body or not body.get('city', '').strip():
        return jsonify({'error': 'Şehir adı boş olamaz.'}), 400
    data = load_data()
    data['city'] = body['city'].strip()
    save_data(data)
    return jsonify({'ok': True})


@app.route('/api/card-order', methods=['POST'])
@login_required
def save_card_order():
    body = request.get_json(silent=True)
    if not body or not isinstance(body.get('order'), list):
        return jsonify({'error': 'Geçersiz sıralama.'}), 400
    data = load_data()
    data['card_order'] = body['order']
    save_data(data)
    return jsonify({'ok': True})


# ── Reminders API ──────────────────────────────────────────────────────────────

@app.route('/api/reminders', methods=['POST'])
@login_required
@rate_limit(20, 60)
def add_reminder():
    body = request.get_json(silent=True)
    if not body or not body.get('title', '').strip() or not body.get('time', '').strip():
        return jsonify({'error': 'Başlık ve saat zorunludur.'}), 400
    data = load_data()
    if 'reminders' not in data:
        data['reminders'] = []
    new_id = max((r['id'] for r in data['reminders']), default=0) + 1
    data['reminders'].append({
        'id': new_id,
        'title': body['title'].strip(),
        'time': body['time'].strip(),
        'repeat': body.get('repeat', 'daily'),
        'active': True,
    })
    save_data(data)
    return jsonify(data['reminders'])


@app.route('/api/reminders/<int:reminder_id>', methods=['DELETE'])
@login_required
def delete_reminder(reminder_id):
    data = load_data()
    data['reminders'] = [r for r in data.get('reminders', []) if r['id'] != reminder_id]
    save_data(data)
    return jsonify(data['reminders'])


@app.route('/api/reminders/<int:reminder_id>/toggle', methods=['POST'])
@login_required
def toggle_reminder(reminder_id):
    data = load_data()
    for r in data.get('reminders', []):
        if r['id'] == reminder_id:
            r['active'] = not r.get('active', True)
            break
    save_data(data)
    return jsonify(data['reminders'])


# ── Habits API ─────────────────────────────────────────────────────────────────

@app.route('/api/habits', methods=['POST'])
@login_required
@rate_limit(20, 60)
def add_habit():
    body = request.get_json(silent=True)
    if not body or not body.get('name', '').strip():
        return jsonify({'error': 'Alışkanlık adı zorunludur.'}), 400
    data = load_data()
    if 'habits' not in data:
        data['habits'] = []
    new_id = max((h['id'] for h in data['habits']), default=0) + 1
    data['habits'].append({
        'id': new_id,
        'name': body['name'].strip(),
        'emoji': body.get('emoji', '✅'),
        'checks': {},
    })
    save_data(data)
    return jsonify(data['habits'])


@app.route('/api/habits/<int:habit_id>', methods=['DELETE'])
@login_required
def delete_habit(habit_id):
    data = load_data()
    data['habits'] = [h for h in data.get('habits', []) if h['id'] != habit_id]
    save_data(data)
    return jsonify(data['habits'])


@app.route('/api/habits/<int:habit_id>/check', methods=['POST'])
@login_required
def check_habit(habit_id):
    from datetime import date
    today = date.today().isoformat()
    data = load_data()
    for h in data.get('habits', []):
        if h['id'] == habit_id:
            if 'checks' not in h:
                h['checks'] = {}
            if h['checks'].get(today):
                del h['checks'][today]
            else:
                h['checks'][today] = True
            break
    save_data(data)
    return jsonify(data['habits'])


# ── Weather ────────────────────────────────────────────────────────────────────

weather_cache = {}

WTTR_CODE_MAP = {
    113: ('Açık',            '☀️',  'sunny'),
    116: ('Parçalı bulutlu', '⛅',  'cloudy'),
    119: ('Bulutlu',         '☁️',  'cloudy'),
    122: ('Kapalı',          '☁️',  'cloudy'),
    143: ('Sisli',           '🌫️', 'cloudy'),
    248: ('Sisli',           '🌫️', 'cloudy'),
    260: ('Sisli',           '🌫️', 'cloudy'),
    200: ('Fırtınalı',       '⛈️', 'stormy'),
    386: ('Fırtınalı',       '⛈️', 'stormy'),
    389: ('Fırtınalı',       '⛈️', 'stormy'),
    392: ('Fırtınalı',       '⛈️', 'stormy'),
    395: ('Fırtınalı',       '⛈️', 'stormy'),
}
WTTR_RAIN = {176, 263, 266, 281, 284, 293, 296, 299, 302, 305, 308, 353, 356, 359}
WTTR_SNOW = {179, 182, 185, 227, 230, 323, 326, 329, 332, 335, 338, 350, 362, 365, 368, 371, 374, 377}


def open_url(url):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15, context=ctx) as res:
        return json.loads(res.read().decode())


def fetch_weather_data(city):
    url  = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
    data = open_url(url)
    cur  = data['current_condition'][0]
    nearest   = data.get('nearest_area', [{}])[0]
    city_name = nearest.get('areaName', [{}])[0].get('value', city)

    temp       = int(cur['temp_C'])
    feels_like = int(cur['FeelsLikeC'])
    humidity   = int(cur['humidity'])
    wind       = int(cur['windspeedKmph'])
    code       = int(cur['weatherCode'])

    if code in WTTR_CODE_MAP:
        desc, icon, anim_type = WTTR_CODE_MAP[code]
    elif code in WTTR_RAIN:
        desc, icon, anim_type = 'Yağmurlu', '🌧️', 'rainy'
    elif code in WTTR_SNOW:
        desc, icon, anim_type = 'Karlı', '🌨️', 'snowy'
    else:
        desc, icon, anim_type = 'Parçalı bulutlu', '⛅', 'cloudy'

    return {
        'city': city_name, 'temp': temp, 'feels_like': feels_like,
        'humidity': humidity, 'wind': wind,
        'desc': desc, 'icon': icon, 'anim_type': anim_type,
    }


@app.route('/api/weather')
@login_required
@rate_limit(10, 60)
def get_weather():
    city = request.args.get('city', 'Istanbul')
    now  = time.time()

    if city in weather_cache and now - weather_cache[city]['time'] < 600:
        return jsonify(weather_cache[city]['data'])

    last_error = None
    for attempt in range(2):
        try:
            result = fetch_weather_data(city)
            weather_cache[city] = {'data': result, 'time': now}
            return jsonify(result)
        except Exception as e:
            last_error = e
            print(f"Weather fetch attempt {attempt+1} failed: {type(e).__name__}: {e}", flush=True)

    if city in weather_cache:
        stale = dict(weather_cache[city]['data'])
        stale['stale'] = True
        return jsonify(stale)

    return jsonify({'error': str(last_error)}), 500


# ── Spotify ────────────────────────────────────────────────────────────────────

@app.route('/spotify/login')
@login_required
def spotify_login():
    params = urllib.parse.urlencode({
        'client_id':     SPOTIFY_CLIENT_ID,
        'response_type': 'code',
        'redirect_uri':  SPOTIFY_REDIRECT_URI,
        'scope':         SPOTIFY_SCOPES,
    })
    return redirect(f'https://accounts.spotify.com/authorize?{params}')


@app.route('/spotify/disconnect')
@login_required
def spotify_disconnect():
    clear_spotify_tokens()
    return redirect('/')


@app.route('/callback')
@login_required
def spotify_callback():
    code = request.args.get('code')
    if not code:
        return 'Hata: kod alınamadı', 400

    credentials = base64.b64encode(
        f'{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}'.encode()
    ).decode()
    data = urllib.parse.urlencode({
        'grant_type':   'authorization_code',
        'code':         code,
        'redirect_uri': SPOTIFY_REDIRECT_URI,
    }).encode()

    req = urllib.request.Request(
        'https://accounts.spotify.com/api/token',
        data=data,
        headers={
            'Authorization': f'Basic {credentials}',
            'Content-Type':  'application/x-www-form-urlencoded',
        }
    )
    with urllib.request.urlopen(req) as res:
        tokens = json.loads(res.read().decode())

    set_spotify_tokens(tokens['access_token'], tokens.get('refresh_token', ''))
    return redirect('/')


def refresh_spotify_token():
    tokens = get_spotify_tokens()
    refresh_token = tokens.get('refresh_token', '')
    if not refresh_token:
        return False
    try:
        credentials = base64.b64encode(
            f'{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}'.encode()
        ).decode()
        data = urllib.parse.urlencode({
            'grant_type':    'refresh_token',
            'refresh_token': refresh_token,
        }).encode()
        req = urllib.request.Request(
            'https://accounts.spotify.com/api/token',
            data=data,
            headers={
                'Authorization': f'Basic {credentials}',
                'Content-Type':  'application/x-www-form-urlencoded',
            }
        )
        with urllib.request.urlopen(req) as res:
            new_tokens = json.loads(res.read().decode())
        set_spotify_tokens(
            new_tokens['access_token'],
            new_tokens.get('refresh_token') or refresh_token
        )
        return True
    except Exception:
        return False


@app.route('/api/spotify')
@login_required
def get_spotify():
    token = get_spotify_tokens().get('access_token', '')
    if not token:
        return jsonify({'connected': False})

    try:
        req = urllib.request.Request(
            'https://api.spotify.com/v1/me/player/currently-playing',
            headers={'Authorization': f'Bearer {token}'}
        )
        with urllib.request.urlopen(req) as res:
            if res.status == 204:
                return jsonify({'connected': True, 'playing': False})
            data = json.loads(res.read().decode())

        item = data.get('item', {})
        return jsonify({
            'connected':  True,
            'playing':    data.get('is_playing', False),
            'title':      item.get('name', ''),
            'artist':     ', '.join(a['name'] for a in item.get('artists', [])),
            'album_art':  item.get('album', {}).get('images', [{}])[0].get('url', ''),
            'progress':   data.get('progress_ms', 0),
            'duration':   item.get('duration_ms', 0),
        })
    except urllib.error.HTTPError as e:
        if e.code == 401:
            if refresh_spotify_token():
                return get_spotify()
            clear_spotify_tokens()
            return jsonify({'connected': False})
        return jsonify({'connected': True, 'playing': False})
    except Exception:
        return jsonify({'connected': True, 'playing': False})


# ── Startup ────────────────────────────────────────────────────────────────────

init_db()

if __name__ == '__main__':
    app.run(debug=True)
