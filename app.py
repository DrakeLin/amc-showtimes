"""Vercel entrypoint: durable favorites and bounded on-demand showtimes."""
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

os.environ['AMC_SERVERLESS'] = '1'
from flask import Flask, jsonify, request, send_from_directory
import amc
import server as legacy
from storage import Store

app = Flask(__name__, static_folder='static')
app.config.update(MAX_CONTENT_LENGTH=8192)
store = Store()
DEFAULT_NAMES = ['AMC NewPark 12', 'AMC Mercado 20']
MAX_FAVORITES = 10
TTL = 24 * 3600


def today():
    return datetime.now(ZoneInfo(os.environ.get('AMC_TIMEZONE', 'America/Los_Angeles'))).date()


def dates():
    return [(today() + timedelta(days=i)).isoformat() for i in range(7)]


@app.after_request
def private_responses(response):
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.errorhandler(Exception)
def error_response(exc):
    from werkzeug.exceptions import HTTPException
    if isinstance(exc, HTTPException):
        return jsonify(ok=False, error=exc.description), exc.code
    app.logger.exception('Request failed')
    return jsonify(ok=False, error='Service temporarily unavailable. Please retry.'), 503


@app.get('/')
def index():
    return send_from_directory('static', 'index.html')


def catalog():
    saved = store.get('catalog.json')
    if saved and time.time() - saved['ts'] < 7 * TTL:
        return saved['theaters']
    token = amc.REQUEST_DEADLINE.set(time.monotonic() + 60)
    theaters = []
    try:
        for page in range(1, 21):
            raw = json.loads(amc._get(f'{amc.AMC_BASE}/theatres?pageNumber={page}&pageSize=100', headers=amc._amc_headers(), retries=2))
            rows = raw.get('_embedded', {}).get('theatres', [])
            for row in rows:
                address = row.get('location', {}).get('address', {})
                if not isinstance(address, dict):
                    address = {}
                theaters.append({'id': int(row['id']), 'name': row['name'],
                                 'city': address.get('city', ''), 'state': address.get('state', '')})
            if not rows or len(theaters) >= raw.get('count', len(theaters)):
                break
        else:
            raise RuntimeError('Theater directory pagination limit reached')
        if not theaters:
            raise RuntimeError('AMC returned an empty theater directory')
        store.put('catalog.json', {'ts': time.time(), 'theaters': theaters})
        return theaters
    except Exception:
        if saved:
            return saved['theaters']
        raise
    finally:
        amc.REQUEST_DEADLINE.reset(token)


def favorites():
    saved = store.get('favorites.json')
    if saved is not None:
        return saved
    names = json.loads(os.environ.get('INITIAL_FAVORITE_NAMES', json.dumps(DEFAULT_NAMES)))
    by_name = {t['name'].casefold(): t for t in catalog()}
    # Never guess theater IDs, or silently initialize only half the requested list.
    selected = [by_name[name.casefold()] for name in names]
    initial = {'theaters': selected, 'revision': secrets.token_hex(12)}
    try:
        store.put('favorites.json', initial, overwrite=False)
    except Exception:
        existing = store.get('favorites.json')
        if existing is None:
            raise
        return existing
    return initial


@app.get('/api/theatres')
def theaters():
    q = request.args.get('q', '').strip().casefold()
    entries = catalog()
    matches = [t for t in entries if q in (t['name'] + ' ' + t['city']).casefold()]
    return jsonify(ok=True, theaters=matches[:40])


