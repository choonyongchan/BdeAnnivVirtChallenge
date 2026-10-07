"""Unit tests for the date helpers: shared.data.local_date decides which local day an
activity counts for (parse UTC, assume UTC if naive, convert, take the date)."""
from datetime import date
from zoneinfo import ZoneInfo

import pytest

from frontend.generate import day_label
from shared.data import local_date

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
    assert local_date(iso, SGT) == expected

