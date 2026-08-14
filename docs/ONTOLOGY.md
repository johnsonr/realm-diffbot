# Diffbot's ontology, and what this realm takes from it

Diffbot defines 16 top-level entity types. This realm declares four of them. What follows is the
whole surface and the reasoning for the subset, so the next person extending this realm knows what
is already available at the source.

## The full list

| Type | What it holds | In this realm |
|---|---|---|
| **Organization** | corporations, local businesses, non-profits — ~180 fields | ✅ `DiffbotOrganization` |
| **Person** | public professional record | ✅ `DiffbotPerson` |
| **Article** | news, blog posts, with entity tags and sentiment | ✅ `DiffbotArticle` |
| **JobPost** | job listings, employer-linked | ✅ `DiffbotJobPost` |
| **Discussion** | forum and social threads with posts | — |
| **Product** | retail products | — |
| **Image** | images found on the web | — |
| **Video** | video content | — |
| **Place** | locations | — |
| **AdministrativeArea** | countries, states, cities — the geographic spine | — |
| **Skills** | a first-class skill taxonomy, linked from Person and JobPost | — |
| **Research** | academic works | — |
| **Event** | events | — |
| **CreativeWork** | superclass for works | — |
| **LegalEntity** | registered legal entities behind an Organization | — |
| **FAQ** | Q&A pairs extracted from sites | — |

Plus nested structures that are not top-level but carry their own shape: `Employment`, `Education`,
`Investment`, `Location`, `Stock`, `Revenue`, `Award`.

## Why this subset

**Organization** is the flagship and the reason to integrate at all. Its corporate-structure block —
`parentCompany`, `ultimateParent`, `subsidiaries`, `acquiredBy`, `legalEntities`, `predecessors`,
`successors` — is precisely the recursive traversal DQL cannot perform, so it is where a Virtual
Cypher layer adds the most over Diffbot's own product. It also carries multi-jurisdiction
classification codes (NAICS, SIC, NACE, ISIC), tax ids, revenue series, traffic and headcount bands.

**Person** is what makes the realm about the user's world rather than about companies in general: the
bridge anchors on a `Person` already in the graph, put there by correspondence.

**Article** gives the "what is being said" layer, and reaches it by entity tag rather than keyword —
so it is genuinely about *this* company rather than about everything sharing its name.

**JobPost** is the underrated one. A labour-market demand signal keyed on the employer's entity id,
one hop from any company already in the graph, and almost nobody knows Diffbot has it.

## Worth adding next

- **LegalEntity** — the registry ↔ company link. This is the join to `realm-gov-uk` (Companies House)
  and `realm-gov-au`: Diffbot's commercial view of a company reconciled against the statutory record.
  Probably the highest-value addition here.
- **Skills** — with `Person` and `JobPost` already declared, a `Skills` type turns "what is this
  company hiring for" into "what capability is this company building", which is a different and
  better question.
- **AdministrativeArea** — not interesting alone, but it is the shared geographic spine every other
  realm's location field could canonicalize onto.
- **Research** — overlaps `realm-research` (OpenAlex). Worth comparing coverage before duplicating.

## Fields deliberately not projected

`DiffbotOrganization` projects about 30 of ~180 fields. The omissions are not oversights:

- **List-valued reference fields** (`subsidiaries`, `competitors`, `investments`, `founders`,
  `boardMembers`, `customers`, `suppliers`, `partnerships`) are reached as *reverse lookups* instead,
  keyed on our own `diffbotId`. A reverse lookup batches and needs no list projection; a forward list
  would need both, and would carry the ids without the records.
- **The classification code blocks** (NAICS/SIC/NACE/ISIC across jurisdictions) are large and only
  matter for a specific kind of question. Add the one your realm needs rather than all of them.
- **Revenue and headcount time series** (`yearlyRevenues`, `quarterlyRevenues`) — the current
  estimate is projected; the series is a separate, larger thing that probably deserves its own
  brought sub-graph.
