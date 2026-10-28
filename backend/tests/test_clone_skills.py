"""The skill store: packs of SKILL.md folders, fetched at a pinned commit and copied into a project for a model to read.

Git is replaced by a function that writes the files a repository would hold; nothing is fetched.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from agentfit.clone import skills
from agentfit.clone.skills import PACK_BY_ID, PACKS, SkillPack, SkillsError, SkillStore

HEADER = '---\nname: {name}\ndescription: >-\n  {description}\n  and a second line.\n---\n\n# {name}\n\n{body}\n'


def git_that_serves(pack: SkillPack, calls: list[list[str]], *, commit: str | None = None, leave_out: tuple[str, ...] = (), body: str = "Use it well. " * 400,
                    headers: dict[str, str] | None = None):
    """A Git that checks out ``pack`` as the repository holds it: its skills, a licence and a README nobody needs.

    Each skill holds what a repository keeps next to the text (``scripts``, ``assets``, ``examples``, ``media``) and an ``assets`` folder inside
    ``references``, which no pack leaves out. ``headers`` gives a skill the name its header says when that is not its folder's.
    """

    def run(args: list[str], cwd: Path | None, timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert cwd is not None
        if args[-3:-1] == ["checkout", "-q"] or "checkout" in args:
            for name in pack.skills:
                if name in leave_out:
                    continue
                folder = cwd / pack.root / name
                (folder / "references" / "assets").mkdir(parents=True)
                (folder / "SKILL.md").write_text(HEADER.format(name=(headers or {}).get(name, name), description=f"How to {name}", body=body), encoding="utf-8")
                (folder / "references" / "more.md").write_text("more", encoding="utf-8")
                (folder / "references" / "assets" / "keep.md").write_text("keep", encoding="utf-8")
                for extra in ("scripts/x.mjs", "assets/a.css", "examples/e.md", "media/m.jpg"):
                    (folder / extra).parent.mkdir(exist_ok=True)
                    (folder / extra).write_text("extra", encoding="utf-8")
            (cwd / pack.root / "other-skill").mkdir(parents=True)
            (cwd / pack.root / "other-skill" / "SKILL.md").write_text("---\nname: other-skill\n---\n", encoding="utf-8")
            (cwd / "LICENSE").write_text("MIT License", encoding="utf-8")
            (cwd / "README.md").write_text("not a skill", encoding="utf-8")
        if args == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, (commit or pack.commit) + "\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    return run


PICTURE_PACKS = ["gsap", "frontend-design", "vercel", "threejs", "playwright"]
DESIGN_PACKS = ["taste", "motionsites", "impeccable"]


def git_that_serves_every_pack(fetched: list[str]):
    """One Git for all the packs: it serves the one whose repository was added last, and ``fetched`` collects the ids in the order they came."""
    served = {p.id: git_that_serves(p, []) for p in PACKS}
    current: list[SkillPack] = []

    def run(args: list[str], cwd: Path | None, timeout: float) -> subprocess.CompletedProcess[str]:
        if args[:3] == ["remote", "add", "origin"]:
            current[:] = [next(p for p in PACKS if args[3] == p.repo + ".git")]
            fetched.append(current[0].id)
        if not current:  # ``git init``, before any repository is named
            return subprocess.CompletedProcess(args, 0, "", "")
        return served[current[0].id](args, cwd, timeout)

    return run


def test_the_packs_are_the_ones_of_the_scripts_and_each_is_pinned_to_a_full_commit():
    assert [p.id for p in PACKS if p.purpose == "picture"] == PICTURE_PACKS
    assert all(re.fullmatch(r"[0-9a-f]{40}", p.commit) for p in PACKS)
    assert len(PACK_BY_ID["gsap"].skills) == 8 and "gsap-scrolltrigger" in PACK_BY_ID["gsap"].skills
    assert PACK_BY_ID["vercel"].skills == ("react-best-practices", "web-design-guidelines")
    assert len(PACK_BY_ID["threejs"].skills) == 11 and PACK_BY_ID["frontend-design"].repo.endswith("/anthropics/skills")
    # The folder is named as the one that is already on the owner's PC for GSAP.
    assert PACK_BY_ID["gsap"].folder_name == "greensock__gsap-skills@aed9cfd32777"


def test_the_design_packs_are_pinned_and_the_picture_packs_are_unchanged():
    assert [p.id for p in PACKS] == PICTURE_PACKS + DESIGN_PACKS
    assert [p.id for p in PACKS if p.purpose == "design"] == DESIGN_PACKS
    # Nothing is left out of a picture pack and each keeps the folder it always had: "From a picture" copies what it copied.
    assert all(p.exclude == () and p.root == "skills" for p in PACKS if p.purpose == "picture")
    taste, motionsites, impeccable = (PACK_BY_ID[i] for i in DESIGN_PACKS)
    assert (taste.owner_repo, taste.root, taste.skills, taste.exclude, taste.license) == (
        "Leonxlnx/taste-skill", "skills", ("taste-skill",), ("scripts", "assets"), "MIT")
    assert (motionsites.owner_repo, motionsites.root, motionsites.skills, motionsites.exclude, motionsites.license) == (
        "Motionsites-AI/claude-skill", "plugins/motionsites-web-builder/skills", ("motionsites-web-builder",), ("examples", "media", "scripts"),
        "MIT (examples and media excluded)")
    assert (impeccable.owner_repo, impeccable.root, impeccable.skills, impeccable.exclude, impeccable.license) == (
        "pbakaus/impeccable", ".claude/skills", ("impeccable",), ("scripts",), "Apache-2.0")
    assert (taste.commit, motionsites.commit, impeccable.commit) == (
        "12af471f627f866411b4bd785b19646d3672784c", "faa30d512e3973e58675c72101a7ef42ed37ae83", "3e3a9179e01a60265000ce239c0a99803ec1e60d")


def test_a_pack_is_fetched_at_its_pinned_commit_keeping_only_its_skills_and_its_licence(tmp_path):
    pack, calls = PACK_BY_ID["vercel"], []
    store = SkillStore(tmp_path, git=git_that_serves(PACK_BY_ID["vercel"], calls))
    folder = store.ensure(pack)
    assert store.ready(pack) and folder.name == pack.folder_name
    assert ["fetch", "--depth", "1", "-q", "origin", pack.commit] in calls
    assert sorted(p.name for p in (folder / "skills").iterdir()) == ["react-best-practices", "web-design-guidelines"]
    assert (folder / "skills/react-best-practices/references/more.md").is_file() and (folder / "LICENSE").read_text(encoding="utf-8") == "MIT License"
    assert not (folder / "README.md").exists() and [p.name for p in tmp_path.iterdir()] == [pack.folder_name]
    before = len(calls)
    store.ensure(pack)
    assert len(calls) == before  # already there: git is not asked again


@pytest.mark.parametrize(("pack_id", "skill", "kept"), [
    ("taste", "taste-skill", ["SKILL.md", "examples", "media", "references"]),
    ("motionsites", "motionsites-web-builder", ["SKILL.md", "assets", "references"]),
    ("impeccable", "impeccable", ["SKILL.md", "assets", "examples", "media", "references"]),
    ("vercel", "react-best-practices", ["SKILL.md", "assets", "examples", "media", "references", "scripts"]),
])
def test_an_excluded_entry_is_not_copied_but_a_nested_one_of_the_same_name_is(tmp_path, pack_id, skill, kept):
    pack = PACK_BY_ID[pack_id]
    folder = SkillStore(tmp_path, git=git_that_serves(pack, [])).ensure(pack)
    top = folder / pack.root / skill
    # Of the entries at the top of the skill only the ones the pack names are left behind; a pack that names none keeps all.
    assert sorted(p.name for p in top.iterdir()) == sorted(kept)
    # The same name one level down is the skill's own text, and the licence of its author is kept.
    assert (top / "references/assets/keep.md").read_text(encoding="utf-8") == "keep" and (top / "references/more.md").is_file()
    assert (folder / "LICENSE").read_text(encoding="utf-8") == "MIT License"


def test_a_pack_that_comes_back_as_another_commit_is_not_used_and_leaves_nothing(tmp_path):
    pack = PACK_BY_ID["gsap"]
    store = SkillStore(tmp_path, git=git_that_serves(pack, [], commit="0" * 40))
    with pytest.raises(SkillsError, match="not the pinned"):
        store.ensure(pack)
    assert not store.ready(pack) and list(tmp_path.iterdir()) == []


def test_a_pack_that_lacks_a_skill_at_that_commit_is_refused(tmp_path):
    pack = PACK_BY_ID["gsap"]
    with pytest.raises(SkillsError, match="gsap-utils"):
        SkillStore(tmp_path, git=git_that_serves(pack, [], leave_out=("gsap-utils",))).ensure(pack)
    assert list(tmp_path.iterdir()) == []


def test_a_failed_fetch_says_why(tmp_path):
    def offline(args, cwd, timeout):
        return subprocess.CompletedProcess(args, 128, "", "fatal: unable to access: Could not resolve host: github.com\n") if args[0] == "fetch" \
            else subprocess.CompletedProcess(args, 0, "", "")

    with pytest.raises(SkillsError, match="Could not resolve host"):
        SkillStore(tmp_path, git=offline).ensure(PACK_BY_ID["gsap"])
    assert list(tmp_path.iterdir()) == []


def test_the_catalog_reads_each_skills_name_and_one_line_description(tmp_path):
    store = SkillStore(tmp_path, git=git_that_serves(PACK_BY_ID["frontend-design"], []))
    assert store.catalog() == []
    store.ensure(PACK_BY_ID["frontend-design"])
    (item,) = store.catalog()
    assert item.name == "frontend-design" and item.description == "How to frontend-design and a second line." and item.pack == "frontend-design"
    assert store.find("frontend-design") is not None and store.find("nope") is None


def test_skills_are_copied_into_the_project_for_the_model_to_read(tmp_path):
    store = SkillStore(tmp_path / "store", git=git_that_serves(PACK_BY_ID["vercel"], []))
    store.ensure(PACK_BY_ID["vercel"])
    project = tmp_path / "project"
    copied = store.copy_into(project, ["web-design-guidelines"])
    assert [s.name for s in copied] == ["web-design-guidelines"]
    assert (project / ".skills/web-design-guidelines/SKILL.md").is_file() and not (project / ".skills/react-best-practices").exists()
    assert len(store.copy_into(project)) == 2


def test_the_catalog_can_be_asked_for_the_skills_of_one_purpose(tmp_path):
    store = SkillStore(tmp_path, git=git_that_serves_every_pack([]))
    store.install(["frontend-design", "impeccable"])
    assert [s.name for s in store.catalog()] == ["frontend-design", "impeccable"]
    assert [s.name for s in store.catalog("picture")] == ["frontend-design"]
    assert [s.name for s in store.catalog("design")] == ["impeccable"]
    # A skill is found by its name whatever it is for.
    found = store.find("impeccable")
    assert found is not None and found.pack == "impeccable"


def test_install_and_copy_into_default_to_the_picture_packs(tmp_path):
    fetched: list[str] = []
    store = SkillStore(tmp_path / "store", git=git_that_serves_every_pack(fetched))
    assert store.install() == PICTURE_PACKS and fetched == PICTURE_PACKS  # five packs, not eight
    assert store.install(["impeccable"]) == ["impeccable"] and fetched[-1] == "impeccable"
    workspace = tmp_path / "workspace"
    copied = store.copy_into(workspace)
    assert {s.pack for s in copied} == set(PICTURE_PACKS) and not (workspace / ".skills/impeccable").exists()
    # Asked for by name, a design skill is copied like any other.
    assert [s.name for s in store.copy_into(workspace, ["impeccable"])] == ["impeccable"]
    assert (workspace / ".skills/impeccable/SKILL.md").is_file()


def test_a_skill_is_asked_for_by_its_header_name_or_by_its_folder_name(tmp_path):
    pack = PACK_BY_ID["taste"]
    store = SkillStore(tmp_path / "store", git=git_that_serves(pack, [], headers={"taste-skill": "taste"}))
    store.ensure(pack)
    for asked in ("taste", "taste-skill"):
        workspace = tmp_path / f"workspace-{asked}"
        assert [s.name for s in store.copy_into(workspace, [asked])] == ["taste"] and (workspace / ".skills/taste/SKILL.md").is_file()
        assert store.install_into(tmp_path / f"project-{asked}", [asked]) == {"copied": ["taste"], "skipped": [], "missing": [], "failed": []}


def test_install_into_copies_to_both_folders_under_the_header_name_and_marks_them(tmp_path):
    pack = PACK_BY_ID["taste"]
    store = SkillStore(tmp_path / "store", git=git_that_serves(pack, [], headers={"taste-skill": "taste"}))
    store.ensure(pack)
    project = tmp_path / "project"
    result = store.install_into(project, ["taste-skill"])
    assert result == {"copied": ["taste"], "skipped": [], "missing": [], "failed": []}
    for folder in (".skills", ".claude/skills"):
        assert (project / folder / "taste/SKILL.md").is_file() and (project / folder / "taste/references/assets/keep.md").is_file()
        assert not (project / folder / "taste-skill").exists()
    assert (project / ".claude/skills/taste" / skills.MARKER).is_file()


def test_a_skill_this_program_copied_before_is_replaced_by_the_next_copy(tmp_path):
    pack = PACK_BY_ID["taste"]
    store = SkillStore(tmp_path / "store", git=git_that_serves(pack, []))
    store.ensure(pack)
    project = tmp_path / "project"
    store.install_into(project, ["taste-skill"])
    copy = project / ".claude/skills/taste-skill"
    (copy / "SKILL.md").write_text("edited", encoding="utf-8")
    (copy / "stray.md").write_text("stray", encoding="utf-8")
    assert store.install_into(project, ["taste-skill"]) == {"copied": ["taste-skill"], "skipped": [], "missing": [], "failed": []}
    assert "How to taste-skill" in (copy / "SKILL.md").read_text(encoding="utf-8")
    assert not (copy / "stray.md").exists() and (copy / skills.MARKER).is_file()


def test_a_copy_that_fails_halfway_leaves_nothing_to_be_taken_for_the_persons_own(tmp_path, monkeypatch):
    pack = PACK_BY_ID["taste"]
    store = SkillStore(tmp_path / "store", git=git_that_serves(pack, []))
    store.ensure(pack)
    project = tmp_path / "project"
    real_copytree = shutil.copytree

    def fails_after_writing(src, dst, *args, **kwargs):
        real_copytree(src, dst, *args, **kwargs)
        raise shutil.Error([(str(src), str(dst), "the path is too long")])

    monkeypatch.setattr(shutil, "copytree", fails_after_writing)
    result = store.install_into(project, ["taste-skill"], folders=(".claude/skills",))
    # The error is reported, not raised, and what was written of the skill is gone.
    assert result["copied"] == [] and len(result["failed"]) == 1 and result["failed"][0].startswith("taste-skill: ") and "too long" in result["failed"][0]
    assert not (project / ".claude/skills/taste-skill").exists()
    monkeypatch.undo()
    assert store.install_into(project, ["taste-skill"], folders=(".claude/skills",)) == {"copied": ["taste-skill"], "skipped": [], "missing": [], "failed": []}


def test_a_claude_skills_folder_the_person_made_is_left_alone_and_reported(tmp_path):
    pack = PACK_BY_ID["taste"]
    store = SkillStore(tmp_path / "store", git=git_that_serves(pack, []))
    store.ensure(pack)
    project = tmp_path / "project"
    own = project / ".claude/skills/taste-skill"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("my own skill", encoding="utf-8")
    result = store.install_into(project, ["taste-skill"])
    assert result == {"copied": [], "skipped": ["taste-skill"], "missing": [], "failed": []}
    # Nothing of the person's folder changed: not its text, not what is in it.
    assert [p.name for p in own.iterdir()] == ["SKILL.md"] and (own / "SKILL.md").read_text(encoding="utf-8") == "my own skill"
    # The folder the program keeps for itself is still written.
    assert (project / ".skills/taste-skill/SKILL.md").is_file()


def test_a_skill_that_is_not_installed_is_reported_missing(tmp_path):
    pack = PACK_BY_ID["taste"]
    store = SkillStore(tmp_path / "store", git=git_that_serves(pack, []))
    store.ensure(pack)  # only this pack is on the PC
    project = tmp_path / "project"
    result = store.install_into(project, ["impeccable", "taste-skill", "nope"])
    assert result == {"copied": ["taste-skill"], "skipped": [], "missing": ["impeccable", "nope"], "failed": []}
    assert not (project / ".claude/skills/impeccable").exists() and not (project / ".skills/impeccable").exists()


GUIDE = """---
name: guide
description: A guide.
---

