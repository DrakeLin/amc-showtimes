"""Storage request counts, freshness, and isolation without live Blob access."""
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from storage import Store


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store()
        self.store.local = self.temp.name

    def test_cached_reads_expire_and_fresh_reads_bypass(self):
        with patch.object(self.store, '_get', side_effect=[{'v': 1}, {'v': 2}, {'v': 3}]) as read:
            with patch('storage.time.monotonic', return_value=100):
                self.assertEqual(self.store.get('a', cached=True), {'v': 1})
                self.assertEqual(self.store.get('a', cached=True), {'v': 1})
                self.assertEqual(read.call_count, 1)
            with patch('storage.time.monotonic', return_value=160):
                self.assertEqual(self.store.get('a', cached=True), {'v': 2})
                self.assertEqual(self.store.get('a'), {'v': 3})
                self.assertEqual(read.call_count, 3)

    def test_missing_records_cached_but_errors_not_cached(self):
        with patch.object(self.store, '_get', side_effect=[RuntimeError('down'), None]) as read:
            with self.assertRaises(RuntimeError):
                self.store.get('a', cached=True)
            self.assertIsNone(self.store.get('a', cached=True))
            self.assertIsNone(self.store.get('a', cached=True))
            self.assertEqual(read.call_count, 2)

    def test_concurrent_reads_share_one_storage_request(self):
        with patch.object(self.store, '_get', return_value={'v': []}) as read:
            with ThreadPoolExecutor(max_workers=8) as pool:
                values = list(pool.map(lambda _: self.store.get('a', cached=True), range(20)))
            self.assertEqual(read.call_count, 1)
            values[0]['v'].append(1)
            self.assertEqual(self.store.get('a', cached=True), {'v': []})

    def test_successful_write_updates_cache_failed_write_invalidates(self):
        value = {'v': []}
        self.store.put('a', value)
        value['v'].append(1)
        self.assertEqual(self.store.get('a', cached=True), {'v': []})
        with patch.object(self.store, '_put', side_effect=RuntimeError('down')):
            with self.assertRaises(RuntimeError):
                self.store.put('a', {'v': [2]})
        with patch.object(self.store, '_get', return_value={'v': [3]}) as read:
            self.assertEqual(self.store.get('a', cached=True), {'v': [3]})
            read.assert_called_once()

    def test_cache_is_bounded(self):
        with patch.object(self.store, '_get', return_value=None):
            for i in range(300):
                self.store.get(str(i), cached=True)
        self.assertEqual(len(self.store._cache), 256)
        self.assertNotIn('0', self.store._cache)

    def test_repeated_claim_does_not_issue_another_put(self):
        with patch.object(self.store, '_put', wraps=self.store._put) as put:
            self.assertTrue(self.store.claim('claims/a'))
            self.assertFalse(self.store.claim('claims/a'))
            self.assertEqual(put.call_count, 1)
        other = Store()
        other.local = self.temp.name
        self.assertFalse(other.claim('claims/a'))

    def test_blob_reads_keep_cdn_cache_and_fresh_reads_bypass_it(self):
        self.store.local = None
        with patch('vercel.blob.BlobClient') as client:
            client.return_value.__enter__.return_value.get.return_value = None
            self.store.get('missing', cached=True)
            self.store.get('missing', cached=True)
            client.return_value.__enter__.return_value.get.assert_called_once_with(
                'missing', access='private', use_cache=True, timeout=10)
            self.store.get('missing')
            self.assertEqual(client.return_value.__enter__.return_value.get.call_count, 2)
            client.return_value.__enter__.return_value.get.assert_called_with(
                'missing', access='private', use_cache=False, timeout=10)
