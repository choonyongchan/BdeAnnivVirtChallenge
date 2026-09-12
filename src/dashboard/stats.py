"""Statistics computation engine for scraped club activities.

Ported from src_bak/report_generator.py. Two changes for the CSV data model:
  * activity/member fields are the flat activities.csv / members.csv columns
    (distance_m, moving_time_s, elev_gain_m, athlete_id, athlete_name, name),
    and numeric columns may be blank -> coerced to 0;
  * athletes are matched by real athlete_id (activities <-> members) before
    falling back to name resolution, instead of a nested athlete dict.
The public compute_stats() signature and ReportStats.to_dict() output shape are
unchanged, so renderer.TEMPLATE consumes it untouched.
"""
from collections import Counter
from dataclasses import asdict, dataclass, field

from .names import NominalRoll


def _num(value) -> float:
    """A CSV cell as a float; blank or unparseable -> 0.0."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class AthleteStats:
    """Accumulated per-athlete totals for one report period."""

    name: str
    """Canonical roster name (or raw Strava name when unmatched)."""
    unit: str = ""
    """Roster unit, e.g. "40SAR"; blank when off the roll."""
    company: str = ""
    """Roster company, unit-qualified ("40SAR/Cougar"); blank when unknown."""

    km: float = 0.0
    """Total distance run, kilometres."""
    elev: float = 0.0
    """Total elevation gain, metres."""
    time_s: float = 0.0
    """Total moving time, seconds."""
    speeds: list = field(default_factory=list)
    """Per-activity m/s speeds, only for runs over 0.5 km (feeds avg_speed)."""
    count_acts: int = 0
    """Number of activities counted."""
    longest: float = 0.0
    """Longest single activity, kilometres."""
    devices: set = field(default_factory=set)
    """Distinct recording device names seen."""

    climber_run_elev: float = 0.0
    """Elevation from "real hill" runs only (>= 5 km and >= 8 m+/km)."""
    climber_run_km: float = 0.0
    """Distance from those same "real hill" runs."""

    break_time: float = 0.0
    """Total elapsed-minus-moving time, i.e. time spent stopped."""

    def add_activity(self, act: dict) -> tuple:
        """Accumulate one activity, returning (dist_km, elev) for club totals."""
        dist_m = _num(act.get("distance_m"))
        dist_km = dist_m / 1000
        elev = _num(act.get("elev_gain_m"))
        time_s = _num(act.get("moving_time_s"))
        elapsed = _num(act.get("elapsed_time_s"))
        speed = (dist_m / time_s) if time_s > 0 else 0
        dev = act.get("device_name", "") or ""

        self.km += dist_km
        self.elev += elev
        self.time_s += time_s
        self.count_acts += 1
        if dist_km > self.longest:
            self.longest = dist_km
        if speed > 0 and dist_km > 0.5:
            self.speeds.append(speed)
        if dev:
            self.devices.add(dev)
        if dist_km >= 5 and (elev / dist_km) >= 8:
            self.climber_run_elev += elev
            self.climber_run_km += dist_km

        self.break_time += max(0, elapsed - time_s)

        return dist_km, elev

    @property
    def avg_speed(self):
        """Mean speed in m/s across qualifying activities, or None."""
        return sum(self.speeds) / len(self.speeds) if self.speeds else None

    @staticmethod
    def fmt_time(seconds: float) -> str:
        """Format a duration as ``"2h 5m"``."""
        h, rem = divmod(int(seconds), 3600)
        return f"{h}h {rem // 60}m"

    @staticmethod
    def spd_kmh(ms: float) -> str:
        """Format a m/s speed as ``"12.3 km/h"``."""
        return f"{ms * 3.6:.1f} km/h"

    def to_leaderboard_entry(self, leader_km: float) -> dict:
        """Render these totals as one display-ready leaderboard row."""
        gap = leader_km - self.km
        avg_speed = self.avg_speed
        return {
            "name": self.name,
            "km": round(self.km, 1),
            "elev": round(self.elev),
            "time": self.fmt_time(self.time_s),
            "time_s": int(self.time_s),
            "acts": self.count_acts,
            "avg_speed": self.spd_kmh(avg_speed) if avg_speed is not None else "–",
            "avg_speed_ms": round(avg_speed, 4) if avg_speed is not None else 0,
            "longest": round(self.longest, 1),
            "gap": f"–{gap:.1f}" if gap > 0 else "leader",
            "elev_per_km": round(self.elev / self.km, 1) if self.km > 0 else None,
            "unit": self.unit,
            "company": self.company,
        }


@dataclass
class ReportStats:
    """Everything one reporting period contributes to the dashboard.

    Every field defaults to its empty value, so a period with no activities
    and no members is a plain ``ReportStats()`` rather than a special case.
    Each award is None when nobody qualified for it.
    """

    total_km: float = 0.0
    """Club-wide distance for the period, kilometres."""
    total_elev: float = 0.0
    """Club-wide elevation gain for the period, metres."""
    run_count: int = 0
    """Number of activities in the period."""
    athlete_count: int = 0
    """Number of registered members (from members.csv), runners or not."""
    leaderboard: list = field(default_factory=list)
    """Display-ready rows, km-ranked, one per athlete incl. zero rows for non-runners."""
    fun_stats: dict = field(default_factory=dict)
    """Novelty awards, e.g. {"breaks": {...} | None}."""
    king_km: dict | None = None
    """Most distance: {"name", "value"} or None if nobody qualified."""
    king_elev: dict | None = None
    """Most elevation gain: {"name", "value"} or None."""
    marathoner: dict | None = None
    """Most moving time: {"name", "value"} or None."""
    fastest: dict | None = None
    """Best average speed: {"name", "value"} or None."""
    longest: dict | None = None
    """Longest single run: {"name", "value"} or None."""
    climber: dict | None = None
    """Steepest sustained climber (>= 30 hill-km): {"name", "value"} or None."""
    flatrunner: dict | None = None
    """Flattest route over >= 50 km: {"name", "value"} or None."""
    device_stats: list = field(default_factory=list)
    """[{"device", "count"}] runners per recording device, hardware first."""

    def to_dict(self) -> dict:
        """Serialise to a plain dict for json.dumps."""
        return asdict(self)


def _device_sort(item):
    """Sort key placing real hardware ahead of virtual platforms."""
    d, c = item
    dl = d.lower()
    if "strava" in dl:
        return (3, 0, dl)
    if "rouvy" in dl:
        return (2, 0, dl)
    if "zwift" in dl:
        return (1, 0, dl)
    return (0, -c, dl)


def _new_athlete(name: str, roll: NominalRoll) -> AthleteStats:
    """Empty accumulator with unit/company filled in from the roll."""
    uc = roll.unit_company(name) if roll else {}
    return AthleteStats(
        name,
        unit=uc.get("unit", ""),
        company=uc.get("company", ""),
    )


def resolve_name(raw_name: str, roll: NominalRoll) -> str:
    """Canonical roster name for a raw Strava display name."""
    return roll.resolve(raw_name) if roll else (raw_name or "").strip()


def _roster_name(act: dict, roll: NominalRoll, member_by_id: dict) -> str:
    """The roster name for an activity: via its athlete_id's member, else its own name."""
    member = member_by_id.get(str(act.get("athlete_id") or ""))
    raw = member.get("name", "") if member else act.get("athlete_name", "")
    return resolve_name(raw, roll)


