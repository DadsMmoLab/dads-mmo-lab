import json, sys
sys.path.insert(0, ".")
from tests import support_compose as sc

fixture = json.loads(open("tests/data/wotlk-compose-config.json", encoding="utf-8").read())
mine = json.loads(open("/tmp/rc-transformed.json", encoding="utf-8").read())

fs, ms = sc.shape_from_config(fixture), sc.shape_from_config(mine)
print("services on each side:", sorted(fs), "|", sorted(ms))
svc = sc.compare(ms, fs)
print("service differences (my capture vs the committed fixture):", len(svc))
for d in svc:
    print("   ", d)
fst, mst = sc.stack_from_config(fixture), sc.stack_from_config(mine)
stack = sc.compare_stack(mst, fst)
print("stack differences (my capture vs the committed fixture):", len(stack))
for d in stack:
    print("   ", d)
print("control  compare_stack(fixture, fixture):", len(sc.compare_stack(fst, fst)), sc.compare_stack(fst, fst))
print("control  compare_stack(mine, mine):      ", len(sc.compare_stack(mst, mst)), sc.compare_stack(mst, mst))
