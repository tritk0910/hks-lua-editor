"""Tests for writer.py — targeted splice into a .lua file (via tmp copies)."""

import os
import shutil

import pytest

import writer
from models import Branch, ComboSequence, ComboStep, randam
from parser import parse_file, iter_function_spans

REF = os.path.join(os.path.dirname(os.path.dirname(__file__)), "710300_battle.lua")


@pytest.fixture(scope="module")
def text():
    with open(REF, encoding="utf-8", errors="ignore") as f:
        return f.read()


def _seq(parsed, ttype, tid):
    return next(s for s in parsed.sequences
               if s.trigger_type == ttype and s.trigger_id == tid)


def _count_funcs(t):
    return len(list(iter_function_spans(t)))


def test_replace_existing_function(text):
    parsed = parse_file(text)
    act04 = _seq(parsed, "act_entry", 4)
    act04.steps[0].anim_id = 9001            # change something visible
    new, summary = writer.apply_sequence(text, act04)
    assert summary == ["Replace Goal.Act04"]
    assert _count_funcs(new) == _count_funcs(text)    # no function added/removed
    reparsed = parse_file(new)
    assert _seq(reparsed, "act_entry", 4).steps[0].anim_id == 9001


def test_insert_new_function(text):
    seq = ComboSequence(name="new", trigger_type="act_entry", trigger_id=77,
                        steps=[ComboStep("ComboFinal", 3999, 10, extra_args=[0, 0])])
    new, summary = writer.apply_sequence(text, seq)
    assert summary == ["Insert Goal.Act77"]
    assert _count_funcs(new) == _count_funcs(text) + 1
    reparsed = parse_file(new)
    assert _seq(reparsed, "act_entry", 77).steps[0].anim_id == 3999


def test_insert_new_kengeki_move(text):
    seq = ComboSequence(name="k", trigger_type="kengeki_move", trigger_id=88,
                        steps=[ComboStep("ComboFinal", 3050, 10, extra_args=[0, 0])])
    new, _ = writer.apply_sequence(text, seq)
    assert "Goal.Kengeki88 = function" in new
    assert _seq(parse_file(new), "kengeki_move", 88).steps[0].anim_id == 3050


def test_registration_added_once(text):
    seq = ComboSequence(name="i", trigger_type="special_effect", trigger_id=987654,
                        steps=[Branch(terms=[randam(50)],
                                      true_branch=[ComboStep("ComboFinal", 3049, 10, extra_args=[0])],
                                      false_branch=[ComboStep("ComboFinal", 3041, 10, extra_args=[0])])])
    new, summary = writer.apply_sequence(text, seq)
    assert any("Add registration for 987654" in s for s in summary)
    assert new.count("AddObserveSpecialEffectAttribute(TARGET_SELF, 987654)") == 1
    # applying again must NOT add a second registration line
    again, summary2 = writer.apply_sequence(new, seq)
    assert not any("Add registration" in s for s in summary2)
    assert again.count("AddObserveSpecialEffectAttribute(TARGET_SELF, 987654)") == 1


def test_interrupt_insert_new_branch(text):
    seq = ComboSequence(name="i", trigger_type="special_effect", trigger_id=987654,
                        steps=[ComboStep("ComboFinal", 3049, 10, extra_args=[0])])
    new, summary = writer.apply_sequence(text, seq)
    assert any("Insert interrupt branch 987654" in s for s in summary)
    assert "elseif interruptEffectIdentifier == 987654 then" in new
    assert _seq(parse_file(new), "special_effect", 987654).steps[0].anim_id == 3049


def test_interrupt_replace_existing_branch(text):
    # 5031 already exists in the reference; writing it must replace, not add
    before = text.count("elseif interruptEffectIdentifier == 5031 then")
    seq = ComboSequence(name="i", trigger_type="special_effect", trigger_id=5031,
                        steps=[ComboStep("ComboFinal", 3049, 10, extra_args=[0])])
    new, summary = writer.apply_sequence(text, seq)
    assert any("Replace interrupt branch 5031" in s for s in summary)
    assert new.count("elseif interruptEffectIdentifier == 5031 then") == before


def test_write_file_makes_backup(text, tmp_path):
    target = tmp_path / "combat.lua"
    shutil.copy2(REF, target)
    original = target.read_text(encoding="utf-8", errors="ignore")
    backup = writer.write_file(str(target), "NEW CONTENT", backup=True)
    assert backup == str(target) + ".bak"
    assert os.path.exists(backup)
    assert open(backup, encoding="utf-8", errors="ignore").read() == original
    assert target.read_text(encoding="utf-8") == "NEW CONTENT"


def test_write_file_never_clobbers_backup(tmp_path):
    target = tmp_path / "combat.lua"
    target.write_text("V1", encoding="utf-8")
    b1 = writer.write_file(str(target), "V2")          # backup .bak has V1
    b2 = writer.write_file(str(target), "V3")          # must NOT overwrite .bak
    assert b1 != b2
    assert open(b1, encoding="utf-8").read() == "V1"   # first backup preserved
    assert open(b2, encoding="utf-8").read() == "V2"
    assert target.read_text(encoding="utf-8") == "V3"
