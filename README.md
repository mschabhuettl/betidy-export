# betidy-export

**Liberate your chores from the BeTidy app — export to CSV / JSON / SQLite, and optionally import into Donetick.**

[BeTidy](https://betidy.io) (`io.betidy.BeTidy`) is an Android cleaning-schedule / chores app with **no export feature and no documented API**. `betidy-export` is a small, unofficial toolkit that authenticates as *you* — with your own credentials — reads *only your own* data out of BeTidy's cloud backend, and writes it to open formats you control. It can also, optionally, import your tasks into a self-hosted [Donetick](https://github.com/donetick/donetick) instance with full recurrence, assignee and priority fidelity.

Everything here is for **personal data portability**. See the [Disclaimer](#disclaimer).

## Features

- **Full export** of your BeTidy data — tasks, projects, completion history and your profile — into a single `betidy_export.json`.
- **Clean, tidy outputs**: four CSV files plus a queryable `betidy.sqlite` database.
- **Optional Donetick import** that preserves recurrence, due dates, room labels, assignees, priority and effort/points — not just name + due date.
- **Safe by default**: `--dry-run` preview, `--limit`, `--skip-existing`, per-room filtering.
- Pure Python, three self-contained scripts, three dependencies.

## How it works

BeTidy is a thin client over an **AWS Amplify** backend: it signs in against an AWS Cognito user pool and reads its data from an AppSync GraphQL API. `betidy-export` does the same three things — it **logs in** with your e-mail and password (Cognito SRP), **pulls your data** by querying the per-user GraphQL index, and **exports or imports** it into open formats. The backend identifiers baked into the scripts were recovered by decompiling the freely downloadable APK.

Read more: [How it works](docs/how-it-works.md) · [Data model](docs/data-model.md) · [Donetick import](docs/donetick-import.md).

## Requirements

- Python **3.9+**
- A BeTidy account (your own e-mail + password)
- *(Optional, for import)* a self-hosted Donetick instance and an API access token
- Python packages: `pycognito`, `boto3`, `requests`

## Install

```bash
git clone https://github.com/mschabhuettl/betidy-export.git
cd betidy-export
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Copy `.env.example` to `.env` for reference, or just export the variables shown below.

## Usage

### 1. Export from BeTidy

```bash
export BETIDY_EMAIL="you@example.com"
export BETIDY_PASSWORD="your-betidy-password"
python betidy_extract.py            # -> betidy_export.json
```

This logs in, resolves your identity id, pulls every record that belongs to you, and writes the raw bundle to `betidy_export.json`.

### 2. Build CSV / SQLite

```bash
python build_exports.py             # -> CSVs + betidy.sqlite
```

Turns `betidy_export.json` into human-readable CSV files and a SQLite database (see [Output files](#output-files)).

### 3. (Optional) Import into Donetick

Generate an access token in Donetick under **Settings → Access Token**, then:

```bash
export DONETICK_URL="https://donetick.example.com"
export DONETICK_TOKEN="your-donetick-access-token"

python donetick_import.py --dry-run              # preview, sends nothing
python donetick_import.py --discover-labels      # list your Donetick label ids
python donetick_import.py --labels-map labels.json   # real import, with room labels
```

`--labels-map` takes a JSON file of `{"<BeTidy room name>": <donetick label id>}`. It's optional — without it, the room is still recorded in each chore's description.

#### Environment variables

| Variable | Used by | Required | Default | Purpose |
|---|---|---|---|---|
| `BETIDY_EMAIL` | `betidy_extract.py` | yes | — | Your BeTidy account e-mail (Cognito username). |
| `BETIDY_PASSWORD` | `betidy_extract.py` | yes | — | Your BeTidy password. |
| `DONETICK_URL` | `donetick_import.py` | yes¹ | — | Base URL of your Donetick instance. |
| `DONETICK_TOKEN` | `donetick_import.py` | yes¹ | — | Donetick access token (sent as the `secretkey` header). |
| `BETIDY_TZ` | all | no | `UTC` | IANA timezone for due dates, e.g. `Europe/Vienna`. |
| `BETIDY_DUE_HOUR` | `donetick_import.py` | no | `8` | Hour of day (0–23) tasks become due. |
| `BETIDY_OUTFILE` | all | no | `betidy_export.json` | Override the bundle filename. |

¹ `DONETICK_URL` is always required by the import script; `DONETICK_TOKEN` is required unless you pass `--dry-run`.

#### Import flags

| Flag | Effect |
|---|---|
| `--dry-run` | Build and print payloads, POST nothing. |
| `--limit N` | Only process the first `N` tasks. |
| `--include-inactive` | Also import inactive / finished tasks. |
| `--skip-existing` | Skip tasks whose name already exists in Donetick. |
| `--room NAME` | Only import tasks from one BeTidy room. |
| `--labels-map FILE` | JSON mapping room name → Donetick label id. |
| `--discover-labels` | Print your circle's label ids (name → id) and exit. |
| `--infile FILE` | Input bundle (default: `betidy_export.json`). |

## Output files

| File | Contents |
|---|---|
| `betidy_export.json` | Raw bundle straight from the backend: `identityId`, `user`, `tasks`, `projects`, `history`. |
| `betidy_tasks.csv` | One row per task. Columns: `id`, `title`, `description`, `room`, `assignee`, `room_id`, `type`, `frequency`, `interval_unit`, `interval_count`, `weekdays`, `todo_date`, `last_todo_date`, `finished_date`, `last_skip_date`, `important`, `effort`, `active`, `creator`, `template_id`, `created_at`, `updated_at`. |
| `betidy_history.csv` | One row per completion: `id`, `task_id`, `title`, `room_type`, `profile`, `effort`, `is_project`, `time`, `template_id`, `created_at`. |
| `betidy_rooms.csv` | Your rooms: `id`, `name`, `type`, `active`. |
| `betidy_profiles.csv` | Household profiles: `id`, `name`, `active`. |
| `betidy.sqlite` | SQLite database with tables `tasks`, `history`, `rooms`, `profiles` (same columns as the CSVs). |

## BeTidy → Donetick mapping

| BeTidy | Donetick |
|---|---|
| `type = INTERVAL`, unit `day`/`week`/`month` × `N` | `frequencyType = interval`, `frequency = N`, `frequencyMetadata.unit = days`/`weeks`/`months` |
| `type = DATE` (one-time) | `frequencyType = once` |
| `todoDate` (often in the past) | rolled forward by the interval to the next occurrence ≥ today; weekly tasks snap to their weekday |
| Room name | Donetick label (via `--labels-map`), and always in the description |
| `important` `0` / `1` / `2` | `priority` `0` / P2 / P1 |
| `effort` | `points` |
| `assigned` profile names | circle member `userId`s, matched by first name |

Each chore's description also gets a line like `[BeTidy] Room: X · Repeats: Y · Assignee: Z · Effort: N` so nothing is lost even when a field has no Donetick equivalent.

## Disclaimer

This is an **unofficial** tool for **personal data portability**. It is **not affiliated with, endorsed by, or connected to** BeTidy or Donetick.

- **Use only with your own account.** The scripts authenticate with *your* credentials and read *only* your own records.
- **The BeTidy app configuration in these scripts is not secret.** The Cognito, identity-pool and AppSync identifiers are public client configuration embedded in the freely downloadable APK — identical for every user.
- **No warranty.** BeTidy has no documented API and may change or break this tool at any time. Use it at your own risk, and respect the app's Terms of Service.

## License

MIT — see [`LICENSE`](LICENSE).