@app.route('/api/favorites', methods=['GET', 'PUT'])
def favorite_settings():
    if request.method == 'GET':
        return jsonify(ok=True, **favorites())
    if not request.is_json:
        return jsonify(ok=False, error='JSON required'), 415
    body = request.get_json(silent=True) or {}
    ids = body.get('ids')
    if not isinstance(ids, list) or len(ids) > MAX_FAVORITES or any(type(i) is not int for i in ids) or len(set(ids)) != len(ids):
        return jsonify(ok=False, error=f'Choose up to {MAX_FAVORITES} different theaters'), 400
    available = {t['id']: t for t in catalog()}
    if any(i not in available for i in ids):
        return jsonify(ok=False, error='Unknown theater'), 400
    saved = {'theaters': [available[i] for i in ids], 'revision': secrets.token_hex(12)}
    store.put('favorites.json', saved)
    return jsonify(ok=True, **saved)


def day_key(tid, day):
    # Reuse seven slots per theater instead of accumulating a blob each day forever.
    return f'schedules/{tid}/{datetime.fromisoformat(day).weekday()}.json'


def cached_day(tid, day):
    saved = store.get(day_key(tid, day))
    return saved if saved and saved.get('date') == day else None


def metadata(theater_ids=()):
    result = store.get('metadata.json') or {'movies': {}, 'ratings': {}}
    for tid in theater_ids:
        saved = store.get(f'metadata/{tid}.json') or {}
        for kind in ('movies', 'ratings'):
            for mid, entry in saved.get(kind, {}).items():
                if entry.get('ts', 0) >= result[kind].get(mid, {}).get('ts', 0):
                    result[kind][mid] = entry
    return result


def metadata_due(meta, mid):
    now = time.time()
    return (now - meta['movies'].get(mid, {}).get('ts', 0) >= 7 * TTL or
            now - meta['ratings'].get(mid, {}).get('ts', 0) >= 7 * TTL)


@app.post('/api/metadata')
def enrich_metadata():
    tid = (request.get_json(silent=True) or {}).get('theater')
    if type(tid) is not int or not find_theater(tid):
        return jsonify(ok=False, error='Unknown theater'), 400
    titles = {}
    for day in dates():
        saved = cached_day(tid, day)
        for row in (saved or {}).get('raw', []):
            if row.get('movieId'):
                titles[str(row['movieId'])] = row.get('movieTitle') or row.get('movieName', '')
    meta = metadata([tid])
    pending = [mid for mid in sorted(titles) if metadata_due(meta, mid)]
    batch = pending[:4]
    retry_after = 0
    if batch:
        # Identical concurrent batches share a claim. The next batch has a new key.
        digest = hashlib.sha256(','.join(batch).encode()).hexdigest()[:16]
        if not store.claim(f'claims/{int(time.time() // 300)}/metadata-{tid}-{digest}.json'):
            retry_after = 5
        else:
            token = amc.REQUEST_DEADLINE.set(time.monotonic() + 35)
            try:
                # Save posters before slower rating lookups, even if a lookup fails.
                for mid in batch:
                    if time.monotonic() >= amc.REQUEST_DEADLINE.get():
                        break
                    if time.time() - meta['movies'].get(mid, {}).get('ts', 0) >= 7 * TTL:
                        try:
                            meta['movies'][mid] = {**amc.get_movie_details(int(mid)), 'ts': time.time()}
                        except Exception:
                            app.logger.warning('Movie details unavailable for %s', mid)
                for mid in batch:
                    if time.monotonic() >= amc.REQUEST_DEADLINE.get():
                        break
                    if mid not in meta['movies']:
                        continue
                    if time.time() - meta['ratings'].get(mid, {}).get('ts', 0) < 7 * TTL:
                        continue
                    details = meta['movies'][mid]
                    try:
                        rating, synopsis, url = amc.get_lb_data(titles[mid], details.get('release_year'), details.get('director'))
                        # Deadline exhaustion is not a genuine unrated movie.
                        if time.monotonic() < amc.REQUEST_DEADLINE.get():
                            meta['ratings'][mid] = {'rating': rating, 'url': url or '', 'ts': time.time()}
                    except Exception:
                        app.logger.warning('Rating unavailable for %s', mid)
            finally:
                amc.REQUEST_DEADLINE.reset(token)
                store.put(f'metadata/{tid}.json', meta)
    return jsonify(ok=True, metadata={mid: {
        **{k: meta['movies'].get(mid, {}).get(k, '') for k in ('poster', 'synopsis', 'director', 'cast')},
        'lb_rating': meta['ratings'].get(mid, {}).get('rating', 'N/A'),
        'lb_url': meta['ratings'].get(mid, {}).get('url', '')
    } for mid in titles}, pending=sum(metadata_due(meta, mid) for mid in titles), retry_after=retry_after)



