# Review — Basic Memory lifecycle fork (`7Knife/basic-memory-lifecycle-fork`, main @ 1ca3c2c)

Scope: the fork's differentiator — the `lifecycle` package, the five review tools, the hit tool,
and the search-time lifecycle treatment. Upstream Basic Memory code (~all of `src/basic_memory`)
is out of scope except where it proves or breaks a lifecycle claim.

Method: static reading of the code path (no runtime; this environment has no `uv`/`git` and the
project's dependency set is not installed). Every finding cites the lines that produce it.

## Findings

### 1. HIGH — `sort_by="weight"` and `include_archived=False` are silent no-ops in production

`_apply_lifecycle_to_payload` reads lifecycle fields from each search row's `metadata`:

- `src/basic_memory/mcp/tools/ranked_search.py:105-119`

But the `metadata` on a search row is the search-index column, and the indexer writes only
`note_type` (entity rows) or `tags` (observation rows):

- `src/basic_memory/services/search_service.py:1075-1077` (`index_entity_file`)
- `src/basic_memory/services/search_service.py:1155-1157` (`index_entity_markdown`, entity row)
- `src/basic_memory/services/search_service.py:1188-1190` (observation row)

That value is what travels to the MCP payload: `SearchIndexRow.metadata` →
`SearchResult.metadata` (`src/basic_memory/api/v2/utils.py:418`) →
`response.model_dump(...)` → the dict the helper post-processes.

Consequence: `weight` / `canon_status` are never present on a real result, so

- `sort_by="weight"` gives every row `FALLBACK_WEIGHT` 1.3, the same factor, and the resulting
  order is exactly the upstream order (the sort at line 117 is a no-op on ties);
- `include_archived=False` never hides anything, because `canon_status` is always `""` (line 106).

The feature is only exercised with fabricated payloads, which is why the tests pass:

- `tests/lifecycle/test_lifecycle.py:106-127` hand-builds `metadata` dicts that the real index
  never produces. No test drives `bm_search` end to end with a note whose frontmatter carries
  lifecycle fields.

Fix direction: copy the lifecycle keys from `entity.entity_metadata` into the entity search row's
`metadata` at index time (and resolve observation/relation hits through their parent entity), or
hydrate them in the payload after search. Whichever path is chosen, add one integration test that
writes a note with `weight`/`canon_status` frontmatter, indexes it, and asserts the ordering and
archived hiding through `bm_search`.

### 2. HIGH — weight decay never runs

`decay_update` (`src/basic_memory/lifecycle/__init__.py:107`) has no production caller. The only
caller is `LifecycleService.decay_entity` (`src/basic_memory/lifecycle/service.py:61-69`), whose
own module docstring says it is deprecated and unused, and nothing constructs `LifecycleService`.

So weights only ever go up (`bm_memory_review_hit`) or get set explicitly through the review
queue. Nothing lowers a weight with age and nothing auto-archives. The README's "weight decay,
canon status" bullet describes behavior the shipped code does not have — `default_weight`
(line 101) and `should_hide` (line 182) are likewise dead code.

Fix direction: decide where decay is triggered (a `bm` CLI verb, an MCP tool, or the hook/index
path), implement it against the same `set_metadata` write path as the hit tool, and delete
`LifecycleService` rather than leaving a second, unwired writer.

### 3. MEDIUM — the review queue is global, not project-scoped

`ReviewQueue` defaults to `~/.basic-memory/review-queue.json` (`lifecycle/__init__.py:228-234`)
and stores a bare `permalink` per proposal (line 195). Two projects can hold the same permalink,
which is the normal case for `memory://...` style notes; the queue cannot tell them apart. The
standalone review server (`src/basic_memory/mcp/review_server.py`) shares that one file too.

Concretely: `bm_memory_review_list` in project B shows project A's proposals, and
`bm_memory_review_apply` returns a patch for a permalink that the caller then applies with
`bm_edit` in whatever project is active. Project isolation — a core Basic Memory boundary — is
lost at this seam.

Fix direction: key the queue by project (store `project_id`/`project_external_id` on the
proposal, or put the queue file under the project directory), and refuse to decide/apply a
proposal that does not belong to the current project.

### 4. MEDIUM — queue updates are read-modify-write without a lock

`propose`, `decide`, and `mark_applied` each do `_load()` → mutate → `_save()`
(`lifecycle/__init__.py:251-281`, `302-318`). `_save` (245-249) writes via `tmp` + `replace`, so
the file cannot be left half-written, but two concurrent writers both read the old list and the
second save drops the first writer's proposal or decision. The main MCP server and the standalone
review server are exactly the two processes this invite.

Fix direction: an advisory lock (`msvcrt`/`fcntl`, or a lock file) around the load-mutate-save
critical section, or a single-writer service. At minimum, document that concurrent review calls
are unsafe — silent proposal loss is the worst failure mode here.

### 5. MEDIUM — a corrupt queue file silently reads as empty and is then overwritten

`_load` returns `[]` on `OSError` or `json.JSONDecodeError` (lines 236-243). `_save` will happily
overwrite the corrupt file on the next `propose`/`decide`, destroying whatever was there. That
contradicts the house rule in `AGENTS.md` ("Fail fast. Do not add silent fallback logic").

Fix direction: raise on parse failure (or quarantine the file to `review-queue.json.corrupt-<ts>`
and surface it in `bm_memory_review_status`).

### 6. LOW — `decide` has no state guard; `list_pending` invents a `draft` state

`decide` (302-310) flips any row, including one already `applied` or `rejected`, so a late call
can re-open a decided proposal. `list_pending` (283-285) treats `draft` as pending, but nothing
ever writes `draft`; `Proposal.status` only ever starts as `pending` (line 200).

Fix direction: allow only `pending → approved|rejected`, return a typed conflict otherwise, and
drop the unreachable `draft` case (or implement it deliberately).

### 7. LOW — the hit "cap" is not a cap when `base_weight` is missing

`hit_bonus_update` (`lifecycle/__init__.py:158-164`) computes `cap = max(base * 1.5, 1.5)`, with
`base` falling back to the *current* weight when `base_weight` is absent. Each hit therefore
raises the ceiling by 1.5x the new weight, so weight ratchets from 1.3 to 3.0 (the `clamp`
ceiling) over a handful of hits — not the "capped weight bonus" the tool description promises
(`src/basic_memory/mcp/tools/lifecycle_hit.py:29-33`). Also `max(..., 1.5)` lets a base-0.5 note
triple.

Fix direction: seed `base_weight` on first hit and cap at `base_weight * HIT_CAP_FACTOR` without
the `max(..., HIT_CAP_FACTOR)` floor, or state the ratchet as intended behavior.

### 8. LOW — decay discards the remainder of a partial interval

`decay_update` (line 134-137) charges whole `DECAY_INTERVAL_DAYS` steps and then stamps
`last_decay_at` with `now`, throwing away the leftover days. A note decayed daily for two weeks
with a 3-day interval loses ~2/3 of the decay it should have taken. Not harmful at these
constants, but it makes decay depend on call cadence (daily vs weekly callers decay differently).

Fix direction: advance `last_decay_at` by `steps * DECAY_INTERVAL_DAYS` rather than to `now`.

## What holds up

- Opt-in gating is consistent: `lifecycle_enabled()` at the helper boundary, the same check in
  the review tools, and a hard `SystemExit` in the standalone server.
- The propose → decide → apply gate is real: `apply` only returns a patch for `approved`
  proposals, refuses `new` proposals, and the actual write goes through the standard
  `bm_edit(set_metadata)` pipeline with a checksum CAS (`lifecycle_hit.py:80-84`).
- `bm_search` stays read-only; hit recording is a separate, explicit tool.
- `applies_to_metadata` filters proposals down to lifecycle keys, so a proposal cannot smuggle
  arbitrary frontmatter through the apply path (`lifecycle/__init__.py:320-332`).

## Verification limits

No runtime checks were possible in this environment (no `uv`, no installed project dependencies,
no `git`). Findings 1 and 2 are proven by the indexer and call-graph reads above; 3-8 are proven
by the code as written. Findings 1 and 2 should be reproduced with one end-to-end test each
before fixing, since both are cases where the unit tests currently mask the gap.
