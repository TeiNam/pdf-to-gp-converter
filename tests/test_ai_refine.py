"""AI 보정 계층 테스트 — 네트워크도 PDF 도 쓰지 않는다.

`corrections` 는 AI 가 IR 을 망치지 못하게 막는 유일한 관문이라, 통과·폐기 양쪽을
모두 확인한다. 실제 모델 호출은 `ask` 를 주입해 대체한다.
"""

import copy

import guitarpro as gp
import pytest

from tab_pdf import ai, build, chords, corrections, refine, smufl

STANDARD_TUNING = [64, 59, 55, 50, 45, 40]


def _beat(notes=(), *, lyric=None, chord=None, stroke=None, techniques=None,
          from_chord=False):
    return {"x": 0.0, "duration": 8, "dotted": False, "chord": chord,
            "from_chord": from_chord, "stroke": stroke,
            "techniques": list(techniques or ()), "lyric": lyric,
            "notes": [{"string": s, "fret": f} for s, f in notes]}


def _measure(index, beats, *, chord_row=None, chord_in_effect=None):
    measure = {"index": index, "time_sig": [4, 4], "kind": "fret",
               "beats": beats, "glyphs": []}
    if chord_row is not None or chord_in_effect is not None:
        measure["chord_row"] = [{"x": 0.0, "name": n} for n in (chord_row or ())]
        measure["chord_in_effect"] = chord_in_effect
    return measure


def _ir(*measures, **extra):
    return {"title": "곡", "artist": "", "tempo": 80,
            "tuning": list(STANDARD_TUNING), "measures": list(measures),
            "warnings": [], **extra}


def _reasons(outcome):
    return [entry["reason"] for entry in outcome.rejected]


# ── 연주법 ───────────────────────────────────────────────────────────────────

def test_technique_lands_on_the_named_string():
    ir = _ir(_measure(0, [_beat([(2, 3), (5, 0)])]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "technique", "measure": 0, "beat": 0, "string": 2, "kind": "bend"}])

    assert len(outcome.applied) == 1, _reasons(outcome)
    assert result["measures"][0]["beats"][0]["techniques"] == [
        {"string": 2, "kind": "bend"}]


def test_technique_without_a_string_covers_the_whole_beat(tmp_path):
    """악센트는 한 줄이 아니라 그 박 전체에 붙는 표기다.

    줄을 요구하면 모델이 근거 없이 하나를 고르고, 스트럼 화음 6음 중 1음만
    악센트가 된다 — 실측에서 44개 악센트가 1·3·4·5·6번줄로 흩어졌다.
    """
    ir = _ir(_measure(0, [_beat([(6, 3), (5, 2), (4, 0), (1, 3)])]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "technique", "measure": 0, "beat": 0, "kind": "accent"}])

    assert len(outcome.applied) == 1, _reasons(outcome)
    assert result["measures"][0]["beats"][0]["techniques"] == [
        {"string": None, "kind": "accent"}]

    out = tmp_path / "accent.gp5"
    build.write_gp5(build.build_song(result), str(out))
    song = gp.parse(str(out), encoding="cp949")
    notes = song.tracks[0].measures[0].voices[0].beats[0].notes
    assert len(notes) == 4
    assert all(n.effect.accentuatedNote for n in notes), "일부 음만 악센트가 됐다"


@pytest.mark.parametrize("kind", sorted(corrections.STRING_TECHNIQUES))
def test_single_string_techniques_must_name_a_string(kind):
    """벤드·해머·슬라이드를 박 전체에 걸면 스트럼 6음을 다 벤딩하는 악보가 된다."""
    ir = _ir(_measure(0, [_beat([(6, 3), (2, 5)])]))
    _, outcome = corrections.apply_corrections(ir, [
        {"op": "technique", "measure": 0, "beat": 0, "kind": kind}])

    assert not outcome.applied, f"{kind} 가 string 없이 통과했다"
    assert "한 줄에만 걸리는 표기" in _reasons(outcome)[0]


@pytest.mark.parametrize("kind", sorted(corrections.BEAT_TECHNIQUES))
def test_beat_wide_techniques_may_omit_the_string(kind):
    ir = _ir(_measure(0, [_beat([(6, 3), (2, 5)])]))
    _, outcome = corrections.apply_corrections(ir, [
        {"op": "technique", "measure": 0, "beat": 0, "kind": kind}])

    assert len(outcome.applied) == 1, _reasons(outcome)


@pytest.mark.parametrize("kind", sorted(corrections.BEAT_ONLY_TECHNIQUES))
def test_staff_level_articulations_refuse_a_guessed_string(kind):
    """악센트는 대상 줄이라는 개념이 없다 — 받아주면 모델이 하나를 짐작한다."""
    ir = _ir(_measure(0, [_beat([(6, 3), (2, 5)])]))
    _, outcome = corrections.apply_corrections(ir, [
        {"op": "technique", "measure": 0, "beat": 0, "string": 2, "kind": kind}])

    assert not outcome.applied
    assert "박 단위 표기" in _reasons(outcome)[0]


@pytest.mark.parametrize("kind",
                         sorted(corrections.BEAT_TECHNIQUES
                                - corrections.BEAT_ONLY_TECHNIQUES))
def test_per_note_techniques_still_accept_a_string(kind):
    """한 줄 뮤트('x')나 한 음 스타카토는 실제로 쓰이는 표기다."""
    ir = _ir(_measure(0, [_beat([(6, 3), (2, 5)])]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "technique", "measure": 0, "beat": 0, "string": 6, "kind": kind}])

    assert len(outcome.applied) == 1, _reasons(outcome)
    assert result["measures"][0]["beats"][0]["techniques"] == [
        {"string": 6, "kind": kind}]


