# world agent owns this file. Building graph, routing, passable state. No LLM, no network, no FastAPI.
"""Load building JSON into a networkx graph and answer routing questions.

Importable and fully exercisable with no API key: nothing here touches the
network or a model. All geometry and distance is deterministic.
"""

import json
from difflib import get_close_matches

import networkx as nx

# One step per node in a path, picked by node type. The last node in a path is
# always "Arrive at {name}" whatever its type.
STEP_TEMPLATES = {
    "entrance": "Leave {name}",
    "corridor": "Follow {name}",
    "stairs": "Take the stairs at {name}",
    "lift": "Take the lift at {name}",
    "room": "Pass through {name}",
}
NODE_TYPES = tuple(STEP_TEMPLATES)


def _suggest(bad, candidates):
    near = get_close_matches(str(bad), candidates, n=1, cutoff=0.5)
    return f" Did you mean '{near[0]}'?" if near else ""


class World:
    def __init__(self, data, source="building.json"):
        self.source = source
        self.building = data.get("building", "")
        self.floor = data.get("floor")
        self.svg_viewbox = data.get("svg_viewbox", [0, 0, 1000, 600])
        self.g = nx.Graph()
        self.nodes = {}          # node id -> attribute dict (the graph's own dict)
        self.equipment = {}      # equipment id -> raw equipment dict
        self.edge_keys = []      # (a, b) in file order, so /state output is stable
        self.observations = []   # main.py appends {node_id, node_name, passable, confidence, evidence}
        self._load_nodes(data)
        self._load_edges(data)
        self._load_equipment(data)
        for nid, attrs in self.nodes.items():   # a node blocked in the file blocks its edges
            if not attrs["passable"]:
                self.set_passable(nid, False)

    def _err(self, msg):
        raise ValueError(f"{self.source}: {msg}")

    def _load_nodes(self, data):
        raw = data.get("nodes")
        if not isinstance(raw, list) or not raw:
            self._err("'nodes' must be a non-empty list")
        for i, n in enumerate(raw):
            if not isinstance(n, dict):
                self._err(f"nodes[{i}] is not a JSON object")
            nid = n.get("id")
            if not isinstance(nid, str) or not nid:
                self._err(f"nodes[{i}] has no string 'id' (name={n.get('name')!r})")
            if nid in self.nodes:
                self._err(f"duplicate node id '{nid}' at nodes[{i}] - node ids must be unique")
            if not isinstance(n.get("name"), str) or not n["name"]:
                self._err(f"node '{nid}' has no string 'name' - it is shown in the route steps")
            if n.get("type") not in NODE_TYPES:
                self._err(
                    f"node '{nid}' has type {n.get('type')!r}, must be one of "
                    f"{', '.join(NODE_TYPES)}.{_suggest(n.get('type'), NODE_TYPES)}"
                )
            for field, size in (("xy", 2), ("svg_rect", 4)):
                v = n.get(field)
                ok = isinstance(v, list) and len(v) == size and all(
                    isinstance(x, (int, float)) and not isinstance(x, bool) for x in v
                )
                if not ok:
                    self._err(f"node '{nid}' needs '{field}' as a list of {size} numbers, got {v!r}")
            attrs = dict(n)
            attrs["passable"] = bool(n.get("passable", True))
            self.g.add_node(nid, **attrs)
            self.nodes[nid] = self.g.nodes[nid]

    def _load_edges(self, data):
        raw = data.get("edges")
        if not isinstance(raw, list) or not raw:
            self._err("'edges' must be a non-empty list")
        known = list(self.nodes)
        seen = set()
        for i, e in enumerate(raw):
            if not isinstance(e, dict):
                self._err(f"edges[{i}] is not a JSON object")
            a, b = e.get("a"), e.get("b")
            for side, v in (("a", a), ("b", b)):
                if v not in self.nodes:
                    self._err(
                        f"edges[{i}] side '{side}' references unknown node id "
                        f"{v!r}.{_suggest(v, known)}"
                    )
            if a == b:
                self._err(f"edges[{i}] joins node '{a}' to itself")
            key = frozenset((a, b))
            if key in seen:
                self._err(f"edges[{i}] duplicates the edge between '{a}' and '{b}'")
            seen.add(key)
            dist = e.get("distance")
            if not isinstance(dist, (int, float)) or isinstance(dist, bool) or dist < 0:
                self._err(f"edge '{a}' - '{b}' needs a non-negative numeric 'distance', got {dist!r}")
            self.g.add_edge(
                a, b,
                distance=dist,
                accessible=bool(e.get("accessible", True)),
                passable=bool(e.get("passable", True)),
            )
            self.edge_keys.append((a, b))

    def _load_equipment(self, data):
        raw = data.get("equipment", [])
        if not isinstance(raw, list):
            self._err("'equipment' must be a list")
        known = list(self.nodes)
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                self._err(f"equipment[{i}] is not a JSON object")
            eid = item.get("id")
            if not isinstance(eid, str) or not eid:
                self._err(f"equipment[{i}] has no string 'id' (name={item.get('name')!r})")
            if eid in self.equipment or eid in self.nodes:
                self._err(f"duplicate id '{eid}' at equipment[{i}] - ids must be unique across nodes and equipment")
            if item.get("node") not in self.nodes:
                self._err(
                    f"equipment '{eid}' sits at unknown node id "
                    f"{item.get('node')!r}.{_suggest(item.get('node'), known)}"
                )
            self.equipment[eid] = item

    # ---- public API -------------------------------------------------------

    def route(self, from_id, to_id, accessible=False):
        """Dijkstra over `distance`, skipping impassable (and optionally
        inaccessible) edges. Never returns a partial route."""
        known = list(self.nodes)
        for label, nid in (("from", from_id), ("to", to_id)):
            if nid not in self.nodes:
                return _no_route(f"Unknown {label} node id {nid!r}.{_suggest(nid, known)}")
        for label, nid in (("from", from_id), ("to", to_id)):
            if not self.nodes[nid]["passable"]:
                return _no_route(f"The {label} location '{self.nodes[nid]['name']}' is currently blocked.")
        allowed = nx.Graph()
        allowed.add_nodes_from(self.g.nodes)
        for a, b, d in self.g.edges(data=True):
            if not d["passable"] or (accessible and not d["accessible"]):
                continue
            allowed.add_edge(a, b, distance=d["distance"])
        try:
            path = nx.dijkstra_path(allowed, from_id, to_id, weight="distance")
        except nx.NetworkXNoPath:
            kind = "step-free route" if accessible else "route"
            return _no_route(
                f"No {kind} from '{self.nodes[from_id]['name']}' to "
                f"'{self.nodes[to_id]['name']}' with the current blockages."
            )
        distance_m = sum(allowed[u][v]["distance"] for u, v in zip(path, path[1:]))
        return {"path": path, "distance_m": distance_m, "steps": self.steps(path)}

    def steps(self, path):
        """Human-readable directions from node types. No model call."""
        out = []
        for i, nid in enumerate(path):
            node = self.nodes[nid]
            last = i == len(path) - 1
            template = "Arrive at {name}" if last else STEP_TEMPLATES.get(node["type"], "Continue to {name}")
            out.append(template.format(name=node["name"]))
        return out

    def set_passable(self, node_id, passable):
        """Flip a node and every edge touching it."""
        if node_id not in self.nodes:
            raise ValueError(f"{self.source}: set_passable got unknown node id {node_id!r}.{_suggest(node_id, list(self.nodes))}")
        passable = bool(passable)
        self.nodes[node_id]["passable"] = passable
        for other in self.g[node_id]:
            # reopening an edge only counts if the far end is open too
            self.g[node_id][other]["passable"] = passable and self.nodes[other]["passable"]

    def state(self):
        return {
            "building": self.building,
            "floor": self.floor,
            "svg_viewbox": self.svg_viewbox,
            "nodes": [dict(self.nodes[nid]) for nid in self.nodes],
            "edges": [
                {
                    "a": a,
                    "b": b,
                    "distance": self.g[a][b]["distance"],
                    "accessible": self.g[a][b]["accessible"],
                    "passable": self.g[a][b]["passable"],
                }
                for a, b in self.edge_keys
            ],
            "equipment": [dict(item) for item in self.equipment.values()],
            "observations": list(self.observations),
        }

    def has(self, id):
        """True for a node id or an equipment id. Used to validate model output."""
        return id in self.nodes or id in self.equipment


def _no_route(reason):
    return {"path": [], "distance_m": None, "steps": [], "reason": reason}


def load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a JSON object")
    return World(data, source=path)
