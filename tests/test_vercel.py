"""Owner authorization, durable state, bounded fetches, and refresh selection."""
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ['AMC_SERVERLESS'] = '1'
import app as web
from storage import Store

# Fixture IDs are deliberately synthetic: production always resolves AMC's catalog.
CATALOG = [{'id': 101, 'name': 'AMC NewPark 12', 'city': 'Newark', 'state': 'CA'},
           {'id': 102, 'name': 'AMC Mercado 20', 'city': 'Santa Clara', 'state': 'CA'},
           {'id': 103, 'name': 'AMC Metreon 16', 'city': 'San Francisco', 'state': 'CA'}]


class VercelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'LOCAL_DATA_DIR': self.temp.name, 'CRON_SECRET': 'cron-test'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.storage_patch = patch.object(web, 'store', Store())
        self.storage_patch.start(); self.addCleanup(self.storage_patch.stop)
        web.app.config.update(TESTING=True)
        web.store.put('catalog.json', {'ts': time.time(), 'theaters': CATALOG})
        self.client = web.app.test_client()
        self.no_network = patch('amc._get', side_effect=AssertionError('Unexpected network call'))
        self.no_network.start(); self.addCleanup(self.no_network.stop)

    def login(self):
        return {}

    def test_defaults_are_newpark_mercado_and_survive_cold_start(self):
        self.assertEqual([t['name'] for t in self.client.get('/api/favorites').json['theaters']], web.DEFAULT_NAMES)
        web.store = Store()
        self.assertEqual([t['id'] for t in web.favorites()['theaters']], [101, 102])

    def test_any_visitor_can_edit_shared_favorites_without_key(self):
        result = self.client.put('/api/favorites', json={'ids': [103]})
        self.assertEqual(result.status_code, 200)
        other = web.app.test_client()
        self.assertEqual([t['id'] for t in other.get('/api/favorites').json['theaters']], [103])

    def test_favorites_require_json(self):
        self.assertEqual(self.client.put('/api/favorites', data='ids=103').status_code, 415)

    def test_invalid_and_unknown_favorites_rejected(self):
        csrf = self.login()
        for ids in [[True], ['101'], [999], [101, 101], [101, 102, 103, 104], 'bad']:
            self.assertEqual(self.client.put('/api/favorites', json={'ids': ids}, headers=csrf).status_code, 400)

    def test_empty_favorites_stay_empty(self):
        self.client.put('/api/favorites', json={'ids': []}, headers=self.login())
        self.assertEqual(self.client.get('/api/favorites').json['theaters'], [])
        self.assertEqual(self.client.get('/api/showtimes').json['pending'], [])

    def test_read_schedule_does_not_scrape_on_cache_miss(self):
        result = self.client.get('/api/showtimes').json
        self.assertEqual(len(result['pending']), 14)
        self.assertEqual(result['movies'], [])

    def test_fetch_cache_and_duplicate_request(self):
        day = web.dates()[0]
        raw = [{'movieId': 8, 'movieTitle': 'Example', 'showDateTimeLocal': day + 'T19:00:00'}]
        with patch('amc.fetch_showtimes', return_value=raw) as fetch:
            first = self.client.post('/api/theater-day', json={'theater': 101, 'date': day})
            second = self.client.post('/api/theater-day', json={'theater': 101, 'date': day})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json['movies'][0]['title'], 'Example')
        self.assertEqual(first.json['cached_at'], second.json['cached_at'])
        self.assertEqual(fetch.call_count, 7)

    def test_failure_preserves_old_snapshot_and_claim_throttles_retries(self):
        day = web.dates()[0]
        old = {'date': day, 'ts': time.time() - 90000, 'raw': []}
        web.store.put(web.day_key(101, day), old)
        with patch('amc.fetch_showtimes', side_effect=TimeoutError) as fetch:
            first = self.client.post('/api/theater-day', json={'theater': 101, 'date': day})
            self.client.post('/api/theater-day', json={'theater': 101, 'date': day})
        self.assertTrue(first.json['warning'])
        self.assertEqual(web.cached_day(101, day), old)
        self.assertEqual(fetch.call_count, 7)

    def test_public_force_refresh_remains_throttled(self):
        with patch('amc.fetch_showtimes', return_value=[]) as fetch:
            body = {'theater': 101, 'date': web.dates()[0], 'refresh': True}
            self.assertEqual(self.client.post('/api/theater-day', json=body).status_code, 200)
            self.client.post('/api/theater-day', json=body)
            self.assertEqual(fetch.call_count, 7)

    def test_cron_protected_and_only_refreshes_current_favorites(self):
        self.client.put('/api/favorites', json={'ids': [102]}, headers=self.login())
        self.assertEqual(self.client.get('/api/cron').status_code, 401)
        with patch.object(web, 'fetch_week', return_value=({'days': {}}, None)) as fetch:
            result = self.client.get('/api/cron', headers={'Authorization': 'Bearer cron-test'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual({call.args[0]['id'] for call in fetch.call_args_list}, {102})

    def test_storage_failure_not_acknowledged_as_saved(self):
        csrf = self.login()
        with patch.object(web.store, 'put', side_effect=RuntimeError('storage down')):
            self.assertEqual(self.client.put('/api/favorites', json={'ids': [101]}, headers=csrf).status_code, 503)

    def test_claim_atomic_and_separate_between_slots(self):
        self.assertTrue(web.store.claim('claims/a.json'))
        self.assertFalse(Store().claim('claims/a.json'))
        self.assertTrue(Store().claim('claims/b.json'))

    def test_expired_deadline_does_not_call_upstream(self):
        self.no_network.stop()
        token = web.amc.REQUEST_DEADLINE.set(time.monotonic() - 1)
        try:
            with patch('urllib.request.urlopen') as request:
                with self.assertRaises(TimeoutError):
                    web.amc._get('https://example.com')
                request.assert_not_called()
        finally:
            web.amc.REQUEST_DEADLINE.reset(token)

    def test_reused_weekday_slot_not_returned_for_wrong_date(self):
        day = web.dates()[0]
        web.store.put(web.day_key(101, day), {'date': '2020-01-01', 'ts': time.time(), 'raw': []})
        self.assertIsNone(web.cached_day(101, day))

    def test_api_never_cached(self):
        self.assertEqual(self.client.get('/api/health').headers['Cache-Control'], 'no-store')

    def test_metadata_populates_posters_and_ratings_without_cron(self):
        day = web.dates()[0]
        web.store.put(web.day_key(101, day), {'date': day, 'ts': time.time(), 'raw': [
            {'movieId': 8, 'movieTitle': 'Example', 'showDateTimeLocal': day + 'T19:00:00'}]})
        with patch('amc.get_movie_details', return_value={'poster': 'https://example.com/poster.jpg', 'release_year': 2026, 'director': 'A Director'}) as details, patch('amc.get_lb_data', return_value=('4.2', '', 'https://letterboxd.com/film/example/')) as rating:
            result = self.client.post('/api/metadata', json={'theater': 101})
            self.assertEqual(result.json['pending'], 0)
            self.assertEqual(result.json['metadata']['8']['lb_rating'], '4.2')
            self.client.post('/api/metadata', json={'theater': 101})
            self.assertEqual(details.call_count, 1)
            self.assertEqual(rating.call_count, 1)
            rating.assert_called_once_with('Example', 2026, 'A Director')
        movie = self.client.get('/api/showtimes').json['movies'][0]
        self.assertEqual(movie['poster'], 'https://example.com/poster.jpg')
        self.assertEqual(movie['lb_rating'], '4.2')

    def test_failed_rating_keeps_successful_poster_and_does_not_cache_failure(self):
        day = web.dates()[0]
        web.store.put(web.day_key(101, day), {'date': day, 'ts': time.time(), 'raw': [{'movieId': 8, 'movieTitle': 'Example'}]})
        with patch('amc.get_movie_details', return_value={'poster': 'poster'}), patch('amc.get_lb_data', side_effect=TimeoutError):
            result = self.client.post('/api/metadata', json={'theater': 101})
        self.assertEqual(result.json['metadata']['8']['poster'], 'poster')
        self.assertEqual(result.json['pending'], 1)
        self.assertNotIn('8', web.store.get('metadata/101.json')['ratings'])

    def test_metadata_rejects_arbitrary_theater(self):
        self.assertEqual(self.client.post('/api/metadata', json={'theater': 999}).status_code, 400)

    def test_ten_theaters_allowed_but_eleven_rejected(self):
        theaters = [{'id': i, 'name': f'AMC Test {i}'} for i in range(201, 212)]
        web.store.put('catalog.json', {'ts': time.time(), 'theaters': theaters})
        ids = [t['id'] for t in theaters]
        self.assertEqual(self.client.put('/api/favorites', json={'ids': ids[:10]}).status_code, 200)
        self.assertEqual(len(self.client.get('/api/favorites').json['theaters']), 10)
        self.assertEqual(self.client.put('/api/favorites', json={'ids': ids}).status_code, 400)
        self.assertEqual(len(self.client.get('/api/favorites').json['theaters']), 10)


    def test_ten_theater_cron_uses_twenty_schedule_writes(self):
        theaters = [{'id': i, 'name': f'AMC Test {i}'} for i in range(201, 211)]
        web.store.put('favorites.json', {'theaters': theaters})
        with patch('amc.fetch_showtimes', return_value=[]) as fetch, patch.object(web.store, 'put', wraps=web.store.put) as put:
            response = self.client.get('/api/cron', headers={'Authorization': 'Bearer cron-test'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(fetch.call_count, 70)
        self.assertEqual(put.call_count, 20)  # ten claims and ten weekly snapshots
        with patch.object(web.store, 'get', wraps=web.store.get) as get:
            response = self.client.get('/api/showtimes')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get.call_count, 22)  # settings + global metadata + two records/theater
        self.assertEqual(response.json['pending'], [])
        self.assertEqual(response.json['metadata_pending'], [])

    def test_week_migrates_old_dates_and_keeps_failed_date(self):
        week = web.dates()
        old = {'date': week[0], 'ts': time.time() - 90000, 'raw': []}
        web.store.put(web.day_key(101, week[0]), old)
        def fetch(tid, day):
            if day.isoformat() == week[0]:
                raise TimeoutError('one date unavailable')
            return []
        with patch('amc.fetch_showtimes', side_effect=fetch), patch.object(web.store, 'put', wraps=web.store.put) as put:
            response = self.client.post('/api/theater-week', json={'theater': 101})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['warning'])
        self.assertEqual(put.call_count, 2)
        saved = web.store.get('schedules/101.json')['days']
        self.assertEqual(len(saved), 7)
        self.assertEqual(saved[week[0]], old)
        self.assertGreater(saved[week[1]]['ts'], old['ts'])
        web.store = Store()  # migration survives a new function instance
        self.assertEqual(web.cached_day(101, week[0]), old)

    def test_week_write_failure_is_not_reported_as_success(self):
        original = web.store.put
        def put(key, *args, **kwargs):
            if key == 'schedules/101.json':
                raise RuntimeError('write failed')
            return original(key, *args, **kwargs)
        with patch('amc.fetch_showtimes', return_value=[]), patch.object(web.store, 'put', side_effect=put):
            response = self.client.post('/api/theater-week', json={'theater': 101})
        self.assertEqual(response.status_code, 503)

    def test_week_rollover_drops_old_dates_and_fetches_only_new_day(self):
        week = web.dates()
        saved = {day: {'date': day, 'ts': time.time(), 'raw': []} for day in week}
        web.store.put('schedules/101.json', {'days': saved})
        tomorrow = web.today() + web.timedelta(days=1)
        with patch.object(web, 'today', return_value=tomorrow), patch('amc.fetch_showtimes', return_value=[]) as fetch:
            response = self.client.post('/api/theater-week', json={'theater': 101})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(fetch.call_count, 1)
        self.assertNotIn(week[0], web.store.get('schedules/101.json')['days'])

    def test_metadata_pending_only_when_known_movies_need_enrichment(self):
        day = web.dates()[0]
        web.store.put('schedules/101.json', {'days': {day: {'date': day, 'ts': time.time(), 'raw': [
            {'movieId': 8, 'movieTitle': 'Example', 'showDateTimeLocal': day + 'T19:00:00'}]}}})
        self.assertEqual(self.client.get('/api/showtimes').json['metadata_pending'], [101])
        web.store.put('metadata/101.json', {'movies': {'8': {'ts': time.time(), 'poster': 'poster'}},
                                          'ratings': {'8': {'ts': time.time(), 'rating': '4.2'}}})
        result = self.client.get('/api/showtimes').json
        self.assertEqual(result['metadata_pending'], [])
        self.assertEqual(result['movies'][0]['poster'], 'poster')
        self.assertEqual(result['movies'][0]['lb_rating'], '4.2')

    def test_week_budget_keeps_existing_data(self):
        with patch('amc.fetch_showtimes') as fetch:
            saved, warning = web.fetch_week(CATALOG[0], deadline=time.monotonic() - 1)
        fetch.assert_not_called()
        self.assertTrue(warning)
        self.assertEqual(saved['days'], {})