def test_technique_on_an_empty_beat_is_rejected():
    ir = _ir(_measure(0, [_beat()]))
    _, outcome = corrections.apply_corrections(ir, [
        {"op": "technique", "measure": 0, "beat": 0, "kind": "accent"}])

    assert not outcome.applied
    assert "음이 없어" in _reasons(outcome)[0]


def test_beat_wide_and_per_string_techniques_coexist():
    ir = _ir(_measure(0, [_beat([(2, 3), (5, 0)])]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "technique", "measure": 0, "beat": 0, "kind": "accent"},
        {"op": "technique", "measure": 0, "beat": 0, "kind": "accent"},
        {"op": "technique", "measure": 0, "beat": 0, "string": 2,
         "kind": "hammer"}])

    assert len(outcome.applied) == 2, _reasons(outcome)
    assert "beat 전체에 accent 가 이미 있다" in _reasons(outcome)[0]
    assert result["measures"][0]["beats"][0]["techniques"] == [
        {"string": None, "kind": "accent"}, {"string": 2, "kind": "hammer"}]


def test_technique_rejected_when_the_string_has_no_note():
    """AI 가 없는 줄을 짚으면 버린다 — 노트 없는 연주법은 GP 에서 사라진다."""
    ir = _ir(_measure(0, [_beat([(2, 3)])]))
    _, outcome = corrections.apply_corrections(ir, [
        {"op": "technique", "measure": 0, "beat": 0, "string": 6, "kind": "bend"}])

    assert not outcome.applied
    assert "6" in _reasons(outcome)[0]


@pytest.mark.parametrize("correction, hint", [
    ({"op": "technique", "measure": 0, "beat": 0, "string": 2, "kind": "trill"},
     "GP5"),
    ({"op": "technique", "measure": 9, "beat": 0, "string": 2, "kind": "bend"},
     "마디"),
    ({"op": "technique", "measure": 0, "beat": 7, "string": 2, "kind": "bend"},
     "beat"),
    ({"op": "technique", "measure": 0, "beat": "0", "string": 2, "kind": "bend"},
     "정수"),
    # 아래 셋은 조회에 쓰기 전에 타입을 검사하지 않으면 TypeError 로 변환 전체가 죽는다
    ({"op": "technique", "measure": [], "beat": 0, "string": 2, "kind": "bend"},
     "measure 이 정수가 아니다"),
    ({"op": "technique", "measure": 0, "beat": 0, "string": 2, "kind": []},
     "GP5"),
    ({"op": "technique", "measure": 0, "beat": 0, "string": {}, "kind": "bend"},
     "string 이 정수가 아니다"),
])
def test_technique_rejections(correction, hint):
    ir = _ir(_measure(0, [_beat([(2, 3)])]))
    _, outcome = corrections.apply_corrections(ir, [correction])

    assert not outcome.applied
    assert hint in _reasons(outcome)[0]


def test_boolean_measure_index_does_not_alias_to_measure_one():
    """`True == 1` 이라 그냥 두면 1번 마디를 가리키는 멀쩡한 인덱스가 된다."""
    ir = _ir(_measure(0, [_beat([(2, 3)])]), _measure(1, [_beat([(2, 3)])]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "technique", "measure": True, "beat": 0, "string": 2,
         "kind": "bend"}])

    assert not outcome.applied, "True 가 마디 1로 통과했다"
    assert result["measures"][1]["beats"][0]["techniques"] == []


def test_duplicate_technique_is_rejected_not_doubled():
    ir = _ir(_measure(0, [_beat([(2, 3)], techniques=[{"string": 2, "kind": "hammer"}])]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "technique", "measure": 0, "beat": 0, "string": 2, "kind": "hammer"}])

    assert not outcome.applied
    assert len(result["measures"][0]["beats"][0]["techniques"]) == 1


# ── 가사 (핵심 불변식) ───────────────────────────────────────────────────────

def test_lyric_realignment_applies_when_syllables_are_unchanged():
    ir = _ir(_measure(0, [_beat([(2, 3)], lyric="나는"), _beat([(2, 5)]),
                          _beat([(2, 7)])]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "lyric", "measure": 0, "beat": 0, "text": "나"},
        {"op": "lyric", "measure": 0, "beat": 2, "text": "는"}])

    assert len(outcome.applied) == 2, _reasons(outcome)
    assert [b["lyric"] for b in result["measures"][0]["beats"]] == ["나", None, "는"]


@pytest.mark.parametrize("texts, original", [
    (["나는", "사랑"], "나는"),          # 글자를 새로 만들었다
    (["나", "는"], "나는내"),            # 글자를 버렸다
    (["나", "가"], "가나"),              # 순서를 뒤집었다 — 다중집합만 보면 통과한다
    (["나눈", ""], "나는"),              # 글자를 바꿨다
])
def test_ai_cannot_change_the_lyric_text(texts, original):
    ir = _ir(_measure(0, [_beat([(2, 3)], lyric=original), _beat([(2, 5)])]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "lyric", "measure": 0, "beat": position, "text": text}
        for position, text in enumerate(texts)])

    assert not outcome.applied, f"{original!r} → {texts} 가 통과했다"
    assert "음절이 바뀌었다" in _reasons(outcome)[0]
    # 원본 배치가 그대로여야 한다 — 반쯤 적용되면 안 된다
    assert [b["lyric"] for b in result["measures"][0]["beats"]] == [original, None]


