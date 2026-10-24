"""Data types of the Project Brain."""

from __future__ import annotations

from pydantic import BaseModel, Field

# What a memory is. "session" is one request and how it ended; the rest is what the
# project knows: how it is built, what was decided, what was changed, what went wrong
# and what fixed it.
KINDS = ("architecture", "decision", "change", "error", "solution", "note", "session")
# What an agent or the user may write. Changes and sessions are recorded by the engine.
WRITABLE_KINDS = ("decision", "architecture", "error", "solution", "note")

# Who wrote a memory: the engine from what happened, an agent with ``remember``, or the user.
SOURCES = ("engine", "agent", "user")


class Project(BaseModel):
    id: str
    # "github:owner/repo" or "folder:<path>".
    key: str
    kind: str
    name: str
    path: str | None = None
    created_at: str
    updated_at: str
    memories: int = 0
    counts: dict[str, int] = Field(default_factory=dict)


class Memory(BaseModel):
    id: int
    project_id: str
    kind: str
    title: str
    body: str
    files: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source: str = "engine"
    agent: str = ""
    run_id: str | None = None
    # Set on the records the engine rewrites in place, such as the project map.
    slot: str | None = None
    created_at: str
    updated_at: str
    # Id of the memory that replaced this one. None while it is current.
    superseded_by: int | None = None
    # Times it was given to an agent.
    uses: int = 0
    # True when it has a vector for the embedding model in use.
    embedded: bool = False


class Hit(BaseModel):
    """A memory found by a search, with why it was found."""

    memory: Memory
    score: float
    # Cosine similarity to the query, when semantic search ran and this memory has a vector.
    semantic: float | None = None
    keyword: bool = False
    # Graph paths that brought it in, in words: "file src/auth.py", "fixes #12".
    via: list[str] = Field(default_factory=list)


class SearchResult(BaseModel):
    hits: list[Hit] = Field(default_factory=list)
    # Current memories of the project that were searched.
    total: int = 0
    # True when the query was compared by meaning, not only by words.
    semantic: bool = False
    model: str | None = None


class EmbedStatus(BaseModel):
    available: bool = False
    model: str | None = None
    # Why semantic search is off: a sentence an agent can read, and a code the interface translates
    # ("off", "no_runtime", "offline", "no_model", "failed").
    reason: str = ""
    code: str = ""


class GraphNode(BaseModel):
    id: str
    # "memory" or "entity".
    type: str
    # Memory kind, or "file" / "concept".
    kind: str
    label: str
    degree: int = 0


class GraphEdge(BaseModel):
    source: str
    target: str
    relation: str
    origin: str = "memory"


class Graph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    # Nodes and edges the project has in all. More than shown when the graph was cut to the most connected.
    total_nodes: int = 0
    total_edges: int = 0
