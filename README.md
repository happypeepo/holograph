# Translucent

made with ❤️ by MnM

Indoor wayfinding for buildings nobody will ever map commercially.

GPS stops at the front door. The products that solve indoor navigation need infrastructure —
Wi-Fi fingerprinting, BLE beacons, or a survey crew that has physically walked the building
with equipment. That cost is only worth paying for airports and malls, which is why your
college block doesn't have it.

This system needs one hand-written JSON file per building. Everything geometric — every
route, distance and step — is Dijkstra over a `networkx` graph. The model only parses
plain-English navigation commands into a fixed action shape; deterministic code handles
the building state and route computation.

**The model parses intent. Deterministic code does geometry.** It is never asked for a
distance, a coordinate, or a path.

---

## Setup

Requires **Python 3.11+**.

```bash
python3 -m pip install fastapi uvicorn networkx google-genai pydantic
```

Copy the env template and add your key:

```bash
cp .env.example .env
```

`.env` is gitignored and must stay that way — this repo is public.

Then run:

```bash
set -a; source .env; set +a && python3 -m uvicorn main:app --port 8000
```

Open <http://127.0.0.1:8000>.

> There is no dotenv dependency by design — the approved dependency list is fixed, so `.env`
> is a plain shell-sourceable file rather than something the app parses.

### What you get

| Route | What it is |
|---|---|
| `/` | Landing page — pick a building |
| `/aryabhatta` | 188 spaces, 5 levels |
| `/bhaskaracharya` | 209 spaces, 7 levels |

---

## Tech stack

| Layer | Choice | Why this, and not the obvious alternative |
|---|---|---|
| Chat parsing | `gemini-robotics-er-2-preview` | Structured command output. The `-streaming-` variant **cannot** do structured output, which every call here depends on, so it is explicitly excluded. |
| Schema | Pydantic | Passed directly as `response_schema`, so malformed output fails at the SDK boundary instead of in our parsing. |
| Geometry | networkx | Dijkstra over ~200 nodes is microseconds. Nothing here justifies PostGIS or a graph database. |
| API | FastAPI + uvicorn | JSON with no ceremony. Route handlers contain no logic. |
| State | One JSON file in memory | The dataset is a building. A database would add operational cost and no capability. |
| Frontend | Vanilla JS + inline SVG | No framework, no bundler, no CDN. Two self-contained files. |
| 3D | Hand-written projection | ~60 lines of matrix maths beats a 600 KB dependency for 200 boxes. |

---

## Gemini Robotics ER 2

Pinned as a constant in `gemini.py`:

```python
MODEL          = "gemini-robotics-er-2-preview"
THINKING_LEVEL = "medium"    # the balance the robotics docs recommend
```

Every Gemini call goes through one helper with `response_mime_type="application/json"` and a
Pydantic class as `response_schema`.

### The chat call

| Function | Input | Returns |
|---|---|---|
| `chat(message, history, world)` | plain English | one command from a fixed six-action set |

`chat` only parses the sentence. Every route, distance and blockage it triggers is computed
against networkx afterwards.

### Every response is validated twice

1. **Schema.** Pydantic, enforced at the SDK boundary.
2. **Identity.** Every id the model returned is checked with `world.has()` against the
   building file. Anything that fails becomes `"unknown"` with confidence `0.0`, and the
   rejection is logged to stderr.

Prompts are module-level constants built from the world model at call time — the candidate
lists are injected fresh on every request. No id is ever hardcoded in a prompt.

### ER 2 also built the maps

The Aryabhatta model was not hand-authored. Its geometry was extracted by ER 2 from
photographs of the building's **Emergency Escape Route boards** — the fire plans that are
legally required on every floor of every public building.

Asking for a whole board at once returned 8 spaces; the room labels are too small at
full-image scale. Cropping to the drawing and sweeping it as a 3×3 grid of overlapping tiles
returns 52 on the ground floor alone. The coordinate convention is defined in the response
schema rather than assumed — the robotics docs specify `[y, x]` normalized 0–1000 for
*points* and say nothing about boxes.

Everything after extraction is deterministic: dedupe, the corridor ring, adjacency, vertical
matching, and all distances.

---

## Layout

```
main.py           FastAPI wiring. No logic beyond calling world and gemini.
world.py          Graph, Dijkstra, passable/accessible state. Zero network, zero model.
gemini.py         ER 2 chat prompt and schema.
index.html        Landing page.
translucent.html  The 3D building view — projection, routing UI, chat.
fixtures/         building.*.json — the world models.
photos/plans/     The escape-route boards the models were built from.
```

`world.py` is importable and fully exercisable **with no API key** — nothing in it touches the
network or a model.

### Endpoints

Per building, mounted at its own prefix:

```
GET  /                  building picker
GET  /health            service health check
GET  /<bldg>/state      GET  /<bldg>/route      POST /<bldg>/block      POST /<bldg>/chat
GET  /buildings         what is mounted, so the frontend never has to guess
```

### The data model

One file per building. `visual_signature` is descriptive metadata for spaces; a good one
names sign text, door colour, and a fixture.

```json
{
  "id": "lab_302", "name": "Lab 302", "type": "room",
  "xy": [120, 340], "svg_rect": [90, 300, 80, 60], "floor": 3,
  "visual_signature": "Blue double door, 'LAB 302' sign on the right at head height,
                       fire extinguisher on the left wall",
  "attributes": { "seats": 30, "accessible": true }
}
```

`type` is one of `room · corridor · stairs · lift · entrance`. Edges carry `distance`,
`accessible` (false on stairs — this is what routes a wheelchair around them) and `passable`.