def test_lyric_rejection_is_scoped_to_one_measure():
    """한 마디가 실패해도 다른 마디의 재배치는 살아야 한다."""
    ir = _ir(_measure(0, [_beat([(2, 3)], lyric="가나"), _beat([(2, 5)])]),
             _measure(1, [_beat([(2, 3)], lyric="다라"), _beat([(2, 5)])]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "lyric", "measure": 0, "beat": 1, "text": "틀림"},
        {"op": "lyric", "measure": 1, "beat": 0, "text": "다"},
        {"op": "lyric", "measure": 1, "beat": 1, "text": "라"}])

    assert len(outcome.applied) == 2
    assert [b["lyric"] for b in result["measures"][0]["beats"]] == ["가나", None]
    assert [b["lyric"] for b in result["measures"][1]["beats"]] == ["다", "라"]


# ── 코드·보이싱 ──────────────────────────────────────────────────────────────

def test_chord_name_applied_and_garbage_rejected():
    ir = _ir(_measure(0, [_beat([(2, 3)]), _beat([(2, 5)])]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "chord", "measure": 0, "beat": 0, "name": "Am"},
        {"op": "chord", "measure": 0, "beat": 1, "name": "Hmm9"}])

    assert [c["name"] for c in outcome.applied] == ["Am"]
    assert result["measures"][0]["beats"][0]["chord"] == "Am"
    assert result["measures"][0]["beats"][1]["chord"] is None


def test_chord_name_must_be_one_the_extractor_actually_read():
    """프롬프트로만 막으면 모델이 낱글자를 다시 조립해 'Cadd9' 를 'C' 로 끊는다.

    보이싱까지 함께 내면 음정 검증도 통과하므로, 관문에서 이름 자체를 막아야 한다.
    """
    ir = _ir(_measure(0, [_beat([(2, 3)])], chord_row=["Cadd9"]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "voicing", "name": "C", "frets": [0, 1, 0, 2, 3, -1]},
        {"op": "chord", "measure": 0, "beat": 0, "name": "C"}])

    assert [c["op"] for c in outcome.applied] == ["voicing"]
    assert "코드 행에서 읽은 이름이 아니다" in _reasons(outcome)[0]
    assert result["measures"][0]["beats"][0]["chord"] is None


def test_chord_carried_over_from_an_earlier_measure_is_accepted():
    """줄바꿈을 넘어 유지되는 코드도 그 마디에서 쓸 수 있는 이름이다."""
    ir = _ir(_measure(0, [_beat([(2, 3)])], chord_row=[], chord_in_effect="Am"))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "chord", "measure": 0, "beat": 0, "name": "Am"}])

    assert len(outcome.applied) == 1, _reasons(outcome)
    assert result["measures"][0]["beats"][0]["chord"] == "Am"


def test_chord_with_a_voicing_from_the_same_batch_is_accepted():
    """새 코드는 voicing 보정을 같이 내면 순서와 무관하게 통과해야 한다."""
    ir = _ir(_measure(0, [_beat([(2, 3)])]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "chord", "measure": 0, "beat": 0, "name": "Bm7"},
        {"op": "voicing", "name": "Bm7", "frets": [2, 3, 2, 4, 2, -1]}])

    assert len(outcome.applied) == 2, _reasons(outcome)
    assert result["measures"][0]["beats"][0]["chord"] == "Bm7"


def test_chord_rename_redraws_the_notes_it_derived():
    """코드에서 만든 음은 새 코드의 보이싱으로 다시 만들어야 한다.

    이름만 갈면 다이어그램은 Am 인데 소리는 G 가 난다.
    """
    ir = _ir(_measure(0, [_beat([(6, 3), (5, 2), (4, 0), (3, 0), (2, 0), (1, 3)],
                                chord="G", from_chord=True)]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "chord", "measure": 0, "beat": 0, "name": "Am"}])

    assert len(outcome.applied) == 1, _reasons(outcome)
    beat = result["measures"][0]["beats"][0]
    assert beat["chord"] == "Am"
    assert {(n["string"], n["fret"]) for n in beat["notes"]} == set(
        chords.VOICINGS["Am"]), "음이 G 보이싱 그대로 남았다"


def test_chord_rename_is_refused_when_the_new_voicing_is_unknown():
    ir = _ir(_measure(0, [_beat([(5, 0)], chord="Am", from_chord=True)]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "chord", "measure": 0, "beat": 0, "name": "Bm7"}])

    assert not outcome.applied
    assert "보이싱을 몰라" in _reasons(outcome)[0]
    assert result["measures"][0]["beats"][0]["chord"] == "Am"


def test_second_chord_on_the_same_beat_is_rejected():
    """마지막 값이 조용히 이기면 무엇이 반영됐는지 알 수 없다."""
    ir = _ir(_measure(0, [_beat([(2, 3)])]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "chord", "measure": 0, "beat": 0, "name": "Am"},
        {"op": "chord", "measure": 0, "beat": 0, "name": "F"}])

    assert len(outcome.applied) == 1
    assert len(outcome.rejected) == 1
    assert result["measures"][0]["beats"][0]["chord"] == "Am"


def test_boolean_measure_cannot_smuggle_into_a_lyric_group():
    """`hash(True) == hash(1)` 이라 measure 를 그대로 그룹 키로 쓰면 섞여 들어간다."""
    ir = _ir(_measure(0, [_beat(lyric="가"), _beat()]),
             _measure(1, [_beat(lyric="가"), _beat()]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "lyric", "measure": 1, "beat": 0, "text": "가"},
        {"op": "lyric", "measure": True, "beat": 1, "text": ""}])

    assert len(outcome.rejected) == 1
    assert "measure 이 정수가 아니다" in _reasons(outcome)[0]
    assert len(outcome.applied) == 1


def test_chord_name_too_long_for_gp5_is_rejected():
    """GP5 는 코드명을 22바이트에서 조용히 자른다 — IR 과 출력이 달라진다."""
    ir = _ir(_measure(0, [_beat([(2, 3)])]))
    _, outcome = corrections.apply_corrections(ir, [
        {"op": "chord", "measure": 0, "beat": 0, "name": "C" + "add9" * 6}])

    assert not outcome.applied
    assert "22바이트" in _reasons(outcome)[0]


