"""The three Gemini Robotics ER 2 calls: locate, identify, observe.

One API call per request. Every response is validated twice: by its Pydantic
schema, then by checking every id it returned against the world model.
An id the world does not know becomes "unknown" with confidence 0.0.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from google import genai
from google.genai import types
from pydantic import BaseModel

MODEL = "gemini-robotics-er-2-preview"
# robotics-overview docs: "use medium for a good balance between latency and performance"
THINKING_LEVEL = "medium"
FRAME_COUNT = 4

# --- prompts -----------------------------------------------------------------
# Edit these freely; they are the only thing worth iterating on in this file.
# {candidates} is built from the world model at call time — never hardcode ids.

LOCATE_PROMPT = """You are localizing a person inside {building}, floor {floor}.
These are the ONLY possible locations:

{candidates}

Look at the photo and decide which of these locations the photographer is
standing at, or immediately outside.

Rules:
- Choose an id from the list above, or return "unknown". Never invent an id.
- Cite concrete visible evidence: sign text you can actually read, door colour,
  fixtures, corridor geometry, visible equipment.
- Do not claim to read signage that is not legible in the image.
- If two locations are plausible, put the second in alternatives.
- confidence is 0.0-1.0 and should reflect how much distinguishing evidence you found.
"""

IDENTIFY_PROMPT = """You are identifying a single piece of equipment inside {building}, floor {floor}.
These are the ONLY possible pieces of equipment:

{candidates}

Look at the photo and decide which of these the photographer is pointing the camera at.

Rules:
- Choose an id from the list above, or return "unknown". Never invent an id.
- Cite concrete visible evidence: labels or model text you can actually read, chassis
  colour and form factor, cabling, mounting, what it sits on or next to.
- Do not claim to read a label that is not legible in the image.
- confidence is 0.0-1.0 and should reflect how much distinguishing evidence you found.
"""

OBSERVE_PROMPT = """These frames were sampled in order from a walkthrough video of {building}, floor {floor}.
These are the ONLY locations you may report on:

{candidates}

For each location you can actually see in the frames, report whether a person could
currently walk through it.

Rules:
- Choose ids from the list above only. Never invent an id. Omit locations you did not see.
- passable is false only if something visibly blocks the way: stacked furniture,
  boxes, a barrier, a closed shutter, construction, standing water, a wet-floor cordon.
- passable is true if the way is visibly clear end to end.
- evidence is ONE sentence naming what you actually saw at that location.
- confidence is 0.0-1.0 and should reflect how clearly the frames show that location.
"""


# --- schemas -----------------------------------------------------------------

class LocateResult(BaseModel):
    node_id: str
    confidence: float
    evidence: list[str]
    alternatives: list[str]


class IdentifyResult(BaseModel):
    equipment_id: str
    confidence: float
    evidence: list[str]


class Observation(BaseModel):
    node_id: str
    passable: bool
    confidence: float
    evidence: str


class ObserveResult(BaseModel):
    observations: list[Observation]


# --- plumbing ----------------------------------------------------------------

_client = None


def _client_lazy():
    # genai.Client() raises without GEMINI_API_KEY, so it must not run at import time.
    global _client
    if _client is None:
        _client = genai.Client()
    return _client


def _reject(kind, bad_id):
    print(f"gemini: rejected {kind} id {bad_id!r} — not in world model", file=sys.stderr)


def _mime(image_bytes):
    return "image/png" if image_bytes[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


def _lines(items):
    return "\n".join(
        f"- {i['id']}: {i.get('visual_signature', '')}" for i in items.values()
    )


def _call(parts, prompt, schema):
    resp = _client_lazy().models.generate_content(
        model=MODEL,
        contents=[*parts, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_level=THINKING_LEVEL),
        ),
    )
    return schema.model_validate_json(resp.text)


def _sample_frames(video_bytes):
    """ffmpeg -i in.mp4 -vf fps=1/5 -frames:v 4 frame_%03d.jpg — returns the frames' bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in.mp4")
        Path(src).write_bytes(video_bytes)
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
             "-vf", "fps=1/5", "-frames:v", str(FRAME_COUNT),
             os.path.join(tmp, "frame_%03d.jpg")],
            check=True,
        )
        return [p.read_bytes() for p in sorted(Path(tmp).glob("frame_*.jpg"))]


# --- the three calls ---------------------------------------------------------

def locate(image_bytes, world):
    prompt = LOCATE_PROMPT.format(
        building=world.building, floor=world.floor, candidates=_lines(world.nodes)
    )
    r = _call([types.Part.from_bytes(data=image_bytes, mime_type=_mime(image_bytes))],
              prompt, LocateResult)

    if r.node_id != "unknown" and not world.has(r.node_id):
        _reject("node", r.node_id)
        r.node_id = "unknown"
    if r.node_id == "unknown":
        r.confidence = 0.0

    kept = []
    for alt in r.alternatives:
        if world.has(alt):
            kept.append(alt)
        else:
            _reject("alternative node", alt)
    r.alternatives = kept
    return r


