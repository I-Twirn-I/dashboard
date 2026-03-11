import json
import os
import urllib.request
import urllib.parse
import ssl
import base64
from flask import Flask, jsonify, request, render_template, redirect

SPOTIFY_CLIENT_ID = 'b4e452520d914d2e8561369726f181f3'
SPOTIFY_CLIENT_SECRET = '67d6a93badba45e7ba42d35d3b928ace'
SPOTIFY_REDIRECT_URI = 'https://dashboard-8rk5.onrender.com/callback'
SPOTIFY_SCOPES = 'user-read-currently-playing user-read-playback-state'

spotify_tokens = {}

app = Flask(__name__)
DATA_FILE = 'data.json'

DEFAULT_DATA = {
    "todos": [],
    "notes": "",
    "bookmarks": [
        {"icon": "🔍", "name": "Google", "url": "https://google.com"},
        {"icon": "📺", "name": "YouTube", "url": "https://youtube.com"},
        {"icon": "🐙", "name": "GitHub", "url": "https://github.com"},
        {"icon": "📰", "name": "Reddit", "url": "https://reddit.com"}
    ],
    "theme": "dark",
    "city": "Istanbul"
}

def load_data():
    if not os.path.exists(DATA_FILE):
        save_data(DEFAULT_DATA)
        return DEFAULT_DATA
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/data')
def get_data():
    return jsonify(load_data())

@app.route('/api/todos', methods=['POST'])
def add_todo():
    data = load_data()
    body = request.get_json()
    new_id = max((t['id'] for t in data['todos']), default=0) + 1
    data['todos'].append({'id': new_id, 'text': body['text'], 'done': False})
    save_data(data)
    return jsonify(data['todos'])

@app.route('/api/todos/<int:todo_id>/toggle', methods=['POST'])
def toggle_todo(todo_id):
    data = load_data()
    for t in data['todos']:
        if t['id'] == todo_id:
            t['done'] = not t['done']
            break
    save_data(data)
    return jsonify(data['todos'])

@app.route('/api/todos/<int:todo_id>', methods=['DELETE'])
def delete_todo(todo_id):
    data = load_data()
    data['todos'] = [t for t in data['todos'] if t['id'] != todo_id]
    save_data(data)
    return jsonify(data['todos'])

@app.route('/api/notes', methods=['POST'])
def save_notes():
    data = load_data()
    data['notes'] = request.get_json()['notes']
    save_data(data)
    return jsonify({'ok': True})

@app.route('/api/bookmarks', methods=['POST'])
def add_bookmark():
    data = load_data()
    data['bookmarks'].append(request.get_json())
    save_data(data)
    return jsonify(data['bookmarks'])

@app.route('/api/bookmarks/<int:index>', methods=['DELETE'])
def delete_bookmark(index):
    data = load_data()
    if 0 <= index < len(data['bookmarks']):
        data['bookmarks'].pop(index)
    save_data(data)
    return jsonify(data['bookmarks'])

@app.route('/api/theme', methods=['POST'])
def toggle_theme():
    data = load_data()
    data['theme'] = 'light' if data['theme'] == 'dark' else 'dark'
    save_data(data)
    return jsonify({'theme': data['theme']})

@app.route('/api/city', methods=['POST'])
def update_city():
    data = load_data()
    data['city'] = request.get_json()['city']
    save_data(data)
    return jsonify({'ok': True})

weather_cache = {}
geo_cache = {
    'istanbul': {'lat': 41.0136, 'lon': 28.9550, 'name': 'İstanbul'},
    'ankara': {'lat': 39.9272, 'lon': 32.8644, 'name': 'Ankara'},
    'izmir': {'lat': 38.4189, 'lon': 27.1287, 'name': 'İzmir'},
}

def open_url(url):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15, context=ctx) as res:
        return json.loads(res.read().decode())