def test_chord_without_a_voicing_is_rejected_because_gp5_shows_nothing():
    """보이싱이 없으면 다이어그램이 안 만들어져 .gp5 에 코드가 아예 안 남는다."""
    ir = _ir(_measure(0, [_beat([(2, 3)])]))
    _, outcome = corrections.apply_corrections(ir, [
        {"op": "chord", "measure": 0, "beat": 0, "name": "Bm7"}])

    assert not outcome.applied
    assert "표시할 수 없다" in _reasons(outcome)[0]


@pytest.mark.parametrize("order", ["technique_first", "chord_first"])
def test_technique_is_checked_against_the_notes_the_chord_leaves_behind(order):
    """코드를 먼저 적용하므로 입력 순서가 결과를 바꾸지 않는다.

    G 에는 6번줄이 있고 Am 에는 없다. 순서에 따라 연주법이 살아남으면 같은 입력이
    다른 악보를 만든다.
    """
    ir = _ir(_measure(0, [_beat(list(chords.VOICINGS["G"]), chord="G",
                                from_chord=True)]))
    technique = {"op": "technique", "measure": 0, "beat": 0, "string": 6,
                 "kind": "hammer"}
    chord = {"op": "chord", "measure": 0, "beat": 0, "name": "Am"}
    proposals = ([technique, chord] if order == "technique_first"
                 else [chord, technique])
    result, outcome = corrections.apply_corrections(ir, proposals)

    assert [c["op"] for c in outcome.applied] == ["chord"]
    assert "인데 6 을 가리켰다" in _reasons(outcome)[0]
    assert result["measures"][0]["beats"][0]["techniques"] == []


@pytest.mark.parametrize("order", ["technique_first", "chord_first"])
def test_technique_survives_when_the_chord_supplies_the_string(order):
    ir = _ir(_measure(0, [_beat(list(chords.VOICINGS["Am"]), chord="Am",
                                from_chord=True)]))
    technique = {"op": "technique", "measure": 0, "beat": 0, "string": 6,
                 "kind": "hammer"}
    chord = {"op": "chord", "measure": 0, "beat": 0, "name": "G"}
    proposals = ([technique, chord] if order == "technique_first"
                 else [chord, technique])
    result, outcome = corrections.apply_corrections(ir, proposals)

    assert len(outcome.applied) == 2, _reasons(outcome)
    assert result["measures"][0]["beats"][0]["techniques"] == [
        {"string": 6, "kind": "hammer"}]


def test_technique_survives_on_a_beat_filled_by_a_new_voicing():
    """보이싱으로 음을 채우기 전에 연주법을 검사하면 멀쩡한 보정이 거부된다."""
    ir = _ir(_measure(0, [_beat(chord="Bm7", from_chord=True)]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "technique", "measure": 0, "beat": 0, "string": 2,
         "kind": "hammer"},
        {"op": "voicing", "name": "Bm7", "frets": [2, 3, 2, 4, 2, -1]}])

    assert len(outcome.applied) == 2, _reasons(outcome)
    assert result["measures"][0]["beats"][0]["techniques"] == [
        {"string": 2, "kind": "hammer"}]


def test_new_voicing_fills_a_silent_chord_beat():
    """보이싱을 몰라 무음이던 슬래시 beat 가 소리 나야 한다 — 그게 voicing 의 목적이다."""
    ir = _ir(_measure(0, [_beat(chord="Bm7", from_chord=True)]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "voicing", "name": "Bm7", "frets": [2, 3, 2, 4, 2, -1]}])

    assert outcome.realized == 1
    assert {(n["string"], n["fret"])
            for n in result["measures"][0]["beats"][0]["notes"]} == {
        (1, 2), (2, 3), (3, 2), (4, 4), (5, 2)}


def test_voicing_is_applied_before_the_chord_rename_that_needs_it():
    """같은 배치에서 순서가 뒤여도 코드명 보정이 새 보이싱을 쓸 수 있어야 한다."""
    ir = _ir(_measure(0, [_beat([(5, 0)], chord="Am", from_chord=True)]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "chord", "measure": 0, "beat": 0, "name": "Bm7"},
        {"op": "voicing", "name": "Bm7", "frets": [2, 3, 2, 4, 2, -1]}])

    assert len(outcome.applied) == 2, _reasons(outcome)
    assert result["measures"][0]["beats"][0]["chord"] == "Bm7"


def test_voicing_fills_unknown_chord():
    ir = _ir()
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "voicing", "name": "Bm7", "frets": [2, 3, 2, 4, 2, -1]}])

    assert len(outcome.applied) == 1, _reasons(outcome)
    assert result["ai_voicings"]["Bm7"] == [[1, 2], [2, 3], [3, 2], [4, 4], [5, 2]]


def test_voicing_never_overwrites_a_hand_verified_one():
    ir = _ir()
    _, outcome = corrections.apply_corrections(ir, [
        {"op": "voicing", "name": "G", "frets": [0, 0, 0, 0, 0, 0]}])

    assert not outcome.applied
    assert "검증" in _reasons(outcome)[0]


