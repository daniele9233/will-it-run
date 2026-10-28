"""The skill store: packs of SKILL.md folders, fetched at a pinned commit and copied into a project for a model to read.

Git is replaced by a function that writes the files a repository would hold; nothing is fetched.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

import pytest

from agentfit.clone import skills
from agentfit.clone.skills import PACK_BY_ID, PACKS, SkillPack, SkillsError, SkillStore

HEADER = '---\nname: {name}\ndescription: >-\n  {description}\n  and a second line.\n---\n\n# {name}\n\n{body}\n'


def git_that_serves(pack: SkillPack, calls: list[list[str]], *, commit: str | None = None, leave_out: tuple[str, ...] = (), body: str = "Use it well. " * 400):
    """A Git that checks out ``pack`` as the repository holds it: its skills, a licence and a README nobody needs."""

    def run(args: list[str], cwd: Path | None, timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert cwd is not None
        if args[-3:-1] == ["checkout", "-q"] or "checkout" in args:
            for name in pack.skills:
                if name in leave_out:
                    continue
                folder = cwd / pack.root / name
                (folder / "references").mkdir(parents=True)
                (folder / "SKILL.md").write_text(HEADER.format(name=name, description=f"How to {name}", body=body), encoding="utf-8")
                (folder / "references" / "more.md").write_text("more", encoding="utf-8")
            (cwd / pack.root / "other-skill").mkdir(parents=True)
            (cwd / pack.root / "other-skill" / "SKILL.md").write_text("---\nname: other-skill\n---\n", encoding="utf-8")
            (cwd / "LICENSE").write_text("MIT License", encoding="utf-8")
            (cwd / "README.md").write_text("not a skill", encoding="utf-8")
        if args == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, (commit or pack.commit) + "\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    return run


def test_the_packs_are_the_ones_of_the_scripts_and_each_is_pinned_to_a_full_commit():
    assert [p.id for p in PACKS] == ["gsap", "frontend-design", "vercel", "threejs", "playwright"]
    assert all(re.fullmatch(r"[0-9a-f]{40}", p.commit) for p in PACKS)
    assert len(PACK_BY_ID["gsap"].skills) == 8 and "gsap-scrolltrigger" in PACK_BY_ID["gsap"].skills
    assert PACK_BY_ID["vercel"].skills == ("react-best-practices", "web-design-guidelines")
    assert len(PACK_BY_ID["threejs"].skills) == 11 and PACK_BY_ID["frontend-design"].repo.endswith("/anthropics/skills")
    # The folder is named as the one that is already on the owner's PC for GSAP.
    assert PACK_BY_ID["gsap"].folder_name == "greensock__gsap-skills@aed9cfd32777"


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
