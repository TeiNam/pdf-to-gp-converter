"""2-way 교차 리뷰에서 나온 충실도 결함의 회귀 테스트.

합성 geometry(가짜 Glyph)로 extract 내부를 직접 검증한다 — SMuFL 사설 영역
글리프는 합성 PDF 의 기본 폰트로 넣을 수 없어서다. 실제 PDF 기반 수치 검증은
`test_tab_pdf.py` 에 있다.
"""

import pathlib

import guitarpro as gp
import pymupdf
import pytest

from tab_pdf import build, extract, geometry

PDF = pathlib.Path(__file__).resolve().parents[1] / "pdf" / "나는반딧불.pdf"
needs_pdf = pytest.mark.skipif(not PDF.exists(), reason=f"입력 PDF 없음: {PDF}")

SYSTEM = geometry.System(
    melody_ys=(100.0, 105.0, 110.0, 115.0, 120.0),
    tab_ys=(160.0, 168.0, 176.0, 184.0, 192.0, 200.0),
)
BOUNDS = (40.0, 400.0)

X_NOTEHEAD = chr(0xE0A9)        # noteheadXBlack — 뮤트 노트
SEGNO = chr(0xE047)
CODA = chr(0xE048)
ORNAMENT_TRILL = chr(0xE566)    # ornamentTrill — 장식음 구획


def _glyph(x: float, y: float, char: str, size: float = 9.3) -> geometry.Glyph:
    return geometry.Glyph(x=x, y=y, x_end=x + 5.0, char=char, font="f", size=size)


def _measure_from(glyphs: list, **kwargs) -> tuple[dict, extract._Warnings]:
    """가짜 글리프만으로 `_build_measure` 를 돌린다."""
    geo = geometry.PageGeometry(glyphs=glyphs)
    warn = extract._Warnings()
    measure = extract._build_measure(
        geo, SYSTEM, BOUNDS, 0, kwargs.get("tokens", []), warn,
        kwargs.get("time_sig", (4, 4)), extract.build_letter_index(geo),
        kwargs.get("syllables", []))
    return measure, warn


# ── #6 X 뮤트 노트헤드가 리듬 이벤트(dead note)가 되어야 한다 ────────────────

def test_x_notehead_becomes_a_dead_note_beat():
    """타브 대역의 noteheadXBlack 은 별도 beat + dead 노트가 된다."""
    glyphs = [
        _glyph(60.0, SYSTEM.tab_ys[2], "0"),            # 3번줄 개방
        _glyph(150.0, SYSTEM.tab_ys[2], X_NOTEHEAD),    # 3번줄 뮤트
    ]
    measure, _ = _measure_from(glyphs)
    assert len(measure["beats"]) == 2, "X 노트헤드가 beat 이 되지 않았다"
    dead_beat = measure["beats"][1]
    assert dead_beat["notes"] == [{"string": 3, "fret": 0, "dead": True}]


def test_dead_note_reaches_gp5_as_dead_type(tmp_path):
    ir = {
        "title": "t", "artist": "", "tempo": 80,
        "tuning": [64, 59, 55, 50, 45, 40],
        "measures": [{
            "index": 0, "time_sig": [4, 4], "kind": "fret", "beats": [
                {"x": 0, "duration": 4, "dotted": False, "chord": None,
                 "from_chord": False, "stroke": None, "lyric": None,
                 "techniques": [],
                 "notes": [{"string": 3, "fret": 0, "dead": True}]},
            ]}],
        "warnings": [],
    }
    out = tmp_path / "dead.gp5"
    build.write_gp5(build.build_song(ir), str(out))
    note = gp.parse(str(out), encoding="cp949").tracks[0].measures[0].voices[0].beats[0].notes[0]
    assert note.type == gp.models.NoteType.dead


