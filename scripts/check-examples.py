#!/usr/bin/env python3
"""Execute every keyTransform few-shot OUTPUT against Diffbot and assert it is valid DQL.

A few-shot example is not documentation — it is the pattern the model imitates, so an invalid one
teaches the model to emit invalid queries. That failure surfaces far from its cause: the producer
gets an HTTP 400, contributes no records, and the query reports a missing relationship type three
hops downstream.

    DIFFBOT_TOKEN=... python3 scripts/check-examples.py
"""
import json, os, sys, time, urllib.parse, urllib.request, yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = os.environ.get("DIFFBOT_TOKEN")


def run(dql):
    url = "https://kg.diffbot.com/kg/v3/dql?" + urllib.parse.urlencode(
        {"token": TOKEN, "query": dql, "size": 1})
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        detail = json.loads(e.read().decode("utf-8", "replace") or "{}")
        return None, (detail.get("message") or "").split("\n")[0][:90]
    except Exception as e:
        return None, str(e)[:90]


def main():
    if not TOKEN:
        print("DIFFBOT_TOKEN is not set", file=sys.stderr)
        return 2
    producers = yaml.safe_load(open(os.path.join(ROOT, "producers/diffbot.yml")))
    bad = 0
    checked = 0
    for p in producers:
        for ex in ((p.get("keyTransform") or {}).get("examples") or []):
            checked += 1
            payload, err = run(ex["output"])
            if err:
                bad += 1
                print(f"INVALID  {p['name']}: {ex['input']!r}")
                print(f"         {ex['output']}")
                print(f"         -> {err}")
            else:
                print(f"ok       {p['name']:<14} hits={payload.get('hits'):<10} {ex['input'][:44]}")
            time.sleep(0.25)
    print(f"\n{checked - bad}/{checked} few-shot examples are valid DQL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
