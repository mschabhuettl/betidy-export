# BeTidy data model

A field-level reference for the data `betidy-export` reads out of BeTidy
(`io.betidy.BeTidy`). BeTidy is a thin client over an **AWS Amplify DataStore**
backend: a Cognito user pool for authentication and an **AppSync GraphQL API**
for data. This document describes the GraphQL models the toolkit reads, the query
variants exposed by the schema, and the exact shape of each record as it lands in
`betidy_export.json`.

The model names and fields below were recovered by decompiling the APK with
[jadx] and reading the generated DataStore classes under
`com/amplifyframework/datastore/generated/model/` together with
`res/raw/amplifyconfiguration.json`. They are public app configuration, not
secrets.

> **Scope.** `betidy_extract.py` reads four models — **UserTask**, **UserProject**,
> **TaskHistory**, and **User**. The schema also defines guest variants
> (`Guest`, `GuestTask`, `GuestProject`) that the app uses *before* you create an
> account; a registered user's data lives entirely in the `User*` models, so those
> are the only ones the toolkit touches.

---

## Backend at a glance

| Component | Value |
| --- | --- |
| Region | `eu-central-1` |
| Cognito user pool | `eu-central-1_M3hzKkFkb` (username = email, `USER_SRP_AUTH`) |
| App client | `50io20n8v16d0a1p26076gn8vs` (public client, no secret) |
| Identity pool | `eu-central-1:25cc0682-e76c-4938-b771-489b7067c75e` |
| AppSync GraphQL | `https://22ld3xjokjfbnchtehtunjg2im.appsync-api.eu-central-1.amazonaws.com/graphql` |
| GraphQL auth mode | `AMAZON_COGNITO_USER_POOLS` — send the Cognito **ID token** in the `Authorization` header (no `Bearer` prefix) |

---

## The `identityId` — your primary key

Every record is scoped by an **`identityId`**, and understanding it is essential to
querying the API.

1. Log in to the Cognito user pool (SRP) → you get an **ID token**.
2. Federate that token through the **identity pool** (`get_id`) → you get a
   federated identity id of the form `eu-central-1:<uuid>`.
3. **BeTidy stores only the part after the colon.** The app does exactly
   `identityId = federatedId.split(":")[1]` (found in
   `AuthBackend._convertIdentityId`), so the value stamped on every `UserTask`,
   `UserProject`, `TaskHistory` and `User` row is the bare UUID **without** the
   `region:` prefix.

You must therefore query with the colon-stripped UUID. Query with the full
`eu-central-1:<uuid>` and you match nothing.

---

## GraphQL query variants

Amplify generates four read operations per model. They are **not** interchangeable —
picking the wrong one against this multi-tenant table is the single biggest trap.

| Variant | Shape | Behaviour on BeTidy | Used by the tool? |
| --- | --- | --- | --- |
| `get<Model>` | `get…(id)` / `getUser(identityId)` | Fetch one record by key. | **Yes**, for `getUser`. |
| `list<Model>` | `list…(filter, limit, nextToken)` | Owner-based auth makes the **unfiltered** `listUserTasks` scan the *entire shared, multi-tenant table*. It returns page after empty page with a `nextToken` and effectively never finishes. **Avoid.** | No |
| `sync<Model>` | `sync…(lastSync, …)` | DataStore delta-sync operation; also table-wide, meant for the app's local replica. | No |
| `<model>ByIdentityId` | `…ByIdentityId(identityId, limit, nextToken)` | Queries a **GSI keyed on `identityId`**, so it returns only *your* rows, efficiently and paginated. **This is the correct way to read your data.** | **Yes**, for tasks / projects / history. |

### What `betidy_extract.py` actually calls

