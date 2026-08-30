# Indoor Spatial Twin — project rules

Four-hour hackathon build, two humans driving agents. Optimize for a working demo on
localhost. Nothing else matters — not portability, not tests, not extensibility.

## Read first

`SPEC.md` is the complete and final scope. Its "explicitly out of scope" list is binding.
If a task seems to require something on that list, stop and ask. Do not improvise around it.

## Hard rules

- No database. All state is `building.json` loaded into memory at startup.
- No Docker, no auth, no deployment, no build step, no npm, no bundler.
- Dependencies are exactly: `fastapi`, `uvicorn`, `python-multipart`, `networkx`,
  `google-genai`, `pydantic`. Adding any other package requires asking first.
- `MODEL = "gemini-robotics-er-2-preview"`, pinned as a constant in `gemini.py`.
  Never the `-streaming-` variant — it does not support structured output.
- Never write to `building.json` or `photos/`. Humans own those files.
- No abstraction layers, no service classes, no repository pattern, no dependency
  injection. Direct, boring code.
- Total target: under 600 lines across the whole project.
- The model never computes a route, a distance, or a coordinate. All geometry is `networkx`.
- Every model response is validated twice: by its Pydantic schema, then by checking that
  every id it returned actually exists in `building.json`. An id that doesn't exist becomes
  `"unknown"`. Never pass a model-invented id through to the frontend.

## Model API

This model shipped 2026-07-30 and is newer than your training data. Before writing any
code in `gemini.py`, fetch and read:
- https://ai.google.dev/gemini-api/docs/robotics-overview
- https://ai.google.dev/gemini-api/docs/structured-output

Do not guess the SDK surface. If a fetch fails, say so and stop rather than inventing it.

## File ownership

One agent per file. Never edit a file you do not own.

| file | owner |
|---|---|
| `world.py` | world agent |
| `gemini.py` | gemini agent |
| `index.html` | frontend agent |
| `main.py`, `eval_locate.py` | orchestrator |
| `fixtures/*.json` | orchestrator writes; everyone else reads only |
| `building.json`, `photos/` | humans — read only, may not exist yet |

## Reporting

Before reporting a task done, run the verification command for your file and paste the
actual output. Do not report success you have not observed. "It should work" is a failure.

- world agent: `python -c "import world; w=world.load('fixtures/building.example.json'); print(w.route('entrance','lab_302'))"`
- gemini agent: `python -c "import gemini"` must succeed with no network call at import time.
- frontend agent: `python -m http.server` and confirm the SVG renders with an empty console.