@needs_pdf
def test_real_pdf_x_noteheads_become_dead_notes():
    """이 악보의 X 노트헤드 8개(m23·m48)가 dead 노트로 살아난다."""
    ir = extract.extract_ir(str(PDF), tempo=80)
    dead = [(m["index"], n) for m in ir["measures"] for b in m["beats"]
            for n in b["notes"] if n.get("dead")]
    assert {index for index, _ in dead} == {23, 48}
    assert len(dead) == 8


# ── #13 출력 경로 충돌·원자적 쓰기 ───────────────────────────────────────────

def test_cli_refuses_output_over_input_pdf(tmp_path):
    """-o 가 입력 PDF 를 가리키면 원본이 지워진다 — 거부해야 한다."""
    import convert

    pdf = _score_pdf(tmp_path / "syn.pdf", barlines=[240.0],
                     note_xs=[50.0, 100.0, 150.0, 200.0])
    before = pathlib.Path(pdf).read_bytes()
    assert convert.main([pdf, "-o", pdf]) == 2
    assert pathlib.Path(pdf).read_bytes() == before


def test_cli_refuses_output_colliding_with_ir(tmp_path):
    import convert

    pdf = _score_pdf(tmp_path / "syn.pdf", barlines=[240.0],
                     note_xs=[50.0, 100.0, 150.0, 200.0])
    same = str(tmp_path / "one.json")
    assert convert.main([pdf, "-o", same, "--ir", same]) == 2


def test_write_gp5_is_atomic(tmp_path, monkeypatch):
    """쓰기 도중 실패해도 기존 파일이 잘리면 안 된다."""
    ir = {"title": "t", "artist": "", "tempo": 80,
          "tuning": [64, 59, 55, 50, 45, 40],
          "measures": [{"index": 0, "time_sig": [4, 4], "kind": "empty",
                        "beats": []}],
          "warnings": []}
    out = tmp_path / "song.gp5"
    build.write_gp5(build.build_song(ir), str(out))
    good = out.read_bytes()

    def boom(song, path, **kwargs):
        # 절반 쓰다 죽는 직렬화 — 대상 경로를 먼저 훼손해 본다
        with open(path, "wb") as handle:
            handle.write(b"partial")
        raise RuntimeError("직렬화 실패")

    monkeypatch.setattr(build.gp, "write", boom)
    with pytest.raises(RuntimeError):
        build.write_gp5(build.build_song(ir), str(out))
    assert out.read_bytes() == good, "실패한 쓰기가 기존 파일을 훼손했다"
    assert not list(tmp_path.glob("*.tmp")), "임시 파일이 남았다"


# ── #8 대체 튜닝·카포 ────────────────────────────────────────────────────────

def test_parse_tuning_drop_d_and_half_step_down():
    """저음→고음 표기를 받아 표준 튜닝에 가장 가까운 옥타브로 푼다."""
    assert extract.parse_tuning("DADGBE") == [64, 59, 55, 50, 45, 38]
    assert extract.parse_tuning("Eb Ab Db Gb Bb Eb") == [63, 58, 54, 49, 44, 39]


def test_parse_tuning_rejects_garbage():
    with pytest.raises(ValueError):
        extract.parse_tuning("XYZ")
    with pytest.raises(ValueError):
        extract.parse_tuning("EADG")           # 4현 — 6현이 아니다


def test_header_capo_is_detected():
    glyphs = [_glyph(60.0 + i * 6.0, 60.0, char) for i, char in enumerate("Capo3")]
    geo = geometry.PageGeometry(glyphs=glyphs)
    fields = extract._header_fields(geo, SYSTEM)
    assert fields.get("capo") == "3"


