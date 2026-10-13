import argparse
import importlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import sys
import subprocess
from pathlib import Path
from threading import Event, Lock, Thread
from time import time

HOST = '127.0.0.1'
DEFAULT_PORT = 8765
DOTA_GSI_CONFIG_NAME = 'gamestate_integration_d2pt_bridge.cfg'

lock = Lock()
draft = {'radiant': [], 'dire': [], 'updated': 0}
recommendations = {'side': '', 'items': [], 'updated': 0}
latest_gsi = {}
last_gsi_draft = object()
last_gsi_match_id = ''
reset_generation = 0
watching_enabled = True
watch_generation = 0
gsi_received = False
screen_capture_status = 'starting'


def _set_watching(enabled):
    global draft, watching_enabled, watch_generation
    if not isinstance(enabled, bool):
        raise ValueError('enabled must be a boolean')
    with lock:
        watching_enabled = enabled
        watch_generation += 1
        draft = {'radiant': [], 'dire': [], 'updated': 0}
        return {'watching': watching_enabled, 'watch_generation': watch_generation}

HERO_ID_MAP = {
    1: 'Antimage', 2: 'Axe', 3: 'Bane', 4: 'Bloodseeker', 5: 'Crystal Maiden',
    6: 'Drow Ranger', 7: 'Earthshaker', 8: 'Juggernaut', 9: 'Mirana', 10: 'Morphling',
    11: 'Nevermore', 12: 'Phantom Lancer', 13: 'Puck', 14: 'Pudge', 15: 'Razor',
    16: 'Sand King', 17: 'Storm Spirit', 18: 'Sven', 19: 'Tiny', 20: 'Vengeful Spirit',
    21: 'Windranger', 22: 'Zeus', 23: 'Kunkka', 25: 'Lina', 26: 'Lion', 27: 'Shadow Shaman',
    28: 'Slardar', 29: 'Tidehunter', 30: 'Witch Doctor', 31: 'Lich', 32: 'Riki',
    33: 'Enigma', 34: 'Tinker', 35: 'Sniper', 36: 'Necrophos', 37: 'Warlock',
    38: 'Queen of Pain', 39: 'Venomancer', 40: 'Faceless Void', 41: 'Wraith King',
    42: 'Skeleton King', 43: 'Death Prophet', 44: 'Phantom Assassin', 45: 'Pugna',
    46: 'Templar Assassin', 47: 'Viper', 48: 'Luna', 49: 'Dragon Knight', 50: 'Dazzle',
    51: 'Clockwerk', 52: 'Leshrac', 53: 'Nature\'s Prophet', 54: 'Lifestealer',
    55: 'Dark Seer', 56: 'Clinkz', 57: 'Omniknight', 58: 'Enchantress', 59: 'Huskar',
    60: 'Night Stalker', 61: 'Broodmother', 62: 'Bounty Hunter', 63: 'Weaver',
    64: 'Jakiro', 65: 'Batrider', 66: 'Chen', 67: 'Spectre', 68: 'Doom', 69: 'Ancient Apparition',
    70: 'Elder Titan', 71: 'Earth Spirit', 72: 'Spirit Breaker', 73: 'Abaddon',
    74: 'Legion Commander', 75: 'Techies', 76: 'Ember Spirit', 77: 'Earthshaker',
    78: 'Storm Spirit', 79: 'Underlord', 80: 'Tusk', 81: 'Chaos Knight',
    82: 'Treant Protector', 83: 'Ogre Magi', 84: 'Undying', 85: 'Rubick',
    86: 'Disruptor', 87: 'Nyx Assassin', 88: 'Bogus', 89: 'Night Stalker',
    90: 'Void Spirit', 91: 'Visage', 92: 'Slark', 93: 'Medusa', 94: 'Troll Warlord',
    95: 'Centaur Warrunner', 96: 'Magnus', 97: 'Timbersaw', 98: 'Bristleback',
    99: 'Tusk', 100: 'Skywrath Mage', 101: 'Abaddon', 102: 'Outworld Destroyer',
    103: 'Shadow Fiend', 104: 'Monkey King', 105: 'Arc Warden', 106: 'Pangolier',
    107: 'Dark Willow', 108: 'Grimstroke', 109: 'Mars', 110: 'Snapfire', 111: 'Void Spirit',
    112: 'Hoodwink', 113: 'Dawnbreaker', 114: 'Muerta', 115: 'Marci', 116: 'Primal Beast',
    117: 'Axe', 118: 'Dota_2_Hero_118'
}


