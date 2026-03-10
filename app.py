import json
import os
import urllib.request
import urllib.parse
import ssl
import base64
from flask import Flask, jsonify, request, render_template, redirect

SPOTIFY_CLIENT_ID = 'b4e452520d914d2e8561369726f181f3'
SPOTIFY_CLIENT_SECRET = '67d6a93badba45e7ba42d35d3b928ace'
SPOTIFY_REDIRECT_URI = 'http://127.0.0.1:5000/callback'
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

@app.route('/api/weather')
def get_weather():
    city = request.args.get('city', 'Istanbul')
    try:
        # Şehirden koordinat al
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(city)}&count=1&language=tr"
        with urllib.request.urlopen(geo_url, timeout=10) as res:
            geo = json.loads(res.read().decode())

        if not geo.get('results'):
            return jsonify({'error': 'Şehir bulunamadı'}), 404

        lat = geo['results'][0]['latitude']
        lon = geo['results'][0]['longitude']
        city_name = geo['results'][0]['name']

        # Hava durumu al
        weather_url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
        )
        with urllib.request.urlopen(weather_url, timeout=10) as res:
            weather = json.loads(res.read().decode())

        current = weather['current']

        weather_codes = {
            0: ('Açık', '☀️'), 1: ('Az bulutlu', '🌤️'), 2: ('Parçalı bulutlu', '⛅'),
            3: ('Kapalı', '☁️'), 45: ('Sisli', '🌫️'), 48: ('Puslu', '🌫️'),
            51: ('Hafif çisenti', '🌦️'), 53: ('Çisenti', '🌦️'), 55: ('Yoğun çisenti', '🌦️'),
            61: ('Hafif yağmur', '🌧️'), 63: ('Yağmurlu', '🌧️'), 65: ('Yoğun yağmur', '🌧️'),
            71: ('Hafif kar', '🌨️'), 73: ('Karlı', '🌨️'), 75: ('Yoğun kar', '🌨️'),
            80: ('Sağanak', '🌧️'), 81: ('Sağanak', '🌧️'), 82: ('Yoğun sağanak', '🌧️'),
            95: ('Gök gürültülü', '⛈️'), 96: ('Fırtına', '⛈️'), 99: ('Şiddetli fırtına', '⛈️'),
        }

        code = current['weather_code']
        desc, icon = weather_codes.get(code, ('Bilinmiyor', '🌤️'))

        if code == 0:
            anim_type = 'sunny'
        elif code in [61,63,65,51,53,55,80,81,82]:
            anim_type = 'rainy'
        elif code in [71,73,75,77]:
            anim_type = 'snowy'
        elif code in [95,96,99]:
            anim_type = 'stormy'
        else:
            anim_type = 'cloudy'

        return jsonify({
            'city': city_name,
            'temp': round(current['temperature_2m']),
            'feels_like': round(current['apparent_temperature']),
            'humidity': current['relative_humidity_2m'],
            'wind': round(current['wind_speed_10m']),
            'desc': desc,
            'icon': icon,
            'anim_type': anim_type,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