def test_capo_and_tuning_reach_gp5(tmp_path):
    ir = {
        "title": "t", "artist": "", "tempo": 80, "capo": 2,
        "tuning": [64, 59, 55, 50, 45, 38],
        "measures": [{
            "index": 0, "time_sig": [4, 4], "kind": "fret", "beats": [
                {"x": 0, "duration": 1, "dotted": False, "chord": None,
                 "from_chord": False, "stroke": None, "lyric": None,
                 "techniques": [], "notes": [{"string": 6, "fret": 0}]}]}],
        "warnings": [],
    }
    out = tmp_path / "capo.gp5"
    build.write_gp5(build.build_song(ir), str(out))
    track = gp.parse(str(out), encoding="cp949").tracks[0]
    assert track.offset == 2
    assert [s.value for s in track.strings] == [64, 59, 55, 50, 45, 38]


def test_cli_tuning_and_capo_flags(tmp_path):
    import convert

    pdf = _score_pdf(tmp_path / "syn.pdf", barlines=[240.0, 440.0],
                     note_xs=[50.0, 100.0, 150.0, 200.0,
                              250.0, 300.0, 350.0, 400.0])
    out = tmp_path / "syn.gp5"
    code = convert.main([pdf, "-o", str(out), "--tuning", "DADGBE", "--capo", "2"])
    assert code == 0
    track = gp.parse(str(out), encoding="cp949").tracks[0]
    assert track.offset == 2
    assert [s.value for s in track.strings] == [64, 59, 55, 50, 45, 38]
    assert convert.main([pdf, "-o", str(out), "--tuning", "broken"]) == 2


# ── #2 타브 쉼표는 rest beat 이 되어야 한다 ──────────────────────────────────

REST_QUARTER = chr(0xE4E5)      # restQuarter
REST_8TH = chr(0xE4E6)


def test_rest_glyph_becomes_rest_beat_with_pinned_duration():
    """쉼표는 이름이 곧 길이다 — x 간격 추정 대신 4분쉼표로 고정된다.

    음표 x=60, 쉼표 x=230 이면 x 간격은 반반(2.0/2.0)이지만, 쉼표가 4분(1.0)
    으로 고정되므로 음표가 점2분(3.0)이 되어야 합이 맞는다.
    """
    glyphs = [
        _glyph(60.0, SYSTEM.tab_ys[2], "0"),
        _glyph(230.0, SYSTEM.tab_ys[3], REST_QUARTER),
    ]
    measure, warn = _measure_from(glyphs)
    assert len(measure["beats"]) == 2
    note_beat, rest_beat = measure["beats"]
    assert rest_beat["rest"] is True and rest_beat["notes"] == []
    assert (rest_beat["duration"], rest_beat["dotted"]) == (4, False)
    assert (note_beat["duration"], note_beat["dotted"]) == (2, True)
    assert not any(w["kind"] in ("empty_beat", "duration_mismatch", "unsupported_glyph")
                   for w in warn.items), "쉼표가 반영됐는데 경고가 남았다"


def test_pinned_durations_constrain_the_fit():
    from tab_pdf import durations

    pinned = {1: durations.LegalDuration(4, False, 1.0)}
    fitted, exact = durations.fit_durations([2.0, 2.0], 4.0, pinned=pinned)
    assert exact
    assert [d.quarters for d in fitted] == [3.0, 1.0]


def test_rest_and_silent_beats_reach_gp5_as_rest_status(tmp_path):
    """#9 — 무음 beat 은 normal/0노트가 아니라 GP 표준인 rest 로 직렬화된다."""
    beat = {"x": 0, "duration": 4, "dotted": False, "chord": None,
            "from_chord": False, "stroke": None, "lyric": None,
            "techniques": [], "notes": []}
    ir = {
        "title": "t", "artist": "", "tempo": 80,
        "tuning": [64, 59, 55, 50, 45, 40],
        "measures": [
            {"index": 0, "time_sig": [4, 4], "kind": "fret", "beats": [
                dict(beat, rest=True),                      # 명시적 쉼표
                dict(beat, from_chord=True),                # 보이싱 모르는 슬래시
                dict(beat, notes=[{"string": 3, "fret": 0}]),
                dict(beat, notes=[{"string": 3, "fret": 0}]),
            ]},
            {"index": 1, "time_sig": [4, 4], "kind": "empty", "beats": []},
        ],
        "warnings": [],
    }
    out = tmp_path / "rest.gp5"
    build.write_gp5(build.build_song(ir), str(out))
    track = gp.parse(str(out), encoding="cp949").tracks[0]
    first = track.measures[0].voices[0].beats
    assert [b.status.name for b in first] == ["rest", "rest", "normal", "normal"]
    # 빈 마디는 beat 0개가 아니라 온쉼표 하나다
    empty = track.measures[1].voices[0].beats
    assert len(empty) == 1
    assert empty[0].status.name == "rest" and empty[0].duration.value == 1


