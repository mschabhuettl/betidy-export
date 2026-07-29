#!/usr/bin/env python3
"""
Import BeTidy tasks (betidy_export.json) into a self-hosted Donetick instance.

Donetick's API-token auth (header `secretkey`) is accepted not only by the minimal
external API but also by the rich internal endpoint `POST /api/v1/chores`, which
lets us preserve recurrence, assignees, room labels, priority and points — far more
than the eAPI's name+due-date. See docs/donetick-import.md for the full mapping and
the two API quirks this script works around.

Setup:
    export DONETICK_URL="https://donetick.example.com"
    export DONETICK_TOKEN="<Donetick Settings -> Access Token>"

Preview (sends nothing):   python donetick_import.py --dry-run
List your Donetick labels: python donetick_import.py --discover-labels
Import:                    python donetick_import.py [--labels-map labels.json]

Flags:
    --dry-run           build & print payloads, POST nothing
    --limit N           only process the first N tasks (testing)
    --include-inactive  also import inactive/finished tasks
    --skip-existing     skip tasks whose name already exists in Donetick
    --room NAME         only import tasks from one BeTidy room
    --labels-map FILE   JSON {"<BeTidy room name>": <donetick label id>} -> attach labels
    --discover-labels   print your circle's label ids (name -> id) and exit
    --infile FILE       input bundle (default: betidy_export.json)

Environment:
    DONETICK_URL, DONETICK_TOKEN   (required, except --dry-run)
    BETIDY_TZ                       IANA timezone for due dates (default: UTC)
    BETIDY_DUE_HOUR                 hour-of-day for due dates, 0-23 (default: 8)
"""
import os
import sys
import json
import time
import argparse
import datetime as dt
from zoneinfo import ZoneInfo
import requests

URL = os.environ.get("DONETICK_URL", "").rstrip("/")
TOKEN = os.environ.get("DONETICK_TOKEN", "")
TZ = os.environ.get("BETIDY_TZ", "UTC")
ZONE = ZoneInfo(TZ)                                  # correct UTC offset incl. DST per date
DUE_HOUR = int(os.environ.get("BETIDY_DUE_HOUR", "8"))

ap = argparse.ArgumentParser(description="Import BeTidy tasks into Donetick.")
ap.add_argument("--dry-run", action="store_true")
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--include-inactive", action="store_true")
ap.add_argument("--skip-existing", action="store_true")
ap.add_argument("--room", default="", help="only import tasks whose BeTidy room name matches")
ap.add_argument("--labels-map", default="", help="JSON file mapping room name -> Donetick label id")
ap.add_argument("--discover-labels", action="store_true", help="print circle label ids and exit")
ap.add_argument("--infile", default=os.environ.get("BETIDY_OUTFILE", "betidy_export.json"))
args = ap.parse_args()

if not URL:
    sys.exit("ERROR: set DONETICK_URL to your Donetick base URL (e.g. https://donetick.example.com).")
if not TOKEN and not args.dry_run:
    sys.exit("ERROR: set DONETICK_TOKEN (Donetick Settings -> Access Token). Use --dry-run to preview.")

H = {"secretkey": TOKEN, "Content-Type": "application/json"}
S = requests.Session()


def api(method, path, **kw):
    return S.request(method, f"{URL}{path}", headers=H, timeout=30, **kw)


# ---------------------------------------------------------------- BeTidy data
d = json.load(open(args.infile, encoding="utf-8"))
user = d["user"] or {}
rooms = {r["id"]: r["name"] for r in json.loads(user.get("rooms") or "[]")}
profiles = {p["id"]: p["name"] for p in json.loads(user.get("profiles") or "[]")}
tasks = [t for t in d["tasks"] if (t.get("active") or args.include_inactive)]
if args.room:
    tasks = [t for t in tasks if rooms.get(t.get("roomId")) == args.room]
if args.limit:
    tasks = tasks[:args.limit]

WD = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
UNIT = {"day": "days", "week": "weeks", "month": "months"}
# BeTidy `important`: 0 none, 1 important, 2 very important. Donetick P1 is highest.
IMPORTANT_TO_PRIORITY = {0: 0, 1: 2, 2: 1}


