import argparse
import importlib
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic, time
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
gsi_state = None
gsi_received_at = 0.0
gsi_generation = 0
automatic_draft = None
GSI_TIMEOUT = 40.0
HERO_SELECTION = 'DOTA_GAMERULES_STATE_HERO_SELECTION'
DRAFT_PHASES = {HERO_SELECTION, 'DOTA_GAMERULES_STATE_STRATEGY_TIME'}
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


def _gsi_allows_capture():
    return (
        gsi_state is not None
        and gsi_state[0] in DRAFT_PHASES
        and gsi_state[1] in ('radiant', 'dire')
        and monotonic() - gsi_received_at <= GSI_TIMEOUT
    )


def _clear_automatic_draft():
    global draft, automatic_draft
    if automatic_draft is not None and draft is automatic_draft:
        draft = {'radiant': [], 'dire': [], 'updated': 0}
        log_event('screen-draft-cleared', reason='gsi-draft-ended')
    automatic_draft = None


def _receive_gsi(payload):
    global gsi_state, gsi_received_at, gsi_generation
    map_state = payload.get('map', {})
    player = payload.get('player', {})
    if not isinstance(map_state, dict) or not isinstance(player, dict):
        raise ValueError('Expected GSI map and player objects')
    phase = str(map_state.get('game_state', ''))[:80]
    team = str(player.get('team_name', '')).lower()
    if team not in ('radiant', 'dire'):
        team = ''
    state = (phase, team, str(map_state.get('matchid', '')))
    with lock:
        was_active = _gsi_allows_capture()
        if state != gsi_state:
            log_event('gsi-state', phase=phase, player_team=team)
        continuing_draft = (
            was_active and phase in DRAFT_PHASES and gsi_state[1:] == state[1:]
        )
        if state != gsi_state and not continuing_draft:
            gsi_generation += 1
            _clear_automatic_draft()
        elif not was_active and phase in DRAFT_PHASES and team:
            gsi_generation += 1
            _clear_automatic_draft()
        gsi_state = state
        gsi_received_at = monotonic()


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
    38: 'Beastmaster', 39: 'Queen of Pain', 40: 'Venomancer', 41: 'Faceless Void',
    42: 'Wraith King', 43: 'Death Prophet', 44: 'Phantom Assassin', 45: 'Pugna',
    46: 'Templar Assassin', 47: 'Viper', 48: 'Luna', 49: 'Dragon Knight', 50: 'Dazzle',
    51: 'Clockwerk', 52: 'Leshrac', 53: "Nature's Prophet", 54: 'Lifestealer',
    55: 'Dark Seer', 56: 'Clinkz', 57: 'Omniknight', 58: 'Enchantress', 59: 'Huskar',
    60: 'Night Stalker', 61: 'Broodmother', 62: 'Bounty Hunter', 63: 'Weaver',
    64: 'Jakiro', 65: 'Batrider', 66: 'Chen', 67: 'Spectre', 68: 'Ancient Apparition',
    69: 'Doom', 70: 'Ursa', 71: 'Spirit Breaker', 72: 'Gyrocopter', 73: 'Alchemist',
    74: 'Invoker', 75: 'Silencer', 76: 'Outworld Destroyer', 77: 'Lycan', 78: 'Brewmaster',
    79: 'Shadow Demon', 80: 'Lone Druid', 81: 'Chaos Knight', 82: 'Meepo',
    83: 'Treant Protector', 84: 'Ogre Magi', 85: 'Undying', 86: 'Rubick',
    87: 'Disruptor', 88: 'Nyx Assassin', 89: 'Naga Siren', 90: 'Keeper of the Light',
    91: 'Io', 92: 'Visage', 93: 'Slark', 94: 'Medusa', 95: 'Troll Warlord',
    96: 'Centaur Warrunner', 97: 'Magnus', 98: 'Timbersaw', 99: 'Bristleback',
    100: 'Tusk', 101: 'Skywrath Mage', 102: 'Abaddon', 103: 'Elder Titan',
    104: 'Legion Commander', 105: 'Techies', 106: 'Ember Spirit', 107: 'Earth Spirit',
    108: 'Underlord', 109: 'Terrorblade', 110: 'Phoenix', 111: 'Oracle',
    112: 'Winter Wyvern', 113: 'Arc Warden', 114: 'Monkey King', 119: 'Dark Willow',
    120: 'Pangolier', 121: 'Grimstroke', 123: 'Hoodwink', 126: 'Void Spirit',
    128: 'Snapfire', 129: 'Mars', 131: 'Ringmaster', 135: 'Dawnbreaker',
    136: 'Marci', 137: 'Primal Beast', 138: 'Muerta', 145: 'Kez', 155: 'Largo'
}


