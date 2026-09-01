"""실제 PDF(`pdf/나는반딧불.pdf`) 기반 테스트.

`pdf/` 는 gitignore 되므로 입력이 없는 clone 에서는 skip 된다.
입력 없이도 도는 테스트는 `test_synthetic.py` 에 있다.
"""

import pathlib
import guitarpro as gp
import pytest


STANDARD_TUNING = [64, 59, 55, 50, 45, 40]


# ── 실제 PDF 기반 ────────────────────────────────────────────────────────────
import pymupdf

PDF = pathlib.Path(__file__).resolve().parents[1] / "pdf" / "나는반딧불.pdf"
needs_pdf = pytest.mark.skipif(not PDF.exists(), reason=f"입력 PDF 없음: {PDF}")


@needs_pdf
def test_real_pdf_system_and_measure_counts():
    from tab_pdf import geometry

    doc = pymupdf.open(PDF)
    per_page, measures = [], 0
    for page in doc:
        geo = geometry.load_page_geometry(page)
        systems = geometry.find_systems(geo)
        per_page.append(len(systems))
        for system in systems:
            measures += len(geometry.measure_bounds(geo, system))
    assert per_page == [4, 5, 5]
    assert measures == 58


@needs_pdf
def test_real_pdf_system1_coordinates():
    from tab_pdf import geometry

    doc = pymupdf.open(PDF)
    geo = geometry.load_page_geometry(doc[0])
    system = geometry.find_systems(geo)[0]
    assert [round(y, 1) for y in system.melody_ys] == [145.4, 150.5, 155.6, 160.8, 165.8]
    assert [round(y, 1) for y in system.tab_ys] == [206.9, 214.6, 222.2, 229.9, 237.6, 245.3]
    assert [round(x) for x in geometry.find_barlines(geo, system)] == [198, 324, 450, 576]


def _count_stroke_glyphs() -> int:
    """PDF 원본에서 스트로크 기호 글리프를 직접 센다 — 추출기와 독립된 기준값."""
    from tab_pdf import extract, geometry

    total = 0
    doc = pymupdf.open(PDF)
    for page in doc:
        geo = geometry.load_page_geometry(page)
        total += sum(1 for g in geo.glyphs
                     if g.char in (extract.SMUFL_STROKE_DOWN, extract.SMUFL_STROKE_UP))
    return total


EXPECTED_MEASURE1 = [
    [(5, 3)], [(3, 0)], [(1, 0), (2, 3)], [(3, 0)],
    [(6, 0)], [(3, 1)], [(1, 0), (2, 3)], [(3, 1)],
]


@needs_pdf
def test_ir_measure1_exact_beats_and_durations():
    """마디1 = 8 beat / 10 노트, 전부 8분음표. beat 3·7 은 2음 화음."""
    from tab_pdf import extract

    ir = extract.extract_ir(str(PDF), tempo=80)
    beats = ir["measures"][0]["beats"]
    got = [sorted((n["string"], n["fret"]) for n in b["notes"]) for b in beats]
    assert got == [sorted(group) for group in EXPECTED_MEASURE1]
    assert [b["duration"] for b in beats] == [8] * 8
    assert not any(b["dotted"] for b in beats)


@needs_pdf
def test_ir_totals_match_measured_values():
    from tab_pdf import extract

    ir = extract.extract_ir(str(PDF), tempo=80)
    assert len(ir["measures"]) == 58
    assert ir["tuning"] == STANDARD_TUNING
    kinds = {}
    for measure in ir["measures"]:
        kinds[measure["kind"]] = kinds.get(measure["kind"], 0) + 1
    # 실측값. 프렛 마디 32 + 슬래시 마디 26. mixed 는 없다 —
    # 한때 mixed 로 보였던 m32 는 "with 16beat arp play" 주석의 '1','6' 이
    # 프렛으로 오인된 것이었고, 실제로는 순수 슬래시 마디다.
    assert kinds == {"fret": 32, "slash": 26}
    assert sum(len(m["beats"]) for m in ir["measures"]) == 497
    notes = sum(len(b["notes"]) for m in ir["measures"] for b in m["beats"])
    fret_notes = sum(len(b["notes"]) for m in ir["measures"]
                     for b in m["beats"] if not b["chord"])
    assert (notes, fret_notes) == (1560, 294)


