"""Cut the KBay 2023 extent into boxes and write one recipe per box from template.json.
Boxes of 12-16 km over x 557-635 km, y 6566-6636 km (v4: the head of the bay and Anchor Point), edges placed between the mapped lakes, each BUILT with a
MARGIN_M margin on every side (so a fill domain near a box edge sees its surroundings) and cropped back
to the nominal box on the mosaic (run_boxes.sh).  Writes composites/kbay_2023/boxes/<id>/recipe.json
and boxes.json (the crop extents)."""
import json, pathlib
HERE = pathlib.Path(__file__).resolve().parent
X0, X1, Y0, Y1 = 557000, 635000, 6566000, 6636000
STEP = 16000; MARGIN_M = 2000
tpl = json.load(open(HERE / "template.json"))
EX = [557000, 572500, 587500, 603500, 619000, 635000]   # column edges: 12-16 km, chosen to cut no mapped lake over 5 ha (2026-10-17)
EY = [6566000, 6579500, 6591500, 6604000, 6620000, 6636000]   # row edges, same rule (Grewingk, Wosnesenski, China Poot, Turquoise whole in a box)
out = []
for r, (y, y1) in enumerate(zip(EY[:-1], EY[1:])):
    for c, (x, x1) in enumerate(zip(EX[:-1], EX[1:])):
        bid = f"kbay_r{r}c{c}"
        rec = json.loads(json.dumps(tpl).replace("KBAY_BOX_ID", bid))
        rec["grid"]["bounds"] = [x - MARGIN_M, y - MARGIN_M, x1 + MARGIN_M, y1 + MARGIN_M]
        d = HERE / "boxes" / bid; d.mkdir(parents=True, exist_ok=True)
        json.dump(rec, open(d / "recipe.json", "w"), indent=1); open(d / "recipe.json", "a").write("\n")
        out.append(dict(id=bid, crop=[x, y, x1, y1], build=rec["grid"]["bounds"]))
json.dump(out, open(HERE / "boxes.json", "w"), indent=1)
print(f"{len(out)} boxes:", ", ".join(f"{b['id']} {b['crop']}" for b in out))
