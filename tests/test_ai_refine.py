"""AI 보정 계층 테스트 — 네트워크도 PDF 도 쓰지 않는다.

`corrections` 는 AI 가 IR 을 망치지 못하게 막는 유일한 관문이라, 통과·폐기 양쪽을
모두 확인한다. 실제 모델 호출은 `ask` 를 주입해 대체한다.
"""

import copy

import guitarpro as gp
import pytest

from tab_pdf import ai, build, corrections, refine

STANDARD_TUNING = [64, 59, 55, 50, 45, 40]


def _beat(notes=(), *, lyric=None, chord=None, stroke=None, techniques=None):
    return {"x": 0.0, "duration": 8, "dotted": False, "chord": chord,
            "stroke": stroke, "techniques": list(techniques or ()),
            "lyric": lyric,
            "notes": [{"string": s, "fret": f} for s, f in notes]}


def _measure(index, beats):
    return {"index": index, "time_sig": [4, 4], "kind": "fret",
            "beats": beats, "glyphs": []}


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
])
def test_technique_rejections(correction, hint):
    ir = _ir(_measure(0, [_beat([(2, 3)])]))
    _, outcome = corrections.apply_corrections(ir, [correction])

    assert not outcome.applied
    assert hint in _reasons(outcome)[0]


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


def test_ai_cannot_invent_lyrics():
    """다중집합이 바뀌면 그 마디의 가사 보정을 통째로 버린다."""
    ir = _ir(_measure(0, [_beat([(2, 3)], lyric="나는"), _beat([(2, 5)])]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "lyric", "measure": 0, "beat": 0, "text": "나는"},
        {"op": "lyric", "measure": 0, "beat": 1, "text": "사랑"}])

    assert not outcome.applied
    assert "새로 생김" in _reasons(outcome)[0]
    # 원본 배치가 그대로여야 한다 — 반쯤 적용되면 안 된다
    assert [b["lyric"] for b in result["measures"][0]["beats"]] == ["나는", None]


def test_ai_cannot_drop_lyrics():
    ir = _ir(_measure(0, [_beat([(2, 3)], lyric="나는내"), _beat([(2, 5)])]))
    result, outcome = corrections.apply_corrections(ir, [
        {"op": "lyric", "measure": 0, "beat": 0, "text": "나"},
        {"op": "lyric", "measure": 0, "beat": 1, "text": "는"}])

    assert not outcome.applied
    assert "사라짐" in _reasons(outcome)[0]
    assert [b["lyric"] for b in result["measures"][0]["beats"]] == ["나는내", None]


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
        {"op": "chord", "measure": 0, "beat": 0, "name": "Bm7"},
        {"op": "chord", "measure": 0, "beat": 1, "name": "Hmm9"}])

    assert [c["name"] for c in outcome.applied] == ["Bm7"]
    assert result["measures"][0]["beats"][0]["chord"] == "Bm7"
    assert result["measures"][0]["beats"][1]["chord"] is None


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
    from tab_pdf import chords

    assert build.voicing_for(ir, "G") == chords.VOICINGS["G"]


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
                                 "name": "Bm7"}]}

    result, outcome = refine_and_assert(_four_measures(), ask)
    assert len(outcome.applied) == 1
    assert result["measures"][4]["beats"][0]["chord"] == "Bm7"
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