def _hero_name_from_value(value):
    if value is None:
        return ''
    text = str(value).strip()
    if not text:
        return ''

    lowered = text.lower()
    if lowered.startswith('npc_dota_hero_'):
        text = text[len('npc_dota_hero_'):]

    text = text.replace('-', ' ').replace('_', ' ')
    words = [word.capitalize() for word in text.split()]
    return ' '.join(words)


def _hero_id_to_name(hero_id):
    try:
        hero_id = int(hero_id)
    except (TypeError, ValueError):
        return ''
    if hero_id <= 0:
        return ''
    return HERO_ID_MAP.get(hero_id, f'Hero {hero_id}')


def extract_draft_from_payload(payload):
    draft_block = payload.get('draft') if isinstance(payload, dict) else None
    if not isinstance(draft_block, dict):
        return {'radiant': [], 'dire': []}

    result = {'radiant': [], 'dire': []}
    for team_key, side in (('team2', 'radiant'), ('team3', 'dire')):
        block = draft_block.get(team_key)
        if not isinstance(block, dict):
            continue

        for slot in range(5):
            hero_name = ''
            for field in (f'pick{slot}_name', f'pick{slot}_class', f'pick{slot}_id'):
                value = block.get(field)
                if value is None:
                    continue
                if isinstance(value, (int, float)):
                    hero_name = _hero_id_to_name(value)
                elif isinstance(value, str):
                    hero_name = _hero_name_from_value(value)
                if hero_name:
                    result[side].append(hero_name)
                    break

    player = payload.get('player') if isinstance(payload, dict) else None
    hero = payload.get('hero') if isinstance(payload, dict) else None
    if isinstance(player, dict) and isinstance(hero, dict):
        team_name = str(player.get('team_name', '')).strip().lower()
        side = team_name if team_name in ('radiant', 'dire') else ''
        hero_name = ''
        name = hero.get('name')
        if isinstance(name, str):
            hero_name = _hero_name_from_value(name)
        if not hero_name:
            hero_name = _hero_id_to_name(hero.get('id'))
        if side and hero_name and hero_name not in result[side]:
            result[side].append(hero_name)

    return result


def _capture_required(payload):
    map_block = payload.get('map') if isinstance(payload, dict) else None
    state = str(map_block.get('game_state', '')) if isinstance(map_block, dict) else ''
    if not state:
        return None
    if 'HERO_SELECTION' in state or 'STRATEGY_TIME' in state:
        return True
    return False


def screen_draft_from_snapshot(snapshot, hero_names):
    if snapshot is None:
        return None

    def names(ids):
        result = []
        for hero_id in ids:
            try:
                name = hero_names.get(int(hero_id), '')
            except (TypeError, ValueError):
                name = ''
            name = str(name).strip()
            if name and name not in result:
                result.append(name)
        return result[:5]

    radiant = names(getattr(snapshot, 'left', []))
    dire = names(getattr(snapshot, 'right', []))
    if not radiant and not dire:
        return None
    return {'radiant': radiant, 'dire': dire}


def _screen_draft_is_reliable(screen_draft):
    if not screen_draft:
        return False
    return len(screen_draft['radiant']) + len(screen_draft['dire']) >= 2


def _merge_hero_names(primary, supplemental):
    merged = []
    seen = set()
    for name in [*primary, *supplemental]:
        name = str(name).strip()
        key = ''.join(char.casefold() for char in name if char.isalnum())
        if key and key not in seen:
            merged.append(name)
            seen.add(key)
    return merged[:5]


