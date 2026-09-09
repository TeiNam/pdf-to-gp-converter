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


# ── #18 타이·꾸밈음 ──────────────────────────────────────────────────────────

def test_geometry_collects_curves(tmp_path):
    path = tmp_path / "curve.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.draw_bezier((100, 200), (110, 190), (130, 190), (140, 200))
    doc.save(str(path))
    doc.close()
    geo = geometry.load_page_geometry(pymupdf.open(str(path))[0])
    assert len(geo.curves) == 1
    curve = geo.curves[0]
    assert (round(curve.x0), round(curve.x1)) == (100, 140)


def test_tie_curve_marks_the_second_note():
    """같은 (줄, 프렛)을 잇는 곡선은 타이다 — 두 번째 음이 tie 가 된다."""
    glyphs = [
        _glyph(60.0, SYSTEM.tab_ys[2], "3"),
        _glyph(150.0, SYSTEM.tab_ys[2], "3"),
    ]
    curves = [geometry.Curve(x0=63.0, y0=178.0, x1=149.0, y1=178.0)]
    geo = geometry.PageGeometry(glyphs=glyphs, curves=curves)
    warn = extract._Warnings()
    measure = extract._build_measure(
        geo, SYSTEM, BOUNDS, 0, [], warn, (4, 4),
        extract.build_letter_index(geo), [])
    extract._apply_tie_curves(geo, SYSTEM, [measure], warn)
    notes = [n for b in measure["beats"] for n in b["notes"]]
    assert notes[0].get("tie") is None
    assert notes[1].get("tie") is True


def test_slur_curve_between_different_frets_is_not_a_tie():
    glyphs = [
        _glyph(60.0, SYSTEM.tab_ys[2], "3"),
        _glyph(150.0, SYSTEM.tab_ys[2], "5"),
    ]
    curves = [geometry.Curve(x0=63.0, y0=178.0, x1=149.0, y1=178.0)]
    geo = geometry.PageGeometry(glyphs=glyphs, curves=curves)
    warn = extract._Warnings()
    measure = extract._build_measure(
        geo, SYSTEM, BOUNDS, 0, [], warn, (4, 4),
        extract.build_letter_index(geo), [])
    extract._apply_tie_curves(geo, SYSTEM, [measure], warn)
    assert all(n.get("tie") is None for b in measure["beats"] for n in b["notes"])


def test_far_away_curve_is_not_a_tie():
    """끝점이 beat 에서 먼 곡선(배경 삽화 등)은 타이가 아니다."""
    glyphs = [
        _glyph(200.0, SYSTEM.tab_ys[2], "3"),
        _glyph(250.0, SYSTEM.tab_ys[2], "3"),
    ]
    curves = [geometry.Curve(x0=60.0, y0=178.0, x1=380.0, y1=178.0)]
    geo = geometry.PageGeometry(glyphs=glyphs, curves=curves)
    warn = extract._Warnings()
    measure = extract._build_measure(
        geo, SYSTEM, BOUNDS, 0, [], warn, (4, 4),
        extract.build_letter_index(geo), [])
    extract._apply_tie_curves(geo, SYSTEM, [measure], warn)
    assert all(n.get("tie") is None for b in measure["beats"] for n in b["notes"])


def test_unattachable_grace_is_dropped_with_warning_not_carried_forever():
    """짝을 못 찾은 꾸밈음은 다음 마디까지만 보고 경고와 함께 버린다."""
    first = [_glyph(60.0, SYSTEM.tab_ys[4], "3", size=8.0),     # 5번줄 꾸밈음
             _glyph(100.0, SYSTEM.tab_ys[2], "0")]              # 3번줄 음뿐
    measure1, warn = _measure_from(first)
    pending = measure1.pop("pending_graces")
    assert pending, "꾸밈음이 이월되지 않았다"
    second = [_glyph(60.0, SYSTEM.tab_ys[2], "0")]              # 여전히 3번줄뿐
    geo = geometry.PageGeometry(glyphs=second)
    measure2 = extract._build_measure(
        geo, SYSTEM, BOUNDS, 1, [], warn, (4, 4),
        extract.build_letter_index(geo), [], pending_graces=pending)
    assert measure2.pop("pending_graces") == [], "꾸밈음이 계속 이월된다"
    assert any(w["kind"] == "grace_dropped" for w in warn.items)