# ── #1 두 자리 프렛은 한 음으로 병합되어야 한다 ──────────────────────────────

def test_two_digit_fret_merges_into_one_note():
    """'1'+'2' 가 5pt 간격으로 붙어 있으면 12프렛 한 음이다 — 두 beat 이 아니다."""
    glyphs = [
        _glyph(60.0, SYSTEM.tab_ys[2], "1"),
        _glyph(65.0, SYSTEM.tab_ys[2], "2"),
    ]
    measure, _ = _measure_from(glyphs)
    assert len(measure["beats"]) == 1, "두 자리 프렛이 두 beat 으로 쪼개졌다"
    assert measure["beats"][0]["notes"] == [{"string": 3, "fret": 12}]


def test_distinct_notes_seven_points_apart_stay_separate():
    """실측: 별개 음의 최소 간격은 6.8pt — 7pt 는 빠른 연속 음 둘이다."""
    glyphs = [
        _glyph(60.0, SYSTEM.tab_ys[2], "1"),
        _glyph(67.0, SYSTEM.tab_ys[2], "2"),
    ]
    measure, _ = _measure_from(glyphs)
    assert len(measure["beats"]) == 2
    assert [b["notes"][0]["fret"] for b in measure["beats"]] == [1, 2]


def test_impossible_merge_stays_separate_with_warning():
    """붙은 '2'+'5' 는 25프렛이 될 수 없다 — 별개 음으로 두되 경고한다."""
    glyphs = [
        _glyph(60.0, SYSTEM.tab_ys[2], "2"),
        _glyph(65.0, SYSTEM.tab_ys[2], "5"),
    ]
    measure, warn = _measure_from(glyphs)
    assert sorted(n["fret"] for b in measure["beats"] for n in b["notes"]) == [2, 5]
    assert any(w["kind"] == "unsupported_glyph" and "붙어" in w["detail"]
               for w in warn.items)


def test_two_digit_frets_on_different_strings_do_not_merge():
    """다른 줄의 세로로 쌓인 화음 숫자는 병합 대상이 아니다."""
    glyphs = [
        _glyph(60.0, SYSTEM.tab_ys[1], "1"),
        _glyph(60.0, SYSTEM.tab_ys[2], "2"),
    ]
    measure, _ = _measure_from(glyphs)
    assert len(measure["beats"]) == 1
    assert sorted((n["string"], n["fret"]) for n in measure["beats"][0]["notes"]) \
        == [(2, 1), (3, 2)]


# ── #3 못갖춘마디(픽업)·불완전 마지막 마디 ───────────────────────────────────

def _score_pdf(path, barlines, note_xs, left=40.0):
    """지정한 마디선·노트 배치로 합성 악보 PDF 를 만든다."""
    melody_ys = [100.0, 105.0, 110.0, 115.0, 120.0]
    tab_ys = [160.0, 168.0, 176.0, 184.0, 192.0, 200.0]
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for y in melody_ys + tab_ys:
        page.draw_line((left, y), (barlines[-1], y), width=0.4)
    for x in barlines:
        page.draw_line((x, melody_ys[0]), (x, tab_ys[-1]), width=0.6)
    for x in note_xs:
        page.insert_text((x, tab_ys[2]), "0", fontsize=9.3)
    doc.save(str(path))
    doc.close()
    return str(path)


