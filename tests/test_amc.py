"""Unit tests for amc.py's pure helpers — no network, no env vars needed.

Run: python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from datetime import date
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


class TheatreConfigTests(unittest.TestCase):
    def test_parse_theatres(self):
        self.assertEqual(
            amc._parse_theatres("AMC Empire 25:375, AMC Lincoln Square 13:2206"),
            {"AMC Empire 25": 375, "AMC Lincoln Square 13": 2206},
        )

    def test_parse_skips_malformed_entries(self):
        self.assertEqual(amc._parse_theatres("no-id,Good:12,:5,Bad:xyz"), {"Good": 12})

    def test_parse_empty_returns_empty(self):
        self.assertEqual(amc._parse_theatres(""), {})

    def test_theatre_short_explicit_mapping(self):
        self.assertEqual(amc.theatre_short("AMC Metreon 16"), "Metreon")

    def test_theatre_short_derived(self):
        self.assertEqual(amc.theatre_short("AMC Empire 25"), "Empire")

    def test_theatre_short_non_amc_name_untouched(self):
        self.assertEqual(amc.theatre_short("Roxie"), "Roxie")


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


def _lb_page(year, director_slug):
    return (
        f'<meta property="og:title" content="Obsession ({year})">'
        f'<a href="/director/{director_slug}/">Dir</a>'
    )


class FilmMatchesTests(unittest.TestCase):
    def test_director_match(self):
        page = _lb_page(2025, "curry-barker")
        self.assertTrue(amc._film_matches(page, 2026, "CURRY BARKER"))

    def test_director_mismatch_rejects_same_title_same_year(self):
        # The real bug: /film/obsession-2026/ is a different film that happens
        # to share the title *and* the year.
        page = _lb_page(2026, "jackson-treadway")
        self.assertFalse(amc._film_matches(page, 2026, "CURRY BARKER"))

    def test_numbered_director_slug_normalized(self):
        page = _lb_page(1997, "andrei-konchalovsky-1")
        self.assertTrue(amc._film_matches(page, 1997, "Andrei Konchalovsky"))

    def test_multiple_directors_any_match(self):
        page = _lb_page(2026, "phil-lord")
        self.assertTrue(
            amc._film_matches(page, 2026, "Christopher Miller, Phil Lord")
        )

    def test_year_used_when_page_has_no_director(self):
        page = '<meta property="og:title" content="The Odyssey (1997)">'
        self.assertFalse(amc._film_matches(page, 2026, ""))
        self.assertTrue(amc._film_matches(page, 1997, ""))

    def test_festival_year_within_tolerance(self):
        page = '<meta property="og:title" content="Obsession (2025)">'
        self.assertTrue(amc._film_matches(page, 2026, ""))

    def test_no_hints_is_inconclusive(self):
        self.assertIsNone(amc._film_matches(_lb_page(2026, "x"), None, ""))

    def test_no_page_evidence_is_inconclusive(self):
        self.assertIsNone(amc._film_matches("<html></html>", 2026, "Curry Barker"))


class LbSlugCandidateTests(unittest.TestCase):
    def test_bare_slug_first_then_release_years(self):
        urls = amc._lb_slug_candidates("the-odyssey", 2026)
        self.assertEqual(urls[0], "https://letterboxd.com/film/the-odyssey/")
        self.assertIn("https://letterboxd.com/film/the-odyssey-2026/", urls)
        self.assertIn("https://letterboxd.com/film/the-odyssey-2025/", urls)

    def test_collision_suffix_included(self):
        urls = amc._lb_slug_candidates("obsession", 2026)
        self.assertIn("https://letterboxd.com/film/obsession-2026-1/", urls)

    def test_falls_back_to_current_year(self):
        urls = amc._lb_slug_candidates("moana", None)
        self.assertIn(f"https://letterboxd.com/film/moana-{date.today().year}/", urls)


class PersonSlugTests(unittest.TestCase):
    def test_uppercase_amc_name(self):
        self.assertEqual(amc._person_slug("CURRY BARKER"), "curry-barker")

    def test_punctuation_dropped(self):
        self.assertEqual(amc._person_slug("Bong Joon-ho"), "bong-joon-ho")


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
