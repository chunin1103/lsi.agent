LSI Keyword API — technical reference
====================================

This service combines **Google Autosuggest** and **Google Ads Keyword Planner** to
produce up to **500 distinct Vietnamese keywords** for one seed term.  
All results are cached in‑memory for one hour (TTL = 3600 s).

---

## 1 . Endpoints

| Verb / Path | Purpose | Returns immediately? |
|-------------|---------|----------------------|
| `GET /keywords` | **Snapshot** of the cache (paged). | ✔ |
| `GET /keywords/stream` | **Live stream**; sends keywords as soon as they are discovered. | ✔ (body keeps streaming) |
| `GET /status` | Cache diagnostics (development). | ✔ |
| `GET /clear_cache` | Flush cache (development). | ✔ |
| `GET /` | One‑line welcome + link to `/docs`. | ✔ |

---

### 1.1  `GET /keywords`

| Query parameter | Type | Default | Notes |
|-----------------|------|---------|-------|
| `seed` | `str` | — | Required. URI‑encode spaces, e.g. `nước%20mắm`. |
| `page` | `int ≥ 1` | `1` | 1‑based index. |
| `page_size` | `int 1…100` | `50` | Items per page. |
| `planner` | `bool` | `true` | If `true`, also launch Google Ads Planner fetch (once per seed). |

**Response (JSON)**

```jsonc
{
  "seed": "nước mắm",
  "total_available": 214,           // may grow while crawler runs
  "page": 1,
  "page_size": 50,
  "keywords": [
    "nước mắm phú quốc",
    "nước mắm nhĩ",
    …
  ]
}
