"""Translucent - FastAPI wiring. No logic here beyond calling world and gemini."""
import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

import gemini
import world


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


# no-store so an edited page never comes back from the browser cache mid-demo
NO_CACHE = {"Cache-Control": "no-store, must-revalidate"}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse("index.html", headers=NO_CACHE)


# PWA assets. sw.js must be served from the root or its scope cannot cover /aryabhatta
# and /bhaskaracharya. Icons are cached hard; the worker itself never is, so a new one
# is picked up on the next navigation.
@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse("manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse("sw.js", media_type="text/javascript", headers=NO_CACHE)


@app.get("/icon-{size}.png")
def icon(size: int):
    if size not in (192, 512):
        raise HTTPException(404)
    return FileResponse(f"icon-{size}.png", media_type="image/png")


class ChatIn(BaseModel):
    message: str
    history: str = ""


BUILDINGS = []   # every mounted building, so the page can offer a switcher without guessing


def mount_building(prefix, w):
    """Serve one building's Translucent view under `prefix`. Called once per
    building — the page derives its own API base from the URL it was served at."""
    BUILDINGS.append({"prefix": prefix, "name": w.building})
    entrance = next((i for i, n in w.nodes.items() if n["type"] == "entrance"), next(iter(w.nodes)))

    def node_of(id):
        """A node id stays put; an equipment id resolves to the node it sits at."""
        if id in w.nodes:
            return id
        eq = w.equipment.get(id)
        return eq["node"] if eq else None

    def offer_gate(said=""):
        """The single reply for "that place is not in this world". The visitor may simply be
        standing in the wrong building, and the entrance is the only useful thing left to
        offer. A "yes" comes back as an ordinary route request to the entrance — the offer
        sentence is in the history and names it — so this needs no pending state anywhere."""
        said = (said or f"I can't find that in {w.building}.").strip()
        if said[-1] not in ".!?":
            said += "."
        return said + f" It may not be in this building — shall I guide you to the {w.nodes[entrance]['name']}?"

    @app.get(prefix)
    def page():
        return FileResponse("translucent.html", headers=NO_CACHE)

    @app.get(prefix + "/state")
    def page_state():
        return w.state()

    @app.get(prefix + "/route")
    def page_route(from_id: str = Query(..., alias="from"), to: str = Query(...), accessible: bool = False):
        return w.route(from_id, to, accessible=accessible)

    @app.post(prefix + "/block")
    def page_block(node: str, passable: bool = False):
        if not w.has(node):
            return {"error": f"unknown node id '{node}'"}
        w.set_passable(node, passable)
        return {"node": node, "passable": passable}

    # gemini.chat only parses the sentence; every route, distance and blockage
    # below is computed here against networkx, never by the model.
    @app.post(prefix + "/chat")
    def page_chat(body: ChatIn):
        c = gemini.chat(body.message, body.history, w)
        out = {"action": c.action, "reply": c.reply, "route": None, "level": None, "changed": []}

        if c.action == "route":
            a, b = node_of(c.from_id) or entrance, node_of(c.to_id)
            if not b:
                out["action"] = "not_found"
                out["reply"] = offer_gate()
                return out
            if a == b and not node_of(c.from_id):
                # they gave no origin, so `a` fell back to the entrance - and the entrance is
                # also what they asked for. Routing that answers "0 m, 1 steps", which is the
                # one useless reply. Ask where they actually are instead.
                out["action"] = "need_origin"
                out["reply"] = (f"Where are you now? Name the space, or click it on the model, "
                                f"and I'll route you to the {w.nodes[b]['name']}.")
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
            # the wire still carries deck indices - the page indexes decks by them -
            # but every word the visitor reads is phrased by gemini.level_name
            decks = sorted({n.get("floor", 1) for n in w.nodes.values()})
            d = gemini.deck(c.level)
            if c.level == "all":
                out["level"], out["reply"] = "all", "Showing every level."
            elif d in decks:
                out["level"] = str(d)
                out["reply"] = f"Showing {'the ground floor' if d == 1 else gemini.level_name(d)}."
            else:
                out["reply"] = ("I only know about "
                                + ", ".join(gemini.level_name(f) for f in decks) + ".")

        elif c.action == "reset_view":
            out["reply"] = "View reset."

        elif c.action == "not_found":
            # the model parsed a destination request but nothing in this world is that place
            out["reply"] = offer_gate(c.reply)

        if not out["reply"]:
            out["reply"] = "Ask me to take you somewhere, block a space, or switch level."
        return out


@app.get("/buildings")
def buildings():
    return BUILDINGS



# the Bhaskaracharya block, modelled from its seven Emergency Escape Route boards
if os.path.exists("fixtures/building.bhaskaracharya.json"):
    bhaskar = world.load("fixtures/building.bhaskaracharya.json")
    mount_building("/bhaskaracharya", bhaskar)
    print(f"loaded bhaskaracharya: {len(bhaskar.nodes)} nodes, {len(bhaskar.equipment)} equipment")

# the Aryabhatta block opposite it, modelled from its five Emergency Escape Route boards
if os.path.exists("fixtures/building.aryabhatta.json"):
    aryabhatta = world.load("fixtures/building.aryabhatta.json")
    mount_building("/aryabhatta", aryabhatta)
    print(f"loaded aryabhatta: {len(aryabhatta.nodes)} nodes, {len(aryabhatta.equipment)} equipment")
