"""실제 PDF(`pdf/나는반딧불.pdf`) 기반 테스트.

`pdf/` 는 gitignore 되므로 입력이 없는 clone 에서는 skip 된다.
입력 없이도 도는 테스트는 `test_synthetic.py` 에 있다.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import guitarpro as gp
import pytest

from controllers.guitar_pro.file_operations import FileOperationsController

STANDARD_TUNING = [64, 59, 55, 50, 45, 40]


def _one_note_song(title="나는 반딧불", artist="황가람"):
    from guitarpro.models import (
        Beat, BeatStatus, Duration, GuitarString, Measure, MeasureHeader,
        Note, NoteType, Song, TimeSignature, Track, Voice,
    )
    song = Song(title=title, artist=artist, tempo=80)
    song.tracks.clear()
    song.measureHeaders.clear()
    track = Track(song, name="Guitar")
    track.strings = [GuitarString(i + 1, v) for i, v in enumerate(STANDARD_TUNING)]
    track.measures.clear()
    header = MeasureHeader(number=1)
    header.timeSignature = TimeSignature()
    song.measureHeaders.append(header)
    measure = Measure(track, header)
    measure.voices.clear()
    voice = Voice(measure)
    beat = Beat(voice, duration=Duration(value=4), status=BeatStatus.normal)
    beat.notes.append(Note(beat, value=3, string=5, type=NoteType.normal))
    voice.beats.append(beat)
    measure.voices.append(voice)
    measure.voices.append(Voice(measure))
    track.measures.append(measure)
    song.tracks.append(track)
    return song


def test_save_and_load_preserve_korean(tmp_path):
    """cp1252 기본값이면 저장에서 UnicodeEncodeError, 읽기에서 mojibake 가 된다."""
    out = tmp_path / "한글제목.gp5"
    controller = FileOperationsController()
    controller.current_song = _one_note_song()
    controller.save_file(str(out))

    reader = FileOperationsController()
    reader.load_file(str(out))
    assert reader.current_song.title == "나는 반딧불"
    assert reader.current_song.artist == "황가람"


def test_server_module_does_not_print_to_stdout():
    """stdio 서버는 stdout 에 MCP 메시지 외 아무것도 쓰지 않아야 한다."""
    source = (pathlib.Path(__file__).resolve().parents[1]
              / "src" / "run_mcp_server.py").read_text(encoding="utf-8")
    offending = [line.strip() for line in source.splitlines()
                 if line.strip().startswith("print(")]
    assert not offending, f"stdout 오염: {offending}"


# ── 실제 PDF 기반 ────────────────────────────────────────────────────────────
import pymupdf

PDF = pathlib.Path(__file__).resolve().parents[2] / "pdf" / "나는반딧불.pdf"
needs_pdf = pytest.mark.skipif(not PDF.exists(), reason=f"입력 PDF 없음: {PDF}")


@needs_pdf
def test_real_pdf_system_and_measure_counts():
    from utils.tab_pdf import geometry

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
    from utils.tab_pdf import geometry

    doc = pymupdf.open(PDF)
    geo = geometry.load_page_geometry(doc[0])
    system = geometry.find_systems(geo)[0]
    assert [round(y, 1) for y in system.melody_ys] == [145.4, 150.5, 155.6, 160.8, 165.8]
    assert [round(y, 1) for y in system.tab_ys] == [206.9, 214.6, 222.2, 229.9, 237.6, 245.3]
    assert [round(x) for x in geometry.find_barlines(geo, system)] == [198, 324, 450, 576]


def _count_stroke_glyphs() -> int:
    """PDF 원본에서 스트로크 기호 글리프를 직접 센다 — 추출기와 독립된 기준값."""
    from utils.tab_pdf import extract, geometry

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
    from utils.tab_pdf import extract

    ir = extract.extract_ir(str(PDF), tempo=80)
    beats = ir["measures"][0]["beats"]
    got = [sorted((n["string"], n["fret"]) for n in b["notes"]) for b in beats]
    assert got == [sorted(group) for group in EXPECTED_MEASURE1]
    assert [b["duration"] for b in beats] == [8] * 8
    assert not any(b["dotted"] for b in beats)


@needs_pdf
def test_ir_totals_match_measured_values():
    from utils.tab_pdf import extract

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
    from utils.tab_pdf import extract

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
    from utils.tab_pdf import extract

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
    from utils.tab_pdf import extract

    ir = extract.extract_ir(str(PDF))
    assert ir["title"] == "나는반딧불"
    assert ".musx" not in ir["title"]
    override = extract.extract_ir(str(PDF), title="반딧불", artist="황가람")
    assert override["title"] == "반딧불"
    assert override["artist"] == "황가람"


@needs_pdf
def test_gp5_roundtrip_preserves_everything(tmp_path):
    from utils.tab_pdf import build, extract

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
    from utils.tab_pdf import build, extract

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
def test_import_tool_end_to_end(tmp_path):
    import mcp_tools
    from controllers import GuitarProController

    controller = GuitarProController()
    result = mcp_tools.import_tab_pdf_impl(
        controller, str(PDF), tempo=80, artist="황가람",
        ir_path=str(tmp_path / "ir.json"))
    assert result["status"] == "success"
    data = result["data"]
    assert (data["measures"], data["beats"], data["notes"]) == (58, 497, 1560)
    assert data["notation_kinds"] == ["fret", "slash"]
    # 결함성 경고는 없고, 미반영 표기 경고만 정보로 남는다
    assert all(w["kind"] == "unsupported_glyph" for w in data["warnings"]), \
        f"결함성 경고: {[w for w in data['warnings'] if w['kind'] != 'unsupported_glyph']}"

    out = tmp_path / "out.gp5"
    controller.save_file(str(out))
    assert gp.parse(str(out), encoding="cp949").title == "나는반딧불"