def _load_screen_hero_names(runtime_root):
    source = runtime_root / 'data_cache' / 'raw' / 'opendota_constants_heroes.json'
    try:
        raw = json.loads(source.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return HERO_ID_MAP.copy()

    names = {}
    if isinstance(raw, dict):
        for key, hero in raw.items():
            if not isinstance(hero, dict):
                continue
            try:
                hero_id = int(hero.get('id', key))
            except (TypeError, ValueError):
                continue
            name = hero.get('localized_name')
            if isinstance(name, str) and name.strip():
                names[hero_id] = name.strip()
    return names or HERO_ID_MAP.copy()


def _run_screen_capture(stop_event):
    global draft, screen_capture_status

    runtime_root = Path(__file__).resolve().parent / 'bridge_runtime'
    if not runtime_root.is_dir():
        screen_capture_status = 'unavailable: bridge runtime folder not found'
        print(f'Screen capture {screen_capture_status}.')
        return
    if str(runtime_root) not in sys.path:
        sys.path.insert(0, str(runtime_root))

    try:
        CaptureSession = importlib.import_module(
            'draft_assist.capture.session').CaptureSession
        LiveProvider = importlib.import_module(
            'draft_assist.ui.providers').LiveProvider
        layout_module = importlib.import_module('draft_assist.vision.layout')
        library = importlib.import_module('draft_assist.vision.library')
        load_layout = layout_module.load_layout

        params = library.load_params()
        portrait_library = library.load(expected_hash_size=params.hash_size)
        session = CaptureSession(load_layout(), portrait_library, params)
        session.layout_is_measured = layout_module.calibration_in_use()
        provider = LiveProvider(session)
        hero_names = _load_screen_hero_names(runtime_root)
    except Exception as exc:
        screen_capture_status = f'unavailable: {exc}'
        print(f'Screen capture {screen_capture_status}')
        print('Restore bridge_runtime/assets/portraits/library.npz to enable screen recognition.')
        return

    screen_capture_status = provider.start()
    if screen_capture_status.startswith('capture not bound — No window titled'):
        screen_capture_status = 'waiting for the Dota 2 window (use borderless windowed mode)'
    print(f'Dota screen reader: {screen_capture_status}')
    last_match_id = ''
    last_screen_draft = None
    last_error = ''
    last_watch_generation = -1

    while not stop_event.is_set():
        with lock:
            payload = latest_gsi
            enabled = watching_enabled
            current_watch_generation = watch_generation

        if current_watch_generation != last_watch_generation:
            last_screen_draft = None
            last_watch_generation = current_watch_generation

        if not enabled:
            session.set_required(False)
            stop_event.wait(0.25)
            continue

        map_block = payload.get('map') if isinstance(payload, dict) else None
        match_id = str(map_block.get('matchid', '')) if isinstance(map_block, dict) else ''
        if match_id and match_id != '0' and match_id != last_match_id:
            if last_match_id:
                session.forget_reading()
            last_match_id = match_id
            last_screen_draft = None
            gsi_draft = extract_draft_from_payload(payload)
            with lock:
                if gsi_draft['radiant'] or gsi_draft['dire']:
                    draft = {**gsi_draft, 'updated': time()}
                else:
                    draft = {'radiant': [], 'dire': [], 'updated': 0}

        required = _capture_required(payload)
        session.set_required(required)
        try:
            snapshot = provider.poll()
            last_error = ''
        except Exception as exc:
            message = str(exc)
            if message != last_error:
                print(f'Screen capture error: {message}')
                last_error = message
            screen_capture_status = f'error: {message}'
            stop_event.wait(1.0)
            continue

        if snapshot.source and snapshot.source != screen_capture_status:
            screen_capture_status = snapshot.source
            print(f'Dota screen reader: {screen_capture_status}')

        if required is False:
            last_screen_draft = None
            with lock:
                draft = {'radiant': [], 'dire': [], 'updated': 0}

        gsi_draft = extract_draft_from_payload(payload)
        screen_draft_active = (
            required is True or
            (required is None and snapshot.mode in ('draft', 'forced')))
        if screen_draft_active:
            screen_draft = screen_draft_from_snapshot(snapshot, hero_names)
            if _screen_draft_is_reliable(screen_draft):
                combined_draft = {
                    side: _merge_hero_names(screen_draft[side], gsi_draft[side])
                    for side in ('radiant', 'dire')
                }
            elif gsi_draft['radiant'] or gsi_draft['dire']:
                combined_draft = {
                    side: gsi_draft[side][:5]
                    for side in ('radiant', 'dire')
                }
            else:
                combined_draft = None
            if combined_draft and combined_draft != last_screen_draft:
                with lock:
                    draft = {**combined_draft, 'updated': time()}
                    last_screen_draft = combined_draft
                print('\n=== SCREEN DRAFT UPDATE ===')
                print(json.dumps(combined_draft, ensure_ascii=False, indent=2))

        stop_event.wait(0.25)

    provider.stop()
    screen_capture_status = 'stopped'


def _ensure_dota_gsi_config(port: int = DEFAULT_PORT):
    candidates = []
    env_dir = os.environ.get('DOTA_DIR')
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(Path(r'D:\Games\Steam\steamapps\common\dota 2 beta'))

    for base in [
        Path(r'C:\Program Files (x86)\Steam'),
        Path(r'C:\Program Files\Steam'),
        Path(r'C:\steam\steamapps'),
    ]:
        if base.exists():
            candidates.append(base / 'steamapps' / 'common' / 'dota 2 beta')
            candidates.append(base / 'steamapps' / 'common' / 'dota 2 beta')

    for candidate in candidates:
        dota_dir = candidate.resolve(strict=False)
        cfg_dir = dota_dir / 'game' / 'dota' / 'cfg' / 'gamestate_integration'
        if cfg_dir.exists() or dota_dir.exists():
            token = secrets.token_urlsafe(18)
            config_path = cfg_dir / DOTA_GSI_CONFIG_NAME
            text = (
                '"D2PT Draft Bridge"\n'
                '{\n'
                f'    "uri"        "http://127.0.0.1:{port}/"\n'
                '    "timeout"    "5.0"\n'
                '    "buffer"     "0.1"\n'
                '    "throttle"   "0.1"\n'
                '    "heartbeat"  "10.0"\n'
                '    "data"\n'
                '    {\n'
                '        "provider"  "1"\n'
                '        "map"       "1"\n'
                '        "player"    "1"\n'
                '        "hero"      "1"\n'
                '        "abilities" "1"\n'
                '        "items"     "1"\n'
                '        "draft"     "1"\n'
                '        "buildings" "1"\n'
                '        "minimap"  "1"\n'
                '        "roshan"   "1"\n'
                '        "couriers" "1"\n'
                '        "neutralitems" "1"\n'
                '        "events"    "1"\n'
                '    }\n'
                '    "auth"\n'
                '    {\n'
                f'        "token"  "{token}"\n'
                '    }\n'
                '}\n'
            )
            try:
                cfg_dir.mkdir(parents=True, exist_ok=True)
                config_path.write_text(text, encoding='utf-8')
                return True, config_path, token
            except OSError:
                pass
    return False, None, None


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(200, {'ok': True})

    def do_GET(self):
        if self.path == '/health':
            return self._send(200, {
                'ok': True,
                'service': 'D2PT Draft Bridge',
                'screen_capture': screen_capture_status,
            })

        if self.path == '/draft':
            with lock:
                return self._send(200, {
                    **draft,
                    'reset_generation': reset_generation,
                    'watching': watching_enabled,
                })

        if self.path == '/recommendations':
            with lock:
                current = recommendations.copy()
            if time() - current['updated'] > 8:
                current = {'side': '', 'items': [], 'updated': 0}
            return self._send(200, current)

        if self.path == '/gsi-debug':
            with lock:
                return self._send(200, {'received': gsi_received, 'gsi': latest_gsi})

        return self._send(404, {'error': 'Not found'})

    def do_POST(self):
        global draft, latest_gsi, last_gsi_draft, last_gsi_match_id
        global reset_generation, gsi_received
        global recommendations

        if self.path == '/reset':
            with lock:
                draft = {'radiant': [], 'dire': [], 'updated': 0}
                reset_generation += 1
                last_gsi_draft = object()
                generation = reset_generation
            return self._send(200, {'ok': True, 'reset_generation': generation})

        if self.path not in ('/', '/draft', '/gsi', '/recommendations', '/watching'):
            return self._send(404, {'error': 'Not found'})

        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size <= 0 or size > 1048576:
                return self._send(413, {'error': 'Invalid payload size'})

            payload = json.loads(self.rfile.read(size).decode('utf-8'))
            if not isinstance(payload, dict):
                return self._send(400, {'error': 'Expected a JSON object'})

            if self.path == '/watching':
                try:
                    state = _set_watching(payload.get('enabled'))
                except ValueError as exc:
                    return self._send(400, {'error': str(exc)})
                return self._send(200, {'ok': True, **state})

            if self.path == '/recommendations':
                side = payload.get('side')
                items = payload.get('items', [])
                if side not in ('Radiant', 'Dire') or not isinstance(items, list):
                    return self._send(400, {'error': 'Expected side and items'})

                cleaned = []
                for item in items[:5]:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get('name', '')).strip()[:64]
                    if not name:
                        continue
                    cleaned.append({
                        'name': name,
                        'role': str(item.get('role', '')).strip()[:32],
                        'matches': str(item.get('matches', '')).strip()[:32],
                    })
                with lock:
                    recommendations = {'side': side, 'items': cleaned, 'updated': time()}
                return self._send(200, {'ok': True})

            if self.path in ('/', '/gsi'):
                with lock:
                    latest_gsi = payload
                    gsi_received = True
                    map_block = payload.get('map')
                    match_id = str(map_block.get('matchid', '')) if isinstance(map_block, dict) else ''
                    if match_id and match_id != '0' and match_id != last_gsi_match_id:
                        last_gsi_match_id = match_id
                        draft = {'radiant': [], 'dire': [], 'updated': 0}
                    if not watching_enabled:
                        draft = {'radiant': [], 'dire': [], 'updated': 0}
                        return self._send(200, {'ok': True})
                    parsed = extract_draft_from_payload(payload)
                    if _capture_required(payload) is False:
                        draft = {'radiant': [], 'dire': [], 'updated': 0}
                        last_gsi_draft = payload.get('draft')
                        print('Draft cleared; Dota is no longer in a draft phase.')
                    elif parsed['radiant'] or parsed['dire']:
                        draft = {'radiant': parsed['radiant'][:5], 'dire': parsed['dire'][:5], 'updated': time()}
                        last_gsi_draft = payload.get('draft')
                        print('\n=== GSI DRAFT UPDATE ===')
                        print(json.dumps(parsed, ensure_ascii=False, indent=2))
                    elif payload.get('draft') != last_gsi_draft:
                        last_gsi_draft = payload.get('draft')
                        print('GSI received; draft block is empty or not yet populated.')
                return self._send(200, {'ok': True})

            radiant = payload.get('radiant', [])
            dire = payload.get('dire', [])

            if not isinstance(radiant, list) or not isinstance(dire, list):
                return self._send(400, {'error': 'radiant and dire must be arrays'})

            radiant = [str(x).strip() for x in radiant if str(x).strip()][:5]
            dire = [str(x).strip() for x in dire if str(x).strip()][:5]

            with lock:
                if not watching_enabled:
                    return self._send(409, {'error': 'Bridge sync is paused'})
                draft = {'radiant': radiant, 'dire': dire, 'updated': time()}

            return self._send(200, {'ok': True, 'radiant': radiant, 'dire': dire})

        except (ValueError, UnicodeDecodeError):
            return self._send(400, {'error': 'Invalid JSON'})

    def log_message(self, fmt, *args):
        print('%s - %s' % (self.address_string(), fmt % args))