@pytest.mark.parametrize("frets, hint", [
    # 개방현 6개는 Bm7 이 아니다 (E·G 가 코드에 없다)
    ([0, 0, 0, 0, 0, 0], "없는 음을 낸다"),
    # 근음 B 하나만 짚으면 어떤 코드든 "부분집합" 이라 통과해버린다
    ([-1, -1, -1, -1, 2, -1], "주장하는 음이 빠졌다"),
    # F#·B 만 — 3도와 7도가 없으니 코드가 아니라 5도 하나다
    ([-1, -1, -1, 4, 2, -1], "주장하는 음이 빠졌다"),
    # 근음이 빠진 보이싱 (D·F#·A = D 장3화음)
    ([2, 3, 2, 0, -1, -1], "주장하는 음이 빠졌다"),
    # B·D·F# 는 7도(A)가 없으니 Bm7 이 아니라 Bm 이다
    ([2, 3, 2, 4, -1, -1], "주장하는 음이 빠졌다"),
])
def test_voicing_must_actually_sound_the_named_chord(frets, hint):
    """이름만 믿으면 악보가 조용히 틀린다."""
    _, outcome = corrections.apply_corrections(
        _ir(), [{"op": "voicing", "name": "Bm7", "frets": frets}])

    assert not outcome.applied, f"{frets} 가 Bm7 로 통과했다"
    assert hint in _reasons(outcome)[0]


@pytest.mark.parametrize("name, frets, tones", [
    ("C", [-1, -1, -1, 2, 3, -1], "C·E"),
    ("Dm", [1, -1, -1, 0, -1, -1], "D·F"),
])
def test_a_partial_voicing_that_only_omits_the_fifth_is_accepted(name, frets, tones):
    """3음을 요구하면 '5도는 언제나 생략 가능' 규칙과 정면으로 부딪친다."""
    _, outcome = corrections.apply_corrections(
        _ir(), [{"op": "voicing", "name": name, "frets": frets}])

    assert len(outcome.applied) == 1, f"{name} {tones}: {_reasons(outcome)}"


@pytest.mark.parametrize("name, root_fret", [
    # 손으로 검증한 표(VOICINGS)에 없는 이름을 쓴다 — 있으면 그쪽 규칙에 먼저 걸린다
    ("C", 3),           # 5번줄 3프렛 = C
    ("B5", 2),          # 5번줄 2프렛 = B — '5' 는 5도가 필수라 이 경로가 따로다
    ("Bm7", 2),
    ("Dadd9", 5),       # 5번줄 5프렛 = D
])
def test_a_lone_root_never_passes_as_a_chord(name, root_fret):
    """근음 하나가 어떤 코드로든 통과하면 검증이 무의미하다.

    각 코드의 **자기 근음**을 짚는다 — 아무 단음이나 쓰면 부분집합 검사에서
    먼저 걸려 정작 필수음 경로를 지나가지 않는다.
    """
    _, outcome = corrections.apply_corrections(
        _ir(), [{"op": "voicing", "name": name,
                 "frets": [-1, -1, -1, -1, root_fret, -1]}])

    assert not outcome.applied, f"{name} 이 근음 단음으로 통과했다"
    assert "주장하는 음이 빠졌다" in _reasons(outcome)[0]


@pytest.mark.parametrize("name, frets, why", [
    # C·F·Bb — 11도와 부딪치는 3도, 9도, 5도를 뺀 표준 보이싱
    ("C11", [-1, -1, 3, 3, 3, -1], "3·9·5도를 뺀 표준 보이싱"),
    # C·E·Bb·A — 9도와 11도를 뺀 표준 보이싱
    ("C13", [5, -1, 3, 2, 3, -1], "9·11도를 뺀 표준 보이싱"),
    # C·E·Bb·F·A — 11도를 포함한 보이싱. 금지하면 안 된다
    ("C13", [5, 6, 3, 2, 3, -1], "11도를 포함한 보이싱"),
    ("C6/9", [3, 3, 2, 2, 3, -1], "성질 이름에 '/' 가 든 코드"),
])
def test_extension_chords_that_omit_tones_in_practice_are_accepted(name, frets, why):
    """확장 코드는 6줄에 다 안 담긴다 — 실제 보이싱을 거부하면 코드가 사라진다."""
    _, outcome = corrections.apply_corrections(
        _ir(), [{"op": "voicing", "name": name, "frets": frets}])

    assert len(outcome.applied) == 1, f"{why}: {_reasons(outcome)}"


def test_an_extension_chord_still_needs_its_own_extension():
    """C13 에 13도(A)가 없으면 C13 이 아니다 — optional 을 너무 넓히면 안 된다."""
    _, outcome = corrections.apply_corrections(
        _ir(), [{"op": "voicing", "name": "C13",
                 "frets": [-1, 6, 3, 2, 3, -1]}])              # C·E·Bb·F, A 없음

    assert not outcome.applied
    assert "주장하는 음이 빠졌다" in _reasons(outcome)[0]


def test_mismatch_reason_names_the_check_that_actually_failed():
    """검사 순서가 어긋나면 '최저음은 4여야 하는데 4다' 같은 모순이 나온다."""
    _, outcome = corrections.apply_corrections(
        _ir(), [{"op": "voicing", "name": "C/E", "frets": [-1, -1, -1, 2, -1, -1]}])

    reason = _reasons(outcome)[0]
    assert "최저음" not in reason, reason
    assert "주장하는 음이 빠졌다" in reason


def test_voicing_may_omit_the_fifth():
    """5음 생략은 기타에서 흔하다 — 그건 통과해야 한다 (B·D·A)."""
    _, outcome = corrections.apply_corrections(
        _ir(), [{"op": "voicing", "name": "Bm7",
                 "frets": [-1, -1, 2, 0, 2, -1]}])

    assert len(outcome.applied) == 1, _reasons(outcome)


def test_unverifiable_chord_quality_is_rejected():
    """신뢰 경계 밖의 입력에서 '판정 불가' 를 허용으로 읽으면 검증이 통째로 우회된다."""
    assert chords.parse("Cfoo") is None
    _, outcome = corrections.apply_corrections(
        _ir(), [{"op": "voicing", "name": "Cfoo",
                 "frets": [3, 3, 3, -1, -1, -1]}])

    assert not outcome.applied
    assert "성질을 몰라" in _reasons(outcome)[0]


