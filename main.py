"""FastAPI wiring. No logic here beyond calling world and gemini."""
import os

from fastapi import FastAPI, File, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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


class ChatIn(BaseModel):
    message: str
    history: str = ""


TWINS = []   # every mounted building, so the page can offer a switcher without guessing


def mount_twin(prefix, w):
    """Serve one building's holographic view under `prefix`. Called once per
    building — the page derives its own API base from the URL it was served at."""
    TWINS.append({"prefix": prefix, "name": w.building})
    entrance = next((i for i, n in w.nodes.items() if n["type"] == "entrance"), next(iter(w.nodes)))

    def node_of(id):
        """A node id stays put; an equipment id resolves to the node it sits at."""
        if id in w.nodes:
            return id
        eq = w.equipment.get(id)
        return eq["node"] if eq else None

    @app.get(prefix)
    def holo():
        return FileResponse("holo.html")

    @app.get(prefix + "/state")
    def holo_state():
        return w.state()

    @app.get(prefix + "/route")
    def holo_route(from_id: str = Query(..., alias="from"), to: str = Query(...), accessible: bool = False):
        return w.route(from_id, to, accessible=accessible)

    @app.post(prefix + "/block")
    def holo_block(node: str, passable: bool = False):
        if not w.has(node):
            return {"error": f"unknown node id '{node}'"}
        w.set_passable(node, passable)
        return {"node": node, "passable": passable}

    # gemini.chat only parses the sentence; every route, distance and blockage
    # below is computed here against networkx, never by the model.
    @app.post(prefix + "/chat")
    def holo_chat(body: ChatIn):
        c = gemini.chat(body.message, body.history, w)
        out = {"action": c.action, "reply": c.reply, "route": None, "level": None, "changed": []}

        if c.action == "route":
            a, b = node_of(c.from_id) or entrance, node_of(c.to_id)
            if not b:
                out["action"] = "none"
                out["reply"] = "I couldn't tell which place you meant — try naming it as it appears on the model."
                return out
            r = w.route(a, b, accessible=c.accessible)
            out["route"] = r
            out["req"] = {"from": a, "to": b, "accessible": c.accessible}   # so the page can replay it after a blockage
            out["reply"] = r["reason"] if not r["path"] else (
                f"{w.nodes[a]['name']} to {w.nodes[b]['name']}"
                + (", step-free" if c.accessible else "")
                + f" — {r['distance_m']} m, {len(r['steps'])} steps. Drawn on the model."
            )

        elif c.action in ("block", "unblock"):
            passable = c.action == "unblock"
            target = node_of(c.node_id)
            if target:
                targets = [target]
            elif passable:
                targets = [i for i, n in w.nodes.items() if not n["passable"]]   # "clear everything"
            else:
                targets = []
            for t in targets:
                w.set_passable(t, passable)
            out["changed"] = targets
            names = ", ".join(w.nodes[t]["name"] for t in targets)
            out["reply"] = (f"{names} is now {'open' if passable else 'blocked'}." if targets
                            else "Nothing to change — which place did you mean?")

        elif c.action == "show_level":
            levels = {str(n.get("floor", 1)) for n in w.nodes.values()}
            out["level"] = c.level if c.level in levels or c.level == "all" else None
            out["reply"] = (f"Showing {'every level' if out['level'] == 'all' else 'level ' + out['level']}."
                            if out["level"] else "I only know about levels " + ", ".join(sorted(levels)) + ".")

        elif c.action == "reset_view":
            out["reply"] = "View reset."

        if not out["reply"]:
            out["reply"] = "Ask me to take you somewhere, block a space, or switch level."
        return out


@app.get("/twins")
def twins():
    return TWINS


mount_twin("/3d", csmt)

# the Bhaskaracharya block, modelled from its seven Emergency Escape Route boards
if os.path.exists("fixtures/building.bhaskaracharya.json"):
    bhaskar = world.load("fixtures/building.bhaskaracharya.json")
    mount_twin("/3d/bhaskaracharya", bhaskar)
    print(f"loaded bhaskaracharya: {len(bhaskar.nodes)} nodes, {len(bhaskar.equipment)} equipment")
