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