def test_quality_names_containing_a_slash_are_not_split_as_bass():
    """'6/9' 를 무조건 분수 코드로 쪼개면 그 항목이 영영 안 쓰인다."""
    spec = chords.parse("C6/9")

    assert spec is not None and spec.bass is None
    assert spec.classes == frozenset({0, 2, 4, 7, 9})


def test_slash_bass_is_one_note_and_must_be_the_lowest():
    """C/E 를 재귀 해석하면 E 장3화음(E·G#·B)까지 허용된다."""
    spec = chords.parse("C/E")
    assert spec.bass == 4
    assert 8 not in spec.classes, "G# 이 허용됐다 — 베이스를 코드로 읽었다"

    tuning = STANDARD_TUNING
    lowest_is_e = [(6, 0), (5, 3), (4, 2), (3, 0), (2, 1), (1, 0)]
    lowest_is_c = [(5, 3), (4, 2), (3, 0), (2, 1), (1, 0)]
    assert chords.voicing_matches("C/E", lowest_is_e, tuning) is True
    assert chords.voicing_matches("C/E", lowest_is_c, tuning) is False


def test_power_chord_is_allowed_to_have_only_two_tones():
    assert chords.voicing_matches("B5", [(6, 7), (5, 9)], STANDARD_TUNING) is True


def test_hand_verified_voicings_pass_their_own_pitch_check():
    """표에 든 5개가 검증기를 통과하지 못하면 검증기나 표가 틀렸다."""
    for name, voicing in chords.VOICINGS.items():
        assert chords.voicing_matches(name, voicing, STANDARD_TUNING) is True, name


def test_chord_rename_is_refused_rather_than_dropping_a_technique():
    """음이 사라지면 연주법도 사라진다 — 지우지 말고 거절해 사용자가 보게 한다."""
    ir = _ir(_measure(0, [_beat(list(chords.VOICINGS["G"]), chord="G",
                                from_chord=True,
                                techniques=[{"string": 6, "kind": "hammer"}])]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "chord", "measure": 0, "beat": 0, "name": "Am"}])

    assert not outcome.applied
    assert "붙을 음이 없어진다" in _reasons(outcome)[0]
    assert result["measures"][0]["beats"][0]["chord"] == "G"
    assert result["measures"][0]["beats"][0]["techniques"] == [
        {"string": 6, "kind": "hammer"}]


@pytest.mark.parametrize("frets, hint", [
    ([2, 3, 2, 4, 2], "6개"),
    ([2, 3, 2, 4, 2, 99], "0..24"),
    ([2, 3, 2, 4, 2, "0"], "정수"),
    ([-1, -1, -1, -1, -1, -1], "미사용"),
    ([1, 12, 3, 15, 2, 7], "짚을 수 없다"),
])
def test_voicing_frets_are_validated(frets, hint):
    _, outcome = corrections.apply_corrections(
        _ir(), [{"op": "voicing", "name": "Bm7", "frets": frets}])

    assert not outcome.applied
    assert hint in _reasons(outcome)[0]


# ── 위생 ─────────────────────────────────────────────────────────────────────

def test_unknown_op_is_rejected():
    _, outcome = corrections.apply_corrections(
        _ir(), [{"op": "retune", "tuning": [0]}, "문자열"])

    assert not outcome.applied
    assert len(outcome.rejected) == 2


def test_original_ir_is_never_mutated():
    ir = _ir(_measure(0, [_beat([(2, 3)], lyric="가")]))
    snapshot = copy.deepcopy(ir)
    corrections.apply_corrections(ir, [
        {"op": "technique", "measure": 0, "beat": 0, "string": 2, "kind": "bend"},
        {"op": "lyric", "measure": 0, "beat": 0, "text": "가"},
        {"op": "voicing", "name": "Bm7", "frets": [2, 3, 2, 4, 2, -1]}])

    assert ir == snapshot


# ── build: 연주법이 실제로 .gp5 에 남는가 ────────────────────────────────────

def test_validator_and_builder_agree_on_technique_kinds():
    """검증을 통과했는데 build 가 모르면 조용히 사라진다."""
    assert corrections.TECHNIQUE_KINDS == build.SUPPORTED_TECHNIQUES


_TECHNIQUE_CHECKS = {
    "hammer": lambda n: n.effect.hammer,
    "vibrato": lambda n: n.effect.vibrato,
    "palm_mute": lambda n: n.effect.palmMute,
    "let_ring": lambda n: n.effect.letRing,
    "staccato": lambda n: n.effect.staccato,
    "accent": lambda n: n.effect.accentuatedNote,
    "heavy_accent": lambda n: n.effect.heavyAccentuatedNote,
    "ghost": lambda n: n.effect.ghostNote,
    "slide": lambda n: n.effect.slides == [gp.SlideType.shiftSlideTo],
    "slide_out_down": lambda n: n.effect.slides == [gp.SlideType.outDownwards],
    "slide_out_up": lambda n: n.effect.slides == [gp.SlideType.outUpwards],
    "slide_in_below": lambda n: n.effect.slides == [gp.SlideType.intoFromBelow],
    "slide_in_above": lambda n: n.effect.slides == [gp.SlideType.intoFromAbove],
    "bend": lambda n: n.effect.bend is not None and len(n.effect.bend.points) == 3,
    "harmonic": lambda n: n.effect.harmonic is not None,
    "dead": lambda n: n.type == gp.NoteType.dead,
}


