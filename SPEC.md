# SPEC.md — Indoor Spatial Twin (4-hour hackathon build)

**Read this file as the complete and final scope. Do not add anything not listed here.**

## Hard constraints

- No database. State lives in one JSON file loaded into memory at startup.
- No Docker, no auth, no deployment. Runs on `localhost` only.
- No ORM, no service layer, no abstraction. One backend file, one frontend file.
- Deterministic geometry and routing are done in Python. The LLM never computes a route,
  a distance, or a coordinate.
- Total target: under 600 lines of code.

## Stack

- Backend: Python 3.11, FastAPI, `networkx`, `google-genai`, `python-multipart`.
- Frontend: one `index.html`, vanilla JS, inline SVG. Served by FastAPI as a static file.
- Video: `ffmpeg` via subprocess, for frame sampling only.

## Model

Use `gemini-robotics-er-2-preview` (Gemini Robotics ER 2, public preview since 2026-07-30).

Pin it as a constant: `MODEL = "gemini-robotics-er-2-preview"`.

Do **not** use `gemini-robotics-er-2-streaming-preview` — the streaming endpoint does not
support structured output, which every call here depends on.

This model is newer than most training data. Do not guess the SDK surface. The call shape is:

```python
from google import genai
from google.genai import types
from pydantic import BaseModel

client = genai.Client()  # reads GEMINI_API_KEY from env

class LocateResult(BaseModel):
    node_id: str
    confidence: float
    evidence: list[str]
    alternatives: list[str]

resp = client.models.generate_content(
    model=MODEL,
    contents=[
        types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
        prompt,
    ],
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=LocateResult,
    ),
)
result = LocateResult.model_validate_json(resp.text)
```

Validate every model response against the Pydantic schema, and then validate that
`node_id` actually exists in `building.json`. If it does not, treat it as `unknown`.
The model is never trusted to name an entity that isn't already in the world model.

## Data: `building.json`

Hand-authored by the humans. The agent must not generate or invent its contents —
only load and validate it. One floor, roughly 12 nodes.

```json
{
  "building": "Main Block",
  "floor": 3,
  "svg_viewbox": [0, 0, 1000, 600],
  "nodes": [
    {
      "id": "lab_302",
      "name": "Lab 302",
      "type": "room",
      "xy": [120, 340],
      "svg_rect": [90, 300, 80, 60],
      "visual_signature": "Blue double door, 'LAB 302' sign on the right at head height, fire extinguisher on the left wall, lift visible about 15m down the corridor",
      "attributes": { "seats": 30, "projector": true, "accessible": true }
    }
  ],
  "edges": [
    { "a": "lab_302", "b": "corridor_c_mid", "distance": 4, "accessible": true, "passable": true }
  ],
  "equipment": [
    {
      "id": "gpu_ws_7",
      "name": "GPU Workstation 7",
      "node": "lab_302",
      "visual_signature": "black tower PC with orange fans, on the bench nearest the window",
      "manual_snippet": "Before use: log in with your lab ID. Check GPU temperature is under 60C. Do not run jobs longer than 4h without booking."
    }
  ]
}
```

Node `type` is one of: `room`, `corridor`, `stairs`, `lift`, `entrance`.
`xy` is used for routing distance and SVG placement. `svg_rect` is `[x, y, w, h]`.

## Endpoints

### `POST /locate` — multipart image
Closed-set visual localization. Sends the image plus every node's `visual_signature`
to ER 2 and asks which one the photographer is standing at.

Response: `{ node_id, node_name, confidence, evidence: [...], alternatives: [...] }`

Prompt (build it from `building.json` at request time):

```
You are localizing a person inside {building}, floor {floor}.
These are the ONLY possible locations:

- lab_302: Blue double door, 'LAB 302' sign on the right...
- corridor_c_mid: ...
[one line per node]

Look at the photo and decide which of these locations the photographer is
standing at, or immediately outside.

Rules:
- Choose an id from the list above, or return "unknown". Never invent an id.
- Cite concrete visible evidence: sign text you can actually read, door colour,
  fixtures, corridor geometry, visible equipment.
- Do not claim to read signage that is not legible in the image.
- If two locations are plausible, put the second in alternatives.
- confidence is 0.0-1.0 and should reflect how much distinguishing evidence you found.
```

### `GET /route?from=&to=&accessible=false`
Pure `networkx` Dijkstra over the edge list, weighted by `distance`.
Edges with `passable: false` are excluded. If `accessible=true`, edges with
`accessible: false` are also excluded (this is what routes around the stairs).

Response: `{ path: [node_ids], distance_m, steps: ["Leave Lab 302, turn left", ...] }`

Steps are generated from node types with a template — no LLM call.
If no path exists, say so explicitly; do not fall back to a partial route.

### `POST /identify` — multipart image
One ER 2 call, closed set over `equipment[]`, same rules as `/locate`.
Response: `{ equipment_id, name, confidence, node, manual_snippet }`.
The manual snippet is looked up from `building.json`, never generated.

### `POST /observe` — multipart video
1. `ffmpeg -i in.mp4 -vf fps=1/5 frame_%03d.jpg` — sample about 4 frames.
2. One ER 2 call with all frames plus the node list, schema:
   `{ observations: [{ node_id, passable: bool, confidence: float, evidence: str }] }`
3. For each observation with `confidence > 0.6`, set `passable` on every edge
   touching that node and record it in an in-memory list.

Response: `{ updates: [...] }`

### `GET /state`
Returns nodes and edges with current `passable` flags, plus the observation list.
The frontend polls this after `/observe` to repaint.

## Frontend — single `index.html`

- Inline SVG floor plan drawn from `svg_rect` values. Rooms are rects, corridors are rects,
  labels are text.
- Route drawn as an animated polyline through `path` node `xy` values.
- Blocked nodes filled red. Located node highlighted with a pulsing outline.
- Three buttons: "Where am I?" (file input, calls `/locate`), "What is this?" (calls `/identify`),
  "Upload camera footage" (calls `/observe`).
- A side panel showing `confidence` and the `evidence` list verbatim. This panel is the demo —
  it is what makes the system look like it is reasoning rather than guessing.
- No framework, no build step, no npm.

## Eval script — `eval_locate.py`

Build this early, not last. Photos live in `photos/` named `{node_id}_{n}.jpg`.
The script runs `/locate` on every one, prints per-photo predicted vs actual,
and an accuracy total. Prompt changes are judged against this number, not against vibes.

## File layout

```
/main.py           FastAPI app, all endpoints
/gemini.py         the three ER 2 calls + schemas
/world.py          load building.json, networkx graph, routing, passable state
/index.html        the entire frontend
/building.json     hand-authored, do not modify
/eval_locate.py
/photos/
```

## Explicitly out of scope — do not build

Floor-plan PDF parsing, PostGIS, pgvector, embeddings, Neo4j, Redis, Docker,
docker-compose, document RAG, PDF ingestion, an event database, temporal memory,
YOLO, ByteTrack, object tracking, user accounts, multi-building support,
multi-floor support, WebSockets, deployment, tests beyond `eval_locate.py`.

If any of these seem necessary, they are not. Stop and ask.