def freq_text(t):
    if t.get("type") == "DATE":
        return "once"
    unit, count = t.get("intervalUnit"), t.get("intervalCount") or 1
    s = f"every {count} {unit}s" if count != 1 else f"every {unit}"
    if unit == "week" and t.get("days"):
        s += " (" + ",".join(WD.get(x, str(x)) for x in t["days"]) + ")"
    return s


def parse_date(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def next_due(t, today):
    """Roll BeTidy's stored date forward by the interval until it is >= today,
    preserving the weekday (weekly) or day-of-month (monthly)."""
    anchor = parse_date(t.get("todoDate")) or parse_date(t.get("lastTodoDate")) or today
    if t.get("type") == "DATE":
        return anchor if anchor >= today else today
    unit, count = t.get("intervalUnit"), t.get("intervalCount") or 1
    date = anchor
    guard = 0
    while date < today and guard < 2000:
        if unit == "day":
            date += dt.timedelta(days=count)
        elif unit == "week":
            date += dt.timedelta(weeks=count)
        elif unit == "month":
            m = date.month - 1 + count
            y = date.year + m // 12
            mo = m % 12 + 1
            leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
            dim = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1]
            date = dt.date(y, mo, min(date.day, dim))
        else:
            break
        guard += 1
    if unit == "week" and t.get("days"):                 # snap to the intended weekday
        targets = {x - 1 for x in t["days"]}             # BeTidy 1=Mon..7=Sun -> Python Mon=0
        for _ in range(7):
            if date.weekday() in targets:
                break
            date += dt.timedelta(days=1)
    return date


def rfc3339(date, hour=DUE_HOUR):
    return dt.datetime(date.year, date.month, date.day, hour, 0, 0, tzinfo=ZONE).isoformat()


def freq_fields(t):
    """Return (frequencyType, frequency, frequencyMetadata) for Donetick."""
    meta = {"unit": None, "time": rfc3339(dt.date(2025, 1, 1)), "timezone": TZ}
    if t.get("type") == "DATE":
        return "once", 1, {"time": meta["time"], "timezone": TZ}
    meta["unit"] = UNIT.get(t.get("intervalUnit"), "days")
    return "interval", t.get("intervalCount") or 1, meta


# ---------------------------------------------------------------- Donetick lookups
def get_members():
    """Map circle member names -> user id. /api/v1/circles/members accepts the API
    token (unlike the Plus-gated eAPI variant)."""
    r = api("GET", "/api/v1/circles/members")
    if r.status_code != 200:
        print(f"  (circle members unavailable: {r.status_code} — assignees default to the token owner)")
        return {}
    body = r.json()
    items = body.get("res", body) if isinstance(body, dict) else body
    out = {}
    for u in items:
        uid = u.get("userId") or u.get("id")
        if not uid:
            continue
        for nm in (u.get("displayName"), u.get("username")):
            if nm:
                nm = nm.strip().lower()
                out.setdefault(nm, uid)
                out.setdefault(nm.split()[0], uid)       # first name: "jane doe" -> "jane"
                out.setdefault(nm.split(".")[0], uid)    # username token: "j.doe" -> "j"
    return out


def discover_labels(max_id=120):
    """Enumerate the circle's labels as {id: name}.

    Donetick's GET /api/v1/labels requires a browser (JWT) session and rejects the
    API token, so we probe: attach each candidate id to a throwaway chore and read
    back the resolved label name, then delete the probe. Invalid ids make the create
    fail, which is how we know an id doesn't exist. This is a helper you run rarely,
    not part of a normal import.
    """
    # clean up any leftovers from an interrupted run
    lst = api("GET", "/api/v1/chores").json()
    lst = lst.get("res", lst) if isinstance(lst, dict) else lst
    for c in lst:
        if (c.get("name") or "") == "__label_probe__":
            api("DELETE", f"/eapi/v1/chore/{c['id']}")

    found = {}
    base = {"name": "__label_probe__", "frequencyType": "once", "frequency": 1,
            "frequencyMetadata": {"timezone": TZ}, "assignStrategy": "random", "isActive": True}
    for i in range(1, max_id + 1):
        r = api("POST", "/api/v1/chores", json={**base, "labelsV2": [{"id": i}]})
        if r.status_code not in (200, 201):
            continue
        cid = (r.json() or {}).get("res")
        g = api("GET", f"/api/v1/chores/{cid}").json()
        chore = g.get("res", g) if isinstance(g, dict) else g
        for L in (chore.get("labelsV2") or []):
            found[L["id"]] = L.get("name")
        api("DELETE", f"/eapi/v1/chore/{cid}")
    return found


