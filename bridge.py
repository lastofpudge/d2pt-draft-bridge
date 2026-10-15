import argparse
import importlib
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
from pathlib import Path
from threading import Event, Lock, Thread
from time import time
from logging.handlers import RotatingFileHandler

HOST = '127.0.0.1'
DEFAULT_PORT = 8765
LOG_PATH = Path(__file__).resolve().with_name('bridge.log')
lock = Lock()
draft = {'radiant': [], 'dire': [], 'updated': 0}
recommendations = {'radiant': [], 'dire': [], 'updated': 0}
reset_generation = 0
watching_enabled = True
watch_generation = 0
screen_capture_status = 'starting'
logger = logging.getLogger('d2pt_bridge')


def _configure_logging():
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(RotatingFileHandler(
        LOG_PATH,
        encoding='utf-8',
        maxBytes=1_000_000,
        backupCount=3,
    ))


def log_event(event, **details):
    logger.info('%s %s', event, json.dumps(details, ensure_ascii=False, sort_keys=True))


def _set_watching(enabled):
    global draft, watching_enabled, watch_generation
    if not isinstance(enabled, bool):
        raise ValueError('enabled must be a boolean')
    with lock:
        watching_enabled = enabled
        watch_generation += 1
        draft = {'radiant': [], 'dire': [], 'updated': 0}
        log_event('watching-changed', enabled=enabled, generation=watch_generation)
        return {'watching': watching_enabled, 'watch_generation': watch_generation}

HERO_ID_MAP = {
    1: 'Anti-Mage', 2: 'Axe', 3: 'Bane', 4: 'Bloodseeker', 5: 'Crystal Maiden',
    6: 'Drow Ranger', 7: 'Earthshaker', 8: 'Juggernaut', 9: 'Mirana', 10: 'Morphling',
    11: 'Shadow Fiend', 12: 'Phantom Lancer', 13: 'Puck', 14: 'Pudge', 15: 'Razor',
    16: 'Sand King', 17: 'Storm Spirit', 18: 'Sven', 19: 'Tiny', 20: 'Vengeful Spirit',
    21: 'Windranger', 22: 'Zeus', 23: 'Kunkka', 25: 'Lina', 26: 'Lion', 27: 'Shadow Shaman',
    28: 'Slardar', 29: 'Tidehunter', 30: 'Witch Doctor', 31: 'Lich', 32: 'Riki',
    33: 'Enigma', 34: 'Tinker', 35: 'Sniper', 36: 'Necrophos', 37: 'Warlock',
    38: 'Queen of Pain', 39: 'Venomancer', 40: 'Faceless Void', 41: 'Wraith King',
    42: 'Wraith King', 43: 'Death Prophet', 44: 'Phantom Assassin', 45: 'Pugna',
    46: 'Templar Assassin', 47: 'Viper', 48: 'Luna', 49: 'Dragon Knight', 50: 'Dazzle',
    51: 'Clockwerk', 52: 'Leshrac', 53: "Nature's Prophet", 54: 'Lifestealer',
    55: 'Dark Seer', 56: 'Clinkz', 57: 'Omniknight', 58: 'Enchantress', 59: 'Huskar',
    60: 'Night Stalker', 61: 'Broodmother', 62: 'Bounty Hunter', 63: 'Weaver',
    64: 'Jakiro', 65: 'Batrider', 66: 'Chen', 67: 'Spectre', 68: 'Doom', 69: 'Ancient Apparition',
    70: 'Elder Titan', 71: 'Earth Spirit', 72: 'Spirit Breaker', 73: 'Abaddon',
    74: 'Legion Commander', 75: 'Techies', 76: 'Ember Spirit', 77: 'Underlord',
    78: 'Tusk', 79: 'Timbersaw', 80: 'Bristleback', 81: 'Chaos Knight',
    82: 'Treant Protector', 83: 'Ogre Magi', 84: 'Undying', 85: 'Rubick',
    86: 'Disruptor', 87: 'Nyx Assassin', 88: 'Hoodwink', 89: 'Void Spirit',
    90: 'Visage', 91: 'Slark', 92: 'Medusa', 93: 'Troll Warlord',
    94: 'Centaur Warrunner', 95: 'Magnus', 96: 'Timbersaw', 97: 'Bristleback',
    98: 'Tusk', 99: 'Skywrath Mage', 100: 'Abaddon', 101: 'Outworld Destroyer',
    102: 'Shadow Fiend', 103: 'Monkey King', 104: 'Arc Warden', 105: 'Pangolier',
    106: 'Dark Willow', 107: 'Grimstroke', 108: 'Mars', 109: 'Snapfire', 110: 'Void Spirit',
    111: 'Hoodwink', 112: 'Dawnbreaker', 113: 'Muerta', 114: 'Marci', 115: 'Primal Beast',
    116: 'Ringmaster', 117: 'Kez'
}


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
    return bool(screen_draft['radiant'] or screen_draft['dire'])


