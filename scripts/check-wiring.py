#!/usr/bin/env python3
"""Static consistency check for this realm's declarations.

A miswired join does not fail loudly. The fetch succeeds, the records come back, and the join edge
simply never forms — so the query returns zero rows and reports success, which reads as "there is
nothing there" rather than as a bug. Two such bugs were present in the first draft of this realm:
a join declared on the wrong target type, and two producers whose returned records identified a
DIFFERENT entity than the anchor they were fetched for.

Both are mechanically detectable, so they are checked here rather than discovered in production.

    python3 scripts/check-wiring.py
"""

import glob
import re
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Labels the host supplies. A join may anchor on these without this realm declaring them.
CORE_LABELS = {"AssistantUser", "Person", "Organization"}


def load(pattern):
    for path in sorted(glob.glob(os.path.join(ROOT, pattern))):
        for entry in yaml.safe_load(open(path)) or []:
            yield path, entry


def check_no_yaml_anchors(problems):
    """YAML anchors parse here and fail in the host.

    The host reads these files with Jackson's YAML parser, which resolves anchors only for SCALARS.
    An alias to a mapping — `project: *orgProjection` — arrives as the bare string "orgProjection",
    which cannot bind to Map<String,String>. Jackson then fails the WHOLE file, so every producer in
    it disappears and the first fetch reports `Unknown producer '<name>' in plan` with nothing
    pointing at the real cause.

    Python's yaml resolves the alias correctly, so this check cannot be done by inspecting the
    parsed structure — it has to read the text.
    """
    for path in sorted(glob.glob(os.path.join(ROOT, "**/*.yml"), recursive=True)):
        for lineno, line in enumerate(open(path), 1):
            stripped = line.split("#", 1)[0].rstrip()
            if re.search(r":\s*[&*][A-Za-z_]", stripped):
                rel = os.path.relpath(path, ROOT)
                problems.append(
                    f"{rel}:{lineno}: YAML anchor/alias — Jackson resolves these only for scalars "
                    f"and fails the whole file. Write the value out in full."
                )


def main():
    producers = {p["name"]: p for _, p in load("producers/*.yml")}
    types = {t["name"]: t for _, t in load("types/*.yml")}

    problems = []
    check_no_yaml_anchors(problems)

    for name, t in types.items():
        props = t.get("properties") or {}
        for join in t.get("virtualJoins") or []:
            rel = join["relationship"]
            where = f"{name}.{rel}"

            producer = producers.get(join["producer"])
            if producer is None:
                problems.append(f"{where}: producer '{join['producer']}' is not declared")
                continue

            # The anchor must be able to supply the key.
            anchor = join["anchorLabel"]
            if anchor not in CORE_LABELS:
                anchor_type = types.get(anchor)
                if anchor_type is None:
                    problems.append(f"{where}: anchorLabel '{anchor}' is not a declared type")
                elif join["keyField"] not in (anchor_type.get("properties") or {}):
                    problems.append(
                        f"{where}: keyField '{join['keyField']}' is not a property of {anchor}"
                    )

            # The record must carry something to match the anchor back on — either a projected
            # field or the producer's echoed key. This is the check that catches the silent one.
            record_key = join.get("recordKeyField")
            if record_key:
                projected = producer.get("project") or {}
                if record_key not in projected and producer.get("echoKeyAs") != record_key:
                    problems.append(
                        f"{where}: recordKeyField '{record_key}' is neither projected by "
                        f"'{producer['name']}' nor its echoKeyAs — the join edge would never form"
                    )
                if record_key not in props:
                    problems.append(
                        f"{where}: recordKeyField '{record_key}' is not declared as a property "
                        f"of {name}"
                    )

            for rule in join.get("resolve") or []:
                if isinstance(rule, dict):
                    for rule_name, config in rule.items():
                        ref = (config or {}).get("producer")
                        if ref and ref not in producers:
                            problems.append(
                                f"{where}: resolve rule '{rule_name}' names unknown producer '{ref}'"
                            )

    # A producer whose records cannot be identified per key must fetch per key.
    for name, p in producers.items():
        if p.get("echoKeyAs") and p.get("batchSafe") is True:
            problems.append(
                f"producer {name}: echoKeyAs implies one key per call, so batchSafe: true is a "
                f"contradiction"
            )

    if problems:
        print(f"{len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    joins = sum(len(t.get("virtualJoins") or []) for t in types.values())
    print(f"OK: {len(types)} types, {len(producers)} producers, {joins} joins consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