def test_every_technique_kind_survives_the_gp5_round_trip(tmp_path):
    assert set(_TECHNIQUE_CHECKS) == build.SUPPORTED_TECHNIQUES, "검사표가 낡았다"

    kinds = sorted(build.SUPPORTED_TECHNIQUES)
    ir = _ir(_measure(0, [_beat([(2, 3)], techniques=[{"string": 2, "kind": kind}])
                          for kind in kinds]))
    out = tmp_path / "techniques.gp5"
    build.write_gp5(build.build_song(ir), str(out))

    song = gp.parse(str(out), encoding="cp949")
    beats = [b for m in song.tracks[0].measures for v in m.voices for b in v.beats]
    assert len(beats) == len(kinds)
    for kind, beat in zip(kinds, beats):
        note = beat.notes[0]
        assert _TECHNIQUE_CHECKS[kind](note), f"{kind} 가 .gp5 왕복에서 사라졌다"


def test_ai_voicing_becomes_a_chord_diagram(tmp_path):
    ir = _ir(_measure(0, [_beat([(2, 3)], chord="Bm7")]),
             ai_voicings={"Bm7": [[1, 2], [2, 3], [3, 2], [4, 4], [5, 2]]})
    out = tmp_path / "voicing.gp5"
    build.write_gp5(build.build_song(ir), str(out))

    song = gp.parse(str(out), encoding="cp949")
    diagram = song.tracks[0].measures[0].voices[0].beats[0].effect.chord
    assert diagram is not None, "AI 보이싱이 다이어그램으로 안 갔다"
    assert diagram.name == "Bm7"
    assert list(diagram.strings) == [2, 3, 2, 4, 2, -1]


def test_hand_verified_voicing_wins_over_ai():
    ir = _ir(ai_voicings={"G": [[1, 0], [2, 0], [3, 0], [4, 0], [5, 0], [6, 0]]})

    assert chords.voicing_in(ir, "G") == chords.VOICINGS["G"]


# ── ai: 설정과 파싱 ──────────────────────────────────────────────────────────

def test_load_config_detects_bedrock():
    config = ai.load_config({"OPENAI_MODEL": "m", "AWS_REGION": "ap-northeast-2",
                             "AWS_BEARER_TOKEN_BEDROCK": "tok"})
    assert config.backend == ai.BACKEND_BEDROCK
    assert config.region == "ap-northeast-2"


def test_load_config_detects_openai_compatible():
    config = ai.load_config({"OPENAI_MODEL": "qwen3:14b",
                             "OPENAI_BASE_URL": "http://localhost:11434/v1"})
    assert config.backend == ai.BACKEND_OPENAI
    assert config.api_key == "not-needed", "ollama 는 키가 없어도 돌아야 한다"


@pytest.mark.parametrize("env, hint", [
    ({"OPENAI_MODEL": "m"}, "AI 설정이 없습니다"),
    ({"AI_BACKEND": "gemini", "OPENAI_MODEL": "m"}, "모릅니다"),
    ({"AI_BACKEND": "bedrock"}, "OPENAI_MODEL"),
    ({"AI_BACKEND": "bedrock", "OPENAI_MODEL": "m"}, "AWS_REGION"),
    ({"AI_BACKEND": "openai", "OPENAI_MODEL": "m", "AI_TEMPERATURE": "뜨겁게"},
     "숫자가 아닙니다"),
])
def test_load_config_refuses_broken_settings(env, hint):
    with pytest.raises(ai.AiUnavailable, match=hint):
        ai.load_config(env)


@pytest.mark.parametrize("text", [
    '{"corrections": []}',
    '```json\n{"corrections": []}\n```',
    '알겠습니다. 결과는 다음과 같습니다:\n{"corrections": []}\n이상입니다.',
])
def test_parse_json_object_digs_the_object_out(text):
    assert ai.parse_json_object(text) == {"corrections": []}


@pytest.mark.parametrize("text", ["", "설명만 했습니다", "[1, 2, 3]"])
def test_parse_json_object_refuses_non_objects(text):
    with pytest.raises(ai.AiUnavailable):
        ai.parse_json_object(text)


# ── refine: 오케스트레이션 ───────────────────────────────────────────────────

_CONFIG = ai.Config(backend=ai.BACKEND_OPENAI, model="stub",
                    base_url="http://stub/v1")


def _four_measures():
    return _ir(*[_measure(i, [_beat([(2, 3)]), _beat([(2, 5)])]) for i in range(8)])


def test_refine_batches_and_records_provenance():
    seen = []

    def ask(config, system, user):
        seen.append(user)
        return {"corrections": [{"op": "technique", "measure": 0, "beat": 0,
                                 "string": 2, "kind": "bend"}],
                "notes": "1번줄에 벤드 표기가 보인다"}

    result, outcome = refine_and_assert(_four_measures(), ask)
    assert len(seen) == 2, "8마디는 4마디씩 두 배치여야 한다"
    # 배치마다 같은 보정을 냈으므로 두 번째는 중복으로 폐기된다
    assert len(outcome.applied) == 1
    assert len(outcome.rejected) == 1
    assert result["refinement"]["proposed"] == 2
    assert len(result["refinement"]["model_notes"]) == 2
    assert result["measures"][0]["beats"][0]["techniques"] == [
        {"string": 2, "kind": "bend"}]


def refine_and_assert(ir, ask, **kwargs):
    snapshot = copy.deepcopy(ir)
    result, outcome = refine.refine_ir(ir, config=_CONFIG, ask=ask, **kwargs)
    assert ir == snapshot, "refine 이 원본 IR 을 건드렸다"
    return result, outcome


