"""AI 계층 테스트 — 연주법이 .gp5 에 남는가, 설정·파싱·오케스트레이션.

실제 모델 호출은 `ask` 를 주입해 대체한다. 네트워크도 PDF 도 쓰지 않는다.
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
    """테스트용 마디.

    `chord_row` 기본값은 손으로 검증한 코드 5개다 — corrections 는 코드 행에서
    읽은 이름만 받으므로, 기본이 비어 있으면 모든 코드 보정 테스트가 그 규칙에
    먼저 걸려 정작 검사하려던 경로를 지나가지 않는다.
    """
    names = sorted(chords.VOICINGS) if chord_row is None else list(chord_row)
    return {"index": index, "time_sig": [4, 4], "kind": "fret",
            "beats": beats, "glyphs": [],
            "chord_row": [{"x": 0.0, "name": name} for name in names],
            "chord_in_effect": chord_in_effect}


def _ir(*measures, **extra):
    return {"title": "곡", "artist": "", "tempo": 80,
            "tuning": list(STANDARD_TUNING), "measures": list(measures),
            "warnings": [], **extra}


def _reasons(outcome):
    return [entry["reason"] for entry in outcome.rejected]


# ── build: 연주법이 실제로 .gp5 에 남는가 ────────────────────────────────────

def test_validator_and_builder_agree_on_technique_kinds():
    """검증을 통과했는데 build 가 모르면 조용히 사라진다."""
    assert corrections.TECHNIQUE_KINDS == build.SUPPORTED_TECHNIQUES


def test_extractor_only_produces_technique_kinds_the_builder_knows():
    """1차 추출은 관문을 지나지 않는다 — 여기서 어긋나면 build 가 조용히 버린다."""
    from tab_pdf import extract

    produced = set(extract.TECHNIQUE_GLYPHS.values())
    assert produced <= build.SUPPORTED_TECHNIQUES, sorted(
        produced - build.SUPPORTED_TECHNIQUES)


def test_prompt_lists_every_kind_the_validator_accepts():
    """프롬프트와 검증기가 어긋나면 모델이 통과 못 할 것을 계속 제안한다."""
    prompt = refine.SYSTEM_PROMPT
    missing = sorted(k for k in corrections.TECHNIQUE_KINDS if k not in prompt)
    assert not missing, f"프롬프트에 없는 허용 연주법: {missing}"
    assert all(op in prompt for op in corrections.OPS)
    # 받지 않는 종류를 프롬프트가 권하면 폐기만 늘어난다
    for rejected in ("trill", "tremolo", "grace"):
        assert rejected not in prompt, f"프롬프트가 {rejected} 를 권하고 있다"


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
