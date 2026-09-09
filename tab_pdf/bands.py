"""시스템 안 수직 대역과 스냅 — 글리프가 '어디에' 있는지만 판정한다."""

from . import geometry

# 숫자 baseline 이 이 거리 안이면 그 타브 선에 속한다 (선 간격 7.7 의 절반 미만)
MAX_STRING_SNAP_DISTANCE = 4.0
# 글리프가 타브 staff 에 속한다고 볼 상하 여유 (pt)
TAB_BAND_MARGIN = 8.0
# 코드 행 대역: 멜로디 5선 위쪽 이만큼 (pt). 제목·부제를 배제한다
CHORD_BAND_HEIGHT = 20.0
# 글리프가 멜로디 staff 안에 있다고 볼 상하 여유 (pt)
SPAN_SLACK = 6.0


def in_tab_band(glyph: geometry.Glyph, system: geometry.System) -> bool:
    return (system.tab_ys[0] - TAB_BAND_MARGIN <= glyph.y
            <= system.tab_ys[-1] + TAB_BAND_MARGIN)


def snap_to_string(y: float, tab_ys: tuple[float, ...]) -> int | None:
    """baseline y 를 가장 가까운 타브 선에 붙여 줄 번호(1..6)를 돌려준다."""
    index = min(range(len(tab_ys)), key=lambda i: abs(y - tab_ys[i]))
    if abs(y - tab_ys[index]) > MAX_STRING_SNAP_DISTANCE:
        return None
    return index + 1        # tab_ys 는 위→아래, 위가 고음 E = string 1


def band_of(glyph: geometry.Glyph, system: geometry.System) -> str | None:
    """글리프가 놓인 대역. 시스템 밖이면 None.

    `between` 은 멜로디 staff 와 타브 staff 사이다 — 가사와 H/P/S 연주법 표기가
    이 대역을 공유하므로 이름으로 둘을 구분하지 않는다.
    """
    top, bottom = system.melody_ys[0], system.melody_ys[-1]
    if top - SPAN_SLACK <= glyph.y <= bottom + SPAN_SLACK:
        return "melody"
    if top - CHORD_BAND_HEIGHT <= glyph.y < top - SPAN_SLACK:
        return "chord"
    if bottom + SPAN_SLACK < glyph.y < system.tab_ys[0]:
        return "between"
    if in_tab_band(glyph, system):
        return "tab"
    return None


def nearest_beat(beat_xs: list[float], x: float) -> int | None:
    if not beat_xs:
        return None
    return min(range(len(beat_xs)), key=lambda i: abs(beat_xs[i] - x))