def test_pending_row_chord_skips_rest_beat():
    """이월된 코드는 쉼표가 아니라 첫 소리 나는 beat 에 붙는다."""
    glyphs = [
        _glyph(60.0, SYSTEM.tab_ys[3], REST_QUARTER),
        _glyph(150.0, SYSTEM.tab_ys[2], "0"),
    ]
    geo = geometry.PageGeometry(glyphs=glyphs)
    warn = extract._Warnings()
    measure = extract._build_measure(
        geo, SYSTEM, BOUNDS, 0, [], warn, (4, 4),
        extract.build_letter_index(geo), [], pending_row_chord="Am")
    rest_beat, sound_beat = measure["beats"]
    assert rest_beat["chord"] is None
    assert sound_beat["chord"] == "Am"


def test_tie_reaches_gp5(tmp_path):
    beat = {"x": 0, "duration": 2, "dotted": False, "chord": None,
            "from_chord": False, "stroke": None, "lyric": None, "techniques": []}
    ir = {
        "title": "t", "artist": "", "tempo": 80,
        "tuning": [64, 59, 55, 50, 45, 40],
        "measures": [{
            "index": 0, "time_sig": [4, 4], "kind": "fret", "beats": [
                dict(beat, notes=[{"string": 3, "fret": 3}]),
                dict(beat, x=10, notes=[{"string": 3, "fret": 3, "tie": True}]),
            ]}],
        "warnings": [],
    }
    out = tmp_path / "tie.gp5"
    build.write_gp5(build.build_song(ir), str(out))
    beats = gp.parse(str(out), encoding="cp949").tracks[0].measures[0].voices[0].beats
    assert beats[1].notes[0].type == gp.models.NoteType.tie


def test_small_digit_becomes_a_grace_note_not_a_beat():
    """8pt 소형 숫자는 자기 beat 을 만들지 않고 다음 음의 꾸밈음이 된다."""
    glyphs = [
        _glyph(60.0, SYSTEM.tab_ys[2], "0"),
        _glyph(140.0, SYSTEM.tab_ys[2], "3", size=8.0),     # 꾸밈음
        _glyph(150.0, SYSTEM.tab_ys[2], "5"),
    ]
    measure, _ = _measure_from(glyphs)
    assert len(measure["beats"]) == 2, "꾸밈음이 beat 을 만들었다"
    principal = measure["beats"][1]["notes"][0]
    assert principal == {"string": 3, "fret": 5, "grace_fret": 3}


def test_grace_reaches_gp5(tmp_path):
    ir = {
        "title": "t", "artist": "", "tempo": 80,
        "tuning": [64, 59, 55, 50, 45, 40],
        "measures": [{
            "index": 0, "time_sig": [4, 4], "kind": "fret", "beats": [
                {"x": 0, "duration": 1, "dotted": False, "chord": None,
                 "from_chord": False, "stroke": None, "lyric": None,
                 "techniques": [],
                 "notes": [{"string": 3, "fret": 5, "grace_fret": 3}]}]}],
        "warnings": [],
    }
    out = tmp_path / "grace.gp5"
    build.write_gp5(build.build_song(ir), str(out))
    note = gp.parse(str(out), encoding="cp949").tracks[0].measures[0].voices[0].beats[0].notes[0]
    assert note.effect.grace is not None and note.effect.grace.fret == 3


@needs_pdf
def test_real_pdf_grace_notes_attach_to_principals():
    """실측: 8pt 소형 숫자 6개 — beat 이 아니라 꾸밈음으로 반영된다."""
    ir = extract.extract_ir(str(PDF), tempo=80)
    graces = [(m["index"], n["string"], n["grace_fret"], n["fret"])
              for m in ir["measures"] for b in m["beats"] for n in b["notes"]
              if n.get("grace_fret") is not None]
    assert graces, "꾸밈음이 하나도 안 잡혔다"
    assert all(string in (1, 2) for _, string, _, _ in graces)


# ── #16 프롬프트 인젝션이 관문 안에서 할 수 있는 왜곡을 줄인다 ────────────────

def _corr_beat(x, notes=(), lyric=None, chord=None):
    return {"x": x, "duration": 8, "dotted": False, "chord": chord,
            "from_chord": False, "stroke": None, "techniques": [],
            "lyric": lyric,
            "notes": [{"string": s, "fret": f} for s, f in notes]}