def make_movies(raw, theater, day, meta, fetched_at=0):
    grouped = {}
    for show in raw:
        title = show.get('movieTitle') or show.get('movieName') or 'Unknown'
        movie_id = str(show.get('movieId') or '')
        details = meta['movies'].get(movie_id, {})
        rating = meta['ratings'].get(movie_id, {})
        movie = grouped.setdefault(movie_id or title, {
            'id': movie_id or title, 'title': title, 'lb_rating': rating.get('rating', 'N/A'),
            'lb_url': rating.get('url', ''), 'synopsis': details.get('synopsis', ''),
            'poster': details.get('poster', ''), 'director': details.get('director', ''),
            'cast': details.get('cast', ''), 'showings': []})
        time24 = legacy._time24(show)
        if not time24:
            continue
        fmt = amc.get_format(show)
        showing = next((s for s in movie['showings'] if s['format'] == fmt), None)
        if showing is None:
            dt = datetime.fromisoformat(day).date()
            showing = {'date': day, 'date_label': dt.strftime('%a %-m/%-d'),
                       'theatre': theater['name'], 'theatre_short': amc.theatre_short(theater['name']),
                       'format': fmt, 'cached_at': fetched_at, 'times': [], 'times24': [], 'statuses': {},
                       'fill_key': legacy._fill_key(theater['id'], dt, title, fmt)}
            movie['showings'].append(showing)
        if time24 not in showing['times24']:
            showing['times24'].append(time24)
            showing['times'].append(amc.fmt_time(show.get('showDateTimeLocal', '')))
        showing['statuses'][time24] = legacy._seat_status(show)
    return list(grouped.values())


