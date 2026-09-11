#!/usr/bin/env python3
"""Set `key = value` in a MaNGOS-style conf, in place.

Rewrites the first assignment of the key whether or not it is commented out;
appends at the end if the key is absent. Values are never echoed for the
database keys: the caller passes them through the environment.
"""
import re, sys

def set_key(path, key, value):
    with open(path, encoding="utf-8", errors="surrogateescape") as fh:
        lines = fh.readlines()
    pat = re.compile(r"^\s*#?\s*" + re.escape(key) + r"\s*=")
    for i, line in enumerate(lines):
        if pat.match(line):
            lines[i] = f"{key} = {value}\n"
            break
    else:
        lines.append(f"{key} = {value}\n")
    with open(path, "w", encoding="utf-8", errors="surrogateescape") as fh:
        fh.writelines(lines)

if __name__ == "__main__":
    path = sys.argv[1]
    for pair in sys.argv[2:]:
        k, _, v = pair.partition("=")
        set_key(path, k, v)
