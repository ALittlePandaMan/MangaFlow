import pytest
from app.utils.text import has_text


@pytest.mark.parametrize("value", [None, 12, "", " \n\t　", "\u200b\ufeff\u200d", "\x00\x07", "\u0301\ufe0f"])
def test_invisible_only_values_are_not_text(value):
    assert not has_text(value)


@pytest.mark.parametrize("value", ["...", "~~~", "？", "0", "原文", "a\u200db", "👩\u200d💻", "สวัสดี"])
def test_punctuation_and_real_text_with_joiners_remain_text(value):
    assert has_text(value)
