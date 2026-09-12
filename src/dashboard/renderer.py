"""HTML rendering: load template.html and substitute its __PLACEHOLDER__ tokens.

The page markup/CSS/JS lives in the sibling template.html (edit it there and
regenerate); this module only fills its placeholders with the computed data.
"""
import html
import json
from pathlib import Path

# The dashboard page, verbatim; render() swaps its __PLACEHOLDER__ tokens.
TEMPLATE = (Path(__file__).with_name("template.html")).read_text(encoding="utf-8")


def _slim_leaderboard(bucket: dict) -> None:
    """Strip the all-constant fields off a bucket's zero-activity rows, in place.

    ~69% of the rows across every historical snapshot are members who hadn't
    run yet, and every field on such a row is fixed except "gap" — which is
    the same string for every zero row in the bucket, so it is hoisted to
    "zero_gap" and stored once. Only name/unit/company carry per-row
    information. expandBucket() in the page reverses this at render time.
    """
    rows = bucket["leaderboard"]
    zero_gap = next((r["gap"] for r in rows if not r["acts"]), None)
    if zero_gap is not None:
        bucket["zero_gap"] = zero_gap
    bucket["leaderboard"] = [
        r if r["acts"] else {"name": r["name"], "unit": r["unit"], "company": r["company"]}
        for r in rows
    ]


def build_announcement_html(path: Path) -> str:
    """Read announcement.md (# Title, then body text) into a dismissible banner.

    Returns '' if the file is missing or has no title, hiding the banner.
    """
    path = Path(path)
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    if not lines:
        return ""
    title = lines[0].lstrip("#").strip()
    body = "\n".join(lines[1:]).strip()
    if not title:
        return ""
    title_html = html.escape(title)
    body_html = html.escape(body)
    return (
        '<div class="announcement" id="announcement">'
        f'<div class="announcement-title">{title_html}</div>'
        f'<div class="announcement-body">{body_html}</div>'
        '<button class="announcement-close" onclick="document.getElementById(\'announcement\').style.display=\'none\'" title="Close">✕</button>'
        '</div>'
    )


def _json_for_script(value) -> str:
    """json.dumps for embedding inside a <script> tag: escape '</' so a literal
    </script> in the data (e.g. an athlete name) can't close the tag early."""
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def render(data, daily, updated_human, weather_html, announcement_html, club_name, club_id):
    """Fill the template's placeholders and return the finished page string."""
    words = club_name.split()
    club_short = "".join(w[0] for w in words).upper() if len(words) > 1 else club_name[:4].upper()

    page = TEMPLATE
    for placeholder, value in (
        ("__DATA__", _json_for_script(data)),
        ("__DAILY_DATA__", _json_for_script(daily)),
        ("__UPDATED_HUMAN__", updated_human),
        ("__WEATHER__", weather_html),
        ("__ANNOUNCEMENT__", announcement_html),
        ("__CLUB_NAME__", club_name),
        ("__CLUB_SHORT__", club_short),
        ("__CLUB_ID__", str(club_id)),
    ):
        page = page.replace(placeholder, value)
    return page