def _corr_ir(measure):
    return {"title": "t", "artist": "", "tempo": 80,
            "tuning": [64, 59, 55, 50, 45, 40],
            "measures": [measure], "warnings": []}


def test_chord_correction_cannot_swap_row_positions():
    """마디 안 표기 코드끼리 자리를 바꾸는 제안은 위치 결속에 걸린다."""
    from tab_pdf import corrections

    measure = {"index": 0, "time_sig": [4, 4], "kind": "fret",
               "beats": [_corr_beat(15.0, [(2, 3)]), _corr_beat(205.0, [(2, 5)])],
               "glyphs": [],
               "chord_row": [{"x": 10.0, "name": "F"}, {"x": 200.0, "name": "G"}],
               "chord_in_effect": None}
    swap = [{"op": "chord", "measure": 0, "beat": 1, "name": "F"}]
    _, outcome = corrections.apply_corrections(_corr_ir(measure), swap)
    assert not outcome.applied
    assert "위치" in outcome.rejected[0]["reason"]

    honest = [{"op": "chord", "measure": 0, "beat": 1, "name": "G"}]
    _, outcome = corrections.apply_corrections(_corr_ir(measure), honest)
    assert len(outcome.applied) == 1, outcome.rejected


def test_lyric_correction_cannot_pile_syllables_far_away():
    """음절을 원래 자리에서 2 beat 넘게 옮기는 재배치는 거절된다."""
    from tab_pdf import corrections

    measure = {"index": 0, "time_sig": [4, 4], "kind": "fret",
               "beats": [_corr_beat(float(i * 10), [(2, 3)],
                                    lyric=char)
                         for i, char in enumerate("가나다라")],
               "glyphs": [], "chord_row": [], "chord_in_effect": None}
    pile = [{"op": "lyric", "measure": 0, "beat": 0, "text": "가나다라"}]
    _, outcome = corrections.apply_corrections(_corr_ir(measure), pile)
    assert not outcome.applied
    assert "beat" in outcome.rejected[0]["reason"]

    nudge = [{"op": "lyric", "measure": 0, "beat": 0, "text": "가"},
             {"op": "lyric", "measure": 0, "beat": 1, "text": "나"},
             {"op": "lyric", "measure": 0, "beat": 2, "text": "다라"}]
    _, outcome = corrections.apply_corrections(_corr_ir(measure), nudge)
    assert len(outcome.applied) == 3, outcome.rejected


# ── #4 셋잇단 ────────────────────────────────────────────────────────────────

def test_fit_finds_triplets_when_proportions_say_so():
    from tab_pdf import durations

    third = 1.0 / 3.0
    fitted, exact = durations.fit_durations(
        [third, third, third, 1.0, 1.0, 1.0], 4.0, allow_tuplets=True)
    assert exact
    assert [(d.value, d.tuplet) for d in fitted[:3]] == [(8, (3, 2))] * 3
    assert all(d.tuplet is None for d in fitted[3:])


def test_plain_rhythm_never_flips_to_triplets():
    """정수 비례에는 셋잇단이 끼어들면 안 된다 — 동률이면 평범한 쪽."""
    from tab_pdf import durations

    fitted, exact = durations.fit_durations([0.5] * 8, 4.0, allow_tuplets=True)
    assert exact
    assert [d.value for d in fitted] == [8] * 8
    assert all(d.tuplet is None for d in fitted)


TUPLET_3 = chr(0xE883)          # 잇단음표 숫자 '3'


def test_triplet_beats_extracted_when_marked():
    """'3' 표기가 있는 마디에서 1박 3등분이 셋잇단 8분음표로 스냅된다."""
    xs = [60.0, 90.0, 120.0, 150.0, 240.0, 330.0]
    glyphs = [_glyph(x, SYSTEM.tab_ys[2], "0") for x in xs]
    glyphs.append(_glyph(90.0, SYSTEM.tab_ys[5], TUPLET_3))
    measure, warn = _measure_from(glyphs)
    got = [(b["duration"], b.get("tuplet")) for b in measure["beats"]]
    assert got[:3] == [(8, [3, 2])] * 3, got
    assert got[3:] == [(4, None)] * 3, got
    assert not any(w["kind"] == "duration_mismatch" for w in warn.items)


