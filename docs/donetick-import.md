# Importing BeTidy tasks into Donetick

This guide covers `donetick_import.py` — the third and final step of the
[betidy-export](https://github.com/mschabhuettl/betidy-export) toolkit. It takes the
`betidy_export.json` bundle produced by `betidy_extract.py` and recreates your chores
in a self-hosted [Donetick](https://github.com/donetick/donetick) instance with as
much fidelity as Donetick's data model allows: recurrence, due dates, room labels,
assignees, priority and points.

If you only want CSV/JSON/SQLite, you don't need Donetick at all — that's what
`build_exports.py` is for. This step is entirely optional.

> **Scope & ethics.** Use this only with your own BeTidy account and your own
> Donetick instance. The tool authenticates as you, with your credentials, and reads
> only your own data. No warranty — see [`LICENSE`](../LICENSE).

---

## Prerequisites

1. A running Donetick instance you can reach over HTTPS.
2. `betidy_export.json` in the working directory (run `python betidy_extract.py`
   first — see the [export guide](./export.md) if present, or the repo README).
3. The Python environment from the repo root:

   ```bash
   python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
   ```

---

## 1. Generate a Donetick Access Token

Donetick's API is authenticated with a per-user access token, **not** a `Bearer`
JWT.

1. Open Donetick in your browser and sign in.
2. Go to **Settings → Access Token** and create a new token.
3. Copy it — you won't be able to read it again.

Every request this tool makes sends the token in an HTTP header named `secretkey`:

```
secretkey: <your-donetick-access-token>
```

Point the tool at your instance and token via environment variables:

```bash
export DONETICK_URL="https://donetick.example.com"
export DONETICK_TOKEN="<Donetick Settings -> Access Token>"
```

You can also copy `.env.example` to `.env` and fill these in.

---

## 2. Why the "rich" endpoint instead of the eAPI

Donetick exposes two ways to create a chore:

| Endpoint | Auth | What it accepts |
| --- | --- | --- |
| `POST /eapi/v1/chore` (external API) | `secretkey` token | `name`, `description`, `dueDate`, `createdBy` only — and **forces** `frequencyType=once`. |
| `POST /api/v1/chores` (internal API) | `secretkey` token **or** browser JWT | The full `ChoreReq`: recurrence, assignees, labels, priority, points, … |

The external `/eapi/v1/chore` endpoint is too lossy — it would flatten every
recurring chore into a one-off. The internal `/api/v1/chores` endpoint accepts the
complete `ChoreReq`, and its `multiAuthMiddleware` tries the API key **before** the
JWT session — so the same `secretkey` token works there too. This tool therefore
posts to `/api/v1/chores` and only falls back to the eAPI where it must (see the
label and due-date quirks below).

---

## 3. Quick start

```bash
# 1. Preview — builds and prints payloads, sends nothing. Token not required.
python donetick_import.py --dry-run

# 2. (Optional) discover which label ids exist in your circle
python donetick_import.py --discover-labels

# 3. Real import, attaching room labels from a map you write
python donetick_import.py --labels-map labels.json
```

Start with `--dry-run` to confirm the frequency, due date and room look right, then
run a small real import with `--limit 3` before doing the whole set.

### Flags & environment

| Flag | Effect |
| --- | --- |
| `--dry-run` | Build and print payloads, POST nothing. `DONETICK_TOKEN` not required. |
| `--limit N` | Only process the first `N` tasks (testing). |
| `--include-inactive` | Also import inactive/finished tasks (default: active only). |
| `--skip-existing` | Skip tasks whose name already exists in Donetick (checked via `GET /eapi/v1/chore`). |
| `--room NAME` | Only import tasks from one BeTidy room. |
| `--labels-map FILE` | JSON `{"<BeTidy room name>": <label id>}` — attach Donetick labels. |
| `--discover-labels` | Print your circle's label ids (`name -> id`) and exit. |
| `--infile FILE` | Input bundle (default: `betidy_export.json`). |

| Environment variable | Purpose | Default |
| --- | --- | --- |
| `DONETICK_URL` | Donetick base URL (required). | — |
| `DONETICK_TOKEN` | Access token (required, except `--dry-run`). | — |
| `BETIDY_TZ` | IANA timezone for due dates. | `UTC` |
| `BETIDY_DUE_HOUR` | Hour-of-day (0–23) chores become due. | `8` |

---

## 4. Field mapping

Each active BeTidy `UserTask` becomes one Donetick `ChoreReq`. The tool builds this
payload:

| BeTidy field | Donetick `ChoreReq` field | Notes |
| --- | --- | --- |
| `title` | `name` | — |
| `description` (+ a `[BeTidy]` summary line) | `description` | See [Description](#description) below. |
| `type` + `intervalUnit` + `intervalCount` | `frequencyType`, `frequency`, `frequencyMetadata` | See [Frequency mapping](#5-frequency-mapping). |
| `todoDate` / `lastTodoDate` (rolled forward) | `nextDueDate` (and a follow-up `PUT …/dueDate`) | See [Due dates](#6-due-date-roll-forward). |
| `roomId` → room name | `labelsV2: [{id}]` | Only if the room is in your `--labels-map`. |
| `assigned` (profile ids) → names | `assignees: [{userId}]`, `assignedTo` | Matched to circle members by first name. |
| `important` | `priority` | `0 → 0`, `1 → 2` (P2), `2 → 1` (P1). Donetick **P1 is highest**. |
| `effort` | `points` | Only written when `effort` is non-zero. |
| — | `assignStrategy` | Required by Donetick; chosen from the mapped assignees (see below). |
| — | `isActive` | Always `true`. |

### assignStrategy

Donetick requires an `assignStrategy` on every chore. The tool picks it from how many
circle members it managed to map:

| Mapped assignees | `assignStrategy` |
| --- | --- |
| 2 or more | `round_robin` |
| exactly 1 | `keep_last_assigned` |
| none | `random` |

### Description

The original BeTidy `description` is preserved, and the tool appends one summary line
so the provenance survives even where Donetick has no matching field:

```
[BeTidy] Room: Kitchen · Repeats: every 2 weeks (Mon,Thu) · Assignee: Jane · Effort: 2
```

`Assignee` and `Effort` are only included when present/non-zero.

---

## 5. Frequency mapping

BeTidy has two task types: `INTERVAL` (recurring) and `DATE` (one-time).

| BeTidy | Donetick `frequencyType` | `frequency` | `frequencyMetadata.unit` |
| --- | --- | --- | --- |
| `INTERVAL`, `intervalUnit=day`, `intervalCount=N` | `interval` | `N` | `days` |
| `INTERVAL`, `intervalUnit=week`, `intervalCount=N` | `interval` | `N` | `weeks` |
| `INTERVAL`, `intervalUnit=month`, `intervalCount=N` | `interval` | `N` | `months` |
| `DATE` | `once` | `1` | *(none)* |

`frequencyMetadata` also carries a `time` (an RFC 3339 anchor timestamp) and a
`timezone` (from `BETIDY_TZ`).

**Why `interval` and not `weekly`/`monthly`:** Donetick's `interval` scheduler
computes the next occurrence by adding `frequency × unit` to the current due date.
With `unit=weeks` it lands on the same weekday, and with `unit=months` it lands on the
same day-of-month — **provided the `nextDueDate` we set already falls on the right
day**. That's exactly what the roll-forward step guarantees (next section), so a
"every 2 weeks on Monday" BeTidy task keeps recurring on Mondays in Donetick.

---

## 6. Due-date roll-forward

BeTidy's `todoDate` (the "next due" date) is frequently in the **past** — it reflects
when the chore was last expected, not a future occurrence. Importing that verbatim
would create a pile of overdue chores.

Instead the tool computes the next real occurrence:

1. Take the anchor: `todoDate`, else `lastTodoDate`, else today.
2. **`DATE` tasks:** use the anchor if it's today or later, otherwise today.
3. **`INTERVAL` tasks:** repeatedly add the interval (`N` days / weeks / months —
   month arithmetic clamps to the last valid day, e.g. Jan 31 → Feb 28/29) until the
   date is **≥ today**.
4. **Weekly tasks with a `days` list:** snap forward (up to 7 days) to the intended
   weekday, so the first Donetick occurrence lands on the correct day and every
   subsequent `interval` step preserves it.

The final date is set at `BETIDY_DUE_HOUR` (default 08:00) in `BETIDY_TZ`.

> Because of [Quirk A](#quirk-a-nextduedate-is-ignored-on-create), the due date is
> applied in a **second request** after the chore is created — the `nextDueDate` in
> the create payload is ignored by this Donetick version.

---

## 7. Labels (rooms)

BeTidy rooms map to Donetick labels. This is optional; skip `--labels-map` and no
labels are attached (the room still appears in the description).

Because of [Quirk B](#quirk-b-the-labels-endpoint-needs-a-jwt), the tool cannot list
or create labels with an API token. So the workflow is:

1. **Create the labels you want in Donetick's UI** (Labels section), one per room.
2. **Discover their numeric ids** by probing:

   ```bash
   python donetick_import.py --discover-labels
   ```

   This prints a `name -> id` map, e.g.:

   ```json
   {
     "Kitchen": 3,
     "Bathroom": 4,
     "Living Room": 7
   }
   ```

3. **Write `labels.json`** mapping each **BeTidy room name** to a Donetick label id:

   ```json
   {
     "Kitchen": 3,
     "Bathroom": 4,
     "Living Room": 7
   }
   ```

4. **Import with the map:**

   ```bash
   python donetick_import.py --labels-map labels.json
   ```

A task whose room isn't a key in `labels.json` simply gets no label.

> `--discover-labels` works by attaching each candidate id (1–120) to a throwaway
> `__label_probe__` chore, reading back the resolved label name, then deleting the
> probe. It's a rare helper, not part of a normal import — run it once to build your
> map. Interrupted runs are cleaned up on the next invocation.

---

## 8. Assignees

BeTidy assigns tasks to **profiles** (household members); Donetick assigns to
**circle members**. The tool bridges them by name:

1. It fetches your circle via `GET /api/v1/circles/members` (this endpoint accepts the
   API token — the eAPI variant `/eapi/v1/circle/members` is Plus-gated).
2. It builds a lookup from each member's `displayName` and `username`, indexed by the
   full name, the **first name**, and the pre-`.` username token (so `jane doe` and
   `j.doe` both resolve to that user id).
3. For each BeTidy assignee profile name (lowercased) it looks up a `userId` and adds
   it to `assignees` (`assignedTo` is set to the first match).

If the circle can't be fetched, or a name doesn't match any member, that assignee is
dropped and the chore falls back to `assignStrategy: random` (Donetick decides).
Matching is by first name, so it's best-effort — check important assignments after
import.

---

## 9. Known Donetick API quirks

This tool works around three behaviors of the self-hosted Donetick API. They're
documented here so the extra requests aren't surprising.

### Quirk A: `nextDueDate` is ignored on create

`POST /api/v1/chores` on this Donetick version handles `frequencyType` but does **not**
store the `nextDueDate` from the create payload. So the tool creates the chore, then
sets the date in a second call:

```
PUT /api/v1/chores/:id/dueDate
{ "dueDate": "<RFC3339>", "updatedAt": "<now + 10s, RFC3339 Z>" }
```

Donetick's `CanEdit` guard requires the supplied `updatedAt` to be **≥ the chore's
current `updatedAt`** and **< now + 30s**. The tool sends **now + 10 seconds**, which
satisfies both bounds for a chore that was just created.

### Quirk B: the labels endpoint needs a JWT

`GET`/`POST /api/v1/labels` require a browser (JWT) session and reject the API token
with `401`. The tool therefore cannot list or create labels with a token. Create
labels in the UI, discover their ids with `--discover-labels` (which probes rather
than lists — see §7), and attach them via `--labels-map`.

### Quirk C: you can only delete your own chores

Donetick only lets you `DELETE` chores whose `createdBy` is you — even a circle admin
can't delete another member's chores. The `--discover-labels` probe creates and
deletes its own throwaway chores, so this is fine there; just be aware that if you
re-run an import and want to clean up, you can only remove chores this token created.

---

## 10. Example session

```bash
# environment
export DONETICK_URL="https://donetick.example.com"
export DONETICK_TOKEN="dt_xxxxxxxxxxxxxxxxxxxxxxxx"
export BETIDY_TZ="Europe/Vienna"
export BETIDY_DUE_HOUR=8

# preview the first few tasks
python donetick_import.py --dry-run --limit 5

# find label ids and note them
python donetick_import.py --discover-labels

# write labels.json (BeTidy room name -> Donetick label id)
cat > labels.json <<'JSON'
{
  "Kitchen": 3,
  "Bathroom": 4,
  "Living Room": 7
}
JSON

# real import: active tasks, with room labels, skipping duplicates by name
python donetick_import.py --labels-map labels.json --skip-existing
```

Per-task output during a real import looks like:

```
[  1/42] OK  id=101 Wipe kitchen counters
[  2/42] OK  id=102 Water the plants (dueDate FAILED 400)
...
Done. created=42 failed=0 skipped=0 (of 42 tasks)
```

A `(dueDate FAILED …)` note means the chore was created but the follow-up
`PUT …/dueDate` (Quirk A) didn't land — you can set that chore's date manually in the
UI.

---

## Troubleshooting

- **`401`/`403` on every request** — check `DONETICK_TOKEN` is a current
  **Access Token** (Settings → Access Token), sent as the `secretkey` header, and that
  `DONETICK_URL` has no trailing path.
- **Assignees all end up unassigned** — the BeTidy profile names didn't match any
  circle member's first name; add the people to your Donetick circle, or adjust names.
- **Wrong recurrence day** — confirm the BeTidy task's `days`/`intervalUnit` in
  `betidy_export.json`; weekly snapping only applies when `days` is present.
- **Nothing imported** — by default only `active` tasks are imported; add
  `--include-inactive` to bring in finished/archived ones.
