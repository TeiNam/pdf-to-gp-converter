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