def _accumulate_athletes(activities: list, roll: NominalRoll, member_by_id: dict) -> tuple:
    """Fold every activity into per-athlete accumulators, keyed by resolved name.

    Returns (athletes, total_km, total_elev).
    """
    athletes: dict = {}
    total_km = 0.0
    total_elev = 0.0

    for act in activities:
        name = _roster_name(act, roll, member_by_id)
        if name not in athletes:
            athletes[name] = _new_athlete(name, roll)

        dist_km, elev = athletes[name].add_activity(act)
        total_km += dist_km
        total_elev += elev

    return athletes, total_km, total_elev


def _build_device_stats(athletes: dict) -> list:
    """Count distinct runners per recording device, unnamed devices dropped."""
    counts = Counter(dev for a in athletes.values() for dev in a.devices)
    return [
        {"device": d, "count": c}
        for d, c in sorted(counts.items(), key=_device_sort)
        if d
    ]


def _build_leaderboard(athletes: dict, members: list, roll: NominalRoll) -> list:
    """The full leaderboard, km-ranked, with every non-running member appended
    as a zero row so the table shows the whole unit."""
    ranked = sorted(athletes.values(), key=lambda a: a.km, reverse=True)
    leader_km = ranked[0].km if ranked else 0
    leaderboard = [a.to_leaderboard_entry(leader_km) for a in ranked]

    listed = set(athletes.keys())
    for m in members or []:
        name = resolve_name(m.get("name", ""), roll)
        if name and name not in listed:
            leaderboard.append(_new_athlete(name, roll).to_leaderboard_entry(leader_km))
            listed.add(name)

    return leaderboard