def test_no_tuplet_marker_means_no_triplets():
    """표기 없는 마디에는 x 간격이 3등분에 가까워도 셋잇단을 만들지 않는다."""
    xs = [60.0, 90.0, 120.0, 150.0, 240.0, 330.0]
    glyphs = [_glyph(x, SYSTEM.tab_ys[2], "0") for x in xs]
    measure, _ = _measure_from(glyphs)
    assert all(b.get("tuplet") is None for b in measure["beats"])


def test_triplet_reaches_gp5(tmp_path):
    ir = {
        "title": "t", "artist": "", "tempo": 80,
        "tuning": [64, 59, 55, 50, 45, 40],
        "measures": [{
            "index": 0, "time_sig": [4, 4], "kind": "fret", "beats": (
                [{"x": i, "duration": 8, "dotted": False, "tuplet": [3, 2],
                  "chord": None, "from_chord": False, "stroke": None,
                  "lyric": None, "techniques": [],
                  "notes": [{"string": 3, "fret": 0}]} for i in range(3)]
                + [{"x": 9, "duration": 4, "dotted": False, "chord": None,
                    "from_chord": False, "stroke": None, "lyric": None,
                    "techniques": [], "notes": [{"string": 3, "fret": 0}]}] * 3
            )}],
        "warnings": [],
    }
    out = tmp_path / "trip.gp5"
    build.write_gp5(build.build_song(ir), str(out))
    beats = gp.parse(str(out), encoding="cp949").tracks[0].measures[0].voices[0].beats
    assert (beats[0].duration.tuplet.enters, beats[0].duration.tuplet.times) == (3, 2)
    assert beats[3].duration.tuplet.enters == 1


# ── 2라운드 보완 ─────────────────────────────────────────────────────────────

SMUFL_SHARP = chr(0xE262)       # accidentalSharp
SMUFL_FLAT = chr(0xE260)


def test_smufl_accidental_in_chord_row_becomes_ascii():
    """C + SMuFL♯ + m 은 'Cm' 이 아니라 'C#m' 이다."""
    glyphs = [
        _glyph(60.0, 95.0, "C"),
        geometry.Glyph(x=65.5, y=93.0, x_end=69.0, char=SMUFL_SHARP,
                       font="f", size=7.0),
        _glyph(69.5, 95.0, "m"),
    ]
    geo = geometry.PageGeometry(glyphs=glyphs)
    assert extract._chord_tokens(geo, SYSTEM, 400.0) == [(60.0, "C#m")]


def test_literal_music_accidentals_are_normalized_to_ascii():
    """'♯/♭' 는 cp949 에 없어 GP5 직렬화가 죽는다 — #/b 로 눕힌다."""
    glyphs = ([_glyph(60.0, 95.0, "B"), _glyph(65.5, 95.0, "♭"),
               _glyph(71.0, 95.0, "7")])
    geo = geometry.PageGeometry(glyphs=glyphs)
    assert extract._chord_tokens(geo, SYSTEM, 400.0) == [(60.0, "Bb7")]


def test_row_token_already_carried_by_slash_beat_does_not_hop():
    """슬래시 beat 이 이미 표현한 토큰이 뒤 beat 으로 흘러가면 안 된다."""
    glyphs = [
        _glyph(150.0, SYSTEM.tab_ys[2], "0"),               # 프렛 beat
    ]
    geo = geometry.PageGeometry(glyphs=[
        *glyphs,
        geometry.Glyph(x=60.0, y=SYSTEM.tab_ys[2], x_end=65.0,
                       char=chr(0xE101), font="f", size=9.3),   # 슬래시
    ])
    warn = extract._Warnings()
    measure = extract._build_measure(
        geo, SYSTEM, BOUNDS, 0, [(58.0, "Am")], warn, (4, 4),
        extract.build_letter_index(geo), [])
    slash_beat, fret_beat = measure["beats"]
    assert slash_beat["chord"] == "Am" and slash_beat["from_chord"]
    assert fret_beat["chord"] is None, "슬래시가 표현한 Am 이 프렛 beat 으로 흘렀다"


def test_stale_pending_chord_is_dropped_when_first_beat_has_its_own_token():
    measure, _ = _measure_from(
        [_glyph(60.0, SYSTEM.tab_ys[2], "0")],
        tokens=[(58.0, "G")])
    # pending 은 자기 토큰이 있는 첫 beat 을 밀어내지 못한다
    geo = geometry.PageGeometry(glyphs=[_glyph(60.0, SYSTEM.tab_ys[2], "0")])
    warn = extract._Warnings()
    measure = extract._build_measure(
        geo, SYSTEM, BOUNDS, 0, [(58.0, "G")], warn, (4, 4),
        extract.build_letter_index(geo), [], pending_row_chord="Cadd9")
    assert [b["chord"] for b in measure["beats"]] == ["G"]


