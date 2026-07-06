"""Unit tests for amc.py's pure helpers — no network, no env vars needed.

Run: python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import amc


class CleanTitleTests(unittest.TestCase):
    """clean_title strips AMC marketing cruft but never mangles real titles."""

    def test_format_prefix(self):
        self.assertEqual(amc.clean_title("IMAX My Neighbor Totoro"), "My Neighbor Totoro")

    def test_ghibli_fest_suffix(self):
        self.assertEqual(
            amc.clean_title("My Neighbor Totoro - Studio Ghibli Fest 2026"),
            "My Neighbor Totoro",
        )

    def test_anniversary_without_dash(self):
        self.assertEqual(amc.clean_title("Citizen Kane 85th Anniversary"), "Citizen Kane")

    def test_fan_event(self):
        self.assertEqual(amc.clean_title("Supergirl Opening Night Fan Event"), "Supergirl")

    def test_stacked_format_tokens(self):
        self.assertEqual(amc.clean_title("IMAX 3D Avatar"), "Avatar")

    def test_trailing_rerelease_year(self):
        self.assertEqual(amc.clean_title("Moana 2026"), "Moana")

    def test_plain_title_untouched(self):
        self.assertEqual(amc.clean_title("Obsession"), "Obsession")

    def test_title_with_colon_untouched(self):
        self.assertEqual(
            amc.clean_title("Mission: Impossible - The Final Reckoning"),
            "Mission: Impossible - The Final Reckoning",
        )

    def test_never_returns_empty(self):
        # A title that is nothing but stripped tokens falls back to the input.
        self.assertEqual(amc.clean_title("IMAX"), "IMAX")


class TitleMatchTests(unittest.TestCase):
    def test_exact(self):
        self.assertTrue(amc._titles_plausibly_match("Citizen Kane", "Citizen Kane"))

    def test_prefix(self):
        self.assertTrue(amc._titles_plausibly_match("Dune", "Dune Part Two"))

    def test_token_overlap(self):
        self.assertTrue(amc._titles_plausibly_match("The Lord of the Rings", "Lord of the Rings"))

    def test_unrelated(self):
        self.assertFalse(amc._titles_plausibly_match("Citizen Kane", "Toy Story"))

    def test_empty_candidate(self):
        self.assertFalse(amc._titles_plausibly_match("Citizen Kane", None))


class LbSlugTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(amc.lb_slug("My Neighbor Totoro"), "my-neighbor-totoro")

    def test_punctuation_dropped(self):
        self.assertEqual(amc.lb_slug("What's Up, Doc?"), "whats-up-doc")


class ParseLbFilmPageTests(unittest.TestCase):
    def test_rating_from_twitter_meta(self):
        html = '<meta name="twitter:data2" content="4.19 out of 5" />'
        rating, _ = amc._parse_lb_film_page(html)
        self.assertEqual(rating, "4.19")

    def test_rating_from_ld_json_fallback(self):
        html = '{"ratingValue": "3.8"}'
        rating, _ = amc._parse_lb_film_page(html)
        self.assertEqual(rating, "3.8")

    def test_no_rating(self):
        rating, synopsis = amc._parse_lb_film_page("<html></html>")
        self.assertEqual(rating, "N/A")
        self.assertEqual(synopsis, "")

    def test_synopsis_from_og_description(self):
        html = '<meta property="og:description" content="A gentle forest spirit befriends two sisters in the countryside.">'
        _, synopsis = amc._parse_lb_film_page(html)
        self.assertTrue(synopsis.startswith("A gentle forest spirit"))

    def test_short_synopsis_rejected(self):
        html = '<meta property="og:description" content="Too short.">'
        _, synopsis = amc._parse_lb_film_page(html)
        self.assertEqual(synopsis, "")


class GetFormatTests(unittest.TestCase):
    def test_premium_format_wins(self):
        self.assertEqual(amc.get_format({"premiumFormat": "Dolby Cinema"}), "Dolby Cinema")

    def test_from_attributes(self):
        self.assertEqual(amc.get_format({"attributes": ["IMAXSCREEN"]}), "IMAX")

    def test_default_standard(self):
        self.assertEqual(amc.get_format({}), "Standard")


class FmtTimeTests(unittest.TestCase):
    def test_evening(self):
        self.assertEqual(amc.fmt_time("2026-07-06T19:30:00"), "7:30 PM")

    def test_noon_and_midnight(self):
        self.assertEqual(amc.fmt_time("2026-07-06T12:00:00"), "12:00 PM")
        self.assertEqual(amc.fmt_time("2026-07-06T00:05:00"), "12:05 AM")

    def test_garbage_falls_back_to_slice(self):
        # Unparseable input degrades to the raw HH:MM-position slice.
        self.assertEqual(amc.fmt_time("garbage-in-garbage"), "garbage-in-garbage"[11:16])


class ProgressiveQueriesTests(unittest.TestCase):
    def test_shrinks_on_separators(self):
        queries = list(amc._progressive_queries("Foo: Bar - Baz Edition"))
        self.assertEqual(queries[0], amc.clean_title("Foo: Bar - Baz Edition"))
        self.assertIn("Foo", queries)

    def test_plain_title_single_query(self):
        self.assertEqual(list(amc._progressive_queries("Obsession")), ["Obsession"])


if __name__ == "__main__":
    unittest.main()
