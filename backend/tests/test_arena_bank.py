"""The question bank, held to account: every question passes its own reference answer, fails its wrong ones, and the
packs hold exactly the number of questions the plan promised. The hash of the whole suite is pinned: change a question
and this file stops you until you also change the version."""

from __future__ import annotations

import json

import pytest

from agentfit.arena import SUITE_VERSION
from agentfit.arena.bank import kit
from agentfit.arena.categories import CATEGORIES, PRESETS
from agentfit.arena.suite import CANONICAL_SEED, build_suite
from agentfit.arena.toolprobe import Tools
from agentfit.arena.verifiers import VERIFIERS, Context, verify

# The hash of the complete suite built from the canonical seed. It changes whenever any question, tool, picture, PDF or
# check changes: when it does, bump SUITE_VERSION (results of two versions are not comparable) and paste the new value.
PINNED_HASH = "4b2cfe25299a60902c913aea277cd64df018b0a28b92dcc740a7160916f85b08"
PINNED_VERSION = "1.0.0"

SUITE = build_suite(CANONICAL_SEED, preset="full")
CONTEXT = Context(tools=Tools())


def ids(questions):
    return [q.id for q in questions]


def test_the_suite_has_the_questions_of_every_pack_in_every_category():
    for name, tier in PRESETS.items():
        pack = [q for q in SUITE.questions if q.tier <= tier]
        for category, info in CATEGORIES.items():
            wanted = info.counts[tier - 1]
            got = sum(1 for q in pack if q.category == category)
            assert got == wanted, f"{category} has {got} questions in the {name} pack, the plan says {wanted}"
    assert [sum(1 for q in SUITE.questions if q.tier <= t) for t in (1, 2, 3)] == [48, 115, 192]


def test_ids_are_unique_and_name_their_category():
    assert len(set(ids(SUITE.questions))) == len(SUITE.questions)
    for q in SUITE.questions:
        assert q.id.startswith(q.category + ".") and q.category in CATEGORIES


def test_every_question_is_well_formed():
    for q in SUITE.questions:
        assert q.prompt.strip() and q.title.strip() and q.expected.strip(), q.id
        assert q.check["type"] in VERIFIERS, q.id
        assert q.max_tokens >= 100 and 1 <= q.tier <= 3 and 1 <= q.difficulty <= 3, q.id
        assert q.lang in ("it", "en"), q.id
        if q.category == "vision":
            assert q.images and "vision" in q.needs, q.id
        else:
            assert not q.images, q.id
        if q.check["type"] == "toolcall":
            assert q.tools, q.id
        if q.category != "writing":
            assert q.wrong, f"{q.id} has no wrong answer to test its verifier with"


@pytest.mark.parametrize("question", SUITE.questions, ids=ids(SUITE.questions))
def test_the_reference_answer_passes(question):
    verdict = verify(question, kit.scripted(question.reference), CONTEXT)
    if question.category == "writing":
        assert verdict.status == "unscored" and verdict.kind == "manual"
        return
    assert verdict.status == "passed", [c.reason.render() for c in verdict.checks if not c.ok] or verdict.reason.render()


@pytest.mark.parametrize("question", [q for q in SUITE.questions if q.category != "writing"], ids=[q.id for q in SUITE.questions if q.category != "writing"])
def test_every_wrong_answer_is_not_a_pass(question):
    for entry in question.wrong:
        verdict = verify(question, kit.scripted(entry), CONTEXT)
        assert verdict.status in ("failed", "partial"), f"{question.id}: {entry[:80]!r} was judged {verdict.status}"
        assert verdict.score is not None and verdict.score < 1.0


def test_two_builds_of_the_same_seed_are_the_same_suite():
    again = build_suite(CANONICAL_SEED, preset="full")
    assert again.hash == SUITE.hash and again.bank_hash == SUITE.bank_hash
    assert [q.fingerprint() for q in again.questions] == [q.fingerprint() for q in SUITE.questions]


def test_another_seed_changes_the_generated_questions_but_not_the_fixed_ones():
    other = build_suite(CANONICAL_SEED + 1, preset="full")
    assert other.bank_hash != SUITE.bank_hash
    mine = {q.id: q.fingerprint() for q in SUITE.questions}
    differing = {q.id.split(".")[0] for q in other.questions if q.fingerprint() != mine[q.id]}
    assert {"math", "logic"} <= differing
    # A fixed question does not depend on the seed.
    fixed = next(q for q in SUITE.questions if q.category == "general")
    assert next(q for q in other.questions if q.id == fixed.id).fingerprint() == fixed.fingerprint()


@pytest.mark.parametrize("seed", [1, 7, 99, 2024, 31337])
def test_the_questions_a_seed_generates_pass_their_reference_and_fail_their_wrong_answers(seed):
    """A generator that is only right for the canonical seed (a wrong answer that happens to be the right one, a number
    that cannot be shown, a picture that is ambiguous) is found here, on seeds nobody pinned."""
    base = {q.id: q.fingerprint() for q in SUITE.questions}
    generated = [q for q in build_suite(seed, preset="full").questions if q.fingerprint() != base[q.id]]
    assert len(generated) > 80
    problems = []
    for q in generated:
        verdict = verify(q, kit.scripted(q.reference), CONTEXT)
        expected = "unscored" if q.category == "writing" else "passed"
        if verdict.status != expected:
            problems.append(f"{q.id}: the reference was judged {verdict.status}")
        for entry in q.wrong:
            if verify(q, kit.scripted(entry), CONTEXT).status not in ("failed", "partial"):
                problems.append(f"{q.id}: a wrong answer passes: {entry[:70]!r}")
    assert not problems, "\n".join(problems)


def test_the_packs_are_nested_and_a_category_can_be_chosen():
    quick, standard = build_suite(CANONICAL_SEED, preset="quick"), build_suite(CANONICAL_SEED, preset="standard")
    assert set(ids(quick.questions)) < set(ids(standard.questions)) < set(ids(SUITE.questions))
    only = build_suite(CANONICAL_SEED, preset="full", categories=["math", "sql"])
    assert {q.category for q in only.questions} == {"math", "sql"}
    assert only.bank_hash == SUITE.bank_hash  # the bank is the same, only the selection differs
    assert only.hash != SUITE.hash


def test_the_suite_is_pinned_by_a_hash():
    assert SUITE_VERSION == PINNED_VERSION
    assert SUITE.bank_hash == PINNED_HASH, f"the bank changed: if that was meant, bump SUITE_VERSION and pin {SUITE.bank_hash}"


def test_the_suite_is_json_friendly_and_small_enough_to_store():
    payload = json.dumps([q.to_json() for q in SUITE.questions])
    assert len(payload) < 3_000_000