def test_fixed_voicings_require_matching_tuning():
    """Drop-D 에서 표준 G 모양은 6번줄이 F 가 된다 — 검증 실패로 거부해야 한다."""
    from tab_pdf import chords

    drop_d = [64, 59, 55, 50, 45, 38]
    assert chords.voicing_for("G", drop_d) is None
    assert chords.voicing_for("G", [64, 59, 55, 50, 45, 40]) is not None
    assert chords.voicing_in({"tuning": drop_d}, "G") is None


def test_uniform_small_font_score_has_no_graces(tmp_path):
    """정규 프렛이 8pt 인 악보에서 모든 음이 꾸밈음으로 오인되면 안 된다."""
    path = tmp_path / "small.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    melody = [100.0, 105.0, 110.0, 115.0, 120.0]
    tab = [160.0, 168.0, 176.0, 184.0, 192.0, 200.0]
    for y in melody + tab:
        page.draw_line((40.0, y), (400.0, y), width=0.4)
    page.draw_line((400.0, melody[0]), (400.0, tab[-1]), width=0.6)
    for x in (60.0, 150.0, 240.0, 330.0):
        page.insert_text((x, tab[2]), "0", fontsize=8.0)    # 전부 8pt
    doc.save(str(path))
    doc.close()
    ir = extract.extract_ir(str(path), title="small")
    beats = ir["measures"][0]["beats"]
    assert len(beats) == 4, "8pt 정규 프렛이 꾸밈음으로 오인돼 beat 이 사라졌다"
    assert all(not n.get("grace_fret") for b in beats for n in b["notes"])


def test_quintuplet_mark_does_not_enable_triplets():
    xs = [60.0, 90.0, 120.0, 150.0, 240.0, 330.0]
    glyphs = [_glyph(x, SYSTEM.tab_ys[2], "0") for x in xs]
    glyphs.append(_glyph(90.0, SYSTEM.tab_ys[5], chr(0xE885)))  # tuplet '5'
    measure, warn = _measure_from(glyphs)
    assert all(b.get("tuplet") is None for b in measure["beats"])
    assert any("잇단음표" in w["detail"] for w in warn.items)


def test_melody_band_tuplet_mark_does_not_enable_guitar_triplets():
    xs = [60.0, 90.0, 120.0, 150.0, 240.0, 330.0]
    glyphs = [_glyph(x, SYSTEM.tab_ys[2], "0") for x in xs]
    glyphs.append(_glyph(90.0, 110.0, TUPLET_3))                # 멜로디 대역
    measure, _ = _measure_from(glyphs)
    assert all(b.get("tuplet") is None for b in measure["beats"])


def test_dotted_rest_pins_its_dotted_duration():
    """점4분쉼표는 1.0 이 아니라 1.5 로 고정되어야 한다."""
    glyphs = [
        _glyph(60.0, SYSTEM.tab_ys[2], "0"),
        _glyph(150.0, SYSTEM.tab_ys[2], "0"),
        _glyph(230.0, SYSTEM.tab_ys[3], REST_QUARTER),
        geometry.Glyph(x=238.0, y=SYSTEM.tab_ys[3], x_end=240.0,
                       char=chr(0xE1E7), font="f", size=9.3),   # 붙임점
    ]
    measure, warn = _measure_from(glyphs)
    rest_beat = measure["beats"][2]
    assert (rest_beat["duration"], rest_beat["dotted"]) == (4, True)
    assert not any(w["kind"] == "duration_mismatch" for w in warn.items)