def screen_draft_from_snapshot(snapshot, hero_names):
    if snapshot is None:
        return None

    def names_by_slot(team_name):
        slots = [None] * 5
        read = getattr(snapshot, 'read', None)
        if read and getattr(read, 'slots', None):
            for s in read.slots:
                if getattr(s, 'rect', None) and s.rect.team == team_name and 0 <= s.rect.slot < 5:
                    if s.hero_id is not None and s.hero_id > 0:
                        name = hero_names.get(int(s.hero_id), '')
                        if name:
                            slots[s.rect.slot] = str(name).strip()
            return slots
        ids = getattr(snapshot, 'left' if team_name == 'radiant' else 'right', []) or []
        for i, hero_id in enumerate(ids[:5]):
            if hero_id is not None and hero_id > 0:
                name = hero_names.get(int(hero_id), '')
                if name:
                    slots[i] = str(name).strip()
        return slots

    radiant = names_by_slot('radiant')
    dire = names_by_slot('dire')
    if not any(radiant) and not any(dire):
        return None
    return {'radiant': radiant, 'dire': dire}


def _screen_draft_is_reliable(screen_draft):
    if not screen_draft:
        return False
    return any(screen_draft.get('radiant', [])) or any(screen_draft.get('dire', []))


def _screen_snapshot_has_duplicate_heroes(snapshot, hero_names):
    """A Dota draft cannot contain the same hero twice across all 10 slots."""
    seen = set()
    read = getattr(snapshot, 'read', None)
    if read and getattr(read, 'slots', None):
        for s in read.slots:
            if s.hero_id is not None and s.hero_id > 0:
                try:
                    hid = int(s.hero_id)
                except (TypeError, ValueError):
                    continue
                if hid not in hero_names:
                    continue
                if hid in seen:
                    return True
                seen.add(hid)
        return False

    for side in ('left', 'right'):
        for hero_id in getattr(snapshot, side, []) or []:
            if hero_id is None:
                continue
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
                clean_name = name.strip()
                if clean_name == 'Outworld Devourer':
                    clean_name = 'Outworld Destroyer'
                elif clean_name == 'Ring Master':
                    clean_name = 'Ringmaster'
                names[hero_id] = clean_name
    return names or HERO_ID_MAP.copy()