@needs_pdf
def test_ir_every_measure_sums_to_its_time_signature():
    """58마디 전부 합제약 스냅 성공. 결함성 경고가 하나도 없어야 한다.

    `unsupported_glyph` 는 정보성이다 — 음정·리듬은 정확히 옮겼지만 아티큘레이션이나
    해머온 같은 표기를 반영하지 못했다는 뜻이고, 조용히 버리지 않았다는 증거다.
    """
    from tab_pdf import extract

    ir = extract.extract_ir(str(PDF), tempo=80)
    DEFECTS = {"duration_mismatch", "empty_measure", "empty_beat",
               "unknown_chord", "unsnapped_digit", "time_signature"}
    defects = [w for w in ir["warnings"] if w["kind"] in DEFECTS]
    assert defects == [], f"결함성 경고가 남았다: {defects[:5]}"
    # 반영하지 못한 표기는 반드시 드러나야 한다 (조용한 손실 방지)
    unsupported = [w for w in ir["warnings"] if w["kind"] == "unsupported_glyph"]
    assert unsupported, "미반영 표기가 경고로 남지 않았다"


@needs_pdf
def test_ir_chord_names_are_exactly_the_five():
    from tab_pdf import extract

    ir = extract.extract_ir(str(PDF), tempo=80)
    seen = set()
    for measure in ir["measures"]:
        for beat in measure["beats"]:
            if beat["chord"]:
                seen.add(beat["chord"])
    assert seen == {"Cadd9", "E7", "Am", "F", "G"}
    assert [w for w in ir["warnings"] if w["kind"] == "unknown_chord"] == []


@needs_pdf
def test_ir_title_is_not_pdf_metadata_mojibake():
    """PDF 메타 제목은 mojibake 된 .musx 파일명이다. 파일명 stem 을 써야 한다."""
    from tab_pdf import extract

    ir = extract.extract_ir(str(PDF))
    assert ir["title"] == "나는반딧불"
    assert ".musx" not in ir["title"]
    override = extract.extract_ir(str(PDF), title="반딧불", artist="황가람")
    assert override["title"] == "반딧불"
    assert override["artist"] == "황가람"


@needs_pdf
def test_gp5_roundtrip_preserves_everything(tmp_path):
    from tab_pdf import build, extract

    ir = extract.extract_ir(str(PDF), tempo=80, artist="황가람")
    out = tmp_path / "나는반딧불.gp5"
    build.write_gp5(build.build_song(ir), str(out))

    reparsed = gp.parse(str(out), encoding="cp949")
    track = reparsed.tracks[0]
    assert reparsed.title == "나는반딧불"
    assert reparsed.artist == "황가람"
    assert reparsed.tempo == 80
    assert [s.value for s in track.strings] == STANDARD_TUNING
    assert len(track.measures) == 58

    ir_notes = sum(len(b["notes"]) for m in ir["measures"] for b in m["beats"])
    gp_notes = sum(len(b.notes)
                   for m in track.measures for v in m.voices for b in v.beats)
    assert gp_notes == ir_notes == 1560

    first = [b for v in track.measures[0].voices for b in v.beats]
    assert [b.duration.value for b in first] == [8] * 8
    assert [sorted((n.string, n.value) for n in b.notes) for b in first] == \
           [sorted(group) for group in EXPECTED_MEASURE1]