def test_pickup_measure_gets_a_short_time_signature(tmp_path):
    """한 음짜리 좁은 첫 마디는 온음표가 아니라 1/4 마디가 되어야 한다."""
    pdf = _score_pdf(
        tmp_path / "pickup.pdf",
        barlines=[120.0, 320.0, 520.0],
        # m1: 한 음(60) — 폭 80pt. m2·m3: 4분음 4개씩 (gap 50 ≈ 47.5pt/4분)
        note_xs=[60.0,
                 130.0, 180.0, 230.0, 280.0,
                 330.0, 380.0, 430.0, 480.0])
    ir = extract.extract_ir(pdf, title="pickup")
    first = ir["measures"][0]
    assert first["time_sig"] == [1, 4], f"픽업 마디 박자표: {first['time_sig']}"
    assert [(b["duration"], b["dotted"]) for b in first["beats"]] == [(4, False)]
    assert any(w["kind"] == "pickup_measure" for w in ir["warnings"])
    # 가운데·마지막 마디는 4/4 그대로다
    assert ir["measures"][1]["time_sig"] == [4, 4]
    assert ir["measures"][2]["time_sig"] == [4, 4]


def test_full_first_measure_is_not_mistaken_for_pickup(tmp_path):
    """정상 폭의 첫 마디를 픽업으로 오판하면 안 된다."""
    pdf = _score_pdf(
        tmp_path / "full.pdf",
        barlines=[240.0, 440.0, 640.0],
        note_xs=[50.0, 100.0, 150.0, 200.0,
                 250.0, 300.0, 350.0, 400.0,
                 450.0, 500.0, 550.0, 600.0])
    ir = extract.extract_ir(pdf, title="full")
    assert all(m["time_sig"] == [4, 4] for m in ir["measures"])
    assert not any(w["kind"] == "pickup_measure" for w in ir["warnings"])


# ── #17 아티큘레이션 글리프는 AI 없이도 결정론적으로 매핑되어야 한다 ─────────

ACCENT_BELOW = chr(0xE4A1)      # articAccentBelow
STACCATO_ABOVE = chr(0xE4A2)    # articStaccatoAbove
TENUTO_ABOVE = chr(0xE4A4)      # articTenuto — GP5 에 자리가 없어 경고 유지


def test_accent_glyph_maps_to_beat_technique():
    glyphs = _one_beat_glyphs() + [_glyph(61.0, SYSTEM.tab_ys[5], ACCENT_BELOW)]
    measure, warn = _measure_from(glyphs)
    assert {"string": None, "kind": "accent"} in measure["beats"][0]["techniques"]
    assert not any("아티큘레이션" in w["detail"] for w in warn.items), \
        "매핑된 악센트가 미반영으로도 집계됐다"


def test_staccato_glyph_maps_but_tenuto_stays_warned():
    mapped, _ = _measure_from(
        _one_beat_glyphs() + [_glyph(61.0, SYSTEM.tab_ys[5], STACCATO_ABOVE)])
    assert {"string": None, "kind": "staccato"} in mapped["beats"][0]["techniques"]
    _, warn = _measure_from(
        _one_beat_glyphs() + [_glyph(61.0, SYSTEM.tab_ys[5], TENUTO_ABOVE)])
    assert any("아티큘레이션" in w["detail"] for w in warn.items)


@needs_pdf
def test_real_pdf_accents_are_carried_without_ai():
    """이 악보의 악센트 44개가 --ai 없이 그대로 반영된다."""
    ir = extract.extract_ir(str(PDF), tempo=80)
    accents = [t for m in ir["measures"] for b in m["beats"]
               for t in b["techniques"] if t["kind"] == "accent"]
    assert len(accents) == 44
    assert all(t["string"] is None for t in accents), "악센트는 박 전체 표기다"


