"""Unit tests for members.py's parsers: the <span class='membership-count'> total (commas, singular ok, None when
absent), the club name, athlete search results and their club hit, and the feed/leaderboard member sources."""
import json

from backend.members.members import (LeaderboardMembers, RecentActivityMembers, club_hit, parse_club_name,
                                 parse_member_count, parse_search)


def test_member_count_reads_headline_with_or_without_commas():
    assert parse_member_count("<span class='membership-count'>\n1055 members\n</span>") == 1055
    assert parse_member_count('<span class="membership-count">1,055 members</span>') == 1055
    assert parse_member_count("<span class='membership-count'>1 member</span>") == 1


def test_member_count_absent_is_none():
    assert parse_member_count("<h3>1055 members</h3>") is None      # not the headline span
    assert parse_member_count("") is None


def test_club_name_from_page_title():
    assert parse_club_name("Singapore Club | BDE ANNIVERSARY VIRTUAL CHALLENGE on Strava") == \
        "BDE ANNIVERSARY VIRTUAL CHALLENGE"
    assert parse_club_name("Log In | Strava") is None


def test_search_results_from_next_data():
    html = ('<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({"props": {"pageProps": {"searchResults": [{"idStr": "1"}]}}}) + "</script>")
    assert parse_search(html) == [{"idStr": "1"}]
    assert parse_search("<html>blocked</html>") is None


def test_club_hit_is_the_first_result_sharing_this_club():
    club = "BDE ANNIVERSARY VIRTUAL CHALLENGE"
    other = {"idStr": "1", "analyticReasonCategory": "common_club", "subtitle": "You and Al are both in Other RC"}
    stranger = {"idStr": "2", "analyticReasonCategory": None, "subtitle": None}
    ours = {"idStr": "3", "analyticReasonCategory": "common_club", "subtitle": f"You and Neo are both in {club}"}
    assert club_hit([other, stranger, ours, dict(ours, idStr="4")], club) == ours
    assert club_hit([other, stranger], club) is None


def test_feed_and_leaderboard_members():
    feed = [{"athlete_id": 1, "athlete_name": "Alice Anon"}, {"athlete_id": 2, "athlete_name": None}]
    assert RecentActivityMembers(feed).athletes == {"1": "Alice Anon", "2": ""}
    board = {"2026-09-28": {"2": {"name": "Bob Bogus"}}, "2026-09-21": {"1": {"name": "Alice Anon"}}}
    assert LeaderboardMembers(board).athletes == {"2": "Bob Bogus", "1": "Alice Anon"}
    assert LeaderboardMembers({}).athletes == {}
