import csv, io, os, sys, time, zipfile, datetime as dt
from zoneinfo import ZoneInfo
import requests
from google.transit import gtfs_realtime_pb2

TZ = ZoneInfo("America/Toronto")
TRIP_UPDATES = "https://rtu.york.ca/gtfsrealtime/TripUpdates"
STATIC_ZIP = "https://www.yrt.ca/google/google_transit.zip"
ROUTES = {"605", "77", "13", "105", "10"}
START, END = dt.time(6, 30), dt.time(9, 30)
POLL_SECONDS = 30
OUT = "yrt_delays.csv"

def load_static():
    z = zipfile.ZipFile(io.BytesIO(requests.get(STATIC_ZIP, timeout=60).content))
    trips, sched = {}, {}
    with z.open("trips.txt") as f:
        for r in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")):
            if r["route_id"] in ROUTES:
                trips[r["trip_id"]] = r["route_id"]
    with z.open("stop_times.txt") as f:
        for r in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")):
            if r["trip_id"] in trips:
                h, m, s = map(int, r["arrival_time"].strip().split(":"))
                sched[(r["trip_id"], r["stop_id"])] = h * 3600 + m * 60 + s
    return trips, sched

def fetch():
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(requests.get(TRIP_UPDATES, timeout=20).content)
    return feed

def main():
    trips, sched = load_static()
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        feed = fetch()
        hits = [e for e in feed.entity if e.HasField("trip_update") and e.trip_update.trip.trip_id in trips]
        print(f"Schedule loaded: {len(trips)} trips on your routes")
        print(f"Live feed: {len(feed.entity)} vehicles/trips, {len(hits)} on your routes right now")
        return
    today = dt.datetime.now(TZ).date()
    midnight = dt.datetime.combine(today, dt.time(0), TZ).timestamp()
    latest = {}
    while dt.datetime.now(TZ).time() <= END:
        now = dt.datetime.now(TZ)
        if now.time() >= START:
            try:
                for e in fetch().entity:
                    if not e.HasField("trip_update"):
                        continue
                    tu = e.trip_update
                    tid = tu.trip.trip_id
                    if tid not in trips:
                        continue
                    trip_cancelled = tu.trip.schedule_relationship == 3
                    for u in tu.stop_time_update:
                        key = (tid, u.stop_id)
                        if key not in sched:
                            continue
                        s = midnight + sched[key]
                        if u.HasField("arrival") and u.arrival.time:
                            p = u.arrival.time
                        elif u.HasField("departure") and u.departure.time:
                            p = u.departure.time
                        elif u.HasField("arrival"):
                            p = s + u.arrival.delay
                        else:
                            p = s
                        cancelled = trip_cancelled or u.schedule_relationship == 1
                        latest[key] = (trips[tid], tid, u.stop_id, s, p, now.timestamp(), cancelled)
            except Exception as ex:
                print("poll failed:", ex)
        time.sleep(POLL_SECONDS)
    new = not os.path.exists(OUT)
    with open(OUT, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "route", "trip_id", "stop_id", "scheduled", "actual", "delay_min", "last_seen_gap_s", "cancelled"])
        for route, tid, stop, s, p, seen, canc in latest.values():
            w.writerow([today, route, tid, stop,
                        dt.datetime.fromtimestamp(s, TZ).strftime("%H:%M"),
                        dt.datetime.fromtimestamp(p, TZ).strftime("%H:%M:%S"),
                        round((p - s) / 60, 1), round(p - seen), canc])
    print(f"Saved {len(latest)} stop records for {today}")

if __name__ == "__main__":
    main()