@needs_pdf
def test_gp5_records_strum_direction(tmp_path):
    """추출한 다운/업 스트로크가 .gp5 에 남아야 한다."""
    from tab_pdf import build, extract

    ir = extract.extract_ir(str(PDF))
    ir_strokes = [b for m in ir["measures"] for b in m["beats"] if b["stroke"]]
    # PDF 안의 실제 스트로크 글리프 수와 같아야 한다 (독립 oracle).
    # 시스템 범위를 제한하지 않았을 때는 다른 시스템 기호까지 집어 27개로 부풀었다.
    assert len(ir_strokes) == _count_stroke_glyphs(), f"IR 스트로크 {len(ir_strokes)}개"

    out = tmp_path / "stroke.gp5"
    build.write_gp5(build.build_song(ir), str(out))
    reparsed = gp.parse(str(out), encoding="cp949")
    recorded = [b for m in reparsed.tracks[0].measures for v in m.voices
                for b in v.beats if b.effect.stroke.value]
    assert len(recorded) == len(ir_strokes), ".gp5 에 스트로크가 보존되지 않았다"


@needs_pdf
def test_cli_end_to_end(tmp_path):
    """실제 악보를 CLI 로 변환해 .gp5 가 나오고 결함성 경고가 없어야 한다."""
    import convert

    out = tmp_path / "out.gp5"
    code = convert.main([str(PDF), "-o", str(out), "--tempo", "80",
                         "--artist", "황가람", "--ir", str(tmp_path / "ir.json")])
    assert code == 0, "결함성 경고가 있다"
    song = gp.parse(str(out), encoding="cp949")
    assert song.title == "나는반딧불"
    assert song.artist == "황가람"
    assert len(song.tracks[0].measures) == 58


@needs_pdf
def test_gp5_has_chord_diagrams_at_chord_changes(tmp_path):
    """코드가 바뀌는 지점마다 다이어그램이 붙어야 한다.

    첫 페이지 상단 코드 목록은 GP7/8 의 DiagramCollection 기능이라 .gp5 로는
    표현할 수 없다. GP5 는 beat 에 붙이는 방식만 있다.
    """
    from tab_pdf import build, chords, extract

    ir = extract.extract_ir(str(PDF))
    out = tmp_path / "chords.gp5"
    build.write_gp5(build.build_song(ir), str(out))

    song = gp.parse(str(out), encoding="cp949")
    diagrams = [b.effect.chord
                for m in song.tracks[0].measures for v in m.voices
                for b in v.beats if b.effect.chord]
    assert diagrams, ".gp5 에 코드 다이어그램이 없다"
    assert {d.name for d in diagrams} == set(chords.VOICINGS)

    # 매 beat 가 아니라 변화 지점에만 — 코드 붙은 beat 수보다 훨씬 적어야 한다
    chord_beats = sum(1 for m in ir["measures"] for b in m["beats"] if b["chord"])
    assert len(diagrams) < chord_beats / 2, \
        f"다이어그램 {len(diagrams)}개 / 코드 beat {chord_beats}개 — 너무 많다"

    # 보이싱이 정확히 옮겨졌는지 (0=1번줄 … 5=6번줄, 미사용 -1)
    sample = next(d for d in diagrams if d.name == "Cadd9")
    expected = dict(chords.VOICINGS["Cadd9"])
    assert list(sample.strings) == [expected.get(i + 1, -1) for i in range(6)]


@needs_pdf
def test_techniques_land_on_the_right_string(tmp_path):
    """H/P/S 표기가 직전 노트의 줄에 붙어야 한다.

    표기 자체의 y 는 대상 줄과 무관하다 — 실측에서 표기 y 는 5·3·4·1번줄로
    흩어지는데 대상은 전부 2번줄이었다. 직전 프렛 노트의 줄을 써야 한다.
    """
    from tab_pdf import build, extract

    ir = extract.extract_ir(str(PDF))
    techniques = [(m["index"], t)
                  for m in ir["measures"] for b in m["beats"]
                  for t in b.get("techniques", ())]
    assert len(techniques) == 6, f"연주법 {len(techniques)}개"
    assert all(t["string"] == 2 for _, t in techniques), "전부 2번줄이어야 한다"
    kinds = sorted(t["kind"] for _, t in techniques)
    assert kinds == ["hammer"] * 4 + ["slide"] * 2

    out = tmp_path / "tech.gp5"
    build.write_gp5(build.build_song(ir), str(out))
    song = gp.parse(str(out), encoding="cp949")
    notes = [n for m in song.tracks[0].measures for v in m.voices
             for b in v.beats for n in b.notes]
    assert sum(1 for n in notes if n.effect.hammer) == 4
    assert sum(1 for n in notes if n.effect.slides) == 2
    assert all(n.string == 2 for n in notes if n.effect.hammer or n.effect.slides)


