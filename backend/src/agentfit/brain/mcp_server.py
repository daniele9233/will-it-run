"""The project's memory as a Model Context Protocol server, for any coding agent: Claude Code, LM Studio, Cursor, Codex.

Three tools, on purpose. Every tool a client lists is sent to the model on every request, and a local model with a small
window has none to spare. ``recall`` finds what is known, ``remember`` saves one fact, ``recent`` lists the newest entries
(and the errors nobody fixed). Requires the optional ``mcp`` package: ``pip install "agentfit[mcp]"``.

    willitrun memory mcp --workspace D:\\SotwareKikko
"""

from __future__ import annotations

from pathlib import Path

from agentfit.brain.external import ProjectMemory

INSTRUCTIONS = (
    "The permanent memory of this project: its decisions and lessons (from docs/DECISIONS.md and docs/LESSONS.md), and what earlier "
    "sessions did, which checks failed and what fixed them. Call recall before you change a file you did not write and when you meet an "
    "error. It can be out of date: when a file says otherwise, the file is right."
)


def _server_class():
    """The server class of the installed SDK. Imported here so the rest of the app does not need the package."""
    try:
        from mcp.server.mcpserver import MCPServer  # mcp 2.x

        return MCPServer
    except ImportError:
        from mcp.server.fastmcp import FastMCP  # mcp 1.x: the same class under its first name

        return FastMCP


def build_server(memory: ProjectMemory):
    server = _server_class()("willitrun-memory", instructions=INSTRUCTIONS)

    @server.tool()
    def recall(query: str = "", file: str = "", id: int = 0, kind: str = "") -> str:
        """Search the project's memory. Give query (what you want to know), or file (a path: what is known about it), or id (one entry in full). kind narrows to decision, error, solution, note, session or architecture."""
        return memory.recall(query=query, file=file, id=id, kind=kind)

    @server.tool()
    def remember(kind: str, title: str, text: str, files: str = "", fixes: int = 0) -> str:
        """Save one fact for later sessions: a decision with its reason, how a part works, an error with its fix. kind is decision, architecture, error, solution or note; files is a comma-separated list of paths; fixes is the number of the error entry a solution closes."""
        return memory.remember(kind=kind, title=title, text=text, files=files, fixes=fixes)

    @server.tool()
    def recent(kind: str = "", open_only: bool = False, limit: int = 8) -> str:
        """The newest entries, one line each. open_only lists the errors that nobody has fixed yet."""
        return memory.recent(kind=kind, open_only=open_only, limit=limit)

    return server


def serve(workspace: str | Path) -> None:
    """Run over standard input and output until the client closes it. Nothing else may print to standard output."""
    build_server(ProjectMemory.open(workspace)).run()
