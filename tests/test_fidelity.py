"""2-way 교차 리뷰에서 나온 충실도 결함의 회귀 테스트.

합성 geometry(가짜 Glyph)로 extract 내부를 직접 검증한다 — SMuFL 사설 영역
글리프는 합성 PDF 의 기본 폰트로 넣을 수 없어서다. 실제 PDF 기반 수치 검증은
`test_tab_pdf.py` 에 있다.
"""

import pathlib

import guitarpro as gp
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
