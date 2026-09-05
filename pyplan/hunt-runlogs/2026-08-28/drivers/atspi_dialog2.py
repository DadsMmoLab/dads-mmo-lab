import sys
import pyatspi


def dump(obj, depth=0, max_depth=14):
    try:
        name = obj.name
        role = obj.getRoleName()
    except Exception as e:
        print("  " * depth + f"<error {e}>")
        return
    print("  " * depth + f"[{role}] {name!r}")
    if depth >= max_depth:
        return
    try:
        n = obj.childCount
    except Exception:
        n = 0
    for i in range(n):
        try:
            child = obj.getChildAtIndex(i)
        except Exception:
            continue
        dump(child, depth + 1, max_depth)


def find(obj, role=None, name_contains=None):
    try:
        r = obj.getRoleName()
        n = obj.name
    except Exception:
        return None
    ok = True
    if role is not None and r != role:
        ok = False
    if name_contains is not None and name_contains.lower() not in (n or "").lower():
        ok = False
    if ok:
        return obj
    try:
        cnt = obj.childCount
    except Exception:
        cnt = 0
    for i in range(cnt):
        try:
            child = obj.getChildAtIndex(i)
        except Exception:
            continue
        found = find(child, role, name_contains)
        if found is not None:
            return found
    return None


desktop = pyatspi.Registry.getDesktop(0)
app = None
for a in desktop:
    try:
        nm = a.name
    except Exception:
        continue
    if nm == "yulon":
        try:
            if a.childCount > 0:
                app = a
                break
        except Exception:
            continue

if app is None:
    print("APP NOT FOUND")
    sys.exit(1)

dlg = find(app, role="dialog")
if dlg is None:
    print("no dialog found")
    sys.exit(1)

print("=== DIALOG TREE ===")
dump(dlg)
