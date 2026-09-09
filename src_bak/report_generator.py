"""Statistics computation engine for Strava club activities."""
import math
from collections import Counter
from dataclasses import asdict, dataclass, field

from nominal_roll import NominalRoll


@dataclass
class AthleteStats:
    """Accumulated per-athlete totals for one report period."""

    name: str
    unit: str = ""
    company: str = ""

    km: float = 0.0
    elev: float = 0.0
    time_s: float = 0.0
    speeds: list = field(default_factory=list)
    count_acts: int = 0
    longest: float = 0.0
    devices: set = field(default_factory=set)

    # Climber — only runs with >= 8 m+/km (real hills)
    climber_run_elev: float = 0.0
    climber_run_km: float = 0.0

    break_time: float = 0.0

    def add_activity(self, act: dict) -> tuple:
        """Accumulate one activity, returning (dist_km, elev) for club totals."""
        dist_km = act.get("distance", 0) / 1000
        elev = act.get("total_elevation_gain", 0)
        time_s = act.get("moving_time", 0)
        elapsed = act.get("elapsed_time", 0)
        speed = (act.get("distance", 0) / time_s) if time_s > 0 else 0
        dev = act.get("device_name", "")

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

    Callers serialise with ``dataclasses.asdict`` at the JSON boundary.
    """

    total_km: float = 0.0
    total_elev: float = 0.0
    run_count: int = 0
    athlete_count: int = 0
    leaderboard: list = field(default_factory=list)
    fun_stats: dict = field(default_factory=dict)
    king_km: dict | None = None
    king_elev: dict | None = None
    marathoner: dict | None = None
    fastest: dict | None = None
    longest: dict | None = None
    climber: dict | None = None
    flatrunner: dict | None = None
    device_stats: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialise to a plain dict that json.dumps can always emit.

        Strava occasionally reports a distance or elapsed time that makes a
        derived average NaN or infinite. Standard JSON has no literal for
        either, so they are flattened to 0.0 here rather than left for the
        caller to trip over.
        """
        return _json_safe(asdict(self))


def _json_safe(obj):
    """Recursively replace NaN and Inf floats with 0.0."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(i) for i in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return 0.0
    return obj


def _device_sort(item):
    """Sort key placing real hardware ahead of virtual platforms.

    Hardware first (by descending runner count), then Zwift, Rouvy, and
    finally Strava's own manual entries.
    """
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


def resolved_name(person: dict, roll: NominalRoll) -> str:
    """Canonical roster name for a Strava athlete/member dict."""
    raw = NominalRoll.full_name(person)
    return roll.resolve(raw) if roll else raw


def _accumulate_athletes(activities: list, roll: NominalRoll) -> tuple:
    """Fold every activity into per-athlete accumulators.

    Returns (athletes, total_km, total_elev), athletes keyed by resolved name.
    """
    athletes: dict = {}
    total_km = 0.0
    total_elev = 0.0

    for act in activities:
        name = resolved_name(act.get("athlete", {}), roll)
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
        name = resolved_name(m, roll)
        if name and name not in listed:
            leaderboard.append(_new_athlete(name, roll).to_leaderboard_entry(leader_km))
            listed.add(name)

    return leaderboard


def _award(values: dict, val_fn, pick=max) -> dict | None:
    """Winner of one category, or None when nobody qualified.

    values maps athlete name to the numeric value being ranked; pick is max
    for "highest wins" and min for "lowest wins".
    """
    if not values:
        return None
    name = pick(values, key=values.get)
    return {"name": name, "value": val_fn(values[name])}


def _compute_awards(athletes: dict) -> dict:
    """Pick the winner of every award category.

    Climber and flat runner carry qualifying thresholds, so either may be
    absent even when runners exist.
    """
    active = [a for a in athletes.values() if a.count_acts > 0]

    # Climber — avg m+/km over hilly runs only, needing 30 km of them and an
    # average above 5 m+/km.
    climber = {
        a.name: a.climber_run_elev / a.climber_run_km
        for a in athletes.values()
        if a.climber_run_km >= 30 and a.climber_run_elev / a.climber_run_km > 5
    }
    # Flat runner — lowest m+/km over at least 50 km.
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
    """Novelty statistics shown alongside the main awards. Each is None when
    nobody cleared its threshold."""
    breaks = None
    times = {a.name: a.break_time for a in athletes.values()}
    if times:
        name = max(times, key=times.get)
        if times[name] > 60:
            breaks = {"name": name, "value": f"{int(times[name] // 60)} min of rest"}
    return {"breaks": breaks}


def compute_stats(activities: list, members: list = None, roll: NominalRoll = None) -> ReportStats:
    """Compute all leaderboard, award, and fun statistics for one period.

    members is used to add zero rows for club members who did not run.
    All-zero when there are neither activities nor members.
    """
    if not activities and not members:
        return ReportStats()

    athletes, total_km, total_elev = _accumulate_athletes(activities, roll)

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
