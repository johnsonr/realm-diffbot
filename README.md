# realm-diffbot

The [Diffbot Knowledge Graph](https://www.diffbot.com/products/knowledge-graph/) — around 10 billion
entities and a trillion facts crawled and entity-resolved from the public web — exposed to an Embabel
world through Virtual Cypher.

## Why this exists

Diffbot's graph is genuinely interlinked. `parentCompany`, `ceo`, `employer`, `competitors` and an
article's `tags` are all resolved references carrying the target's entity id, not strings. The
missing piece is in the retrieval surface: **DQL cannot traverse.**

DQL filters one entity type at a time with dot-path predicates, `or()`, `not()`, comparisons, regex,
proximity, sorting and one aggregation (`facet:`). It can do a single reverse lookup —
`type:Organization parentCompany.id:or("E1","E2")` is legal and well documented. What it cannot do is
follow that reference to the next level. Two hops means running the query again with the ids you read
out of the first response; there is no path syntax, no join between result sets, and no way to start
from anything that is not already in Diffbot's index.

This realm supplies the traversal, and — more importantly — the **anchor**. The joins hang off `Person`
and `Organization`, which are core labels in every Embabel world, populated from the user's own
correspondence. So the questions this enables are not "search Diffbot", they are:

```cypher
// Who ultimately owns the companies my correspondents work for?
MATCH (me:AssistantUser)-[:EMAILED]->(p:Person)-[:WORKS_FOR]->(o:Organization)
MATCH (o)-[:HAS_DIFFBOT_ORG]->(d:DiffbotOrganization)-[:PARENT_COMPANY*1..4]->(owner)
RETURN o.name, owner.name, owner.country
```

```cypher
// Recent bad-toned coverage of anyone in my world — date and sentiment pushed down into DQL
MATCH (o:Organization)-[:HAS_DIFFBOT_ORG]->(d)-[:MENTIONED_IN]->(a:DiffbotArticle)
WHERE a.date > '2026-07-01' AND a.sentiment < -0.3
RETURN o.name, a.title, a.siteName, a.pageUrl ORDER BY a.sentiment
```

```cypher
// A whole group's scale, rolled up from the subsidiary tree
MATCH (:DiffbotOrganization {name:'Acme'})<-[:SUBSIDIARY_OF*1..3]-(sub)
RETURN count(sub) AS entities, sum(sub.nbEmployees) AS estimatedHeadcount
```

```cypher
// No anchor at all — ask in plain language, then compose the hops onto the results
MATCH (:DiffbotOrgSearch {ask:'mining companies in Western Australia over 500 staff'})-[:MATCHED]->(o)
MATCH (o)-[:IS_HIRING_FOR]->(j:DiffbotJobPost)
RETURN o.name, j.title, j.city
```

## Setup

One deployment-level token — a Diffbot account is a subscription, not a per-user identity, so every
world on the deployment shares one credential and one credit pool.

```bash
export DIFFBOT_TOKEN=…      # https://app.diffbot.com/get-started/
```

Then install the realm. Nothing in the realm reads the secret: the vendored spec declares the token
as an `apiKey in: query`, and the host injects it.

## What it declares

### Types

| Type | Reached from | Notes |
|---|---|---|
| `DiffbotOrganization` | core `Organization` (persisted bridge), or a search | ~30 projected fields off Diffbot's ~180 |
| `DiffbotPerson` | core `Person` (persisted bridge), or a search | public professional record only |
| `DiffbotArticle` | an organization or a person | reached by entity tag, not keyword |
| `DiffbotJobPost` | an organization | hiring signal |
| `DiffbotOrgSearch` / `DiffbotPersonSearch` | literal-seeded from the question | plain language → DQL |

### Edges

| Edge | Direction | How it resolves |
|---|---|---|
| `(:Organization)-[:HAS_DIFFBOT_ORG]->(:DiffbotOrganization)` | bridge | Enhance by **domain**, persisted, 90-day refresh |
| `(:Person)-[:HAS_DIFFBOT_PERSON]->(:DiffbotPerson)` | bridge | Enhance by **email set**, persisted, 90-day refresh |
| `-[:PARENT_COMPANY*1..n]->` | self-recursive | `id:or(…)` per level |
| `-[:ULTIMATE_PARENT]->` | one hop | Diffbot's own rollup, kept as a cross-check on the walk |
| `<-[:SUBSIDIARY_OF*1..n]-` | self-recursive | `parentCompany.id:or(…)` — the best-supported query here |
| `-[:ACQUIRED_BY]->` | one hop | `id:or(…)` |
| `-[:COMPETES_WITH]->` | inbound | `competitors.id:or(…)` — see the caveat below |
| `-[:LED_BY]->(:DiffbotPerson)` | one hop | CEO |
| `-[:EMPLOYS]->(:DiffbotPerson)` | reverse | publicly visible current staff |
| `(:DiffbotPerson)-[:WORKS_AT]->` | one hop | current employer |
| `-[:MENTIONED_IN]->(:DiffbotArticle)` | reverse | `tags.uri:or(…)`, with date/sentiment pushdown |
| `-[:IS_HIRING_FOR]->(:DiffbotJobPost)` | reverse | `employer.id:or(…)`, with date/remote pushdown |

Almost every hop keys on **our own bound node's `diffbotId`** and runs as a reverse lookup. That is
deliberate: a reverse lookup batches (`or()` takes many ids in one call) and needs no list-valued
projection, so a wide traversal is one call rather than N.

Four of those edges (`COMPETES_WITH`, `EMPLOYS`, both `MENTIONED_IN`) fetch **one anchor per call**
and echo the queried id onto each record. The records they return identify a *different* entity than
the anchor they were fetched for, so matching on the record's own id would compare two different
companies and the edge would silently never form — a zero-row answer that reads as "there is nothing
there". [`scripts/check-wiring.py`](scripts/check-wiring.py) checks this mechanically; it caught two
such bugs in the first draft.

```bash
python3 scripts/check-wiring.py
```

### Views

`CompanyOwnership`, `GroupRollup`, `ContactCompanyProfiles`, `ContactCompanyNews`, `HiringSignals`,
`CompanySearch`, `CompanyProfile` — see [`views/diffbot.yml`](views/diffbot.yml).

## Cost

Diffbot bills **per entity exported** (~25 credits each), not per call, and plans start around
$299/month. That changes the shape of good declarations here:

- every producer caps `size` at the endpoint maximum of 50, and none pages;
- `maxAnchors` on each join is the realm's declared spending ceiling for that hop, deliberately
  tighter than the engine's default of 200 — articles and employees are capped at 10 anchors because
  they fan out hardest;
- bridges are `writeThrough` with a 90-day `refreshAfter`, so the expensive part (identity
  resolution) is paid once and everything reached through it rolls back;
- negative results are cached too. Most private companies and most personal email addresses are
  simply not in Diffbot, and without a negative TTL those misses are the anchors re-asked most often.

A query's own `WHERE` scopes the fan-out before anything is fetched, so a filtered question over a
large address book costs a handful of calls, not one per person.

## Known limitations

**Unverified against the live API.** This realm was written against Diffbot's published
documentation without an API token to test with. The DQL shapes it emits — `id:or(…)`,
`parentCompany.id:or(…)`, `competitors.id:or(…)`, `employments.{employer.id:… isCurrent:true}`,
`tags.uri:or(…)`, `employer.id:or(…)` — follow documented patterns, and the first two appear verbatim
in Diffbot's own docs, but the others are extrapolated from the ontology. **Verify each join returns
rows before trusting it**, and treat the field projections the same way: Diffbot's Organization record
has ~180 fields and the ~30 projected here were chosen from documentation, not from a live response.

**No paging, so a wide search truncates at 50.** DQL pages by `from` offset, which the host's paging
walker (page-number or cursor) does not model. Every producer therefore fetches one page of at most 50
entities. The response's own `hits` field reports what the index actually matched, so the truncation is
visible in the raw response — but it is not currently surfaced as a `PARTIAL_RESULT` warning, which
means a wide question can be answered narrowly without saying so. **Narrow the query rather than
trusting a count.** Fixing this properly needs either an offset paging style in the host or a handler.

**`COMPETES_WITH` is inbound.** It finds organizations that name this one as a competitor, which is
not the same as the record's own `competitors` list. The two disagree. Presenting the inbound view as
the company's own claim would be a fabrication, hence the distinct name.

**`EMPLOYS` is coverage, not headcount.** It returns the staff Diffbot can see — heavily skewed
toward people with a public profile. `nbEmployees` on the organization is the headcount estimate;
counting `EMPLOYS` nodes counts Diffbot's coverage.

**Terms of use.** Diffbot's contract governs retention and redistribution of exported entities. This
realm persists exactly one thing: the identity bridge (`Organization.diffbotId` and the linked bridge
node), as a warm cache. Every fetched fact — headcount, revenue, articles, job posts — is transient
and rolls back with the query. Confirm that posture is permitted under your subscription before
deploying; if bridge persistence is not allowed, set `writeThrough: false` on the two bridges and pay
the resolution cost per query.

## What Diffbot is and is not

Public web only. A record is a fusion of what many websites say, not a register of fact —
`nbOrigins` on every entity is the honest confidence signal, and a record built from three sources
deserves less weight than one built from 500. Headcount and revenue are estimates. Coverage is very
uneven: large listed companies are deep, private companies are thin, and people are covered only in
proportion to their public professional footprint. An absent record means Diffbot has not indexed
something, never that it does not exist.

**A note on YAML anchors:** do not use them. The host parses these files with Jackson, which
resolves anchors only for scalars — an alias to a mapping arrives as a bare string, fails to bind,
and takes the entire file down with it, so every producer in it vanishes and the only symptom is
`Unknown producer '<name>' in plan`. `check-wiring.py` rejects anchors textually, because Python's
YAML parser resolves them correctly and would not notice.