def test_refine_survives_a_failed_batch():
    """배치 하나가 죽어도 나머지는 반영되고, 실패는 경고로 남는다."""
    def ask(config, system, user):
        if '"measure": 0' in user:
            raise ai.AiUnavailable("서버가 끊겼다")
        return {"corrections": [{"op": "chord", "measure": 4, "beat": 0,
                                 "name": "Am"}]}

    result, outcome = refine_and_assert(_four_measures(), ask)
    assert len(outcome.applied) == 1
    assert result["measures"][4]["beats"][0]["chord"] == "Am"
    assert len(result["refinement"]["failed_batches"]) == 1
    assert [w["kind"] for w in result["warnings"]] == ["ai_batch_failed"]


def test_refine_rejects_a_malformed_answer():
    result, _ = refine_and_assert(_four_measures(),
                                  lambda *_: {"corrections": "없음"})

    assert len(result["refinement"]["failed_batches"]) == 2
    assert "배열이 아니다" in result["refinement"]["failed_batches"][0]["reason"]


def test_refine_limit_caps_the_number_of_calls():
    calls = []
    refine_and_assert(_four_measures(),
                      lambda c, s, u: calls.append(u) or {"corrections": []},
                      limit=1)

    assert len(calls) == 1


def test_rejected_corrections_become_warnings():
    result, _ = refine_and_assert(
        _four_measures(),
        lambda *_: {"corrections": [{"op": "technique", "measure": 0, "beat": 0,
                                     "string": 6, "kind": "bend"}]})

    kinds = [w["kind"] for w in result["warnings"]]
    assert kinds == ["ai_rejected", "ai_rejected"], "폐기를 조용히 넘기면 안 된다"


def test_a_batch_cannot_reach_outside_its_own_measures():
    """환각이나 PDF 프롬프트 인젝션이 보지도 않은 마디를 고치면 안 된다."""
    def ask(config, system, user):
        # 앞 배치(마디 0..3)만 응답하고, 뒤 배치(마디 4..7)를 노린다
        if '"measure": 0' not in user:
            return {"corrections": []}
        return {"corrections": [{"op": "chord", "measure": 7, "beat": 0,
                                 "name": "Am"}]}

    result, outcome = refine_and_assert(_four_measures(), ask)
    assert not outcome.applied
    assert result["measures"][7]["beats"][0]["chord"] is None
    assert "밖의 마디" in outcome.rejected[0]["reason"]
    assert [w["kind"] for w in result["warnings"]] == ["ai_rejected"]


def test_voicing_is_allowed_to_cross_batches():
    """보이싱은 마디에 매이지 않는다 — 범위 검사에 걸리면 안 된다."""
    _, outcome = refine_and_assert(
        _four_measures(),
        lambda *_: {"corrections": [{"op": "voicing", "name": "Bm7",
                                     "frets": [2, 3, 2, 4, 2, -1]}]},
        limit=1)

    assert len(outcome.applied) == 1, _reasons(outcome)


def test_model_notes_are_stripped_of_terminal_control_characters():
    """메모는 모델(→PDF) 출처라 신뢰 경계 밖이다. 그대로 찍으면 화면을 위조한다."""
    result, _ = refine_and_assert(
        _four_measures(),
        lambda *_: {"corrections": [],
                    "notes": "정상\x1b[2J\x07\x00 텍스트"},
        limit=1)

    note = result["refinement"]["model_notes"][0]
    assert "\x1b" not in note and "\x07" not in note and "\x00" not in note
    assert note.endswith("정상[2J 텍스트")


def test_config_label_hides_url_secrets():
    config = ai.Config(backend=ai.BACKEND_OPENAI, model="m",
                       base_url="https://user:s3cr3t@host.example:8443/v1?key=abc")

    assert config.label == "openai:m @ https://host.example:8443"
    assert "s3cr3t" not in config.label and "abc" not in config.label


@pytest.mark.parametrize("url", ["http://host:99999/v1", "http://host:abc/v1"])
def test_config_label_survives_a_malformed_url(url):
    """오류를 알리려고 부르는 함수가 오류로 죽으면 원인을 못 본다."""
    config = ai.Config(backend=ai.BACKEND_OPENAI, model="m", base_url=url)

    assert "잘못된 URL" in config.label


def test_report_survives_a_malformed_op_from_the_model():
    """모델이 op 에 리스트를 넣으면 Counter 가 죽어 CLI 보고 자체가 중단됐다."""
    import convert

    result, _ = refine_and_assert(
        _four_measures(),
        lambda *_: {"corrections": [{"op": ["technique"], "measure": 0}]},
        limit=1)
    convert._report_refinement(result)      # 예외가 나면 실패다


# ── smufl: 기호 식별 ────────────────────────────────────────────────────────

def test_smufl_names_the_glyphs_that_matter_for_techniques():
    """구획 라벨만으로는 악센트인지 스타카토인지 알 수 없다 — 이름이 있어야 한다."""
    assert smufl.name(chr(0xE4A1)) == "articAccentBelow"
    assert smufl.label(chr(0xE4A1)) == "아티큘레이션"
    assert smufl.codepoint(chr(0xE4A1)) == "U+E4A1"
    assert smufl.name(chr(0xE0A9)) == "noteheadXBlack"
    assert smufl.name(chr(0xE1E7)) == "augmentationDot"
    assert smufl.name("가") is None, "평범한 글자에는 이름이 없다"
    assert smufl.label("가") is None


def test_smufl_names_stay_inside_their_declared_ranges():
    """표와 구획이 어긋나면 라벨과 이름이 서로 다른 기호를 가리킨다."""
    for code, name in smufl.NAMES.items():
        char = chr(code)
        assert smufl.in_range(char, smufl.PRIVATE_USE), f"{name} 이 사설영역 밖이다"
        assert smufl.label(char) != "미분류기호", f"{name} 이 어느 구획에도 안 든다"