def fetch_weather_data(city):
    key = city.lower().strip()
    if key in geo_cache:
        lat, lon, city_name = geo_cache[key]['lat'], geo_cache[key]['lon'], geo_cache[key]['name']
    else:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(city)}&count=1&language=tr&format=json"
        geo = open_url(geo_url)
        if not geo.get('results'):
            raise ValueError('Şehir bulunamadı')
        loc = geo['results'][0]
        lat, lon, city_name = loc['latitude'], loc['longitude'], loc['name']
        geo_cache[key] = {'lat': lat, 'lon': lon, 'name': city_name}

    wx_url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,wind_speed_10m,weather_code"
        f"&wind_speed_unit=kmh&timezone=auto"
    )
    wx = open_url(wx_url)

    cur = wx['current']
    temp = round(cur['temperature_2m'])
    feels_like = round(cur['apparent_temperature'])
    humidity = cur['relative_humidity_2m']
    wind = round(cur['wind_speed_10m'])
    code = cur['weather_code']

    if code == 0:
        desc, icon, anim_type = 'Açık', '☀️', 'sunny'
    elif code in (1, 2):
        desc, icon, anim_type = 'Parçalı bulutlu', '⛅', 'cloudy'
    elif code == 3:
        desc, icon, anim_type = 'Bulutlu', '☁️', 'cloudy'
    elif code in (45, 48):
        desc, icon, anim_type = 'Sisli', '🌫️', 'cloudy'
    elif code in (51, 53, 55, 56, 57):
        desc, icon, anim_type = 'Çiseleyen', '🌦️', 'rainy'
    elif code in (61, 63, 65, 66, 67, 80, 81, 82):
        desc, icon, anim_type = 'Yağmurlu', '🌧️', 'rainy'
    elif code in (71, 73, 75, 77, 85, 86):
        desc, icon, anim_type = 'Karlı', '🌨️', 'snowy'
    elif code in (95, 96, 99):
        desc, icon, anim_type = 'Fırtınalı', '⛈️', 'stormy'
    else:
        desc, icon, anim_type = 'Parçalı bulutlu', '⛅', 'cloudy'

    return {
        'city': city_name,
        'temp': temp,
        'feels_like': feels_like,
        'humidity': humidity,
        'wind': wind,
        'desc': desc,
        'icon': icon,
        'anim_type': anim_type,
    }

@app.route('/api/weather')
def get_weather():
    import time
    city = request.args.get('city', 'Istanbul')
    now = time.time()

    # Cache geçerliyse direkt döndür
    if city in weather_cache and now - weather_cache[city]['time'] < 600:
        return jsonify(weather_cache[city]['data'])

    # 2 kez dene
    last_error = None
    for attempt in range(2):
        try:
            result = fetch_weather_data(city)
            weather_cache[city] = {'data': result, 'time': now}
            return jsonify(result)
        except Exception as e:
            last_error = e
            print(f"Weather fetch attempt {attempt+1} failed: {type(e).__name__}: {e}", flush=True)

    # Tüm denemeler başarısız → eski cache varsa onu döndür
    if city in weather_cache:
        stale = dict(weather_cache[city]['data'])
        stale['stale'] = True
        return jsonify(stale)

    return jsonify({'error': str(last_error)}), 500

@app.route('/spotify/login')
def spotify_login():
    params = urllib.parse.urlencode({
        'client_id': SPOTIFY_CLIENT_ID,
        'response_type': 'code',
        'redirect_uri': SPOTIFY_REDIRECT_URI,
        'scope': SPOTIFY_SCOPES,
    })
    return redirect(f'https://accounts.spotify.com/authorize?{params}')

@app.route('/callback')
def spotify_callback():
    code = request.args.get('code')
    if not code:
        return 'Hata: kod alınamadı', 400

    credentials = base64.b64encode(f'{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}'.encode()).decode()
    data = urllib.parse.urlencode({
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': SPOTIFY_REDIRECT_URI,
    }).encode()

    req = urllib.request.Request(
        'https://accounts.spotify.com/api/token',
        data=data,
        headers={
            'Authorization': f'Basic {credentials}',
            'Content-Type': 'application/x-www-form-urlencoded',
        }
    )
    with urllib.request.urlopen(req) as res:
        tokens = json.loads(res.read().decode())

    spotify_tokens['access_token'] = tokens['access_token']
    spotify_tokens['refresh_token'] = tokens.get('refresh_token', '')
    return redirect('/')

def refresh_spotify_token():
    refresh_token = spotify_tokens.get('refresh_token')
    if not refresh_token:
        return False
    try:
        credentials = base64.b64encode(f'{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}'.encode()).decode()
        data = urllib.parse.urlencode({
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
        }).encode()
        req = urllib.request.Request(
            'https://accounts.spotify.com/api/token',
            data=data,
            headers={
                'Authorization': f'Basic {credentials}',
                'Content-Type': 'application/x-www-form-urlencoded',
            }
        )
        with urllib.request.urlopen(req) as res:
            tokens = json.loads(res.read().decode())
        spotify_tokens['access_token'] = tokens['access_token']
        if 'refresh_token' in tokens:
            spotify_tokens['refresh_token'] = tokens['refresh_token']
        return True
    except Exception:
        return False

@app.route('/api/spotify')
def get_spotify():
    token = spotify_tokens.get('access_token')
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
            'connected': True,
            'playing': data.get('is_playing', False),
            'title': item.get('name', ''),
            'artist': ', '.join(a['name'] for a in item.get('artists', [])),
            'album_art': item.get('album', {}).get('images', [{}])[0].get('url', ''),
            'progress': data.get('progress_ms', 0),
            'duration': item.get('duration_ms', 0),
        })
    except urllib.error.HTTPError as e:
        if e.code == 401:
            if refresh_spotify_token():
                return get_spotify()
            spotify_tokens.clear()
            return jsonify({'connected': False})
        return jsonify({'connected': True, 'playing': False})
    except Exception:
        return jsonify({'connected': True, 'playing': False})

if __name__ == '__main__':
    app.run(debug=True)
