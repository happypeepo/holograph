"""Accuracy harness for locate(). Photos are photos/{node_id}_{n}.jpg. Judge prompt changes by this number."""
import glob
import os

import gemini
import world

BUILDING = "building.json" if os.path.exists("building.json") else "fixtures/building.example.json"
w = world.load(BUILDING)

photos = sorted(glob.glob("photos/*.jpg") + glob.glob("photos/*.jpeg"))
if not photos:
    raise SystemExit("no photos found - drop them in photos/ named {node_id}_{n}.jpg")

hits = 0
for p in photos:
    actual = os.path.basename(p).rsplit("_", 1)[0]
    if not w.has(actual):
        print(f"  ! '{actual}' is not a node id - check the filename of {os.path.basename(p)}")
    r = gemini.locate(open(p, "rb").read(), w)
    hits += r.node_id == actual
    print(f"{'OK  ' if r.node_id == actual else 'MISS'} {os.path.basename(p):28} "
          f"actual={actual:18} predicted={r.node_id:18} conf={r.confidence:.2f}")

print(f"\naccuracy {hits}/{len(photos)} = {hits / len(photos):.0%}")