def main():
    if args.discover_labels:
        labels = discover_labels()
        print(json.dumps({name: i for i, name in sorted(labels.items(), key=lambda x: x[0])},
                         ensure_ascii=False, indent=2))
        print(f"\n{len(labels)} labels found. Build a --labels-map JSON of "
              f'{{"<BeTidy room name>": <label id>}}.')
        return

    room_label = {}
    if args.labels_map:
        room_label = {k: int(v) for k, v in json.load(open(args.labels_map, encoding="utf-8")).items()}

    today = dt.date.today()
    members = {} if args.dry_run else get_members()

    existing = set()
    if args.skip_existing and not args.dry_run:
        r = api("GET", "/eapi/v1/chore")
        if r.status_code == 200:
            existing = {c.get("name") for c in r.json()}

    ok = fail = skip = 0
    for i, t in enumerate(tasks, 1):
        name = t["title"]
        if name in existing:
            skip += 1
            continue
        room = rooms.get(t.get("roomId"), t.get("roomId") or "")
        assignee_names = [profiles.get(a, a) for a in (t.get("assigned") or [])]
        ftype, freq, fmeta = freq_fields(t)
        due = next_due(t, today)

        desc = [t.get("description") or ""]
        desc.append(f"[BeTidy] Room: {room} · Repeats: {freq_text(t)}"
                    + (f" · Assignee: {', '.join(assignee_names)}" if assignee_names else "")
                    + (f" · Effort: {t.get('effort')}" if t.get("effort") else ""))
        description = "\n".join(p for p in desc if p).strip()

        label_ids = [{"id": room_label[room]}] if room in room_label else []

        mapped = []
        for n in assignee_names:
            uid = members.get(n.lower())
            if uid and uid not in mapped:
                mapped.append(uid)

        payload = {
            "name": name,
            "description": description,
            "frequencyType": ftype,
            "frequency": freq,
            "frequencyMetadata": fmeta,
            "nextDueDate": rfc3339(due),
            "assignStrategy": "round_robin" if len(mapped) > 1 else "keep_last_assigned",
            "isActive": True,
            "priority": IMPORTANT_TO_PRIORITY.get(t.get("important") or 0, 0),
        }
        if t.get("effort"):
            payload["points"] = t["effort"]
        if label_ids:
            payload["labelsV2"] = label_ids
        if mapped:
            payload["assignees"] = [{"userId": u} for u in mapped]
            payload["assignedTo"] = mapped[0]
        else:
            payload["assignStrategy"] = "random"

        if args.dry_run:
            print(f"[{i:3}] {name[:40]:40} | {ftype}/{freq} {fmeta.get('unit') or ''} | due {due} | room={room}")
            if i <= 3:
                print("      payload:", json.dumps(payload, ensure_ascii=False))
            ok += 1
            continue

        r = api("POST", "/api/v1/chores", json=payload)
        if r.status_code not in (200, 201):
            fail += 1
            print(f"[{i:3}/{len(tasks)}] ERR create {r.status_code} {name[:40]}: {r.text[:140]}")
            continue
        cid = (r.json() or {}).get("res") if r.text else None
        # This Donetick version ignores nextDueDate on create -> set it via the dueDate endpoint.
        due_note = ""
        if cid:
            # updatedAt must be >= the chore's just-set updatedAt and < now+30s -> now+10s.
            stamp = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
            dr = api("PUT", f"/api/v1/chores/{cid}/dueDate", json={"dueDate": rfc3339(due), "updatedAt": stamp})
            if dr.status_code != 200:
                due_note = f" (dueDate FAILED {dr.status_code})"
        ok += 1
        print(f"[{i:3}/{len(tasks)}] OK  id={cid} {name[:44]}{due_note}")
        time.sleep(0.15)

    print(f"\nDone. created={ok} failed={fail} skipped={skip} (of {len(tasks)} tasks)"
          + ("  [DRY RUN — nothing sent]" if args.dry_run else ""))


if __name__ == "__main__":
    main()
