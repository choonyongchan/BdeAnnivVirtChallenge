"""
Activity and member ledgers — the append-only stores generate.py builds on.

Split out of generate.py to keep that module focused on fetch -> compute ->
render. build_daily_history() stays in generate.py because it's built on
build_grouped_data().
"""
import json
from itertools import groupby
from pathlib import Path

LEDGER_PATH = Path(__file__).parent / "activity-ledger.json"
CLEAN_LEDGER_PATH = Path(__file__).parent / "activity-ledger-clean.json"


def _load(path: Path) -> list:
    """The JSON list at path, or [] if it doesn't exist yet."""
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _save(path: Path, entries: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")


class Ledger:
    """The raw (append-only) activity ledger plus its deduped "clean" derivative.

    ponytail: no activity id/timestamp in Strava's club feed, so new entries
    are found by anchoring on the ledger's recent entries inside the fresh
    fetch. Strava can revise a logged activity's own fields after the fact
    (elevation correction), so a single mutated entry shouldn't sink the
    whole anchor: match each of the last ANCHOR_WINDOW ledger entries
    independently and require only ANCHOR_MIN_MATCHES of them to be found.
    Matches must also land in the same relative order as the ledger entries
    they came from (both are newest-first) — two unrelated activities from
    the same runner's routine loop can coincidentally share a content key,
    and without an order check that coincidence alone could satisfy
    ANCHOR_MIN_MATCHES and pick a wrong cut point. Anchor still misses if
    too few match or matches are out of order (>197 new activities in an
    hour, or multiple anchored activities edited/deleted) -> append
    everything, accept occasional double-count.
    """

    ANCHOR_WINDOW = 8
    ANCHOR_MIN_MATCHES = 3

    def __init__(self):
        self.path = LEDGER_PATH
        self.clean_path = CLEAN_LEDGER_PATH
        self.activities = _load(self.path)
        self.anchor_missed = False

    @staticmethod
    def _activity_key(act: dict) -> tuple:
        """Content key used for both anchor matching and dedup.

        total_elevation_gain is excluded — Strava revises it after the fact
        (elevation correction), which would otherwise change an already-
        ledgered activity's key and let it slip past the existing_keys dedup
        as a "new" entry, double-counting it.
        """
        athlete = act.get("athlete") or {}
        name = (athlete.get("firstname", ""), athlete.get("lastname", ""))
        return (
            name,
            act.get("name"),
            act.get("distance"),
            act.get("moving_time"),
            act.get("elapsed_time"),
            act.get("device_name"),
        )

    def merge(self, fresh: list, now_iso: str) -> int:
        """Merge freshly fetched activities (newest-first) into self.activities
        (newest-first), in place. Returns the count of new entries.

        The anchor match is only a fast path for finding new activities — Strava's
        club feed has no activity id/timestamp, so the anchor can legitimately miss
        (>197 new activities in an hour, or too many anchored activities
        edited/deleted). Content-key dedup below is the actual correctness
        guarantee: it runs unconditionally, so a missed or stale anchor can
        degrade to "append everything" but can never reintroduce an activity
        already in the ledger.
        """
        ledger = self.activities
        if not ledger:
            new_entries, self.anchor_missed = fresh, False
            print("  Ledger empty — no anchor to match, treating full fetch as new.")
        else:
            candidates = ledger[:self.ANCHOR_WINDOW]
            fresh_index = {}
            for i, a in enumerate(fresh):
                fresh_index.setdefault(self._activity_key(a), i)

            # candidates and fresh are both newest-first, so genuine matches
            # must appear in fresh in the same relative order as in candidates.
            matched_at = [fresh_index[self._activity_key(a)] for a in candidates
                          if self._activity_key(a) in fresh_index]
            ordered = all(a < b for a, b in zip(matched_at, matched_at[1:]))
            required = min(self.ANCHOR_MIN_MATCHES, len(candidates))
            self.anchor_missed = len(matched_at) < required or not ordered

            if self.anchor_missed:
                new_entries = fresh
                if ordered:
                    print(f"  Anchor NOT found: only {len(matched_at)}/{required} of last "
                          f"{len(candidates)} ledger entries matched in fresh fetch.")
                else:
                    print(f"  Anchor REJECTED: {len(matched_at)} matches found but out of "
                          f"order ({matched_at}) — likely a coincidental content-key collision.")
            else:
                cut_at = min(matched_at)
                new_entries = fresh[:cut_at]
                print(f"  Anchor found: {len(matched_at)}/{len(candidates)} of last "
                      f"{len(candidates)} ledger entries matched, cutting at fresh[{cut_at}].")

        existing_keys = {self._activity_key(a) for a in ledger}
        new_entries = [a for a in new_entries if self._activity_key(a) not in existing_keys]

        stamped = [{**a, "ingested_at": now_iso} for a in new_entries]
        self.activities = stamped + ledger
        return len(new_entries)

    def save(self):
        _save(self.path, self.activities)

    @staticmethod
    def _upload_batch(act: dict) -> tuple:
        """Identity of the upload batch an entry belongs to: same athlete,
        same scrape."""
        ath = act.get("athlete") or {}
        return act.get("ingested_at"), ath.get("firstname"), ath.get("lastname")

    @classmethod
    def dedup_consecutive(cls, entries: list) -> list:
        """Collapse runs of *consecutive* same-athlete activities ingested in the
        same batch (e.g. multiple runs uploaded together) into the single
        longest-distance entry, preserving order."""
        return [max(group, key=lambda a: a.get("distance") or 0)
                for _key, group in groupby(entries, key=cls._upload_batch)]

    def build_clean(self, new_count: int) -> list:
        """Update and persist the clean (deduped) ledger given how many new
        raw entries were just merged in by merge()."""
        if self.clean_path.exists():
            clean_ledger = self.dedup_consecutive(self.activities[:new_count]) + _load(self.clean_path)
        else:
            # bootstrap: no prior clean ledger, dedup the whole history at once
            clean_ledger = self.dedup_consecutive(self.activities)
        _save(self.clean_path, clean_ledger)
        return clean_ledger


MEMBERS_LEDGER_PATH = Path(__file__).parent / "members-ledger.json"


class MembersLedger:
    """Append-only ledger of club members, structured like the activity
    Ledger. Each entry is a raw member dict (as fetched from Strava) plus
    'ingested_at' — stamped only the first time that member is seen, so it
    doubles as a proxy "registration date" (Strava's club API exposes none).
    Existing entries are never rewritten, matching how the activity ledger
    treats ingested_at as immutable once stamped.
    """

    def __init__(self):
        self.path = MEMBERS_LEDGER_PATH
        # [{...raw member fields, "ingested_at": iso}, ...]
        self.members = _load(self.path)

    @staticmethod
    def _member_key(m: dict):
        """id when Strava provides one, else the raw name pair — mirrors
        Ledger._activity_key's role as a stable identity for dedup."""
        if m.get("id") is not None:
            return m["id"]
        return (m.get("firstname"), m.get("lastname"))

    def merge(self, fresh: list, now_iso: str) -> int:
        """Append only members not already in the ledger, stamped with
        now_iso. Returns the count of newly-registered members."""
        existing_keys = {self._member_key(m) for m in self.members}
        new_entries = [m for m in fresh if self._member_key(m) not in existing_keys]
        stamped = [{**m, "ingested_at": now_iso} for m in new_entries]
        self.members = stamped + self.members
        return len(new_entries)

    def save(self) -> None:
        _save(self.path, self.members)

    def members_for_date(self, date_str: str) -> list:
        """Every member whose first-seen date is on or before date_str —
        i.e. who was already registered as of that date."""
        return [m for m in self.members if m.get("ingested_at", "")[:10] <= date_str]
