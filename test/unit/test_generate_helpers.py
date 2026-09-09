"""Unit tests for generate.py's pure date/timezone helpers.

`_local_date` is the rule that decides which local day an activity counts for:
parse the UTC stamp, assume UTC if it is naive, convert to the challenge
timezone, take the date. A bad timezone name degrades to UTC rather than
raising. (load_config is deliberately not covered here — config handling is
mid-refactor.)
"""
from datetime import date, timezone
from zoneinfo import ZoneInfo

import pytest

from src.dashboard.generate import _local_date, _zone, day_label

SGT = ZoneInfo("Asia/Singapore")


@pytest.mark.parametrize("d,text", [
    (date(2026, 9, 5), "5.9.2026"),
    (date(2026, 12, 25), "25.12.2026"),
])
def test_day_label_is_not_zero_padded(d, text):
    assert day_label(d) == text


@pytest.mark.parametrize("iso,expected", [
    ("2026-09-14T02:00:00Z", "2026-09-14"),   # 10:00 SGT — same day
    ("2026-09-13T20:00:00Z", "2026-09-14"),   # 04:00 SGT — rolled forward a day
    ("2026-09-14T23:00:00Z", "2026-09-15"),   # 07:00 SGT next day
    ("2026-09-14T15:30:00", "2026-09-14"),    # naive -> assumed UTC -> 23:30 SGT
    ("2026-09-14", "2026-09-14"),             # date only
    ("", ""),
    ("   ", ""),
    ("not-a-date", ""),
    (None, ""),
])
def test_local_date(iso, expected):
    assert _local_date(iso, SGT) == expected


def test_zone_resolves_valid_name():
    assert _zone("Asia/Singapore") == SGT


def test_zone_falls_back_to_utc_on_bad_name():
    assert _zone("Not/AZone") is timezone.utc
