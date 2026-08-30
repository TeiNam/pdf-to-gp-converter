"""합성 PDF 기반 테스트.

입력 PDF 가 gitignore 되어 없는 clone 에서도 파이프라인 전체가 실행되어야 한다.
실제 악보 기반 검증은 `test_tab_pdf.py` 에 있다.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import pymupdf
import pytest

from utils.tab_pdf import durations


def test_target_quarters():
    assert durations.target_quarters(4, 4) == 4.0
    assert durations.target_quarters(3, 4) == 3.0
    assert durations.target_quarters(6, 8) == 3.0


def test_proportions_sum_to_target():
    """비례값은 스냅 전에 항상 정확히 target 으로 합해진다."""
    props = durations.proportions([10.0, 20.0, 30.0, 40.0],
                                  measure_end_x=50.0, target=4.0)
    assert len(props) == 4
    assert abs(sum(props) - 4.0) < 1e-9


def test_fit_uniform_eighths():
    fitted, exact = durations.fit_durations([0.5] * 8, 4.0)
    assert exact
    assert [d.value for d in fitted] == [8] * 8
    assert not any(d.dotted for d in fitted)


def test_fit_repairs_rounding_drift():
    """독립 스냅이면 합이 4.25 가 되는 입력을 정확히 4.0 으로 맞춘다."""
    fitted, exact = durations.fit_durations([0.26, 0.26, 0.26, 3.22], 4.0)
    assert exact
    assert abs(sum(d.quarters for d in fitted) - 4.0) < 1e-9


def test_fit_reports_failure_instead_of_lying():
    """맞출 수 없으면 조용히 틀린 값을 주지 않고 False 를 돌려준다."""
    fitted, exact = durations.fit_durations([4.0, 4.0], 4.0)
    assert not exact


# ── 합성 악보 ────────────────────────────────────────────────────────────────
# 실제 PDF 시스템1 배치를 축약 재현한다
SYN_MELODY_YS = [100.0, 105.0, 110.0, 115.0, 120.0]
SYN_TAB_YS = [160.0, 168.0, 176.0, 184.0, 192.0, 200.0]
SYN_STAFF_X0, SYN_STAFF_X1 = 40.0, 400.0
SYN_BARLINES = [200.0, 400.0]
SYN_NOTE_XS = [60.0, 100.0, 140.0, 180.0]


def _synthetic_score(path: pathlib.Path) -> pathlib.Path:
    """5선+6선 한 시스템, 마디 2개, 3번선 개방 4음(균등)인 최소 악보."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for y in SYN_MELODY_YS + SYN_TAB_YS:
        page.draw_line((SYN_STAFF_X0, y), (SYN_STAFF_X1, y), width=0.4)
    for x in SYN_BARLINES:
        page.draw_line((x, SYN_MELODY_YS[0]), (x, SYN_TAB_YS[-1]), width=0.6)
    for x in SYN_NOTE_XS:
        page.insert_text((x, SYN_TAB_YS[2]), "0", fontsize=9.3)
    doc.save(str(path))
    doc.close()
    return path


def test_synthetic_finds_one_system(tmp_path):
    from utils.tab_pdf import geometry

    doc = pymupdf.open(_synthetic_score(tmp_path / "syn.pdf"))
    systems = geometry.find_systems(geometry.load_page_geometry(doc[0]))
    assert len(systems) == 1
    assert [round(y) for y in systems[0].melody_ys] == [100, 105, 110, 115, 120]
    assert [round(y) for y in systems[0].tab_ys] == [160, 168, 176, 184, 192, 200]


def test_synthetic_finds_two_measures(tmp_path):
    from utils.tab_pdf import geometry

    doc = pymupdf.open(_synthetic_score(tmp_path / "syn.pdf"))
    geo = geometry.load_page_geometry(doc[0])
    system = geometry.find_systems(geo)[0]
    assert [round(x) for x in geometry.find_barlines(geo, system)] == [200, 400]
    assert len(geometry.measure_bounds(geo, system)) == 2


def test_glyph_carries_ink_end(tmp_path):
    """x_end 가 origin 보다 커야 한다 — 코드명 토큰화가 여기 의존한다."""
    from utils.tab_pdf import geometry

    doc = pymupdf.open(_synthetic_score(tmp_path / "syn.pdf"))
    geo = geometry.load_page_geometry(doc[0])
    digits = [g for g in geo.glyphs if g.char == "0"]
    assert digits
    assert all(g.x_end > g.x for g in digits)


def test_voicings_cover_this_song():
    from utils.tab_pdf import chords

    for name in ("Cadd9", "E7", "Am", "F", "G"):
        voicing = chords.voicing_for(name)
        assert voicing, f"{name} 보이싱 없음"
        assert all(1 <= string <= 6 and fret >= 0 for string, fret in voicing)
        assert len({string for string, _ in voicing}) == len(voicing), "줄 중복"


def test_unknown_chord_returns_none_not_a_guess():
    from utils.tab_pdf import chords

    assert chords.voicing_for("Bm7b5") is None
    assert chords.voicing_for(None) is None