def identify(image_bytes, world):
    prompt = IDENTIFY_PROMPT.format(
        building=world.building, floor=world.floor, candidates=_lines(world.equipment)
    )
    r = _call([types.Part.from_bytes(data=image_bytes, mime_type=_mime(image_bytes))],
              prompt, IdentifyResult)

    if r.equipment_id != "unknown" and not world.has(r.equipment_id):
        _reject("equipment", r.equipment_id)
        r.equipment_id = "unknown"
    if r.equipment_id == "unknown":
        r.confidence = 0.0
    return r


def observe(frame_bytes_list, world):
    """frame_bytes_list is the raw video bytes; frames are sampled here."""
    frames = _sample_frames(frame_bytes_list)
    prompt = OBSERVE_PROMPT.format(
        building=world.building, floor=world.floor, candidates=_lines(world.nodes)
    )
    parts = [types.Part.from_bytes(data=f, mime_type="image/jpeg") for f in frames]
    r = _call(parts, prompt, ObserveResult)

    for obs in r.observations:
        if obs.node_id != "unknown" and not world.has(obs.node_id):
            _reject("node", obs.node_id)
            obs.node_id = "unknown"
        if obs.node_id == "unknown":
            obs.confidence = 0.0  # main.py's confidence > 0.6 filter drops it
    return r


# --- chat: plain English -> one command --------------------------------------
# The model only parses intent. It never routes, never measures, never names a
# distance — main.py runs the command against the networkx world and writes the
# factual half of the reply itself.

CHAT_ACTIONS = ("route", "block", "unblock", "show_level", "reset_view", "not_found", "none")


# `floor` in the building file is a 1-based deck index: the ground floor is 1. Humans
# and the model both say "ground" and "level 6". These two are the only translation
# between the conventions - main.py phrases its replies with them too, so the sentence
# the visitor reads can never disagree with the deck the page lights up.
def level_name(floor):
    return "ground" if floor == 1 else f"level {floor - 1}"


def deck(level):
    """Inverse of level_name. None for anything that is not a level."""
    # the model answers "6", "level 6", "floor 6" or "L6" - all name the same deck
    s = str(level or "").strip().lower()
    for prefix in ("level", "floor", "lvl", "l"):
        s = s.removeprefix(prefix).strip()
    if s in ("ground", "g", "0"):
        return 1
    return int(s) + 1 if s.isdigit() else None

CHAT_PROMPT = """You turn a visitor's plain-English request into ONE command for the
navigation system of {building}.

Locations (id: name, type, level):
{nodes}

Equipment (id: name, at location):
{equipment}

Recent conversation:
{history}

The visitor now says: {message}

Choose exactly one action:
- "route"       they want to get somewhere. Set from_id and to_id.
- "block"       they report a place shut, blocked, flooded, closed or under repair. Set node_id.
- "unblock"     they report a place open or clear again. Set node_id, or leave node_id empty
                to clear every blockage at once.
- "show_level"  they want to look at one level or at all of them. Set level to "ground", to a
                level number exactly as it is written in the list above, or to "all".
- "reset_view"  they want the camera back where it started.
- "not_found"   they want to get somewhere, but no id in the lists above is the place they named.
- "none"        anything else: a greeting, or a question you cannot express as the actions above.

Rules:
- Every id MUST be copied from the lists above. Never invent one. Leave a field "" when it does not apply.
- If they name a piece of equipment as the destination, put the EQUIPMENT id in to_id.
- If they never say where they are starting from, leave from_id "" and the system starts at the entrance.
- accessible is true only when they ask for step-free, no stairs, wheelchair, pram, luggage or lift access.
- Resolve "there", "it", "that one", "the same place" against the recent conversation above.
- reply: ONE short friendly sentence, used only when the action is "none" or "not_found".
  Never state a distance, a number of metres, a level count or a list of steps — the
  system works those out itself.
- for "not_found", reply names the place you could not find and stops there. The system
  adds the offer of help itself, so do not offer anything or ask a question.
"""


class ChatCommand(BaseModel):
    action: str
    from_id: str
    to_id: str
    node_id: str
    level: str
    accessible: bool
    reply: str


def chat(message, history, world):
    nodes = "\n".join(
        f"- {n['id']}: {n['name']}, {n['type']}, {level_name(n.get('floor', 1))}"
        for n in world.nodes.values()
    )
    equipment = "\n".join(
        f"- {e['id']}: {e['name']}, at {e['node']}" for e in world.equipment.values()
    ) or "- (none)"
    prompt = CHAT_PROMPT.format(
        building=world.building, nodes=nodes, equipment=equipment,
        history=history or "(nothing yet)", message=message,
    )
    r = _call([], prompt, ChatCommand)

    if r.action not in CHAT_ACTIONS:
        _reject("chat action", r.action)
        r.action = "none"
    for field in ("from_id", "to_id", "node_id"):
        value = getattr(r, field)
        if value and not world.has(value):
            _reject(f"chat {field}", value)
            setattr(r, field, "")
    return r
