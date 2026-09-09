"""Unit tests for the members-page HTML parser.

The members page has more than one `<ul class='list-athletes'>` block (a small
club-admins list plus the paginated grid). `parse_members` must read anchors
from every block, trim names, skip empty ones, and leave id-dedupe to the
caller.
"""
from src.members.members import parse_members

HTML = """
<div>
  <ul class='list-athletes'>
    <li><a href="/athletes/101" class="avatar">Alice Anon</a></li>
    <li><a href="/athletes/102">Bob Bogus</a></li>
  </ul>
  <p>unrelated markup</p>
  <ul class='list-athletes'>
    <li><a href="/athletes/102">Bob Bogus</a></li>
    <li><a href="/athletes/103">  Cara Cipher  </a></li>
    <li><a href="/athletes/104">    </a></li>
  </ul>
</div>
"""


def test_reads_anchors_from_every_block_and_trims():
    out = parse_members(HTML)
    assert ("101", "Alice Anon") in out
    assert ("103", "Cara Cipher") in out                 # surrounding spaces trimmed


def test_duplicates_across_blocks_are_left_for_the_caller():
    assert parse_members(HTML).count(("102", "Bob Bogus")) == 2


def test_blank_anchor_text_is_skipped():
    assert all(aid != "104" for aid, _ in parse_members(HTML))


def test_no_list_block_returns_empty():
    assert parse_members("<div>no athletes here</div>") == []
    assert parse_members("") == []