# ── #10 스트로크는 프렛 노트가 있는 beat 에서도 반영되어야 한다 ──────────────

def test_stroke_lands_on_fret_beat_too():
    """스트럼 화살표가 슬래시 beat 전용이 아니다 — 프렛 코드 위에도 그려진다."""
    glyphs = [
        _glyph(60.0, SYSTEM.tab_ys[0], "3"),
        _glyph(60.0, SYSTEM.tab_ys[1], "0"),
        _glyph(62.0, SYSTEM.tab_ys[4], extract.SMUFL_STROKE_DOWN),
    ]
    measure, _ = _measure_from(glyphs)
    assert measure["beats"][0]["stroke"] == "down"


# ── #7 fret 마디에 표기된 코드명도 beat 에 배정되어야 한다 ───────────────────

def test_chord_token_lands_on_fret_beat():
    """코드 행 토큰이 슬래시 beat 없이도 그 자리 beat 의 chord 가 된다."""
    glyphs = [
        _glyph(60.0, SYSTEM.tab_ys[2], "0"),
        _glyph(150.0, SYSTEM.tab_ys[2], "2"),
    ]
    measure, _ = _measure_from(glyphs, tokens=[(58.0, "Am"), (149.0, "G")])
    assert [b["chord"] for b in measure["beats"]] == ["Am", "G"]
    # 음은 프렛 숫자에서 온 그대로다 — 코드 배정이 음을 갈아치우면 안 된다
    assert measure["beats"][0]["notes"] == [{"string": 3, "fret": 0}]
    assert not measure["beats"][0]["from_chord"]


# ── #5 반복·진행 기호가 GP5 direction 으로 옮겨져야 한다 ────────────────────

def _one_beat_glyphs() -> list:
    return [_glyph(60.0, SYSTEM.tab_ys[2], "0")]


def test_segno_glyph_becomes_direction_target():
    glyphs = _one_beat_glyphs() + [_glyph(45.0, 90.0, SEGNO)]
    measure, _ = _measure_from(glyphs)
    assert measure.get("direction") == "Segno"


def test_coda_near_measure_start_is_target_near_end_is_jump():
    at_start, _ = _measure_from(_one_beat_glyphs() + [_glyph(45.0, 90.0, CODA)])
    assert at_start.get("direction") == "Coda"
    at_end, _ = _measure_from(_one_beat_glyphs() + [_glyph(390.0, 90.0, CODA)])
    assert at_end.get("from_direction") == "Da Coda"


def test_ds_al_coda_text_becomes_jump():
    x = 200.0
    text_glyphs = []
    for i, char in enumerate("D.S.alCoda"):
        text_glyphs.append(_glyph(x + i * 6.0, 140.0, char))    # between 대역
    measure, _ = _measure_from(_one_beat_glyphs() + text_glyphs)
    assert measure.get("from_direction") == "Da Segno al Coda"


def test_directions_reach_gp5_headers(tmp_path):
    ir = {
        "title": "t", "artist": "", "tempo": 80,
        "tuning": [64, 59, 55, 50, 45, 40],
        "measures": [
            {"index": 0, "time_sig": [4, 4], "kind": "fret", "direction": "Segno",
             "beats": [{"x": 0, "duration": 1, "dotted": False, "chord": None,
                        "from_chord": False, "stroke": None, "lyric": None,
                        "techniques": [], "notes": [{"string": 3, "fret": 0}]}]},
            {"index": 1, "time_sig": [4, 4], "kind": "fret",
             "from_direction": "Da Segno al Coda",
             "beats": [{"x": 0, "duration": 1, "dotted": False, "chord": None,
                        "from_chord": False, "stroke": None, "lyric": None,
                        "techniques": [], "notes": [{"string": 3, "fret": 0}]}]},
        ],
        "warnings": [],
    }
    out = tmp_path / "dir.gp5"
    build.write_gp5(build.build_song(ir), str(out))
    headers = gp.parse(str(out), encoding="cp949").measureHeaders
    assert headers[0].direction is not None and headers[0].direction.name == "Segno"
    assert (headers[1].fromDirection is not None
            and headers[1].fromDirection.name == "Da Segno al Coda")


