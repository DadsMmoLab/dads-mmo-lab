import time
import sys
import pyatspi


def dump(obj, depth=0, max_depth=16):
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

target_btn = None


def find_wotlk_use_existing(obj):
    global target_btn
    if target_btn is not None:
        return
    try:
        role = obj.getRoleName()
    except Exception:
        return
    if role == "panel":
        try:
            cnt = obj.childCount
            first_label = obj.getChildAtIndex(0).name if cnt else ""
        except Exception:
            first_label = ""
        if "WotLK" in first_label:
            for i in range(obj.childCount):
                c = obj.getChildAtIndex(i)
                if "existing" in (c.name or "").lower():
                    target_btn = c
                    return
    try:
        cnt = obj.childCount
    except Exception:
        cnt = 0
    for i in range(cnt):
        try:
            child = obj.getChildAtIndex(i)
        except Exception:
            continue
        find_wotlk_use_existing(child)
        if target_btn is not None:
            return


find_wotlk_use_existing(app)
print("target button:", target_btn)
if target_btn is not None:
    target_btn.queryAction().doAction(0)
    print("clicked Use existing")
    time.sleep(2)
    print("=== TREE AFTER CLICK ===")
    dump(app)
else:
    print("BUTTON NOT FOUND")
