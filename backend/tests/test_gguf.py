"""Reading the MTP marker from the header of a GGUF file."""

from __future__ import annotations

import pytest

from agentfit.lmstudio.gguf import has_mtp_head
from fakes import write_gguf


def test_a_file_with_a_next_token_head_is_recognised(tmp_path):
    assert has_mtp_head(write_gguf(tmp_path / "m-mtp.gguf", mtp_layers=1)) is True


def test_a_plain_file_and_a_head_of_zero_layers_are_not(tmp_path):
    assert has_mtp_head(write_gguf(tmp_path / "plain.gguf")) is False
    assert has_mtp_head(write_gguf(tmp_path / "zero.gguf", mtp_layers=0)) is False


def test_the_key_is_found_whatever_the_architecture_is_called(tmp_path):
    path = write_gguf(tmp_path / "other.gguf", mtp_layers=2)
    path.write_bytes(path.read_bytes().replace(b"qwen35.nextn_predict_layers", b"gemma4.nextn_predict_layers"))
    assert has_mtp_head(path) is True


@pytest.mark.parametrize("content", [b"", b"GGUF", b"not a gguf file at all, just text" * 4, b"GGUF" + b"\xff" * 40])
def test_anything_that_is_not_a_readable_gguf_is_simply_not_one_with_a_head(tmp_path, content):
    path = tmp_path / "junk.gguf"
    path.write_bytes(content)
    assert has_mtp_head(path) is False


def test_a_file_cut_in_the_middle_of_its_metadata_and_a_missing_file_are_not_a_problem(tmp_path):
    whole = write_gguf(tmp_path / "whole.gguf", mtp_layers=1).read_bytes()
    cut = tmp_path / "cut.gguf"
    cut.write_bytes(whole[:60])
    assert has_mtp_head(cut) is False
    assert has_mtp_head(tmp_path / "missing.gguf") is False


def test_an_old_format_version_is_not_guessed_at(tmp_path):
    assert has_mtp_head(write_gguf(tmp_path / "v1.gguf", mtp_layers=1, version=1)) is False
