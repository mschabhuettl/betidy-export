#!/usr/bin/env python3
"""Transform betidy_export.json into clean CSV files and a SQLite database."""
import json, csv, sqlite3, os

INFILE = os.environ.get("BETIDY_OUTFILE", "betidy_export.json")
d = json.load(open(INFILE, encoding="utf-8"))
user = d["user"] or {}
rooms    = json.loads(user.get("rooms") or "[]")
profiles = json.loads(user.get("profiles") or "[]")
room_name    = {r["id"]: r["name"] for r in rooms}
room_active   = {r["id"]: r.get("active") for r in rooms}
profile_name = {p["id"]: p["name"] for p in profiles}

WEEKDAYS = {1:"Mon",2:"Tue",3:"Wed",4:"Thu",5:"Fri",6:"Sat",7:"Sun"}  # BeTidy day ints
UNIT = {"day":"day","week":"week","month":"month"}

def freq_human(t):
    if t.get("type") == "DATE":
        return "once" + (f" on {t['todoDate']}" if t.get("todoDate") else "")
    unit, cnt = t.get("intervalUnit"), t.get("intervalCount")
    if not unit:
        return ""
    word = UNIT.get(unit, unit)
    base = f"every {cnt} {word}s" if cnt and cnt != 1 else f"every {word}"
    if unit == "week" and t.get("days"):
        base += " (" + ",".join(WEEKDAYS.get(x, str(x)) for x in t["days"]) + ")"
    return base

def profiles_str(ids):
    if not ids: return ""
    return ", ".join(profile_name.get(i, i) for i in ids)

# ---------- tasks CSV ----------
COLS = ["id","title","description","room","assignee","room_id","type","frequency","interval_unit",
        "interval_count","weekdays","todo_date","last_todo_date","finished_date","last_skip_date",
        "important","effort","active","creator","template_id","created_at","updated_at"]

rows = []
for t in d["tasks"]:
    rows.append({
        "id": t["id"],
        "title": t.get("title",""),
        "description": t.get("description","") or "",
        "room": room_name.get(t.get("roomId"), t.get("roomId") or ""),
        "room_id": t.get("roomId") or "",
        "type": t.get("type",""),
        "frequency": freq_human(t),
        "interval_unit": t.get("intervalUnit") or "",
        "interval_count": t.get("intervalCount") if t.get("intervalCount") is not None else "",
        "weekdays": ",".join(WEEKDAYS.get(x,str(x)) for x in (t.get("days") or [])),
        "todo_date": t.get("todoDate") or "",
        "last_todo_date": t.get("lastTodoDate") or "",
        "finished_date": t.get("finishedDate") or "",
        "last_skip_date": t.get("lastSkipDate") or "",
        "important": t.get("important") if t.get("important") is not None else "",
        "effort": t.get("effort") if t.get("effort") is not None else "",
        "active": t.get("active"),
        "assignee": profiles_str(t.get("assigned")),
        "creator": profile_name.get(t.get("creator"), t.get("creator") or ""),
        "template_id": t.get("templateId") or "",
        "created_at": t.get("createdAt") or "",
        "updated_at": t.get("updatedAt") or "",
    })
# sort: active first, then room, then title
rows.sort(key=lambda r: (not r["active"], r["room"], r["title"].lower()))

with open("betidy_tasks.csv","w",newline="",encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=COLS); w.writeheader(); w.writerows(rows)
print(f"betidy_tasks.csv: {len(rows)} rows ({sum(r['active'] for r in rows)} active)")

# ---------- history CSV ----------
HCOLS = ["id","task_id","title","room_type","profile","effort","is_project","time","template_id","created_at"]
hrows = []
for h in d["history"]:
    hrows.append({
        "id": h["id"], "task_id": h.get("taskId") or "",
        "title": h.get("title","") or "",
        "room_type": h.get("roomType","") or "",
        "profile": profile_name.get(h.get("profileId"), h.get("profileId") or ""),
        "effort": h.get("effort") if h.get("effort") is not None else "",
        "is_project": h.get("isProject"),
        "time": h.get("time") or "", "template_id": h.get("templateId") or "",
        "created_at": h.get("createdAt") or "",
    })
hrows.sort(key=lambda r: r["time"], reverse=True)
with open("betidy_history.csv","w",newline="",encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=HCOLS); w.writeheader(); w.writerows(hrows)
print(f"betidy_history.csv: {len(hrows)} rows")

# ---------- rooms & profiles CSV ----------
with open("betidy_rooms.csv","w",newline="",encoding="utf-8-sig") as f:
    w = csv.writer(f); w.writerow(["id","name","type","active"])
    for r in rooms: w.writerow([r["id"], r["name"], r.get("type",""), r.get("active")])
with open("betidy_profiles.csv","w",newline="",encoding="utf-8-sig") as f:
    w = csv.writer(f); w.writerow(["id","name","active"])
    for p in profiles: w.writerow([p["id"], p["name"], p.get("active")])
print(f"betidy_rooms.csv: {len(rooms)} | betidy_profiles.csv: {len(profiles)}")

# ---------- SQLite ----------
if os.path.exists("betidy.sqlite"): os.remove("betidy.sqlite")
con = sqlite3.connect("betidy.sqlite"); cur = con.cursor()
cur.execute("""CREATE TABLE tasks(id TEXT PRIMARY KEY, title TEXT, description TEXT, room TEXT,
  assignee TEXT, room_id TEXT, type TEXT, frequency TEXT, interval_unit TEXT, interval_count INT, weekdays TEXT,
  todo_date TEXT, last_todo_date TEXT, finished_date TEXT, last_skip_date TEXT, important INT,
  effort INT, active INT, creator TEXT, template_id TEXT, created_at TEXT, updated_at TEXT)""")
cur.executemany("INSERT INTO tasks VALUES (%s)" % ",".join("?"*len(COLS)),
                [[r[c] for c in COLS] for r in rows])
cur.execute("""CREATE TABLE history(id TEXT PRIMARY KEY, task_id TEXT, title TEXT, room_type TEXT,
  profile TEXT, effort INT, is_project INT, time TEXT, template_id TEXT, created_at TEXT)""")
cur.executemany("INSERT INTO history VALUES (%s)" % ",".join("?"*len(HCOLS)),
                [[h[c] for c in HCOLS] for h in hrows])
cur.execute("CREATE TABLE rooms(id TEXT PRIMARY KEY, name TEXT, type TEXT, active INT)")
cur.executemany("INSERT INTO rooms VALUES (?,?,?,?)", [(r["id"],r["name"],r.get("type",""),r.get("active")) for r in rooms])
cur.execute("CREATE TABLE profiles(id TEXT PRIMARY KEY, name TEXT, active INT)")
cur.executemany("INSERT INTO profiles VALUES (?,?,?)", [(p["id"],p["name"],p.get("active")) for p in profiles])
con.commit(); con.close()
print("betidy.sqlite written (tables: tasks, history, rooms, profiles)")
