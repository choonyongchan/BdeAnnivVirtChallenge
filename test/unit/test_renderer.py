"""Unit tests for HTML rendering.

`_slim_leaderboard` shrinks the historical payload by dropping the all-constant
fields off non-runner rows and hoisting their shared gap string once.
`build_announcement_html` hides the banner unless there is a real title, and
escapes everything. `render` must consume every placeholder token and drop
valid JSON into the data slots.
"""
import json

from src.dashboard.renderer import build_announcement_html, render, _slim_leaderboard


def test_slim_leaderboard_collapses_only_zero_rows():
    bucket = {"leaderboard": [
        {"name": "A", "acts": 3, "km": 10, "gap": "leader", "unit": "40SAR", "company": "x"},
        {"name": "B", "acts": 0, "km": 0, "gap": "–10.0", "unit": "41SAR", "company": "y"},
        {"name": "C", "acts": 0, "km": 0, "gap": "–10.0", "unit": "", "company": ""},
    ]}
    _slim_leaderboard(bucket)

    assert bucket["zero_gap"] == "–10.0"
    assert bucket["leaderboard"][0]["acts"] == 3                       # runner untouched
    assert bucket["leaderboard"][1] == {"name": "B", "unit": "41SAR", "company": "y"}
    assert set(bucket["leaderboard"][2]) == {"name", "unit", "company"}


def test_announcement_hidden_when_no_usable_title(tmp_path):
    assert build_announcement_html(tmp_path / "missing.md") == ""

    empty = tmp_path / "empty.md"
    empty.write_text("", encoding="utf-8")
    assert build_announcement_html(empty) == ""

    blank_title = tmp_path / "blank.md"
    blank_title.write_text("#   \nbody text", encoding="utf-8")
    assert build_announcement_html(blank_title) == ""


def test_announcement_strips_hash_and_keeps_body(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("#  Welcome Commander\nThe challenge starts Sunday.", encoding="utf-8")
    html = build_announcement_html(p)
    assert 'class="announcement"' in html
    assert ">Welcome Commander<" in html
    assert "The challenge starts Sunday." in html


def test_announcement_escapes_markup(tmp_path):
    p = tmp_path / "a.md"
    p.write_text('# <b>Hi</b> & "go"\nbody <script>x()</script>', encoding="utf-8")
    html = build_announcement_html(p)
    assert "<b>Hi</b>" not in html and "&lt;b&gt;Hi&lt;/b&gt;" in html
    assert "<script>x()</script>" not in html and "&lt;script&gt;" in html


def test_render_consumes_every_placeholder_and_embeds_json():
    data = {"today": {"all": {"leaderboard": [], "total_km": 1.5}}}
    daily = {"2026-09-14": {"date": "2026-09-14", "label": "14.9.2026"}}
    page = render(data, daily, "5.9.2026 14:30", "<span>W</span>", "<div>A</div>",
                  "8SAB Anniversary Challenge", 2211123)

    for token in ("__DATA__", "__DAILY_DATA__", "__UPDATED_HUMAN__", "__WEATHER__",
                  "__ANNOUNCEMENT__", "__CLUB_NAME__", "__CLUB_SHORT__", "__CLUB_ID__"):
        assert token not in page

    assert json.dumps(data, ensure_ascii=False) in page
    assert json.dumps(daily, ensure_ascii=False) in page
    assert "8SAB Anniversary Challenge" in page
    assert "2211123" in page                       # club_id stringified


def test_render_club_short_from_name_shape():
    multi = render({}, {}, "", "", "", "Xqz Wvu Tsr", "1")
    assert "XWT" in multi                           # initials of a multi-word name
    single = render({}, {}, "", "", "", "Zzyx", "1")
    assert "ZZYX" in single                         # first 4 chars of a single word


def test_render_escapes_script_breakout_in_embedded_json():
    data = {"today": {"all": {"leaderboard": [
        {"name": "</script><script>alert(1)</script>", "acts": 0},
    ]}}}
    page = render(data, {}, "", "", "", "Club", "1")
    assert "</script><script>alert(1)</script>" not in page
