"""Unit tests for members.py's parsers: the <span class='membership-count'> total (commas, singular ok, None when
absent) and the roster rows of the members page's ul.list-athletes lists (Admins and Members)."""
from backend.members.members import parse_member_count, parse_roster


def test_member_count_reads_headline_with_or_without_commas():
    assert parse_member_count("<span class='membership-count'>\n1055 members\n</span>") == 1055
    assert parse_member_count('<span class="membership-count">1,055 members</span>') == 1055
    assert parse_member_count("<span class='membership-count'>1 member</span>") == 1


def test_member_count_absent_is_none():
    assert parse_member_count("<h3>1055 members</h3>") is None      # not the headline span
    assert parse_member_count("") is None


def _li(aid, name):
    """One roster row, as Strava's members page renders it (admin view, with its action buttons)."""
    return (f"<li><div class='avatar'></div><div class='text-headline'><a href=\"/athletes/{aid}\">{name}</a></div>"
            f"<div class='location'>Singapore</div><div class='action right'>"
            f"<a class=\"minimal compact button\" href=\"/clubs/1/members/{aid}?move=grant_admin\">Make Admin</a>"
            f"</div></li>")


def page(admins, members):
    return ("<nav><a class='nav-link' href='/athletes/999'>Me</a></nav>"          # own profile link: not a member row
            "<h2>Admins</h2><ul class='list-athletes'>" + "".join(_li(*a) for a in admins) + "</ul>"
            "<h2>Members</h2><ul class=\"list-athletes\">" + "".join(_li(*m) for m in members) + "</ul>"
            "<div class='text-headline'><a href=\"/athletes/888\">Sidebar</a></div>")  # outside the lists


def test_roster_reads_admins_and_members_only():
    html = page([("1", "Alice Anon")], [("2", "Bob &amp; Co"), ("3", " Cara ")])
    assert parse_roster(html) == {"1": "Alice Anon", "2": "Bob & Co", "3": "Cara"}


def test_roster_of_a_page_past_the_end_is_just_the_admins():
    assert parse_roster(page([("1", "Alice Anon")], [])) == {"1": "Alice Anon"}
    assert parse_roster("<p>blocked</p>") == {}
