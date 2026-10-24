"""After a session: a question about its work is answered by one model, and the next request goes on where the team worked."""

from __future__ import annotations

from pathlib import Path

from agentfit.chat import answering_model, session_chat_id
from agentfit.workflows.dsl import load_yaml
from test_api import api, wait_for_run  # noqa: F401  (the fixture is used by name)

TEAM = {"specializations": ["enterprise_docs"], "mode": "automatic"}


def finished_session(api, task: str = "Write a small website") -> dict:  # noqa: F811
    workflow_id = api.post("/api/teams", json=TEAM).json()["workflow_id"]
    started = api.post("/api/runs", json={"workflow_id": workflow_id, "task": task, "approval": "bypass"})
    assert started.status_code == 200, started.text
    done = wait_for_run(api, started.json()["id"])
    assert done["run"]["status"] == "completed"
    return {"workflow_id": workflow_id, **done}


def roles_called(api, since: int) -> list[str]:  # noqa: F811
    return [call["role"] for call in api.runtime.calls[since:]]


def test_a_question_about_a_finished_session_is_answered_by_one_model_where_the_team_worked(api):  # noqa: F811
    session = finished_session(api)
    run_id = session["run"]["id"]
    workspace = session["events"][0]["payload"]["workspace"]
    Path(workspace, "index.html").write_text("<h1>Hi</h1>", encoding="utf-8")
    calls_before = len(api.runtime.calls)

    asked = api.post(f"/api/runs/{run_id}/ask", json={"message": "How do I open the website?", "approval": "bypass"})
    assert asked.status_code == 200, asked.text
    data = asked.json()
    assert data["chat"] == session_chat_id(run_id) and data["model_key"] == "qwen3-14b"
    answer = wait_for_run(api, data["run"])
    assert answer["run"]["status"] == "completed"
    # One model, not the team again.
    assert set(roles_called(api, calls_before)) == {"Assistant"}
    # In the same place, told what the team was asked and delivered.
    assert answer["events"][0]["payload"]["workspace"] == workspace
    prompt = next(m["content"] for m in api.runtime.calls[-1]["messages"] if m["role"] == "user")
    assert "## The work these questions are about" in prompt and "A team of agents was asked: Write a small website" in prompt
    assert prompt.index("How do I open the website?") < prompt.index("## The work these questions are about")

    questions = api.get(f"/api/runs/{run_id}/questions").json()["chat"]
    assert [turn["message"] for turn in questions["turns"]] == ["How do I open the website?"]
    assert questions["session"] == run_id
    # Neither the answer nor its chat is listed apart from the session.
    assert all(chat["id"] != questions["id"] for chat in api.get("/api/chats").json()["chats"])
    assert [r["id"] for r in api.get("/api/runs").json()["runs"]] == [run_id]

    # The second question is a turn of the same conversation.
    again = api.post(f"/api/runs/{run_id}/ask", json={"message": "And on a phone?", "approval": "bypass"}).json()
    wait_for_run(api, again["run"])
    prompt = next(m["content"] for m in api.runtime.calls[-1]["messages"] if m["role"] == "user")
    assert "## The conversation so far" in prompt and "How do I open the website?" in prompt
    assert len(api.get(f"/api/runs/{run_id}/questions").json()["chat"]["turns"]) == 2

    # Deleting the session takes its questions with it, and leaves its files.
    assert api.delete(f"/api/runs/{run_id}").json() == {"deleted": run_id}
    assert api.get(f"/api/runs/{run_id}/questions").json()["chat"] is None
    assert Path(workspace, "index.html").is_file()


def test_no_question_while_the_team_is_working_and_none_without_words(api):  # noqa: F811
    first = finished_session(api)["run"]
    # Seen as working, as it would be at a plan gate: the team has the workspace, a question waits.
    api.services.db.execute("UPDATE workflow_run SET status = 'waiting_approval' WHERE id = ?", (first["id"],))
    busy = api.post(f"/api/runs/{first['id']}/ask", json={"message": "How is it going?"})
    assert busy.status_code == 409 and "still working" in busy.json()["detail"]
    api.services.db.execute("UPDATE workflow_run SET status = 'completed' WHERE id = ?", (first["id"],))
    assert api.post(f"/api/runs/{first['id']}/ask", json={"message": "  "}).status_code == 400
    assert api.post("/api/runs/ghost/ask", json={"message": "x"}).status_code == 404
    assert api.get(f"/api/runs/{first['id']}/questions").json() == {"chat": None}


def test_the_next_request_of_a_session_without_a_folder_goes_on_in_the_same_workspace(api):  # noqa: F811
    session = finished_session(api)
    workspace = session["events"][0]["payload"]["workspace"]
    second = api.post("/api/runs", json={
        "workflow_id": session["workflow_id"], "task": "Now add a contact page", "continue_from": session["run"]["id"], "approval": "bypass",
    })
    assert second.status_code == 200, second.text
    again = wait_for_run(api, second.json()["id"])
    payload = again["events"][0]["payload"]
    assert payload["workspace"] == workspace and payload["source"] == {"kind": "none"} and payload["continued"] is True
    # A new session (no continue_from) still gets a workspace of its own.
    fresh = api.post("/api/runs", json={"workflow_id": session["workflow_id"], "task": "Something else", "approval": "bypass"}).json()
    assert wait_for_run(api, fresh["id"])["events"][0]["payload"]["workspace"] != workspace


def test_the_answering_model_is_the_one_that_wrote_the_files():
    definition = load_yaml("""
workflow: {id: w, name: W}
models:
  lead: {model_key: big-model}
  code: {model_key: coder-model}
agents:
  - {id: lead, role: Lead, model: lead}
  - {id: dev, role: Developer, model: code, tools: [read_file, write_file]}
edges:
  - {from: START, to: lead}
  - {from: lead, to: dev}
  - {from: dev, to: END}
""")
    assert answering_model(definition, ["big-model", "coder-model"]) == "coder-model"
    assert answering_model(definition, ["big-model"]) == "big-model"  # the writer's model is gone
    assert answering_model(definition, ["other"]) == "other"
    assert answering_model(None, ["other"]) == "other" and answering_model(definition, []) is None
