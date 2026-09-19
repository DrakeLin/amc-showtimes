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
        self.secret = patch.object(web, 'owner_key', 'test-only-access-key-' + 'x' * 32)
        self.secret.start(); self.addCleanup(self.secret.stop)
        web.app.config.update(TESTING=True, SECRET_KEY='test-only-session-key', SESSION_COOKIE_SECURE=False)
        web.store.put('catalog.json', {'ts': time.time(), 'theaters': CATALOG})
        self.client = web.app.test_client()
        self.no_network = patch('amc._get', side_effect=AssertionError('Unexpected network call'))
        self.no_network.start(); self.addCleanup(self.no_network.stop)

    def login(self):
        result = self.client.post('/api/login', json={'key': web.owner_key})
        self.assertEqual(result.status_code, 200)
        return {'X-CSRF-Token': result.json['csrf']}

    def test_defaults_are_newpark_mercado_and_survive_cold_start(self):
        self.assertEqual([t['name'] for t in self.client.get('/api/favorites').json['theaters']], web.DEFAULT_NAMES)
        web.store = Store()
        self.assertEqual([t['id'] for t in web.favorites()['theaters']], [101, 102])

    def test_visitors_cannot_edit_even_with_forged_csrf(self):
        result = self.client.put('/api/favorites', json={'ids': [103]}, headers={'X-CSRF-Token': 'forged'})
        self.assertEqual(result.status_code, 401)
        self.assertIsNone(web.store.get('favorites.json'))

    def test_owner_needs_csrf_can_replace_favorites_and_logout(self):
        csrf = self.login()
        self.assertEqual(self.client.put('/api/favorites', json={'ids': [103]}).status_code, 403)
        result = self.client.put('/api/favorites', json={'ids': [103]}, headers=csrf)
        self.assertEqual(result.status_code, 200)
        self.assertEqual([t['id'] for t in Store().get('favorites.json')['theaters']], [103])
        self.client.post('/api/logout', headers=csrf)
        self.assertEqual(self.client.put('/api/favorites', json={'ids': []}, headers=csrf).status_code, 401)

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
        self.assertEqual(fetch.call_count, 1)

    def test_failure_preserves_old_snapshot_and_claim_throttles_retries(self):
        day = web.dates()[0]
        old = {'date': day, 'ts': time.time() - 90000, 'raw': []}
        web.store.put(web.day_key(101, day), old)
        with patch('amc.fetch_showtimes', side_effect=TimeoutError) as fetch:
            first = self.client.post('/api/theater-day', json={'theater': 101, 'date': day})
            self.client.post('/api/theater-day', json={'theater': 101, 'date': day})
        self.assertTrue(first.json['warning'])
        self.assertEqual(web.cached_day(101, day), old)
        self.assertEqual(fetch.call_count, 1)

    def test_force_refresh_requires_owner(self):
        result = self.client.post('/api/theater-day', json={'theater': 101, 'date': web.dates()[0], 'refresh': True})
        self.assertEqual(result.status_code, 401)

    def test_cron_protected_and_only_refreshes_current_favorites(self):
        self.client.put('/api/favorites', json={'ids': [102]}, headers=self.login())
        self.assertEqual(self.client.get('/api/cron').status_code, 401)
        with patch.object(web, 'fetch_day', return_value=({'raw': []}, None)) as fetch:
            result = self.client.get('/api/cron', headers={'Authorization': 'Bearer cron-test'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(fetch.call_count, 7)
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
        self.assertEqual(self.client.get('/api/session').headers['Cache-Control'], 'no-store')
