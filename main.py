"""FastAPI wiring. No logic here beyond calling world and gemini."""
import os

from fastapi import FastAPI, File, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import gemini
import world

BUILDING = "building.json" if os.path.exists("building.json") else "fixtures/building.example.json"
w = world.load(BUILDING)
print(f"loaded {BUILDING}: {len(w.nodes)} nodes, {len(w.equipment)} equipment")

# second, read-only world behind /3d — the holographic landmark view. Does not touch the world above.
csmt = world.load("fixtures/building.csmt.json")

app = FastAPI()
# so the page still works in USE_FIXTURES mode when served from here
app.mount("/fixtures", StaticFiles(directory="fixtures"), name="fixtures")


@app.get("/")
def index():
    return FileResponse("index.html")


@app.post("/locate")
async def locate(file: UploadFile = File(...)):
    r = gemini.locate(await file.read(), w)
    node = w.nodes.get(r.node_id)
    return {"node_id": r.node_id, "node_name": node["name"] if node else "Unknown",
            "confidence": r.confidence, "evidence": r.evidence, "alternatives": r.alternatives}


@app.post("/identify")
async def identify(file: UploadFile = File(...)):
    r = gemini.identify(await file.read(), w)
    eq = w.equipment.get(r.equipment_id)
    return {"equipment_id": r.equipment_id, "name": eq["name"] if eq else "Unknown",
            "confidence": r.confidence, "node": eq["node"] if eq else None,
            "manual_snippet": eq["manual_snippet"] if eq else None}


@app.post("/observe")
async def observe(file: UploadFile = File(...)):
    r = gemini.observe(await file.read(), w)
    updates = []
    for o in r.observations:
        if o.confidence <= 0.6 or o.node_id not in w.nodes:
            continue
        w.set_passable(o.node_id, o.passable)
        u = {"node_id": o.node_id, "node_name": w.nodes[o.node_id]["name"], "passable": o.passable,
             "confidence": o.confidence, "evidence": o.evidence}
        w.observations.append(u)
        updates.append(u)
    return {"updates": updates}


@app.get("/route")
def route(from_id: str = Query(..., alias="from"), to: str = Query(...), accessible: bool = False):
    return w.route(from_id, to, accessible=accessible)


@app.get("/state")
def state():
    return w.state()


@app.get("/3d")
def holo():
    return FileResponse("holo.html")


@app.get("/3d/state")
def holo_state():
    return csmt.state()


@app.get("/3d/route")
def holo_route(from_id: str = Query(..., alias="from"), to: str = Query(...), accessible: bool = False):
    return csmt.route(from_id, to, accessible=accessible)


@app.post("/3d/block")
def holo_block(node: str, passable: bool = False):
    if not csmt.has(node):
        return {"error": f"unknown node id '{node}'"}
    csmt.set_passable(node, passable)
    return {"node": node, "passable": passable}
