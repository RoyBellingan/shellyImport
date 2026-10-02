#!/usr/bin/env python3
"""Monthly and period totals for each device, for review.

    python3 monthly.py [data/room_28] [--from 2025-09] [--to 2026-09]

For each device and month (local time) it writes:
  - the total from the hourly CSV made by to_csv.py
  - the monthly total that the cloud gives directly (separate API call)
  - the difference, and how many hours of the month have data

Writes <room>/monthly_totals.csv and <room>/period_totals.csv.
The raw cloud responses go to <room>/monthly_cloud/.
"""
import argparse
import collections
import csv
import datetime as dt
import json
import pathlib

from download import HERE, Cloud, read_key


def month_range(first, last):
    y, m = map(int, first.split("-"))
    while f"{y:04d}-{m:02d}" <= last:
        yield f"{y:04d}-{m:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def hours_in_month(month, tz_name="Europe/Dublin"):
    from zoneinfo import ZoneInfo
    tz, utc = ZoneInfo(tz_name), dt.timezone.utc
    y, m = map(int, month.split("-"))
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    a = dt.datetime(y, m, 1, tzinfo=tz).astimezone(utc)
    b = dt.datetime.combine(nxt, dt.time(), tz).astimezone(utc)
    return int((b - a).total_seconds() // 3600)


TOLERANCE_WH = 5  # rounding in the cloud and in the CSV


def cloud_daily_sum(cloud, path, dev_id, ch, month):
    """Second check: sum of the cloud's daily values for one month, in Wh."""
    body = cloud.call(path, {"id": dev_id, "channel": ch, "date_range": "month",
                             "date_from": f"{month}-01 00:00:00"})
    hist = body["history"]
    phases = hist if hist and isinstance(hist[0], list) else [hist]
    return sum(r.get("consumption") or 0 for p in phases for r in p if not r.get("missing"))


def kwh(wh):
    return "" if wh is None else f"{wh / 1000:.3f}".replace("-0.000", "0.000")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("room", nargs="?", default=HERE / "data" / "room_28", type=pathlib.Path)
    ap.add_argument("--from", dest="first", default="2025-09")
    ap.add_argument("--to", dest="last", default="2026-09")
    a = ap.parse_args()

    months = list(month_range(a.first, a.last))
    devices = json.loads((a.room / "devices.json").read_text())["devices"]
    cloud = Cloud(*read_key(HERE / "key.ini"))
    raw_dir = a.room / "monthly_cloud"
    raw_dir.mkdir(exist_ok=True)
    y, m = map(int, a.last.split("-"))
    date_to = dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)

    monthly, period = [], []
    for folder in sorted(d for d in a.room.iterdir() if d.is_dir() and "_ch" in d.name):
        dev_id, rest = folder.name.split("_ch", 1)
        ch = int(rest.split("__")[0])
        info = devices[dev_id if ch == 0 else f"{dev_id}_{ch}"]
        three = info.get("mode") == "triphase"

        # Totals from the hourly CSV
        energy, returned, filled = (collections.defaultdict(float) for _ in range(3))
        with open(f"{folder}.csv") as fh:
            for r in csv.DictReader(fh):
                e = r.get("energy_total_Wh", r.get("energy_Wh"))
                if e:
                    mo = r["local_start"][:7]
                    energy[mo] += float(e)
                    returned[mo] += float(r.get("returned_total_Wh", r.get("returned_Wh")) or 0)
                    filled[mo] += 1

        # Monthly totals straight from the cloud
        path = ("v2/statistics/power-consumption/em-3p" if three
                else "v2/statistics/power-consumption")
        body = cloud.call(path, {"id": dev_id, "channel": ch, "date_range": "custom",
                                 "date_from": f"{a.first}-01 00:00:00",
                                 "date_to": f"{date_to} 23:59:59"})
        (raw_dir / f"{folder.name}.json").write_text(json.dumps(body))
        if body.get("interval") != "month":
            raise SystemExit(f"{folder.name}: expected monthly data, got {str(body)[:200]}")
        hist = body["history"]
        phases = hist if hist and isinstance(hist[0], list) else [hist]
        cloud_wh = collections.defaultdict(lambda: None)
        for phase in phases:
            for r in phase:
                if not r.get("missing"):
                    mo = r["datetime"][:7]
                    cloud_wh[mo] = (cloud_wh[mo] or 0) + (r.get("consumption") or 0)

        base = {"room": info.get("room_id"), "device": info["name"], "device_id": dev_id,
                "channel": ch, "type": info["type"], "phases": 3 if three else 1}
        tot_e = tot_r = tot_c = 0.0
        tot_h = tot_f = 0
        for mo in months:
            h = hours_in_month(mo)
            c = cloud_wh[mo]
            daily = None
            if c is None and not filled[mo]:
                check = "no data"
            elif c is None or abs(energy[mo] - c) > TOLERANCE_WH:
                daily = cloud_daily_sum(cloud, path, dev_id, ch, mo)
                if abs(energy[mo] - daily) <= TOLERANCE_WH:
                    check = "cloud monthly value differs; cloud daily values match"
                else:
                    check = "DIFFERS"
            else:
                check = "ok"
            monthly.append(dict(base, month=mo,
                                energy_kWh=kwh(energy[mo]), returned_kWh=kwh(returned[mo]),
                                cloud_energy_kWh=kwh(c),
                                difference_kWh=kwh(energy[mo] - c) if c is not None else "",
                                cloud_daily_sum_kWh=kwh(daily), check=check,
                                hours_with_data=int(filled[mo]), hours_in_month=h,
                                coverage_pct=f"{100 * filled[mo] / h:.1f}"))
            tot_e += energy[mo]; tot_r += returned[mo]; tot_c += c or 0
            tot_h += h; tot_f += int(filled[mo])
        checks = {r["month"]: r["check"] for r in monthly if r["device_id"] == dev_id
                  and r["channel"] == ch and r["check"] != "ok"}
        period.append(dict(base, period_from=months[0], period_to=months[-1],
                           energy_kWh=kwh(tot_e), returned_kWh=kwh(tot_r),
                           cloud_energy_kWh=kwh(tot_c), difference_kWh=kwh(tot_e - tot_c),
                           check="; ".join(f"{m}: {c}" for m, c in checks.items()) or "ok",
                           hours_with_data=tot_f, hours_in_period=tot_h,
                           coverage_pct=f"{100 * tot_f / tot_h:.1f}"))
        print(f"{info['name']}: {kwh(tot_e)} kWh (cloud {kwh(tot_c)})", flush=True)

    for name, rows in (("monthly_totals.csv", monthly), ("period_totals.csv", period)):
        with open(a.room / name, "w", newline="") as fh:
            w = csv.DictWriter(fh, list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(a.room / name)


if __name__ == "__main__":
    main()