# Guide

Apply it when you release a page. It talks about eases, and a release is not one.

## Easing

- **duration**: seconds (default 0.5).
- **ease**: a string. Prefer built-in: `"power1.out"`, and
  `"power3.out"` for an entrance.
- **repeat**: a number.

```javascript
ease: "power1.out"   // the ease of a code block is not guidance
```

| ease | strength |
|------|----------|
| power1 | gradual |

Motion is used sparingly. One orchestrated moment lands better than scattered effects.

Another paragraph about something else.
"""


def test_the_sentences_of_a_skill_about_a_choice_come_one_to_a_line_without_headings_tables_or_code():
    text = skills.pick(GUIDE, ("ease", "non-existent"), 2000)
    # A bullet is a unit with its continuation lines; "release" is not "ease"; the code and the table are not guidance.
    assert text.splitlines() == ['Apply it when you release a page. It talks about eases, and a release is not one.',
                                 '- **ease**: a string. Prefer built-in: `"power1.out"`, and `"power3.out"` for an entrance.']
    assert skills.pick(GUIDE, ("motion",), 2000) == "Motion is used sparingly. One orchestrated moment lands better than scattered effects."
    assert skills.pick(GUIDE, (), 2000) == "" and skills.pick(GUIDE, ("zebra",), 2000) == ""
    assert "name: guide" not in skills.pick(GUIDE, ("guide",), 2000)


def test_an_excerpt_is_whole_units_that_fit_the_room_there_is(tmp_path):
    first, second = "- **duration**: seconds (default 0.5).", "- **ease**: a string. Prefer built-in: `\"power1.out\"`, and `\"power3.out\"` for an entrance."
    both = skills.pick(GUIDE, ("duration", "built-in"), 2000)
    assert both.splitlines() == [first, second]
    # The first unit that does not fit ends the excerpt, and nothing is cut in the middle.
    assert skills.pick(GUIDE, ("duration", "built-in"), len(first) + 2) == first
    assert skills.pick(GUIDE, ("duration",), 5) == ""


def test_a_stored_skill_gives_its_excerpt_and_a_missing_one_gives_nothing(tmp_path):
    store = SkillStore(tmp_path, git=git_that_serves(PACK_BY_ID["frontend-design"], [], body="Use it well.\n\nThen stop.\n"))
    store.ensure(PACK_BY_ID["frontend-design"])
    assert store.excerpt("frontend-design", ("well", "stop"), 100) == "Use it well.\nThen stop."
    assert store.excerpt("frontend-design", ("zebra",), 100) == "" and store.excerpt("missing", ("well",), 100) == ""


def test_status_says_which_packs_are_on_this_pc(tmp_path):
    store = SkillStore(tmp_path, git=git_that_serves(PACK_BY_ID["gsap"], []))
    assert store.status()["ready"] is False and not any(p["ready"] for p in store.status()["packs"])
    store.ensure(PACK_BY_ID["gsap"])
    packs = {p["id"]: p for p in store.status()["packs"]}
    assert packs["gsap"]["ready"] is True and len(packs["gsap"]["skills"]) == 8 and packs["gsap"]["skills"][0]["description"]
    assert packs["threejs"]["ready"] is False and packs["threejs"]["license"] == "MIT"


def test_status_names_the_purpose_of_each_pack_and_keeps_the_two_kinds_apart(tmp_path):
    store = SkillStore(tmp_path, git=git_that_serves_every_pack([]))
    assert (store.status()["ready"], store.status()["design_ready"]) == (False, False)
    store.install(["taste", "impeccable"])
    assert store.status()["design_ready"] is False  # MotionSites is not on the PC yet
    store.install(["motionsites"])
    status = store.status()
    assert {p["id"]: p["purpose"] for p in status["packs"]} == {**dict.fromkeys(PICTURE_PACKS, "picture"), **dict.fromkeys(DESIGN_PACKS, "design")}
    # "ready" is about the packs of "From a picture": the design packs alone do not make it true.
    assert status["design_ready"] is True and status["ready"] is False
    store.install()
    assert store.status()["ready"] is True


def test_the_install_runs_in_the_background_and_reports_its_end(tmp_path):
    served = {p.id: git_that_serves(p, []) for p in PACKS}

    def git(args, cwd, timeout):
        # One runner for every pack: the one whose repository was added is the one served.
        if args[:3] == ["remote", "add", "origin"]:
            git.current = next(p for p in PACKS if args[3].startswith(p.repo))
        return served[git.current.id](args, cwd, timeout)

    git.current = PACKS[0]
    store = SkillStore(tmp_path, git=git)
    store.install_in_background(["gsap", "playwright"])
    for _ in range(200):
        if (store.status()["job"] or {}).get("state") != "running":
            break
        time.sleep(0.05)
    job = store.status()["job"]
    assert job["state"] == "done" and job["error"] is None and any("is ready" in line for line in job["lines"])
    assert store.ready(PACK_BY_ID["gsap"]) and store.ready(PACK_BY_ID["playwright"]) and not store.ready(PACK_BY_ID["vercel"])


def test_install_into_reports_an_over_long_path_and_goes_on_with_the_other_skills(tmp_path, monkeypatch):
    store = SkillStore(tmp_path / "store", git=git_that_serves_every_pack([]))
    store.install(["taste", "playwright"])
    real_copytree = shutil.copytree

    def too_long_for_taste(src, dst, *args, **kwargs):
        if "taste-skill" in str(dst):
            raise FileNotFoundError(3, "The system cannot find the path specified", str(dst))
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copytree", too_long_for_taste)
    result = store.install_into(tmp_path / "project", ["taste-skill", "playwright-cli"])
    assert result["copied"] == ["playwright-cli"] and result["failed"][0].startswith("taste-skill: ")
    assert not (tmp_path / "project/.claude/skills/taste-skill").exists()


def test_a_header_name_that_is_not_a_folder_name_is_never_used_as_a_path(tmp_path):
    pack = PACK_BY_ID["taste"]
    store = SkillStore(tmp_path / "store", git=git_that_serves(pack, [], headers={"taste-skill": "../../outside"}))
    store.ensure(pack)
    victim = tmp_path / "outside"
    victim.mkdir()
    (victim / "keep.txt").write_text("keep", encoding="utf-8")
    result = store.install_into(tmp_path / "a" / "b" / "project", ["taste-skill"])
    assert result["copied"] == [] and "not a folder name" in result["failed"][0] and (victim / "keep.txt").is_file()


def test_both_names_of_a_skill_list_it_once(tmp_path):
    pack = PACK_BY_ID["taste"]
    store = SkillStore(tmp_path / "store", git=git_that_serves(pack, [], headers={"taste-skill": "taste"}))
    store.ensure(pack)
    result = store.install_into(tmp_path / "project", ["taste", "taste-skill", "taste", "nope", "nope"])
    assert result == {"copied": ["taste"], "skipped": [], "missing": ["nope"], "failed": []}


def test_the_marker_goes_last_when_a_copy_of_ours_is_removed(tmp_path, monkeypatch):
    folder = tmp_path / "skill"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "a.md").write_text("a", encoding="utf-8")
    (folder / skills.MARKER).write_text("ours", encoding="utf-8")
    removed: list[str] = []
    real_remove = skills.remove_tree

    def remove_and_stop(path):
        removed.append(path.name)
        raise OSError("stopped")

    monkeypatch.setattr(skills, "remove_tree", remove_and_stop)
    with pytest.raises(OSError, match="stopped"):
        skills.remove_ours(folder)
    assert (folder / skills.MARKER).is_file()  # stopped before the marker: the folder is still known for ours
    monkeypatch.setattr(skills, "remove_tree", real_remove)
    skills.remove_ours(folder)
    assert not folder.exists()


def test_the_marker_names_the_repository_the_licence_and_the_credit_and_speaks_the_language(tmp_path):
    store = SkillStore(tmp_path / "store", git=git_that_serves_every_pack([]))
    store.install(["impeccable"])
    project = tmp_path / "project"
    store.install_into(project, ["impeccable"])
    text = (project / ".claude/skills/impeccable" / skills.MARKER).read_text(encoding="utf-8")
    assert "https://github.com/pbakaus/impeccable" in text and "Apache-2.0" in text and "ehmo/platform-design-skills (MIT)" in text
    from agentfit.i18n import set_default_language

    set_default_language("it")
    try:
        store.install_into(project, ["impeccable"])
        assert "Cancella questo file" in (project / ".claude/skills/impeccable" / skills.MARKER).read_text(encoding="utf-8")
    finally:
        set_default_language("en")


def test_the_notice_files_of_a_pack_are_kept_next_to_its_licence(tmp_path):
    pack = PACK_BY_ID["impeccable"]
    serve = git_that_serves(pack, [])

    def git(args, cwd, timeout):
        done = serve(args, cwd, timeout)
        if "checkout" in args:
            (cwd / "NOTICE.md").write_text("credits ehmo", encoding="utf-8")
        return done

    folder = SkillStore(tmp_path, git=git).ensure(pack)
    assert (folder / "LICENSE").is_file() and (folder / "NOTICE.md").read_text(encoding="utf-8") == "credits ehmo"


def test_a_skill_of_a_picture_pack_is_copied_like_one_of_a_design_pack(tmp_path):
    store = SkillStore(tmp_path / "store", git=git_that_serves_every_pack([]))
    store.install(["playwright", "motionsites"])
    result = store.install_into(tmp_path / "project", ["playwright-cli", "motionsites-web-builder"])
    assert result["copied"] == ["playwright-cli", "motionsites-web-builder"]
    assert (tmp_path / "project/.claude/skills/playwright-cli/SKILL.md").is_file()