| Model | Query field | `identityId` arg type | Field selection |
| --- | --- | --- | --- |
| UserTask | `userTasksByIdentityId` | `String!` | see [UserTask](#usertask) |
| UserProject | `userProjectsByIdentityId` | `String!` | see [UserProject](#userproject) |
| TaskHistory | `taskHistoriesByIdentityId` | `String!` | see [TaskHistory](#taskhistory) |
| User | `getUser` | `ID!` | see [User](#user) |

> **Gotcha — argument typing.** The GSI queries type `identityId` as **`String!`**,
> while `getUser` types it as **`ID!`**. The value is the same colon-stripped UUID;
> only the GraphQL variable declaration differs (`$id:String!` vs `$id:ID!`). Get
> the type wrong and AppSync rejects the whole operation.

Each `…ByIdentityId` call pages with `limit: 1000`, following `nextToken` until it
is null.

---

## UserTask

A single chore. This is the model the Donetick importer maps from. Fields, in the
order `betidy_extract.py` selects them:

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | String | Task id (primary key). |
| `identityId` | String | Owner's colon-stripped identity UUID. |
| `active` | Bool | `true` = live chore; `false` = archived/deleted. The importer skips inactive tasks unless `--include-inactive`. |
| `templateId` | String \| null | Id of the BeTidy preset this task was created from (e.g. `betidy_pro_task1`); `null` for user-created tasks. |
| `title` | String | Chore name. |
| `roomId` | String \| null | References a room `id` in the [User](#user) `rooms` array. |
| `projectId` | String \| null | References a [UserProject](#userproject) `id`; `null` for standalone chores. |
| `type` | Enum string | **`INTERVAL`** = recurring, **`DATE`** = one-time. Drives recurrence mapping. |
| `important` | Int | Priority: **0** = none, **1** = important, **2** = very important. |
| `intervalUnit` | String \| null | Recurrence unit for `INTERVAL` tasks: `"day"`, `"week"`, or `"month"`. |
| `intervalCount` | Int \| null | Recurrence multiplier — e.g. `intervalUnit:"month"`, `intervalCount:3` = every 3 months. |
| `lastTodoDate` | Date \| null | The previous scheduled/occurrence date (`YYYY-MM-DD`). |
| `todoDate` | Date \| null | The **next due date** (`YYYY-MM-DD`). Note: BeTidy often leaves this **in the past** — it is the last-computed occurrence, not a rolled-forward one. The importer rolls it forward by the interval to the next occurrence `>=` today. |
| `finishedDate` | Date \| null | Set when a one-time task is completed; `null` while pending. |
| `description` | String | Free-text notes (may be empty). |
| `assigned` | List<String> | Ids of the **profiles** this chore is assigned to (see User `profiles`). |
| `creator` | String | Profile id of whoever created the chore. |
| `effort` | Int | Effort/weight, **0–2**. Mapped to Donetick `points`. |
| `lastHistoryId` | String \| null | Id of the most recent [TaskHistory](#taskhistory) completion. |
| `lastSkipDate` | Date \| null | Date the task was last skipped, if ever. |
| `days` | List<Int> \| null | For weekly tasks, the intended weekdays as **1 = Mon … 7 = Sun**. `null` for non-weekly tasks. |
| `createdAt` | DateTime | ISO-8601 creation timestamp. |
| `updatedAt` | DateTime | ISO-8601 last-modified timestamp. |

### Interpreting recurrence

* `type = "DATE"` → a one-shot task; `todoDate` is the (single) due date. `intervalUnit`/`intervalCount`/`days` are unused.
* `type = "INTERVAL"` → repeats every `intervalCount × intervalUnit`.
  * With `intervalUnit = "week"`, `days` lists the target weekday(s); a task with `days:[1]` recurs on Mondays.
  * `todoDate` is the anchor the importer rolls forward (and, for weekly tasks, snaps onto the weekday in `days`).

### Example record

Straight from [`examples/betidy_export.sample.json`](../examples/betidy_export.sample.json) —
a weekly "Take out the trash" chore, recurring every Monday (`days:[1]`), assigned to
profile `bbbb2222` (Bob), with `todoDate` sitting in the past:

```json
{
  "id": "task-0001",
  "identityId": "00000000-1111-2222-3333-444444444444",
  "active": true,
  "templateId": "betidy_pro_task1",
  "title": "Take out the trash",
  "roomId": "allgemein",
  "projectId": null,
  "type": "INTERVAL",
  "important": 0,
  "intervalUnit": "week",
  "intervalCount": 1,
  "lastTodoDate": "2025-06-02",
  "todoDate": "2025-01-06",
  "finishedDate": null,
  "description": "",
  "assigned": ["bbbb2222"],
  "creator": "aaaa1111",
  "effort": 0,
  "lastHistoryId": null,
  "lastSkipDate": null,
  "days": [1],
  "createdAt": "2025-01-01T10:00:00.000Z",
  "updatedAt": "2025-06-02T10:00:00.000Z"
}
```

---

## UserProject

A "project" groups related tasks (e.g. a before/after cleaning job). The sample
export contains none, but `betidy_extract.py` still pulls the model. Selected fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | String | Project id (primary key); referenced by `UserTask.projectId`. |
| `identityId` | String | Owner's colon-stripped identity UUID. |
| `templateId` | String \| null | Preset the project was created from, if any. |
| `roomId` | String \| null | References a room `id` in User `rooms`. |
| `title` | String | Project name. |
| `startDate` | Date \| null | When the project starts. |
| `type` | String | Project type/category. |
| `beforeImage` | String \| null | Storage key / URL for the "before" photo. |
| `afterImage` | String \| null | Storage key / URL for the "after" photo. |
| `creator` | String | Profile id of the creator. |
| `createdAt` | DateTime | ISO-8601 creation timestamp. |
| `updatedAt` | DateTime | ISO-8601 last-modified timestamp. |

---

## TaskHistory

One row per completion — the audit log of who did what, when. Selected fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | String | History id (primary key). |
| `identityId` | String | Owner's colon-stripped identity UUID. |
| `taskId` | String | The `UserTask.id` that was completed. |
| `effort` | Int | Effort recorded for this completion (0–2). |
| `profileId` | String | Profile id of whoever completed it (see User `profiles`). |
| `time` | DateTime | When the completion happened (ISO-8601). |
| `isProject` | Bool | `true` if the completion belongs to a project rather than a task. |
| `roomType` | String | The room `type` slug at completion time (e.g. `kueche`, `allgemein`). |
| `title` | String | Task title captured at completion time. |
| `templateId` | String \| null | Template id captured at completion time. |
| `createdAt` | DateTime | ISO-8601 creation timestamp. |
| `updatedAt` | DateTime | ISO-8601 last-modified timestamp. |

Example:

```json
{
  "id": "hist-0001",
  "identityId": "00000000-1111-2222-3333-444444444444",
  "taskId": "task-0001",
  "effort": 0,
  "profileId": "bbbb2222",
  "time": "2025-06-02T09:00:00Z",
  "isProject": false,
  "roomType": "allgemein",
  "title": "Take out the trash",
  "templateId": "betidy_pro_task1",
  "createdAt": "2025-06-02T09:00:00Z",
  "updatedAt": "2025-06-02T09:00:00Z"
}
```

---

## User

Your account record — one per identity, fetched with `getUser`. It carries your
rooms and profiles, which the exporters use to resolve the ids referenced by tasks
and history. Selected fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `identityId` | String | Your colon-stripped identity UUID (primary key). |
| `name` | String | Account display name. |
| `email` | String | Account email (= Cognito username). |
| `dataPrivacy` | **JSON string** | Privacy settings, JSON-encoded (often `"{}"`). |
| `rooms` | **JSON string** | JSON-encoded array of room objects — see below. |
| `profiles` | **JSON string** | JSON-encoded array of profile objects — see below. |
| `pro` | **JSON string** | Pro/subscription state, JSON-encoded. |
| `packages` | **JSON string** | Purchased packages, JSON-encoded array. |
| `holidays` | **JSON string** | Holiday configuration, JSON-encoded array. |
| `createdAt` | DateTime | ISO-8601 creation timestamp. |
| `updatedAt` | DateTime | ISO-8601 last-modified timestamp. |

> **Gotcha — these are strings, not objects.** `rooms`, `profiles` (and
> `dataPrivacy`, `pro`, `packages`, `holidays`) come back as **JSON-encoded
> strings**, so you must `json.loads()` them before use. The toolkit decodes only
> `rooms` and `profiles`:
>
> ```python
> rooms    = json.loads(user.get("rooms")    or "[]")
> profiles = json.loads(user.get("profiles") or "[]")
> ```

### `rooms[]` inner shape

| Key | Type | Meaning |
| --- | --- | --- |
| `id` | String | Room id — the value `UserTask.roomId` points at. |
| `name` | String | Human-readable room name (e.g. `"Kitchen"`). |
| `type` | String | Room type slug (e.g. `kueche`, `badezimmer`, `allgemein`); matches `TaskHistory.roomType`. |
| `active` | Bool | Whether the room is in use. |

```json
{"id": "kitchen", "name": "Kitchen", "type": "kueche", "active": true}
```

### `profiles[]` inner shape

| Key | Type | Meaning |
| --- | --- | --- |
| `id` | String | Profile id — the value `UserTask.assigned` / `creator` and `TaskHistory.profileId` point at. |
| `name` | String | Person's name (e.g. `"Alice"`). The Donetick importer matches these to circle members by first name. |
| `active` | Bool | Whether the profile is active. |

```json
{"id": "aaaa1111", "name": "Alice", "active": true}
```

> Real records may carry a few extra profile keys (e.g. `limited`,
> `notificationActive`); the toolkit only reads `id`, `name`, and `active`.

---

## Enumeration quick-reference

| Field | Values |
| --- | --- |
| `UserTask.type` | `INTERVAL` (recurring), `DATE` (one-time) |
| `UserTask.important` | `0` none, `1` important, `2` very important |
| `UserTask.intervalUnit` | `day`, `week`, `month` |
| `UserTask.days` | weekday ints, **1 = Mon … 7 = Sun** |
| `UserTask.effort` / `TaskHistory.effort` | `0`–`2` |

---

## How the export bundle is assembled

`betidy_extract.py` writes a single `betidy_export.json` with this top-level shape:

```json
{
  "identityId": "<your colon-stripped UUID>",
  "user":     { /* User record */ },
  "tasks":    [ /* UserTask[] */ ],
  "projects": [ /* UserProject[] */ ],
  "history":  [ /* TaskHistory[] */ ]
}
```

`build_exports.py` then flattens this into CSVs and `betidy.sqlite`, resolving
`roomId` → room name and profile ids → names via the decoded `User.rooms` /
`User.profiles` arrays.

[jadx]: https://github.com/skylot/jadx