def test_repeat_rightleft_closes_previous_and_opens_current(tmp_path):
    """:‖: 겹반복은 앞 마디를 닫고 이 마디를 연다 — 한 마디에 둘 다가 아니다."""
    pdf = _score_pdf(tmp_path / "rr.pdf", barlines=[240.0, 440.0],
                     note_xs=[50.0, 100.0, 150.0, 200.0,
                              250.0, 300.0, 350.0, 400.0])
    doc = pymupdf.open(pdf)
    geo = geometry.load_page_geometry(doc[0])
    system = geometry.find_systems(geo)[0]
    # 겹반복 글리프가 두 번째 마디 시작(경계 직후)에 있다
    geo.glyphs.append(geometry.Glyph(
        x=242.0, y=system.tab_ys[0], x_end=247.0,
        char=chr(0xE042), font="f", size=12.0))
    measures = []
    warn = extract._Warnings()
    letter_index = extract.build_letter_index(geo)
    for i, bounds in enumerate(geometry.measure_bounds(geo, system)):
        m = extract._build_measure(geo, system, bounds, i, [], warn, (4, 4),
                                   letter_index, [])
        m.pop("pending_row_chord", None); m.pop("pending_graces", None)
        measures.append(m)
    extract._resolve_boundary_repeats(measures)
    assert measures[0].get("repeat_close") is True
    assert measures[0].get("repeat_open") is None
    assert measures[1].get("repeat_open") is True
    assert measures[1].get("repeat_close") is None


# ── 3라운드 보완 ─────────────────────────────────────────────────────────────

def test_far_dot_does_not_make_a_rest_dotted():
    """다른 줄·다른 마디의 붙임점이 쉼표를 점쉼표로 만들면 안 된다."""
    glyphs = [
        _glyph(60.0, SYSTEM.tab_ys[2], "0"),
        _glyph(150.0, SYSTEM.tab_ys[2], "0"),
        _glyph(230.0, SYSTEM.tab_ys[3], REST_QUARTER),
        # 붙임점이 x 로는 가깝지만 1번줄(다른 세로 위치)의 점음표 것이다
        geometry.Glyph(x=238.0, y=SYSTEM.tab_ys[0], x_end=240.0,
                       char=chr(0xE1E7), font="f", size=9.3),
    ]
    measure, _ = _measure_from(glyphs)
    rest_beat = measure["beats"][2]
    assert rest_beat["dotted"] is False, "남의 붙임점이 쉼표에 붙었다"


def test_rest_only_measure_carries_pending_chord_forward():
    """소리 나는 beat 이 없는 마디는 이월 코드를 삼키지 말고 넘겨야 한다."""
    glyphs = [_glyph(200.0, SYSTEM.tab_ys[3], REST_QUARTER)]
    geo = geometry.PageGeometry(glyphs=glyphs)
    warn = extract._Warnings()
    measure = extract._build_measure(
        geo, SYSTEM, BOUNDS, 0, [], warn, (4, 4),
        extract.build_letter_index(geo), [], pending_row_chord="Am")
    assert measure.pop("pending_row_chord") == "Am", "이월 코드가 사라졌다"


def test_extra_verse_start_is_its_first_syllable_measure(tmp_path, monkeypatch):
    """시스템 중간에서 시작하는 2절은 그 마디에서 시작해야 한다."""
    pdf = _score_pdf(tmp_path / "verse.pdf", barlines=[240.0, 440.0],
                     note_xs=[50.0, 100.0, 150.0, 200.0,
                              250.0, 300.0, 350.0, 400.0])
    monkeypatch.setattr(
        extract, "_lyric_syllables",
        lambda geo, system, index, warn: (
            [(50.0, "가"), (250.0, "나")],
            [[(260.0, "다"), (300.0, "라")]]))     # 2절은 두 번째 마디부터
    ir = extract.extract_ir(pdf, title="verse")
    assert ir["extra_lyric_rows"] == [{"start": 1, "text": "다 라"}]


# ── #19 진행 지시 단어는 코드가 아니다 ───────────────────────────────────────

def test_progression_words_are_not_chords():
    """'Fine'·'Coda' 는 A~G 로 시작하지만 코드명이 아니다."""
    from tab_pdf import chords

    assert not chords.looks_like_chord("Fine")
    assert not chords.looks_like_chord("Coda")
    # 한 글자 루트는 여전히 코드다
    assert chords.looks_like_chord("F")
    assert chords.looks_like_chord("C")


# ── #14 코드명 위첨자·유니코드 임시표 ────────────────────────────────────────

def test_unicode_accidental_chord_is_recognized():
    from tab_pdf import chords

    assert chords.looks_like_chord("C♯m")
    assert chords.looks_like_chord("B♭7")


