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
| `DiffbotCompany` | literal-seeded from a NAME | resolves to exactly ONE company |
| `DiffbotOrgSearch` / `DiffbotPersonSearch` / `DiffbotMovieSearch` | literal-seeded from the question | plain language → DQL, up to 20 results |

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
| `-[:FORMERLY_EMPLOYED]->(:DiffbotPerson)` | reverse | `isCurrent:false` — see the caveat below |
| `(:DiffbotPerson)-[:CAREER_AT]->` | per-element | every employer in a person's history, from the `employerIds` list |
| `(:DiffbotMovie)-[:PRODUCED_BY]->` | per-element | production company names, resolved by scored `enhance` match |
| `(:DiffbotMovie)-[:DIRECTED_BY]->(:DiffbotPerson)` | per-element | director names, same scored match |
| `(:Movie)-[:HAS_DIFFBOT_MOVIE]->(:DiffbotMovie)` | cross-realm | IMDb id shared with realm-movie |

**`FORMERLY_EMPLOYED` is not "left the company".** Diffbot's `isCurrent:false` returns anyone holding
a non-current employment record there, so someone promoted internally comes back as their own
company's alum — Tim Cook is an Apple alum by this filter. The honest set is this edge MINUS
`EMPLOYS`, which is what `TalentFlow` does, and it is a set difference Diffbot cannot express.

**`location` is the headquarters; `locations` is every office.** They differ by an `s` and answer
different questions: `locations.city.name:"Sydney"` returns 672 companies led by Google, Microsoft,
IBM and Siemens — every multinational with a Sydney branch — while `location.city.name:"Sydney"`
returns 113 actual Sydney companies. The plural reads as the natural translation of "companies in
Sydney" and is confidently wrong, so the DQL-authoring prompt teaches the singular for "in X" and
the plural only for "has an office in X", with a worked example of each. Verified live 2026-08-14.

**A film's people and companies arrive as NAMES, not ids.** Diffbot resolves `parentCompany` to an
entity id but not a film's `directors` or `productionCompanies`. Those hops therefore go through
`enhance` with a 0.75 threshold rather than a DQL name search — `type:Organization name:"Warner Bros.
Entertainment"` returns 127 hits including Telepictures, and since a join links every returned record
to its anchor, that would attribute a film to whichever company shared a word.

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

### One company, or many — pick the right entry point

```cypher
MATCH (:DiffbotCompany  {name: 'Apple'})-[:RESOLVED_TO]->(d)   -- exactly ONE
MATCH (:DiffbotOrgSearch {ask: 'AI companies'})-[:MATCHED]->(o) -- the whole result set
```

Get this backwards and the engine stops you:

```
fetching 'DiffbotPerson' via 'EMPLOYS' from each (:DiffbotOrganization) would bind 50 source
nodes, exceeding maxAnchors 10 — narrow the (:DiffbotOrganization) set
```

which is correct behaviour: twenty companies came back from a search, and a per-company fetch would
spend fifty calls answering a question about one. `RESOLVED_TO` binds a single scored best match, so
every downstream hop starts from one anchor. Views that genuinely fan out from a search carry an
explicit `companies` / `films` ceiling instead.

### Views

22 saved views in [`views/diffbot.yml`](views/diffbot.yml), all exported so they run by name.

**Start from a name or a market**

| View | Answers |
|---|---|
| `CompanySearch` / `CompanyProfile` / `PeopleSearch` | plain language → DQL → entities |
| `CompanyOwnership` | who ultimately owns this, level by level |
| `GroupRollup` | a group's scale, summed over the subsidiary tree |
| `SiblingCompanies` | who else sits under the same owner as a company you deal with |
| `WhoOwnsThisMarket` | search a sector, then walk every result to its ultimate owner |
| `BoardInterlocks` | people on the boards of BOTH of two companies |
| `TalentFlow` | where a company's leavers actually went |
| `CompetitorHiring` | what the competitive set is staffing up for, with skills |
| `SkillDemand` | what capability a whole market is buying, counted by skill |

**Anchored in your own graph** — the ones Diffbot could not run at any price, because the starting
point is who has emailed you

| View | Answers |
|---|---|
| `ContactCompanyProfiles` | every company your correspondents work for, enriched |
| `HiddenCommonOwner` | two companies in YOUR world that quietly share an owner |
| `QuietlyAbsorbed` | companies in your world acquired or dissolved without you noticing |
| `ContactCompanyNews` | recent negative-toned coverage of your accounts |
| `ExecutiveExposure` | press about the executives leading them, personally |
| `HiringSignals` | what your accounts are advertising for |

**Film** — `Movie` is absent from Diffbot's published ontology but real: ~2.5M entities with
directors, cast and character names, writers, genres, production companies, IMDb link and a
Wikipedia pageview trend. It earns a place here because a film's production companies are
organizations, so the catalogue opens directly into the ownership graph.

| View | Answers |
|---|---|
| `MovieSearch` / `DirectorFilmography` / `FilmCast` | films, filmographies, actor↔character credits |
| `FilmOwnership` | who ultimately owns the studios behind a set of films |
| `MyFilmsByOwner` | the films YOU rated, grouped by who owns the studio (needs realm-movie) |

`MyFilmsByOwner` is the one that needs three worlds to agree — your own ratings, Diffbot's film
catalogue, and its corporate ownership graph — joined on an IMDb id and a company name.

### Requires realm-movie (optional)