REPEAT_LEFT = chr(0xE040)       # 반복 시작 바라인
REPEAT_RIGHT = chr(0xE041)      # 반복 끝 바라인


def test_repeat_barline_glyphs_become_repeat_flags():
    opened, _ = _measure_from(_one_beat_glyphs() + [_glyph(42.0, 160.0, REPEAT_LEFT)])
    assert opened.get("repeat_open") is True
    closed, _ = _measure_from(_one_beat_glyphs() + [_glyph(395.0, 160.0, REPEAT_RIGHT)])
    assert closed.get("repeat_close") is True


def test_repeat_flags_reach_gp5_headers(tmp_path):
    beat = {"x": 0, "duration": 1, "dotted": False, "chord": None,
            "from_chord": False, "stroke": None, "lyric": None,
            "techniques": [], "notes": [{"string": 3, "fret": 0}]}
    ir = {
        "title": "t", "artist": "", "tempo": 80,
        "tuning": [64, 59, 55, 50, 45, 40],
        "measures": [
            {"index": 0, "time_sig": [4, 4], "kind": "fret",
             "repeat_open": True, "beats": [dict(beat)]},
            {"index": 1, "time_sig": [4, 4], "kind": "fret",
             "repeat_close": True, "beats": [dict(beat)]},
        ],
        "warnings": [],
    }
    out = tmp_path / "rep.gp5"
    build.write_gp5(build.build_song(ir), str(out))
    headers = gp.parse(str(out), encoding="cp949").measureHeaders
    assert headers[0].isRepeatOpen
    assert headers[1].repeatClose >= 1


def test_ornament_in_tab_band_is_warned_not_silent():
    glyphs = _one_beat_glyphs() + [_glyph(150.0, SYSTEM.tab_ys[2], ORNAMENT_TRILL)]
    _, warn = _measure_from(glyphs)
    assert any(w["kind"] == "unsupported_glyph" and "장식음" in w["detail"]
               for w in warn.items)


@needs_pdf
def test_real_pdf_directions_land_on_their_measures():
    """실측: m24 세뇨(도착), m30 코다(도약·마디 끝), m48 D.S. al Coda, m49 코다(도착)."""
    ir = extract.extract_ir(str(PDF), tempo=80)
    got = {m["index"]: (m.get("direction"), m.get("from_direction"))
           for m in ir["measures"]
           if m.get("direction") or m.get("from_direction")}
    assert got == {
        24: ("Segno", None),
        30: (None, "Da Coda"),
        48: (None, "Da Segno al Coda"),
        49: ("Coda", None),
    }


@needs_pdf
def test_real_pdf_every_chord_token_lands_on_a_beat():
    """chord_row 토큰은 같은 마디 beat 또는 다음 마디 첫 beat 에 배정된다.

    이 조판은 마디 마지막 코드를 마디선 직전(모든 beat 뒤)에 표기해 다음
    마디 첫 beat 을 선행 지시한다 — 그 이월까지 포함해 소실이 없어야 한다.
    """
    ir = extract.extract_ir(str(PDF), tempo=80)
    measures = ir["measures"]
    for i, measure in enumerate(measures):
        if not measure["chord_row"]:
            continue
        names = {token["name"] for token in measure["chord_row"]}
        carried = {b["chord"] for b in measure["beats"] if b.get("chord")}
        missing = names - carried
        if missing:
            next_first = (measures[i + 1]["beats"][0].get("chord")
                          if i + 1 < len(measures) and measures[i + 1]["beats"]
                          else None)
            assert missing == {next_first}, (
                f"m{measure['index']} 의 코드 {missing} 가 어디에도 배정되지 않았다")
