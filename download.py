#!/usr/bin/env python3
"""Download hourly consumption statistics from Shelly Cloud.

Stores the raw JSON response for each device channel and day:
    data/room_<room>/<device>_ch<channel>__<name>/<YYYY-MM-DD>.json

Days that already have a file are skipped, so the script can be stopped
and run again. The current day is never downloaded (it is not complete).
"""
import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
REQ_INTERVAL = 1.1  # Shelly Cloud allows about 1 request per second


def read_key(path):
    cfg = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith(("#", ";", "[")):
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.lstrip("=").strip()
    server = cfg["SHELLY_SERVER"]
    if not server.startswith("http"):
        server = "https://" + server
    return server.rstrip("/"), cfg["SHELLY_AUTH_KEY"]


class Cloud:
    def __init__(self, server, key):
        self.server, self.key, self.last = server, key, 0.0

    def call(self, path, params, post=False):
        params = dict(params, auth_key=self.key)
        for attempt in range(8):
            wait = self.last + REQ_INTERVAL - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            data = urllib.parse.urlencode(params)
            url = f"{self.server}/{path}"
            req = (urllib.request.Request(url, data=data.encode()) if post
                   else urllib.request.Request(f"{url}?{data}"))
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = json.load(r)
            except Exception as e:  # network error or HTTP 5xx
                print(f"  retry {attempt + 1}: {e}", file=sys.stderr)
                time.sleep(5 * (attempt + 1))
                continue
            if isinstance(body, dict) and "max_req" in str(body.get("errors", "")):
                time.sleep(2 * (attempt + 1))
                continue
            return body
        raise RuntimeError(f"failed after retries: {path} {params.get('id')}")


def safe(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--room", type=int, default=28)
    ap.add_argument("--start", default="2025-09-01")
    ap.add_argument("--end", help="last day to download (default: yesterday)")
    ap.add_argument("--key", default=HERE / "key.ini", type=pathlib.Path)
    ap.add_argument("--out", default=HERE / "data", type=pathlib.Path)
    a = ap.parse_args()

    cloud = Cloud(*read_key(a.key))
    lists = cloud.call("interface/device/get_all_lists", {}, post=True)
    if not lists.get("isok"):
        sys.exit(f"device list failed: {lists}")
    room_dir = a.out / f"room_{a.room}"
    room_dir.mkdir(parents=True, exist_ok=True)
    (room_dir / "devices.json").write_text(json.dumps(lists["data"], indent=1))

    devices = [d for d in lists["data"]["devices"].values() if d.get("room_id") == a.room]
    if not devices:
        sys.exit(f"no devices in room {a.room}")

    start = dt.date.fromisoformat(a.start)
    end = dt.date.fromisoformat(a.end) if a.end else dt.date.today() - dt.timedelta(days=1)
    days = [start + dt.timedelta(n) for n in range((end - start).days + 1)]

    for d in sorted(devices, key=lambda d: d["name"]):
        dev_id = d["id"].split("_")[0]
        ch = d.get("channel", 0)
        path = ("v2/statistics/power-consumption/em-3p" if d.get("mode") == "triphase"
                else "v2/statistics/power-consumption")
        ddir = room_dir / f"{dev_id}_ch{ch}__{safe(d['name'])}"
        ddir.mkdir(exist_ok=True)
        todo = [day for day in days if not (ddir / f"{day}.json").exists()]
        print(f"{d['name']} ({d['type']}, {dev_id} ch{ch}): {len(todo)} days to fetch", flush=True)
        for i, day in enumerate(todo, 1):
            body = cloud.call(path, {"id": dev_id, "channel": ch, "date_range": "day",
                                     "date_from": f"{day} 00:00:00"})
            if "history" not in body:
                print(f"  {day}: unexpected response {str(body)[:200]}", flush=True)
                continue
            tmp = ddir / f"{day}.json.tmp"
            tmp.write_text(json.dumps(body))
            tmp.rename(ddir / f"{day}.json")
            if i % 50 == 0:
                print(f"  {i}/{len(todo)} ({day})", flush=True)
    print("done")


if __name__ == "__main__":
    main()