def test_superscript_quality_joins_its_root():
    """위첨자 'add9'(작은 크기, 살짝 위 baseline)가 루트와 한 토큰이어야 한다."""
    glyphs = [_glyph(60.0, 95.0, "C", size=10.0)]
    for i, char in enumerate("add9"):
        glyphs.append(geometry.Glyph(
            x=66.0 + i * 4.0, y=92.0, x_end=66.0 + i * 4.0 + 3.5,
            char=char, font="f", size=7.0))
    geo = geometry.PageGeometry(glyphs=glyphs)
    tokens = extract._chord_tokens(geo, SYSTEM, 400.0)
    assert tokens == [(60.0, "Cadd9")], f"위첨자가 쪼개졌다: {tokens}"


def test_stacked_text_rows_in_chord_band_stay_separate():
    """코드 대역의 위아래 두 행(10pt 간격)은 한 토큰으로 붙으면 안 된다."""
    glyphs = [
        _glyph(60.0, 85.0, "A"), _glyph(65.0, 85.0, "m"),
        _glyph(60.0, 96.0, "G"),
    ]
    geo = geometry.PageGeometry(glyphs=glyphs)
    tokens = extract._chord_tokens(geo, SYSTEM, 400.0)
    assert sorted(name for _, name in tokens) == ["Am", "G"]


# ── #15 박자표 견고성 ────────────────────────────────────────────────────────

def _timesig_digit(value: int) -> str:
    return chr(0xE080 + value)


def test_two_digit_numerator_is_parsed():
    """12/8 — 분자 숫자 두 개는 x 열이 아니라 y 행(위/아래)으로 갈라 읽는다."""
    glyphs = [
        _glyph(100.0, 105.0, _timesig_digit(1)),
        _glyph(105.0, 105.0, _timesig_digit(2)),
        _glyph(102.0, 115.0, _timesig_digit(8)),
    ]
    geo = geometry.PageGeometry(glyphs=glyphs)
    warn = extract._Warnings()
    assert extract._detect_time_signatures(geo, SYSTEM, 0, warn) \
        == [(100.0, (12, 8))]


def test_common_and_cut_time_glyphs():
    common = geometry.PageGeometry(glyphs=[_glyph(100.0, 110.0, chr(0xE08A))])
    cut = geometry.PageGeometry(glyphs=[_glyph(100.0, 110.0, chr(0xE08B))])
    warn = extract._Warnings()
    assert extract._detect_time_signatures(common, SYSTEM, 0, warn) \
        == [(100.0, (4, 4))]
    assert extract._detect_time_signatures(cut, SYSTEM, 0, warn) \
        == [(100.0, (2, 2))]


def test_unparsable_timesig_digits_are_warned():
    """분모 없는 외톨이 숫자는 조용히 무시하지 않는다."""
    geo = geometry.PageGeometry(glyphs=[_glyph(100.0, 105.0, _timesig_digit(3))])
    warn = extract._Warnings()
    assert extract._detect_time_signatures(geo, SYSTEM, 0, warn) == []
    assert any(w["kind"] == "time_signature" for w in warn.items)


def test_mid_system_time_signature_change_applies_from_its_measure(
        tmp_path, monkeypatch):
    """시스템 중간의 박자 변경이 시스템 첫 마디로 소급되면 안 된다."""
    pdf = _score_pdf(tmp_path / "mid.pdf", barlines=[240.0, 390.0],
                     note_xs=[50.0, 100.0, 150.0, 200.0,   # m0: 4/4 4분음 4개
                              250.0, 295.0, 340.0])        # m1: 3/4 4분음 3개
    monkeypatch.setattr(
        extract, "_detect_time_signatures",
        lambda geo, system, index, warn: [(45.0, (4, 4)), (245.0, (3, 4))])
    ir = extract.extract_ir(pdf, title="mid")
    assert ir["measures"][0]["time_sig"] == [4, 4]
    assert ir["measures"][1]["time_sig"] == [3, 4]
    assert [b["duration"] for b in ir["measures"][1]["beats"]] == [4, 4, 4]
    assert not any(w["kind"] == "duration_mismatch" for w in ir["warnings"])


# ── #12 다절 가사는 x순으로 섞이면 안 된다 ───────────────────────────────────

