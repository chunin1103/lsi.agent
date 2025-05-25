LSI Keyword API — Technical Reference (v2.3.0 - SSE & Clustering)
================================================================

This service combines **Google Autosuggest** and (optionally) **Google Ads Keyword Planner** to produce a stream of distinct Vietnamese keywords for a given seed term. It also provides an endpoint to cluster a batch of keywords into SEO topics using an LLM.
All keyword generation results are cached in-memory for one hour (TTL = 3600 s).

---

## 1. Endpoints

| Verb / Path            | Purpose                                                                          | Returns Immediately?      |
|------------------------|----------------------------------------------------------------------------------|---------------------------|
| `GET /keywords/stream` | **Live stream (SSE)**; sends keywords as Server-Sent Events.                     | ✔ (body keeps streaming)  |
| `POST /topics/cluster` | **Cluster keywords (batch)**; groups a list of keywords into SEO topics via LLM. | ✔                         |
| `GET /status`          | Cache & Task diagnostics for keyword generation (development).                   | ✔                         |
| `GET /clear_cache`     | Flush cache and task statuses for keyword generation (development).              | ✔                         |
| `GET /`                | One-line welcome + link to `/docs` (FastAPI's interactive documentation).        | ✔                         |

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

    This event indicates an issue with a specific keyword producer or the stream itself.

* **Comments (Pings):**
    * The stream may occasionally send comment lines starting with a colon (`:`) for keep-alive purposes (e.g., `: ping - <timestamp>`). Clients should typically ignore these.

**Example cURL Usage:**
```bash
curl -N "[http://127.0.0.1:8000/keywords/stream?seed=n%C6%B0%E1%BB%9Bc%20m%E1%BA%AFm&planner=true](http://127.0.0.1:8000/keywords/stream?seed=n%C6%B0%E1%BB%9Bc%20m%E1%BA%AFm&planner=true)"
(The -N flag disables buffering in curl, allowing you to see events as they arrive.)1.2 POST /topics/clusterAccepts a batch of keywords and, using an LLM, groups them into distinct SEO topics. Optionally considers a list of existing topic names to guide the generation of new, distinct topic names for the current batch.Request Body (JSON):The request body should conform to the TopicClusterRequest schema.{
  "keywords": [ // Required: A list (batch) of keywords to be clustered. Min 1 item.
    "vietnam travel",
    "hanoi food guide",
    "best pho in saigon",
    "vietnam beaches",
    "halong bay tours"
  ],
  "existing_topic_names": [ // Optional: List of topic names already generated in this session.
    "Vietnam Tourist Attractions",
    "Vietnamese Recipes"
  ],
  "config": { // Optional: Configuration for the LLM call for this specific request.
    "llm_model": "gpt-4o",        // e.g., "gpt-4o", "claude-3-opus-20240229"
    "llm_provider": "OpenAI",     // e.g., "Bing", "Google", "OpenAI", "Liaobots" (string name)
    "llm_temperature": 0.7        // e.g., 0.0 to 2.0
  }
}
Response Body (JSON):On success (HTTP 200 OK), the response body will conform to the TopicClusteringResponse schema.{
  "clusters": [ // Topics generated for the submitted batch of keywords.
    {
      "topic_name": "Vietnam Coastal Experiences", // Ideally distinct from existing_topic_names
      "keywords": ["vietnam beaches", "halong bay tours"] // Keywords from input batch
    },
    {
      "topic_name": "Saigon Culinary Highlights",
      "keywords": ["best pho in saigon"]
    },
    {
      "topic_name": "Hanoi Gastronomy",
      "keywords": ["hanoi food guide"]
    }
    // ... other clusters from the batch
  ],
  "metadata": {
    "total_keywords_processed_in_batch": 5,
    "total_topics_found_in_batch": 3,
    "llm_model_used": "gpt-4o" // Reflects the model actually used for this request
  }
}
Error Responses:422 Unprocessable Entity: If the request body fails validation or if the LLM response cannot be processed.500 Internal Server Error / 503 Service Unavailable: If a critical error occurs during clustering or with the LLM service.1.3 GET /statusProvides the current status of keyword generation tasks (Suggest and Planner) for a given seed keyword, and the total number of unique keywords cached so far.Query Parameters:Query ParameterTypeDefaultNotesseedstr—Required. The seed keyword to check the status for. Min length: 1.plannerbooltrueIndicates if the planner task is expected to run for this seed. This helps interpret the planner_task_complete field in the response.Response (JSON):{
  "seed": "nước mắm",
  "total_cached_keywords": 214,
  "suggest_task_complete": true,
  "planner_task_complete": false
}
1.4 GET /clear_cacheFlushes all in-memory keyword caches and resets the completion status of all background keyword generation tasks.Query Parameters: None.Response (Plain Text):Cache and task statuses cleared successfully.
1.5 GET /Returns a one-line welcome message and a link to the interactive API documentation.Query Parameters: None.Response (Plain Text):LSI Keyword API (SSE - Refactored & Clustering) - v2.3.0
API Documentation available at: /docs or /redoc