def _award(values: dict, val_fn, pick=max) -> dict | None:
    """Winner of one category, or None when nobody qualified."""
    if not values:
        return None
    name = pick(values, key=values.get)
    return {"name": name, "value": val_fn(values[name])}


def _compute_awards(athletes: dict) -> dict:
    """Pick the winner of every award category."""
    active = [a for a in athletes.values() if a.count_acts > 0]

    climber = {
        a.name: a.climber_run_elev / a.climber_run_km
        for a in athletes.values()
        if a.climber_run_km >= 30 and a.climber_run_elev / a.climber_run_km > 5
    }
    flat = {a.name: a.elev / a.km for a in athletes.values() if a.km >= 50}

    return {
        "king_km":    _award({a.name: a.km for a in active}, lambda v: f"{v:.1f} km"),
        "king_elev":  _award({a.name: a.elev for a in active},
                             lambda v: f"{v:,.0f} m elevation".replace(",", " ")),
        "marathoner": _award({a.name: a.time_s for a in active}, AthleteStats.fmt_time),
        "fastest":    _award({a.name: a.avg_speed for a in athletes.values()
                              if a.avg_speed is not None}, AthleteStats.spd_kmh),
        "longest":    _award({a.name: a.longest for a in active}, lambda v: f"{v:.1f} km"),
        "climber":    _award(climber, lambda v: f"{v:.1f} m+/km"),
        "flatrunner": _award(flat, lambda v: f"{v:.1f} m+/km", pick=min),
    }


def _compute_fun_stats(athletes: dict) -> dict:
    """Novelty statistics shown alongside the main awards."""
    breaks = None
    times = {a.name: a.break_time for a in athletes.values()}
    if times:
        name = max(times, key=times.get)
        if times[name] > 60:
            breaks = {"name": name, "value": f"{int(times[name] // 60)} min of rest"}
    return {"breaks": breaks}


def compute_stats(activities: list, members: list = None, roll: NominalRoll = None) -> ReportStats:
    """Compute all leaderboard, award, and fun statistics for one period.

    members (rows of members.csv) adds zero rows for club members who did not run
    and provides the athlete_id -> name join. All-zero when there are neither
    activities nor members.
    """
    if not activities and not members:
        return ReportStats()

    member_by_id = {str(m.get("athlete_id") or ""): m for m in (members or [])}
    athletes, total_km, total_elev = _accumulate_athletes(activities, roll, member_by_id)

    return ReportStats(
        total_km=total_km,
        total_elev=total_elev,
        run_count=len(activities),
        athlete_count=len(members or []),
        leaderboard=_build_leaderboard(athletes, members, roll),
        fun_stats=_compute_fun_stats(athletes),
        device_stats=_build_device_stats(athletes),
        **_compute_awards(athletes),
    )