def fetch_day(theater, day, force=False, deadline=None):
    old = cached_day(theater['id'], day)
    if old and time.time() - old['ts'] < (300 if force else TTL):
        return old, None
    # Claim lasts for this time slot, even on upstream error: retries cannot hammer AMC.
    slot = int(time.time() // 300)
    key = f'claims/{slot}/{theater["id"]}-{day}.json'
    if not store.claim(key):
        return old, 'This theater is being refreshed, or was recently attempted. Retry in a few minutes.'
    token = amc.REQUEST_DEADLINE.set(min(deadline or float('inf'), time.monotonic() + 35))
    try:
        raw = amc.fetch_showtimes(theater['id'], datetime.fromisoformat(day).date())
        saved = {'date': day, 'ts': time.time(), 'raw': raw}
        store.put(day_key(theater['id'], day), saved)
        return saved, None
    except Exception:
        app.logger.exception('Theater-day fetch failed')
        return old, 'Could not refresh this date. Previous showtimes are kept; retry in a few minutes.'
    finally:
        amc.REQUEST_DEADLINE.reset(token)


def find_theater(tid):
    return next((t for t in catalog() if t['id'] == tid), None)


@app.get('/api/showtimes')
def showtimes():
    selected = favorites()['theaters']
    requested = request.args.get('theater')
    if requested:
        try:
            theater = find_theater(int(requested))
        except ValueError:
            theater = None
        if not theater:
            return jsonify(ok=False, error='Unknown theater'), 400
        selected = [theater]
    meta = metadata([t['id'] for t in selected])
    movies = []
    pending = []
    stamps = []
    for day in dates():
        for theater in selected:
            saved = cached_day(theater['id'], day)
            if saved:
                movies.extend(make_movies(saved['raw'], theater, day, meta, saved['ts']))
                stamps.append(saved['ts'])
            if not saved or time.time() - saved['ts'] >= TTL:
                pending.append({'theater': theater['id'], 'date': day, 'name': theater['name']})
    return jsonify(ok=True, movies=merge_movies(movies), pending=pending, theaters=selected, dates=dates(),
                   cached_at=min(stamps) if stamps else 0)


def merge_movies(movies):
    grouped = {}
    for movie in movies:
        key = movie['id']
        if key in grouped:
            grouped[key]['showings'].extend(movie['showings'])
        else:
            grouped[key] = {**movie, 'showings': list(movie['showings'])}
    return sorted(grouped.values(), key=lambda m: (-float(m['lb_rating']) if m['lb_rating'] != 'N/A' else 0, m['title']))


@app.post('/api/theater-day')
def theater_day():
    body = request.get_json(silent=True) or {}
    tid, day = body.get('theater'), body.get('date')
    if type(tid) is not int or day not in dates():
        return jsonify(ok=False, error='Choose a theater and a date in the next seven days'), 400
    theater = find_theater(tid)
    if not theater:
        return jsonify(ok=False, error='Unknown theater'), 400
    force = bool(body.get('refresh'))
    saved, warning = fetch_day(theater, day, force)
    return jsonify(ok=True, movies=make_movies(saved['raw'], theater, day, metadata([tid]), saved['ts']) if saved else [],
                   cached_at=saved['ts'] if saved else 0, warning=warning)


@app.get('/api/cron')
def cron():
    secret = os.environ.get('CRON_SECRET', '')
    if not secret or not hmac.compare_digest(request.headers.get('Authorization', '').encode(), ('Bearer ' + secret).encode()):
        return jsonify(ok=False, error='Unauthorized'), 401
    started = time.monotonic()
    selected = favorites()['theaters']
    deadline = started + 200
    refreshed, warnings, movie_ids = 0, [], {}
    for day in dates():
        for theater in selected:
            if time.monotonic() >= deadline:
                warnings.append('Refresh budget reached; remaining dates will load on demand')
                break
            saved, warning = fetch_day(theater, day, force=True, deadline=deadline)
            if warning:
                warnings.append(warning)
            if saved:
                refreshed += 1
                for row in saved['raw']:
                    if row.get('movieId'):
                        movie_ids[str(row['movieId'])] = row.get('movieTitle') or row.get('movieName', '')
    # Enrichment is optional and happens after showtimes have safely been saved.
    meta = metadata()
    token = amc.REQUEST_DEADLINE.set(min(time.monotonic() + 35, started + 250))
    dirty = False
    try:
        for mid, title in movie_ids.items():
            if time.monotonic() >= amc.REQUEST_DEADLINE.get():
                break
            if time.time() - meta['movies'].get(mid, {}).get('ts', 0) >= 7 * TTL:
                details = amc.get_movie_details(int(mid))
                meta['movies'][mid] = {**details, 'ts': time.time()}
                dirty = True
            if time.time() - meta['ratings'].get(mid, {}).get('ts', 0) >= 7 * TTL:
                details = meta['movies'][mid]
                rating, synopsis, url = amc.get_lb_data(title, details.get('release_year'), details.get('director'))
                meta['ratings'][mid] = {'rating': rating, 'url': url or '', 'ts': time.time()}
                dirty = True
    except Exception:
        app.logger.exception('Optional metadata enrichment paused')
    finally:
        amc.REQUEST_DEADLINE.reset(token)
        if dirty:
            store.put('metadata.json', meta)
    return jsonify(ok=True, refreshed=refreshed, warnings=warnings)


@app.get('/api/health')
def health():
    return jsonify(ok=True, mode='vercel',
                   amc_configured=bool(amc.VENDOR_KEY),
                   storage_configured=bool(os.environ.get('BLOB_READ_WRITE_TOKEN') or store.local))