def test_looks_like_chord_rejects_page_numbers():
    """실제 PDF 코드 대역에는 페이지 번호 '2','3' 이 섞여 들어온다."""
    from utils.tab_pdf import chords

    assert chords.looks_like_chord("Cadd9")
    assert chords.looks_like_chord("Bm7")      # 미등록이지만 코드 형태다
    assert not chords.looks_like_chord("2")
    assert not chords.looks_like_chord("3")
    assert not chords.looks_like_chord("H")    # 해머온 표기


def test_synthetic_ir_uniform_quarters(tmp_path):
    """합성 악보 균등 4음 -> 4분음표 4개, 합 4.0. PDF 없이 파이프라인이 돈다."""
    from utils.tab_pdf import extract

    ir = extract.extract_ir(str(_synthetic_score(tmp_path / "syn.pdf")), title="syn")
    assert ir["title"] == "syn"
    assert len(ir["measures"]) == 2
    beats = ir["measures"][0]["beats"]
    assert len(beats) == 4
    assert [b["duration"] for b in beats] == [4, 4, 4, 4]
    assert [b["notes"] for b in beats] == [[{"string": 3, "fret": 0}]] * 4
    assert [w for w in ir["warnings"] if w["kind"] == "duration_mismatch"] == []


def test_rejects_pdf_without_tab_staff(tmp_path):
    from utils.tab_pdf import extract

    plain = tmp_path / "plain.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "hello, not a score")
    doc.save(str(plain))
    doc.close()

    with pytest.raises(extract.NotATabPdf):
        extract.extract_ir(str(plain))


def test_rejects_pdf_without_text_layer(tmp_path):
    from utils.tab_pdf import extract

    blank = tmp_path / "blank.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(blank))
    doc.close()

    with pytest.raises(extract.NotATabPdf):
        extract.extract_ir(str(blank))


def test_synthetic_gp5_roundtrip(tmp_path):
    """PDF 없이도 IR -> .gp5 -> 재파싱 전 과정이 검증된다."""
    import guitarpro as gp
    from utils.tab_pdf import build, extract

    ir = extract.extract_ir(str(_synthetic_score(tmp_path / "syn.pdf")),
                            title="합성곡", artist="테스트")
    out = tmp_path / "syn.gp5"
    build.write_gp5(build.build_song(ir), str(out))

    reparsed = gp.parse(str(out), encoding="cp949")
    assert reparsed.title == "합성곡"        # 한글이 cp949 로 왕복
    track = reparsed.tracks[0]
    assert len(track.measures) == 2
    first = [b for v in track.measures[0].voices for b in v.beats]
    assert [b.duration.value for b in first] == [4, 4, 4, 4]
    assert [sorted((n.string, n.value) for n in b.notes) for b in first] == [[(3, 0)]] * 4


def test_default_output_path_rules(tmp_path):
    """pdf/ 안이면 형제 gp/, 밖이면 PDF 옆 gp/. 폴더는 만들어진다."""
    import mcp_tools

    inside = tmp_path / "pdf" / "song.pdf"
    inside.parent.mkdir()
    inside.touch()
    assert mcp_tools.default_output_path(str(inside)) == str(tmp_path / "gp" / "song.gp5")
    assert (tmp_path / "gp").is_dir()

    outside = tmp_path / "loose" / "song.pdf"
    outside.parent.mkdir()
    outside.touch()
    assert mcp_tools.default_output_path(str(outside)) == str(
        tmp_path / "loose" / "gp" / "song.gp5")


def test_import_tool_reports_error_without_raising():
    import mcp_tools
    from controllers import GuitarProController

    controller = GuitarProController()
    result = mcp_tools.import_tab_pdf_impl(controller, "/nope/none.pdf")
    assert result["status"] == "error"
    assert controller.current_song is None


def test_import_tool_is_atomic_on_ir_write_failure(tmp_path):
    """IR 저장이 실패하면 current_song 을 바꾸지 않고 error 를 돌려준다."""
    import mcp_tools
    from controllers import GuitarProController

    pdf = _synthetic_score(tmp_path / "syn.pdf")
    controller = GuitarProController()
    result = mcp_tools.import_tab_pdf_impl(
        controller, str(pdf), ir_path=str(tmp_path / "no_such_dir" / "ir.json"))
    assert result["status"] == "error"
    assert controller.current_song is None, "실패했는데 상태가 바뀌었다"


def test_import_tool_loads_synthetic_song(tmp_path):
    import mcp_tools
    from controllers import GuitarProController

    pdf = _synthetic_score(tmp_path / "syn.pdf")
    controller = GuitarProController()
    ir_path = tmp_path / "ir.json"
    result = mcp_tools.import_tab_pdf_impl(
        controller, str(pdf), title="합성곡", ir_path=str(ir_path))
    assert result["status"] == "success"
    assert result["data"]["measures"] == 2
    assert result["data"]["suggested_output"].endswith("gp/syn.gp5")
    assert ir_path.exists()
    assert controller.current_song is not None


def test_open_in_guitar_pro_validates_path():
    import mcp_tools

    assert mcp_tools.open_in_guitar_pro_impl("/nope/none.gp5")["status"] == "error"
