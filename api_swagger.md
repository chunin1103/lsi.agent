LSI Keyword API — Technical Reference (v2.3.0 - SSE)
====================================================

This service combines **Google Autosuggest** and (optionally) **Google Ads Keyword Planner** to produce a stream of distinct Vietnamese keywords for a given seed term.
All results are cached in-memory for one hour (TTL = 3600 s).

---

## 1. Endpoints

| Verb / Path            | Purpose                                                                 | Returns Immediately?      |
|------------------------|-------------------------------------------------------------------------|---------------------------|
| `GET /keywords/stream` | **Live stream (SSE)**; sends keywords as Server-Sent Events.            | ✔ (body keeps streaming)  |
| `GET /status`          | Cache & Task diagnostics (development).                                 | ✔                         |
| `GET /clear_cache`     | Flush cache and task statuses (development).                            | ✔                         |
| `GET /`                | One-line welcome + link to `/docs` (FastAPI's interactive documentation). | ✔                         |

---

### 1.1 `GET /keywords/stream` (Server-Sent Events)

This endpoint streams keywords in real-time using Server-Sent Events (SSE) as they are discovered.

**Query Parameters:**

| Query Parameter | Type    | Default | Notes                                                                 |
|-----------------|---------|---------|-----------------------------------------------------------------------|
| `seed`          | `str`   | —       | Required. The initial keyword to generate ideas from. URI-encode spaces, e.g., `n%C6%B0%E1%BB%9Bc%20m%E1%BA%AFm`. Min length: 1. |
| `planner`       | `bool`  | `true`  | If `true`, also includes keywords from Google Ads Keyword Planner.      |

**SSE Response Stream:**

The client will receive a stream of events. Each event block is terminated by a double newline (`\n\n`).

* **Keyword Event:**
    * `id: <event_id>` (e.g., `id: 1`)
    * `event: keyword`
    * `data: {"keyword": "nước mắm phú quốc"}`

    This event is sent for each unique keyword found. The `data` is a JSON string containing the keyword.

* **Done Event:**
    * `id: <event_id>` (e.g., `id: 150`)
    * `event: done`
    * `data: {"seed": "nước mắm", "message": "All keyword generation tasks complete."}`

    This event is sent once all keyword sources (Autosuggest and Planner, if enabled) have finished processing for the given `seed`.

* **Error Event (if an error occurs during generation for a source):**
    * `id: <event_id>`
    * `event: error`
    * `data: {"error": "Suggest crawler failed", "detail": "Specific error message..."}`
    OR
    * `data: {"message": "An unexpected error occurred in the SSE stream.", "detail": "Specific error message..."}`

    This event indicates an issue with a specific keyword producer or the stream itself. The stream might continue if other producers are active, or it might terminate.

* **Comments (Pings):**
    * The stream may occasionally send comment lines starting with a colon (`:`) for keep-alive purposes (e.g., `: ping - <timestamp>`). Clients should typically ignore these.

**Example cURL Usage:**
```bash
curl -N "[http://127.0.0.1:8000/keywords/stream?seed=n%C6%B0%E1%BB%9Bc%20m%E1%BA%AFm&planner=true](http://127.0.0.1:8000/keywords/stream?seed=n%C6%B0%E1%BB%9Bc%20m%E1%BA%AFm&planner=true)"
```

*(The `-N` flag disables buffering in curl, allowing you to see events as they arrive.)*

---

### 1.2 `GET /status`

Provides the current status of keyword generation tasks (Suggest and Planner) for a given seed keyword, and the total number of unique keywords cached so far. This may initiate background caching tasks if the seed is new.

**Query Parameters:**

| Query Parameter | Type    | Default | Notes                                                                 |
|-----------------|---------|---------|-----------------------------------------------------------------------|
| `seed`          | `str`   | —       | Required. The seed keyword to check the status for. Min length: 1.    |
| `planner`       | `bool`  | `true`  | Indicates if the planner task is expected to run for this seed. This helps interpret the `planner_task_complete` field in the response. |

**Response (JSON):**

```jsonc
{
  "seed": "nước mắm",
  "total_cached_keywords": 214, // Total unique keywords currently in cache for this seed
  "suggest_task_complete": true,  // True if the Autosuggest task has finished for this seed
  "planner_task_complete": false  // True if the Planner task has finished (or was not run)
}
```
* `total_cached_keywords`: May grow if background tasks are still running.
* `suggest_task_complete` / `planner_task_complete`: Reflects the completion status of the background caching tasks.

---

### 1.3 `GET /clear_cache`

Flushes all in-memory keyword caches and resets the completion status of all background tasks. Useful for development and testing.

**Query Parameters:** None.

**Response (Plain Text):**
```
Cache and task statuses cleared successfully.
```

---

### 1.4 `GET /`

Returns a one-line welcome message and a link to the interactive API documentation (usually `/docs` or `/redoc` provided by FastAPI).

**Query Parameters:** None.

**Response (Plain Text):**
```
LSI Keyword API (SSE - Refactored) - v2.3.0
API Documentation available at: /docs or /redoc
```
