# Health and Trust principles

The quality dimensions come from established documentation and agent-engineering practice: a useful entry point, safe paths, valid links and anchors, discoverable maintained routes, clear titles, explicit source evidence, and bounded retrieval. These dimensions are consistent with Diátaxis' reader-needs framing, docs-as-code validation practice, and evidence-first agent operation.

Research provenance is intentionally dimension-level, not a claim that these exact weights were validated elsewhere. [Diátaxis](https://diataxis.fr/) grounds organisation in distinct reader needs and its [quality discussion](https://diataxis.fr/quality/) explicitly separates independent quality dimensions. Google's primary technical-writing guidance recommends [navigation, headings, and links](https://developers.google.com/tech-writing/two/large-docs), while Anthropic's agent-evaluation guidance argues for evaluation evidence matched to the behavior being measured in [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents). These sources motivate what to observe; they do not supply or validate the numeric rubric below.

The exact weights are Diátaxis Docs rubric v2: a versioned, testable local operationalization for comparison, not an externally validated scientific or universal constant.

| Structural category | Weight |
| --- | ---: |
| Entry | 20 |
| Path safety | 15 |
| Links | 20 |
| Anchors | 10 |
| Reachability | 25 |
| Titles | 10 |

A useful multi-document entry requires a readable map, a usable H1, and at least one valid navigation route. A repository with one maintained document may instead qualify when that document has an H1, a body paragraph, and a secondary heading. A self-only stub earns no navigation, anchor, or reachability credit.

The structural percentage does not prove factual accuracy. Scope, semantic coverage, and hash freshness are separate evidence with explicit provenance. Freshness is implemented in v2 as a Trust gate, not assigned numeric weight until that weight has independent evidence.

Trust routes are a normalized, deduplicated union. They come from configured hot/current-truth paths, valid operational-state hot paths, every verified document and source route, and explicitly marked map links. A map declaration uses exactly one same-line suffix:

```markdown
[Current state](STATE.md) <!-- docs:current -->
[Authoritative API](reference/api.md) <!-- docs:authoritative -->
```

Markers are lowercase and apply only to existing repository-confined local files. Prose and ordinary links never imply authority. Coverage reports every route and all provenance sources. An empty union is unverified. Trust precedence is blocked by an open P0, then stale digest evidence, then partial coverage, then verified. An open P1 prevents the overall verdict from being healthy even when Trust is verified.

Verified text uses SHA-256 after CRLF/CR normalization and Unicode NFC normalization; non-UTF-8 content uses a byte digest. Only state-declared verified document/source routes are hashed. Timestamps are audit metadata, never hash, score, or event-identity inputs.

Hot-path bytes are provenance-tagged telemetry. `provisional_target_bytes: 16384` is an optimization hypothesis only: it awards no points, changes no status or verdict, emits no standalone finding, and supplies no pressure to delete or compress maintained truth.