def _run_screen_capture(stop_event, debug_dir=None):
    global draft, automatic_draft, screen_capture_status

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

    screen_capture_status = 'waiting for confirmed hero selection'
    last_screen_draft = None
    last_snapshot_signature = None
    last_rejected_snapshot_signature = None
    last_error = ''
    last_capture_generation = None
    capture_running = False
    retry_capture_at = 0.0
    debug_capture_at = 0.0

    while not stop_event.is_set():
        with lock:
            enabled = watching_enabled and _gsi_allows_capture()
            capture_generation = (watch_generation, reset_generation, gsi_generation)
            if not enabled:
                _clear_automatic_draft()

        if not enabled or capture_generation != last_capture_generation:
            session.forget_reading()
            last_screen_draft = None
            last_capture_generation = capture_generation

        if not enabled:
            session.set_required(False)
            if capture_running:
                provider.stop()
                capture_running = False
            screen_capture_status = 'waiting for confirmed hero selection'
            stop_event.wait(0.25)
            continue

        session.set_required(True)
        if not capture_running:
            if monotonic() < retry_capture_at:
                stop_event.wait(0.25)
                continue
            screen_capture_status = provider.start()
            log_event('screen-capture-started', status=screen_capture_status)
            retry_capture_at = monotonic() + 5.0
            if not screen_capture_status.startswith('capturing window '):
                continue
            capture_running = True
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

        if snapshot.stalled or not snapshot.frames_arrived:
            if monotonic() >= retry_capture_at:
                log_event('screen-capture-rebind', frames_arrived=snapshot.frames_arrived)
                provider.stop()
                session.forget_reading()
                capture_running = False
            stop_event.wait(0.25)
            continue

        if snapshot.source and snapshot.source != screen_capture_status:
            screen_capture_status = snapshot.source
            print(f'Dota screen reader: {screen_capture_status}')
            log_event('screen-capture-status', status=screen_capture_status)

        if debug_dir is not None and snapshot.frame is not None and monotonic() >= debug_capture_at:
            debug_capture_at = monotonic() + 2.0
            try:
                cv2 = importlib.import_module('cv2')
                debug_dir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(debug_dir / 'latest.png'), snapshot.frame)
                raw_slots = getattr(snapshot.read_raw, 'slots', [])
                details = {
                    'updated': time(),
                    'frame_size': list(snapshot.frame.shape),
                    'frames_arrived': snapshot.frames_arrived,
                    'frozen_for': snapshot.frozen_for,
                    'slots': [{
                        'team': slot.rect.team,
                        'slot': slot.rect.slot,
                        'hero_id': slot.hero_id,
                        'label': slot.best_label,
                        'distance': slot.distance,
                        'margin': slot.margin,
                        'rect': list(slot.rect.to_pixels(
                            snapshot.frame.shape[1], snapshot.frame.shape[0])),
                    } for slot in raw_slots],
                }
                (debug_dir / 'latest.json').write_text(
                    json.dumps(details, indent=2), encoding='utf-8')
            except Exception as exc:
                log_event('debug-capture-error', error=str(exc))

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
        elif snapshot.source == "capturing 'Dota 2'" and _screen_draft_is_reliable(screen_draft):
            if screen_draft != last_screen_draft:
                with lock:
                    if (not watching_enabled or not _gsi_allows_capture()
                            or capture_generation != (watch_generation, reset_generation, gsi_generation)):
                        continue
                    draft = automatic_draft = {**screen_draft, 'updated': time()}
                    last_screen_draft = screen_draft
                print('\n=== SCREEN DRAFT UPDATE ===')
                print(json.dumps(screen_draft, ensure_ascii=False, indent=2))
                log_event('screen-draft', **screen_draft)
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
            with lock:
                gsi_phase = gsi_state[0] if gsi_state else ''
                gsi_fresh = gsi_state is not None and monotonic() - gsi_received_at <= GSI_TIMEOUT
                gsi_drafting = _gsi_allows_capture()
            return self._send(200, {
                'ok': True,
                'service': 'D2PT Draft Bridge',
                'screen_capture': screen_capture_status,
                'gsi_phase': gsi_phase,
                'gsi_connected': gsi_fresh,
                'gsi_drafting': gsi_drafting,
            })

        if self.path == '/draft':
            with lock:
                if not _gsi_allows_capture():
                    _clear_automatic_draft()
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

        if self.path not in ('/draft', '/gsi', '/log', '/recommendations', '/watching'):
            return self._send(404, {'error': 'Not found'})

        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size <= 0 or size > 1048576:
                return self._send(413, {'error': 'Invalid payload size'})

            payload = json.loads(self.rfile.read(size).decode('utf-8'))
            if not isinstance(payload, dict):
                return self._send(400, {'error': 'Expected a JSON object'})

            if self.path == '/gsi':
                _receive_gsi(payload)
                return self._send(200, {'ok': True})

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
    parser.add_argument('--debug-dir', type=Path, help='Save the latest draft frame and slot readings')
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
    Thread(target=_run_screen_capture, args=(stop_capture, args.debug_dir), daemon=True).start()
    print('Keep this window open. Press Ctrl+C to stop.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_capture.set()
        server.server_close()
        log_event('bridge-stopped')
