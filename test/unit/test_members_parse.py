"""Unit tests for the members-page headline-count parser.

`parse_member_count` reads the club's total from the
`<span class='membership-count'>` headline (commas allowed, singular "member"
too) and returns None when that span is absent.
"""
from src.members.members import parse_member_count


def test_member_count_reads_headline_with_or_without_commas():
    assert parse_member_count("<span class='membership-count'>\n1055 members\n</span>") == 1055
    assert parse_member_count('<span class="membership-count">1,055 members</span>') == 1055
    assert parse_member_count("<span class='membership-count'>1 member</span>") == 1


def test_member_count_absent_is_none():
    assert parse_member_count("<h3>1055 members</h3>") is None      # not the headline span
    assert parse_member_count("") is None
