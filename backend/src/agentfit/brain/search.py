"""Finding the few memories a question needs.

Three ways of looking are combined, because each misses what another finds:

* by meaning: the question and every memory are vectors from an embedding model, and the
  closest ones are taken ("login is broken" finds "authentication fails after refresh");
* by words: an index of the words of every memory, where exact names count
  (``AuthService``, an error code, a file name);
* by the graph: a memory that shares a file with a good match, and the fix that belongs
  to an error, come along even when their own words are far from the question.

The three rankings are merged by reciprocal rank fusion: a memory scores by its position
in each list, not by numbers that cannot be compared with each other.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from agentfit.brain import text
from agentfit.brain.embed import Embedder
from agentfit.brain.models import Hit, Memory, SearchResult
from agentfit.brain.store import BrainStore, entity_node, memory_node

# Reciprocal rank fusion: score = weight / (RRF_K + position).
RRF_K = 10
WEIGHT_SEMANTIC = 1.0
WEIGHT_KEYWORD = 1.0
WEIGHT_NAMED = 1.0
WEIGHT_GRAPH = 0.5
# With fewer vectors than this, "stands out from the rest" means nothing: take the closest few.
MIN_FOR_STANDING = 8
# How far above the project's usual similarity a memory has to be to count as a match by meaning:
# (spreads above the mean, and never less than this much in similarity). The first pair is for a
# search someone reads; the second for context nobody asked for, where a wrong entry costs more
# than a missing one. Something always sits above the mean, so the fixed margin is what says no.
SEMANTIC_GATE = {False: (0.5, 0.05), True: (1.5, 0.10)}
# Context nobody asked for: a memory found by words alone has to share this many of them.
STRICT_SHARED_WORDS = 2
# The best matches whose neighbours in the graph are looked at.
SEEDS = 5
# What a search for context prefers when two memories are equally close.
KIND_WEIGHT = {"architecture": 1.1, "decision": 1.1, "error": 1.1, "solution": 1.1, "note": 1.0, "change": 0.9, "session": 0.85}

_FILE_WORD = re.compile(r"[\w./\\-]*[\w-]+\.[A-Za-z0-9]{1,8}\b|[\w.-]+(?:[/\\][\w.-]+)+")


def embed_text(memory: Memory) -> str:
    """What is embedded for a memory: what it is, its title, then its text."""
    files = f"\nfiles: {', '.join(memory.files[:12])}" if memory.files else ""
    return f"{memory.kind}: {memory.title}\n{memory.body}{files}"


@dataclass
class _Candidate:
    score: float = 0.0
    semantic: float | None = None
    keyword: bool = False
    named: bool = False
    via: list[str] = field(default_factory=list)


def _age_days(stamp: str) -> float:
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return 365.0
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - then).total_seconds() / 86400)


class Searcher:
    def __init__(self, store: BrainStore, embedder: Embedder) -> None:
        self.store = store
        self.embedder = embedder

    # -- vectors ------------------------------------------------------------
    def ensure_vectors(self, project_id: str, limit: int = 64) -> int:
        """Embed memories that have no vector for the model in use. Returns how many were embedded."""
        status = self.embedder.status()
        if not status.available or not status.model:
            return 0
        pending = self.store.without_vector(project_id, status.model, limit)
        if not pending:
            return 0
        result = self.embedder.embed([embed_text(m) for m in pending])
        if result is None:
            return 0
        model, vectors = result
        done = 0
        for memory, vector in zip(pending, vectors, strict=False):
            blob = text.pack(vector)
            if blob is not None:
                self.store.set_vector(memory.id, model, blob)
                done += 1
        return done

    def _query_vector(self, query: str) -> tuple[str, object] | None:
        result = self.embedder.embed([query], query=True)
        if result is None or not result[1]:
            return None
        vector = text.unpack(text.pack(result[1][0]))
        return (result[0], vector) if vector is not None else None

    def closest(self, project_id: str, memory: Memory, kind: str) -> tuple[int, float] | None:
        """The current memory of ``kind`` nearest in meaning to ``memory``, with its similarity."""
        status = self.embedder.status()
        if not status.available or not status.model:
            return None
        own = text.unpack(self.store.vector_of(memory.id, status.model))
        if own is None:
            return None
        best: tuple[int, float] | None = None
        for other_id, blob in self.store.vectors(project_id, status.model, [kind]):
            if other_id == memory.id:
                continue
            score = text.similarity(own, text.unpack(blob))
            if score is not None and (best is None or score > best[1]):
                best = (other_id, score)
        return best

    # -- search -------------------------------------------------------------
    def _named(self, project_id: str, query: str) -> dict[int, str]:
        """Memories about a file or concept the question names, with how they were reached."""
        found: dict[int, str] = {}
        for raw in dict.fromkeys(_FILE_WORD.findall(query)):
            for entity_id, name in self.store.find_entities(project_id, "file", text.clean_path(raw)):
                for memory_id in self.store.memories_about(project_id, entity_id)[:12]:
                    found.setdefault(memory_id, f"file {name}")
        concepts = {name.lower(): entity_id for entity_id, (_, name) in self.store.entities(project_id, "concept").items()}
        if concepts:
            words = set(text.terms(query))
            for name, entity_id in concepts.items():
                if set(text.terms(name)) and set(text.terms(name)) <= words:
                    for memory_id in self.store.memories_about(project_id, entity_id)[:12]:
                        found.setdefault(memory_id, f"topic {name}")
        return found

    def search(
        self, project_id: str, query: str, *, kinds: list[str] | None = None, limit: int = 8, strict: bool = False
    ) -> SearchResult:
        """The memories that answer ``query``, best first.

        ``strict`` is for context nobody asked for (what an agent is handed when it
        starts): a memory comes back only if it shares words with the query, clearly
        stands out by meaning, or is about a file the query names. A plain search is more
        generous, because whoever searches reads the result and judges it.
        """
        query = (query or "").strip()
        counts = self.store.counts(project_id)
        total = sum(n for kind, n in counts.items() if not kinds or kind in kinds)
        result = SearchResult(total=total)
        if not query or not total:
            return result
        candidates: dict[int, _Candidate] = {}

        def add(ranked: list[int], weight: float) -> None:
            for position, memory_id in enumerate(ranked):
                candidates.setdefault(memory_id, _Candidate()).score += weight / (RRF_K + position + 1)

        keyword = self.store.keyword_ranks(project_id, query)
        if strict:
            # One common word in common is not a reason to hand a memory to an agent.
            wanted = text.stems(query)
            needed = min(STRICT_SHARED_WORDS, len(wanted))
            found = self.store.get_many(keyword)
            keyword = [
                i for i in keyword
                if i in found and text.shared(wanted, f"{found[i].title} {found[i].body} {' '.join(found[i].files)} {' '.join(found[i].tags)}") >= needed
            ]
        add(keyword, WEIGHT_KEYWORD)
        for memory_id in keyword:
            candidates[memory_id].keyword = True

        self.ensure_vectors(project_id, 32)
        asked = self._query_vector(query)
        if asked is not None:
            model, vector = asked
            scores: dict[int, float] = {}
            for memory_id, blob in self.store.vectors(project_id, model, kinds):
                score = text.similarity(vector, text.unpack(blob))  # type: ignore[arg-type]
                if score is not None:
                    scores[memory_id] = score
            ranked = sorted(scores, key=lambda i: -scores[i])
            if len(scores) >= MIN_FOR_STANDING:
                mean, spread = text.standing(scores.values())
                spreads, margin = SEMANTIC_GATE[strict]
                floor = mean + max(spreads * spread, margin)
                close = [i for i in ranked if scores[i] >= floor][: limit * 3]
            else:
                # A project this young holds little: its closest entries are given, and the budget bounds them.
                close = ranked[:limit]
            add(close, WEIGHT_SEMANTIC)
            for memory_id, score in scores.items():
                if memory_id in candidates:
                    candidates[memory_id].semantic = round(score, 3)
            result.semantic, result.model = True, model

        named = self._named(project_id, query)
        add(list(named), WEIGHT_NAMED)
        for memory_id, how in named.items():
            candidates[memory_id].named = True
            candidates[memory_id].via.append(how)

        # Neighbours of the best matches: what shares a file with them.
        direct = set(candidates)
        if not strict:
            seeds = sorted(direct, key=lambda i: -candidates[i].score)[:SEEDS]
            entity_names = self.store.entities(project_id)
            neighbours: dict[int, str] = {}
            for seed in seeds:
                for edge in self.store.edges_of(project_id, memory_node(seed)):
                    if edge.source != memory_node(seed) or not edge.target.startswith("e:"):
                        continue
                    entity_id = int(edge.target[2:])
                    kind, name = entity_names.get(entity_id, ("", ""))
                    for memory_id in self.store.memories_about(project_id, entity_id)[:6]:
                        if memory_id not in direct:
                            neighbours.setdefault(memory_id, f"{'file' if kind == 'file' else 'topic'} {name}")
            add(list(neighbours)[:limit], WEIGHT_GRAPH)
            for memory_id, how in neighbours.items():
                if memory_id in candidates:
                    candidates[memory_id].via.append(how)

        memories = self.store.get_many(candidates, result.model)
        scored: list[tuple[float, Memory]] = []
        for memory_id, candidate in candidates.items():
            memory = memories.get(memory_id)
            if memory is None or memory.superseded_by is not None or (kinds and memory.kind not in kinds):
                continue
            recency = 1 + 0.1 * math.exp(-_age_days(memory.updated_at) / 60)
            scored.append((candidate.score * KIND_WEIGHT.get(memory.kind, 1.0) * recency, memory))
        scored.sort(key=lambda pair: (-pair[0], -pair[1].id))

        hits = [
            Hit(
                memory=memory, score=round(score, 4), semantic=candidates[memory.id].semantic,
                keyword=candidates[memory.id].keyword, via=list(dict.fromkeys(candidates[memory.id].via)),
            )
            for score, memory in scored[:limit]
        ]
        result.hits = self._with_pairs(project_id, hits, kinds, result.model)
        return result

    def _with_pairs(self, project_id: str, hits: list[Hit], kinds: list[str] | None, model: str | None) -> list[Hit]:
        """An error is never returned without the fix that is known for it, nor a fix without its error.

        The two are also kept together, error first, wherever each of them ranked.
        """
        by_id = {h.memory.id: h for h in hits}
        emitted: set[int] = set()
        out: list[Hit] = []

        def partners(memory: Memory) -> list[Hit]:
            found: list[Hit] = []
            node = memory_node(memory.id)
            for edge in self.store.edges_of(project_id, node):
                if edge.relation != "fixed_by":
                    continue
                other_id = int((edge.target if edge.source == node else edge.source)[2:])
                if other_id in emitted:
                    continue
                if other_id in by_id:
                    found.append(by_id[other_id])
                    continue
                other = self.store.get(other_id, model)
                if other is None or other.superseded_by is not None or (kinds and other.kind not in kinds):
                    continue
                how = f"fixes #{memory.id}" if other.kind == "solution" else f"fixed by #{memory.id}"
                found.append(Hit(memory=other, score=by_id[memory.id].score, via=[how]))
            return found

        for hit in hits:
            if hit.memory.id in emitted:
                continue
            group = [hit]
            if hit.memory.kind == "error":
                group = [hit, *partners(hit.memory)]
            elif hit.memory.kind == "solution":
                group = [*partners(hit.memory), hit]
            for member in group:
                if member.memory.id not in emitted:
                    emitted.add(member.memory.id)
                    out.append(member)
        return out

    # -- one file -----------------------------------------------------------
    def about_file(self, project_id: str, path: str) -> dict[str, object] | None:
        """What the graph holds about one file: what it imports, what imports it, and its memories."""
        matches = self.store.find_entities(project_id, "file", text.clean_path(path))
        if not matches:
            return None
        entity_id, name = matches[0]
        names = self.store.entities(project_id, "file")
        imports: list[str] = []
        imported_by: list[str] = []
        node = entity_node(entity_id)
        for edge in self.store.edges_of(project_id, node):
            if edge.relation != "imports":
                continue
            other = int((edge.target if edge.source == node else edge.source)[2:])
            if other in names:
                (imports if edge.source == node else imported_by).append(names[other][1])
        ids = self.store.memories_about(project_id, entity_id)
        found = self.store.get_many(ids)
        return {
            "file": name, "imports": sorted(imports), "imported_by": sorted(imported_by),
            "memories": [found[i] for i in ids if i in found], "others": [n for _, n in matches[1:]],
        }