class BridgeServer(ThreadingHTTPServer):
    allow_reuse_address = False


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Standalone Dota 2 GSI -> D2PT Drafter bridge.')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    installed, config_path, token = _ensure_dota_gsi_config(args.port)
    if installed and config_path:
        print(f'Dota GSI config written: {config_path}')
        print('Restart Dota 2 or start a new match so it picks up the new GSI config.')
    else:
        print('Dota install not found automatically. If Dota is installed elsewhere, create a GSI config pointing to http://127.0.0.1:{0}/.'.format(args.port))
        print('Set the Dota launch option: -gamestateintegration')

    print(f'D2PT Draft Bridge running at http://{HOST}:{args.port}')
    print(f'GSI endpoint: http://127.0.0.1:{args.port}/')
    print(f'Draft endpoint: http://127.0.0.1:{args.port}/draft')
    try:
        server = BridgeServer((HOST, args.port), Handler)
    except OSError as exc:
        print(f'Another bridge is already using port {args.port}: {exc}')
        raise SystemExit(1)

    panel_script = Path(__file__).resolve().with_name('recommendation_panel.py')
    panel_python = Path(sys.executable).with_name('pythonw.exe')
    if panel_script.is_file() and panel_python.is_file():
        try:
            subprocess.Popen(
                [str(panel_python), str(panel_script)],
                cwd=str(panel_script.parent),
                close_fds=True,
            )
        except OSError as exc:
            print(f'Recommendation panel could not start: {exc}')

    stop_capture = Event()
    Thread(target=_run_screen_capture, args=(stop_capture,), daemon=True).start()
    print('Keep this window open. Press Ctrl+C to stop.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_capture.set()
        server.server_close()