def test_second_lyric_row_does_not_interleave_with_first():
    """1절과 2절이 위아래 두 행이면, beat 배정은 1절만 받는다."""
    row1_y, row2_y = 140.0, 150.0            # 멜로디(120)~타브(160) 사이 두 행
    glyphs = [
        _glyph(60.0, row1_y, "가"), _glyph(150.0, row1_y, "나"),
        _glyph(60.0, row2_y, "다"), _glyph(150.0, row2_y, "라"),
    ]
    geo = geometry.PageGeometry(glyphs=glyphs)
    warn = extract._Warnings()
    syllables, extra_rows = extract._lyric_syllables(geo, SYSTEM, 0, warn)
    assert [char for _, char in syllables] == ["가", "나"], "절이 섞였다"
    assert [[char for _, char in row] for row in extra_rows] == [["다", "라"]]
    assert any(w["kind"] == "lyric_extra_rows" for w in warn.items)


def test_extra_lyric_rows_land_on_gp5_lyric_lines(tmp_path):
    ir = {
        "title": "t", "artist": "", "tempo": 80,
        "tuning": [64, 59, 55, 50, 45, 40],
        "extra_lyric_rows": [{"start": 0, "text": "다 라"}],
        "measures": [{
            "index": 0, "time_sig": [4, 4], "kind": "fret", "beats": [
                {"x": 0, "duration": 2, "dotted": False, "chord": None,
                 "from_chord": False, "stroke": None, "lyric": "가",
                 "techniques": [], "notes": [{"string": 3, "fret": 0}]},
                {"x": 10, "duration": 2, "dotted": False, "chord": None,
                 "from_chord": False, "stroke": None, "lyric": "나",
                 "techniques": [], "notes": [{"string": 3, "fret": 0}]},
            ]}],
        "warnings": [],
    }
    out = tmp_path / "verse.gp5"
    build.write_gp5(build.build_song(ir), str(out))
    lyrics = gp.parse(str(out), encoding="cp949").lyrics
    assert lyrics.lines[0].lyrics == "가 나"
    assert lyrics.lines[1].lyrics == "다 라", "2절이 GP5 가사 줄로 넘어가지 않았다"


# ── #11 시스템·페이지 조용한 스킵 금지 ───────────────────────────────────────

def test_unpairable_staff_group_is_warned(tmp_path):
    """5+7선처럼 짝지을 수 없는 staff 는 조용히 사라지면 안 된다."""
    path = tmp_path / "odd.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    melody = [100.0, 105.0, 110.0, 115.0, 120.0]
    tab = [160.0, 168.0, 176.0, 184.0, 192.0, 200.0]
    seven = [400.0 + i * 8.0 for i in range(7)]          # 7선 — 타브가 아니다
    for y in melody + tab + seven:
        page.draw_line((40.0, y), (400.0, y), width=0.4)
    page.draw_line((400.0, melody[0]), (400.0, tab[-1]), width=0.6)
    for x in (60.0, 150.0, 240.0, 330.0):
        page.insert_text((x, tab[2]), "0", fontsize=9.3)
    doc.save(str(path))
    doc.close()

    ir = extract.extract_ir(str(path), title="odd")
    assert len(ir["measures"]) == 1
    skipped = [w for w in ir["warnings"] if w["kind"] == "system_skipped"]
    assert skipped, "짝지어지지 않은 staff 그룹이 경고 없이 사라졌다"


def test_system_without_barlines_is_warned(tmp_path):
    """마디선이 없는 시스템은 마디를 만들 수 없다 — 경고로 드러나야 한다."""
    path = tmp_path / "nobar.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    melody = [100.0, 105.0, 110.0, 115.0, 120.0]
    tab = [160.0, 168.0, 176.0, 184.0, 192.0, 200.0]
    melody2 = [400.0, 405.0, 410.0, 415.0, 420.0]
    tab2 = [460.0, 468.0, 476.0, 484.0, 492.0, 500.0]
    for y in melody + tab + melody2 + tab2:
        page.draw_line((40.0, y), (400.0, y), width=0.4)
    page.draw_line((400.0, melody[0]), (400.0, tab[-1]), width=0.6)  # 시스템1만
    for x in (60.0, 150.0, 240.0, 330.0):
        page.insert_text((x, tab[2]), "0", fontsize=9.3)
        page.insert_text((x, tab2[2]), "0", fontsize=9.3)
    doc.save(str(path))
    doc.close()

    ir = extract.extract_ir(str(path), title="nobar")
    skipped = [w for w in ir["warnings"] if w["kind"] == "system_skipped"]
    assert skipped, "마디선 없는 시스템이 경고 없이 사라졌다"


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
