#!/usr/bin/env python3
"""Exercise every producer in this realm against the live Diffbot API.

Reads the SHIPPED producers/diffbot.yml and renders each producer's own `args.query` the way the
host would — substituting {keys} from `keyTemplate`/`keyJoin`, and {filters} as empty — so this
tests what the realm actually declares rather than what its author remembers declaring.

For each producer it reports whether the call succeeded, how many entities came back, and whether
the fields the joins depend on are actually present. A join whose `recordKeyField` never arrives
forms no edge and returns zero rows while reporting success, so "reachable" is not enough: the
column that matters is KEYFIELD.

    DIFFBOT_TOKEN=... python3 scripts/probe-live.py [producer-name ...]

Costs roughly 25 credits per entity returned. Sizes are forced to 2 here regardless of what the
producer declares, so a full sweep is a few hundred credits.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = os.environ.get("DIFFBOT_TOKEN")
BASE = "https://kg.diffbot.com/kg/v3"

# Real keys to drive each producer with, and the field each one's joins match the anchor back on.
# APPLE/DIFFBOT are stable Diffbot entity ids.
APPLE = "EHb0_0NEcMwyY8b083taTTw"
DIFFBOT = "EYX1i02YVPsuT7fPLUYgRhQ"
TIM_COOK = "E84vWTe2yP6qQ0u7kbL3ZGA"
SHAZAM = "Ew_hKLxtZM9Ki5SqzsQ89MQ"

PROBES = {
    "orgEnhanceByDomain":   {"key": "diffbot.com",              "expect": ["diffbotId", "matchedFor"]},
    "orgEnhanceByName":     {"key": "Silver Pictures",          "expect": ["diffbotId", "matchedFor"]},
    "personEnhanceByEmail": {"key": "mike@diffbot.com",         "expect": ["diffbotId", "matchedFor"]},
    "personEnhanceByName":  {"key": "Lana Wachowski",           "expect": ["diffbotId", "matchedFor"]},
    # Shazam, NOT Apple: Apple has no parent, so probing it reports parentCompanyId missing when the
    # projection is fine and the company simply sits at the top of its own tree.
    "orgsById":             {"key": SHAZAM,                     "expect": ["diffbotId", "parentCompanyId", "ultimateParentId"]},
    "peopleById":           {"key": TIM_COOK,                   "expect": ["diffbotId", "employerIds"]},
    "orgsByParent":         {"key": APPLE,                      "expect": ["diffbotId", "parentCompanyId"]},
    "orgsByCompetitor":     {"key": DIFFBOT,                    "expect": ["diffbotId", "matchedFor"]},
    "peopleByEmployer":     {"key": APPLE,                      "expect": ["diffbotId", "matchedFor", "employerIds", "titles"]},
    "alumniByEmployer":     {"key": DIFFBOT,                    "expect": ["diffbotId", "matchedFor", "currentEmployerId"]},
    "articlesByEntity":     {"key": APPLE,                      "expect": ["diffbotId", "matchedFor", "dateTimestamp", "sentiment"]},
    "jobsByEmployer":       {"key": APPLE,                      "expect": ["diffbotId", "employerId", "dateTimestamp", "skills"]},
    "orgsByAsk":            {"key": 'type:Organization location.city.name:"Sydney" industries:"Software"',
                             "expect": ["diffbotId", "matchedFor"], "raw_dql": True},
    "peopleByAsk":          {"key": 'type:Person employments.{employer.id:or("%s") isCurrent:true}' % DIFFBOT,
                             "expect": ["diffbotId", "matchedFor"], "raw_dql": True},
    "moviesByAsk":          {"key": 'type:Movie genres:"cyberpunk" releaseDate>"1990-01-01"',
                             "expect": ["diffbotId", "matchedFor", "productionCompanyNames", "directorNames"], "raw_dql": True},
    "moviesByImdbId":       {"key": "tt0133093",                "expect": ["diffbotId", "matchedFor", "productionCompanyNames"]},
}


def get(path, params):
    params = dict(params, token=TOKEN)
    url = f"{BASE}/{path}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=45) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        return None, f"HTTP {e.code}: {body}"
    except Exception as e:  # noqa: BLE001 - report whatever the network did
        return None, str(e)


def resolve(value, record):
    """Mirror the host's projection resolver: dotted paths, numeric indexes, and [*] flattening."""
    if "[*]" in value:
        head, _, tail = value.partition("[*]")
        seq = resolve(head, record) if head else record
        if not isinstance(seq, list):
            return None
        tail = tail.lstrip(".")
        out = [resolve(tail, item) if tail else item for item in seq]
        out = [v for v in out if v is not None]
        return out or None
    node = record
    for part in value.split("."):
        if node is None:
            return None
        if part.isdigit() and isinstance(node, list):
            node = node[int(part)] if int(part) < len(node) else None
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            return None
    return node


def probe(name, spec, cfg):
    key = cfg["key"]
    if spec["operation"] == "enhance":
        params = dict(spec.get("args") or {})
        params.pop("size", None)
        params["size"] = 1
        params[spec["keyArg"]] = key
        payload, err = get("enhance", params)
    else:
        query = (spec.get("args") or {}).get("query", "")
        if cfg.get("raw_dql"):
            rendered = key
        else:
            template = spec.get("keyTemplate", "{key}")
            rendered = query.replace("{keys}", template.replace("{key}", key))
        rendered = rendered.replace("{filters}", "").replace("  ", " ").strip()
        payload, err = get("dql", {"query": rendered, "size": 2})

    if err:
        return {"ok": False, "detail": err, "n": 0, "missing": []}

    rows = [r["entity"] for r in (payload.get("data") or [])]
    if not rows:
        return {"ok": False, "detail": "0 entities returned", "n": 0, "missing": []}

    project = spec.get("project") or {}
    echo = spec.get("echoKeyAs")
    missing = []
    for field in cfg["expect"]:
        if field == echo:
            continue  # stamped by the host after the fetch, not present in the raw record
        path = project.get(field)
        if path is None:
            missing.append(f"{field}(not projected)")
        elif not any(resolve(path, r) is not None for r in rows):
            missing.append(f"{field}<-{path}")
    return {"ok": True, "detail": f"hits={payload.get('hits')}", "n": len(rows), "missing": missing}


def main():
    if not TOKEN:
        print("DIFFBOT_TOKEN is not set", file=sys.stderr)
        return 2
    producers = {p["name"]: p for p in yaml.safe_load(open(os.path.join(ROOT, "producers/diffbot.yml")))}
    wanted = sys.argv[1:] or list(PROBES)

    print(f"{'PRODUCER':<22} {'CALL':<6} {'ROWS':>4}  {'DETAIL':<18} FIELDS")
    print("-" * 100)
    failures = 0
    for name in wanted:
        if name not in PROBES:
            print(f"{name:<22} SKIP   no probe defined")
            continue
        result = probe(name, producers[name], PROBES[name])
        if not result["ok"]:
            failures += 1
            status, fields = "FAIL", ""
        elif result["missing"]:
            failures += 1
            status, fields = "PARTIAL", "MISSING: " + ", ".join(result["missing"])
        else:
            status, fields = "OK", "all projected fields present"
        print(f"{name:<22} {status:<6} {result['n']:>4}  {result['detail']:<18} {fields}")
        time.sleep(0.25)  # stay inside the Startup tier's 5/sec
    print("-" * 100)
    print(f"{len(wanted) - failures}/{len(wanted)} producers healthy")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
