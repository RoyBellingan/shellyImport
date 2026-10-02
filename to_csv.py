#!/usr/bin/env python3
"""Convert the raw daily JSON of device folders to one hourly CSV per folder.

    python3 to_csv.py data/room_28                     # all devices in the room
    python3 to_csv.py data/room_28/ece334ea27a4_ch0__3_phase_Mains

The CSV is written next to the folder, as <folder>.csv.
One row per real hour. Hours with no data stay in the file with empty values.
"""
import csv
import datetime as dt
import json
import pathlib
import sys
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc
PHASES = "abc"

# Output columns, in order. Remove or reorder lines to change the CSV.
COLUMNS_3PHASE = [
    "utc_start",            # hour start, UTC, ISO 8601
    "local_start",          # hour start, local time with UTC offset
    "energy_a_Wh", "energy_b_Wh", "energy_c_Wh", "energy_total_Wh",
    "returned_a_Wh", "returned_b_Wh", "returned_c_Wh", "returned_total_Wh",
    "vmin_a_V", "vmax_a_V", "vmin_b_V", "vmax_b_V", "vmin_c_V", "vmax_c_V",
]
COLUMNS_1PHASE = [
    "utc_start",
    "local_start",
    "energy_Wh",
    "returned_Wh",
    "voltage_V",            # plugs only; Pro EM always gives no voltage
]


MISSING = {"missing": True}


def day_hours(day, tz):
    """All real hours of a local day as UTC times (23, 24 or 25 hours)."""
    start = dt.datetime.combine(day, dt.time(), tz).astimezone(UTC)
    end = dt.datetime.combine(day + dt.timedelta(1), dt.time(), tz).astimezone(UTC)
    while start < end:
        yield start
        start += dt.timedelta(hours=1)


def hours(folder):
    """Yield (utc_start, tz, rows) for each real hour; rows has one dict per phase.

    The cloud does not always sort the rows, and it adds a "missing" row for
    the local hour that does not exist when clocks go forward. So each row is
    put in its hour by (local time, fold); a repeated local time (clocks go
    back) is the second, later hour.
    """
    for f in sorted(folder.glob("*.json")):
        d = json.loads(f.read_text())
        tz = ZoneInfo(d["timezone"])
        history = d["history"]
        if history and isinstance(history[0], dict):
            history = [history]
        phases = []
        for phase in history:
            by_key = {}
            for r in phase:
                naive = dt.datetime.fromisoformat(r["datetime"])
                fold = 1 if (naive, 0) in by_key and not r.get("missing") else 0
                if not r.get("missing") or (naive, fold) not in by_key:
                    by_key[(naive, fold)] = r
            phases.append(by_key)
        for utc in day_hours(dt.date.fromisoformat(f.stem), tz):
            local = utc.astimezone(tz)
            key = (local.replace(tzinfo=None, fold=0), local.fold)
            yield utc, tz, [p.get(key, MISSING) for p in phases]


def num(v):
    return "" if v is None else f"{v:g}"


def is_3phase(folder):
    first = next(folder.glob("*.json"))
    return isinstance(json.loads(first.read_text())["history"][0], list)


def convert(folder):
    three = is_3phase(folder)
    columns = COLUMNS_3PHASE if three else COLUMNS_1PHASE
    out = folder.parent / f"{folder.name}.csv"
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, columns, extrasaction="ignore")
        w.writeheader()
        for utc, tz, rows in hours(folder):
            rec = {"utc_start": utc.strftime("%Y-%m-%dT%H:%MZ"),
                   "local_start": utc.astimezone(tz).isoformat(timespec="minutes")}
            if any(r.get("missing") for r in rows):
                pass
            elif three:
                for p, r in zip(PHASES, rows):
                    rec[f"energy_{p}_Wh"] = num(r["consumption"])
                    rec[f"returned_{p}_Wh"] = num(r["reversed"])
                    rec[f"vmin_{p}_V"] = num(r["min_voltage"])
                    rec[f"vmax_{p}_V"] = num(r["max_voltage"])
                rec["energy_total_Wh"] = num(round(sum(r["consumption"] for r in rows), 2))
                rec["returned_total_Wh"] = num(round(sum(r["reversed"] for r in rows), 2))
            else:
                r = rows[0]
                rec["energy_Wh"] = num(r["consumption"])
                rec["returned_Wh"] = num(r["reversed"])
                rec["voltage_V"] = num(r.get("voltage"))
            w.writerow(rec)
    print(out)


def main(paths):
    for p in map(pathlib.Path, paths):
        folders = [p] if any(p.glob("*.json")) and not (p / "devices.json").exists() \
            else sorted(d for d in p.iterdir() if d.is_dir())
        for folder in folders:
            convert(folder)


if __name__ == "__main__":
    main(sys.argv[1:] or ["data/room_28"])