`(:Movie)-[:HAS_DIFFBOT_MOVIE]->(:DiffbotMovie)` anchors on realm-movie's `Movie` type, keyed on the
`imdbId` both sides already hold. There is no realm dependency mechanism, so this is a documented
requirement rather than an enforced one. Without realm-movie installed the join resolves to nothing
and everything else here still works — a smaller answer, not a wrong one.

## Plans, and what each one actually buys

There is nothing between free and $299. The published ladder is:

| Plan | Price | Credits/month | Entity exports (~25 credits each) | Rate limit |
|---|---|---|---|---|
| Free | $0 | 10,000 | **400** | 5 requests/**minute** |
| Startup | $299/mo | 250,000 | 10,000 | 5 requests/**second** |
| Plus | $899/mo | 1,000,000 | 40,000 | 25 requests/second |
| Enterprise | custom | custom | — | 25+/second |

Paid plans bill overage at $0.001/credit, so a Startup subscription is a floor rather than a cap —
but there is no pay-as-you-go entry point below it.

**Diffbot for Students** grants Startup-tier access free of charge to students and academic
researchers. It is the only published route to a usable quota without the $299.

The free tier's two limits bite in different ways. 400 entity exports a month is roughly 40 fetches
at this realm's default `size: 10` — enough to demo, not enough to develop against. And 5 requests
per minute is 60x slower than Startup, so a single ownership walk fanning across four hops spends
most of a minute waiting.

**The rate bucket here is paced for STARTUP (`5/sec`).** On the free plan drop it, or the realm
bursts 60x too fast and spends the month on 429s:

```bash
sed -i '' 's|rate: "5/sec"|rate: "4/min"|g' producers/diffbot.yml     # Free
sed -i '' 's|rate: "5/sec"|rate: "25/sec"|g' producers/diffbot.yml    # Plus
```

## Cost — read this before running anything wide

Diffbot bills **per entity exported** (~25 credits each), not per call. A fetch asking for 50
entities bills 50 exports whether the query returns 3 rows or 50, and a single view can fan out
across several hops. This realm's first token was exhausted inside a day of development, and the
wall is not gentle: Diffbot answers an exhausted quota with a plain **HTTP 429 carrying a
`Retry-After` measured in WEEKS**, identical in shape to a per-second burst 429.

**A 429 reaches a query as ZERO ROWS plus a warning.** "No companies in Sydney" and "your quota ran
out three weeks ago" look the same in the result table. Read `warnings` before believing an empty
answer — this is the single most important operational fact about this realm.

- `size` defaults to **10** per fetch (20 for the three plain-language searches), NOT the endpoint
  maximum of 50. Raise it per producer when a query genuinely needs breadth and you are willing to
  pay for it;
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

**Verified live against the API on 2026-08-14.** Every DQL shape the realm emits was executed
against a real token and returned rows:

| Shape | Query | Result |
|---|---|---|
| `id:or(…)` | `type:Organization id:or("EYX1…","EHb0…")` | 2 hits, both entities |
| `parentCompany.id:or(…)` | subsidiaries of Apple | 186 hits |
| `competitors.id:or(…)` | inbound competitors of Diffbot | 1 hit |
| `employments.{employer.id:… isCurrent:true}` | current Diffbot staff | 39 hits |
| `employer.id:or(…)` | Apple job posts | 13,386 hits |
| `tags.uri:or(…)` | articles mentioning Apple | 3.18M hits |
| `enhance?type=Organization&url=` | diffbot.com | score 0.96, correct entity |
| date pushdown | `date>"2026-06-01"` | 21 of 13,386 — and 0 for a future date |
| sentiment pushdown | `sentiment<-0.3` | 799k of 3.18M, sample tone −1.0 |

Three projection bugs were found and fixed this way, all of which would have produced silently
missing properties rather than errors: `stock` is a single OBJECT (not a list, so `stock.0.symbol`
found nothing), `remote` on a JobPost is an object whose token lives at `remote.normalizedValue`,
and **every Diffbot date is an object whose `.str` carries a leading `d`** (`d2026-07-27`).

That last one matters beyond cosmetics. `d` sorts above every digit, so a graph-side
`WHERE a.date > '2026-07-01'` passes *every* row while looking like it filters. Each date is
therefore projected twice: `date` for display and `dateTimestamp` (epoch millis) for filtering.
**Filter on `dateTimestamp`.** The pushdown rules still take an ISO date, because that is what DQL
itself wants at the source.

**A quota 429 is indistinguishable from a burst 429**, so `retry.retryOn` deliberately omits
`rate-limited`: retrying a monthly wall three times per hop bought nothing but latency and log noise.
The cost bucket still paces calls, and the engine backs off once on its own.

**No paging, so a wide search truncates at the requested `size`.** DQL pages by `from` offset, which the host's paging
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

## Coverage, measured

Sampled live on 2026-08-14 rather than assumed, because coverage outside US tech is the thing that
decides whether this realm is a research tool or a demo.

**Listed Australian companies: 9 of 10 resolved by domain**, with 89–1,880 sources each and revenue
on every match. The one miss was a private building-products firm.

**Australian private mid-market** (200–2,000 staff, not listed): **15,420 companies**, and of a
25-company sample every one had industries, 22 had revenue, 9 had a parent company, and the median
source count was 414 with no record under 80 sources.

Read that sample with one caveat: DQL returns results relevance-ranked, so the top 25 are the
best-covered 25 and the true median across all 15,420 is lower. The finding is that AU mid-market
coverage EXISTS at useful depth, not that every record is 400 sources deep. Check `nbOrigins` per
record rather than trusting the aggregate.

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