@needs_pdf
def test_lyrics_are_extracted_and_complete(tmp_path):
    """가사 317자가 한 글자도 유실되지 않아야 한다.

    보컬이 기타 아르페지오보다 촘촘한 구간이 있어(한 마디에 음절 10개 vs beat 8.6개)
    beat 하나에 둘 이상이 몰린다. 버리면 "나는내가빛나는" 이 "나빛나는" 이 되므로
    이어 붙인다.
    """
    from tab_pdf import build, extract

    ir = extract.extract_ir(str(PDF))
    assigned = [b["lyric"] for m in ir["measures"] for b in m["beats"] if b["lyric"]]
    assert sum(len(text) for text in assigned) == 317

    first_line = "".join(
        b["lyric"] or "" for m in ir["measures"] if 8 <= m["index"] < 12
        for b in m["beats"])
    assert first_line == "나는내가빛나는별인줄알았어요한번도의심한적없었죠"

    out = tmp_path / "lyrics.gp5"
    build.write_gp5(build.build_song(ir), str(out))
    song = gp.parse(str(out), encoding="cp949")
    line = song.lyrics.lines[0]
    assert line.startingMeasure == 9
    tokens = line.lyrics.split(" ")
    assert sum(len(t) for t in tokens) == 317, "왕복에서 가사가 유실됐다"


def test_lyric_extraction_excludes_music_glyphs():
    """SMuFL 음악 기호는 유니코드 사설 영역이라 한글 하한을 그냥 넘는다.

    제외하지 않으면 가사 317자가 436자로 부풀었다.
    """
    from tab_pdf import extract

    assert extract._in_range("", extract.PRIVATE_USE_RANGE)   # noteheadBlack
    assert not extract._in_range("나", extract.PRIVATE_USE_RANGE)
    assert ord("") >= extract.LYRIC_MIN_CODEPOINT, "하한만으로는 못 걸러낸다"


@needs_pdf
def test_extractor_never_leaves_a_technique_without_a_note_to_hold_it():
    """줄이 지정된 연주법은 그 beat 에 그 줄의 음이 있어야 한다.

    이 불변식이 깨지면 build 가 연주법을 조용히 버린다. AI 보정 쪽에는
    `corrections` 가 같은 검사를 하지만 1차 추출에는 관문이 없으므로,
    코드가 아니라 테스트로 못박아 둔다.
    """
    from tab_pdf import extract

    ir = extract.extract_ir(str(PDF))
    orphans = [
        (measure["index"], position, technique)
        for measure in ir["measures"]
        for position, beat in enumerate(measure["beats"])
        for technique in beat["techniques"]
        if technique["string"] is not None
        and technique["string"] not in {n["string"] for n in beat["notes"]}
    ]
    assert not orphans, f"붙을 음이 없는 연주법 {len(orphans)}개: {orphans[:3]}"


@needs_pdf
def test_chord_derived_beats_carry_no_extractor_techniques():
    """`from_chord` beat 에 연주법이 붙으면 코드명 보정이 그것을 고아로 만들 수 있다.

    추출기 구조상 연주법은 프렛 글리프 x 에 걸리고 `from_chord` beat 은 슬래시
    글리프에서 오므로 겹치지 않는다. 이 추론이 계속 참인지 실제 악보로 확인한다.
    """
    from tab_pdf import extract

    ir = extract.extract_ir(str(PDF))
    offenders = [(m["index"], i) for m in ir["measures"]
                 for i, b in enumerate(m["beats"])
                 if b["from_chord"] and b["techniques"]]
    assert not offenders, f"from_chord beat 에 연주법이 있다: {offenders[:5]}"