def _screen_snapshot_has_duplicate_heroes(snapshot, hero_names):
    """A Dota draft cannot contain the same hero twice on one side.

    The portrait detector occasionally repeats the previous slot's ID while a
    portrait is animating.  Treat that as an unreadable frame rather than
    publishing a shortened, shifted draft to the browser.
    """
    for side in ('left', 'right'):
        seen = set()
        for hero_id in getattr(snapshot, side, []) or []:
            try:
                hero_id = int(hero_id)
            except (TypeError, ValueError):
                continue
            if hero_id not in hero_names:
                continue
            if hero_id in seen:
                return True
            seen.add(hero_id)
    return False


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
        log_event('screen-capture-unavailable', reason=screen_capture_status)
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
        log_event('screen-capture-unavailable', reason=screen_capture_status)
        print(f'Screen capture {screen_capture_status}')
        print('Restore bridge_runtime/assets/portraits/library.npz to enable screen recognition.')
        return

    screen_capture_status = provider.start()
    if screen_capture_status.startswith('capture not bound — No window titled'):
        screen_capture_status = 'waiting for the Dota 2 window (use borderless windowed mode)'
    print(f'Dota screen reader: {screen_capture_status}')
    log_event('screen-capture-started', status=screen_capture_status)
    last_screen_draft = None
    last_snapshot_signature = None
    last_rejected_snapshot_signature = None
    last_error = ''
    last_watch_generation = -1

    while not stop_event.is_set():
        with lock:
            enabled = watching_enabled
            current_watch_generation = watch_generation

        if current_watch_generation != last_watch_generation:
            last_screen_draft = None
            last_watch_generation = current_watch_generation

        if not enabled:
            session.set_required(False)
            stop_event.wait(0.25)
            continue

        # Without GSI, let the bundled screen detector decide whether the
        # current Dota view is a draft. It periodically probes idle screens,
        # so a missed gate does not permanently suppress recognition.
        session.set_required(None)
        try:
            snapshot = provider.poll()
            last_error = ''
        except Exception as exc:
            message = str(exc)
            if message != last_error:
                print(f'Screen capture error: {message}')
                log_event('screen-capture-error', error=message)
                last_error = message
            screen_capture_status = f'error: {message}'
            stop_event.wait(1.0)
            continue

        if snapshot.source and snapshot.source != screen_capture_status:
            screen_capture_status = snapshot.source
            print(f'Dota screen reader: {screen_capture_status}')
            log_event('screen-capture-status', status=screen_capture_status)

        snapshot_state = {
            'mode': snapshot.mode,
            'source': snapshot.source,
            'radiant_ids': list(snapshot.left or []),
            'dire_ids': list(snapshot.right or []),
            'unknown_slots': snapshot.unknown,
            'gate_score': snapshot.gate_score,
        }
        snapshot_signature = json.dumps(snapshot_state, sort_keys=True)
        if snapshot_signature != last_snapshot_signature:
            log_event('screen-read', **snapshot_state)
            last_snapshot_signature = snapshot_signature

        if snapshot.mode == 'draft':
            screen_draft = screen_draft_from_snapshot(snapshot, hero_names)
            duplicate_heroes = _screen_snapshot_has_duplicate_heroes(snapshot, hero_names)
            if duplicate_heroes:
                if snapshot_signature != last_rejected_snapshot_signature:
                    log_event(
                        'screen-draft-rejected',
                        reason='duplicate-hero-id',
                        radiant_ids=list(snapshot.left or []),
                        dire_ids=list(snapshot.right or []),
                    )
                    last_rejected_snapshot_signature = snapshot_signature
            elif _screen_draft_is_reliable(screen_draft) and screen_draft != last_screen_draft:
                with lock:
                    draft = {**screen_draft, 'updated': time()}
                    last_screen_draft = screen_draft
                print('\n=== SCREEN DRAFT UPDATE ===')
                print(json.dumps(screen_draft, ensure_ascii=False, indent=2))
                log_event('screen-draft', **screen_draft)
        elif last_screen_draft is not None:
            with lock:
                draft = {'radiant': [], 'dire': [], 'updated': 0}
            last_screen_draft = None
            log_event('screen-draft-cleared', reason='draft-screen-not-detected')

        stop_event.wait(0.25)

    provider.stop()
    screen_capture_status = 'stopped'
    log_event('screen-capture-stopped')


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
                current = {'radiant': [], 'dire': [], 'updated': 0}
            return self._send(200, current)

        return self._send(404, {'error': 'Not found'})

    def do_POST(self):
        global draft, reset_generation
        global recommendations

        if self.path == '/reset':
            with lock:
                draft = {'radiant': [], 'dire': [], 'updated': 0}
                reset_generation += 1
                generation = reset_generation
            log_event('draft-reset', generation=generation)
            return self._send(200, {'ok': True, 'reset_generation': generation})

        if self.path not in ('/draft', '/log', '/recommendations', '/watching'):
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

            if self.path == '/log':
                event = str(payload.get('event', '')).strip()[:64]
                details = payload.get('details', {})
                if not event or not isinstance(details, dict):
                    return self._send(400, {'error': 'Expected event and details'})
                log_event(f'extension.{event}', **details)
                return self._send(200, {'ok': True})

            if self.path == '/recommendations':
                radiant = payload.get('radiant', [])
                dire = payload.get('dire', [])
                if not isinstance(radiant, list) or not isinstance(dire, list):
                    return self._send(400, {'error': 'Expected radiant and dire recommendation arrays'})

                def clean_items(items):
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
                    return cleaned
                with lock:
                    recommendations = {
                        'radiant': clean_items(radiant),
                        'dire': clean_items(dire),
                        'updated': time(),
                    }
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
    parser = argparse.ArgumentParser(description='Dota screen -> D2PT Drafter bridge.')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    _configure_logging()

    print(f'D2PT Draft Bridge running at http://{HOST}:{args.port}')
    print(f'Draft endpoint: http://127.0.0.1:{args.port}/draft')
    print(f'Log file: {LOG_PATH}')
    log_event('bridge-started', port=args.port)
    try:
        server = BridgeServer((HOST, args.port), Handler)
    except OSError as exc:
        print(f'Another bridge is already using port {args.port}: {exc}')
        raise SystemExit(1)

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
        log_event('bridge-stopped')
