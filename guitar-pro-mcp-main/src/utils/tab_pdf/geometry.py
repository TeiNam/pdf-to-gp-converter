"""PDF 저수준 기하 수집. 음악적 해석은 하지 않는다."""

from dataclasses import dataclass, field

MELODY_LINE_COUNT = 5
TAB_LINE_COUNT = 6

# staff 선으로 인정할 수평선 최소 길이 (pt)
MIN_STAFF_LINE_WIDTH = 50.0
# 같은 staff 묶음으로 볼 인접 선 간격 상한 (pt). 타브 간격 ≈7.7, 시스템 간은 40 이상
MAX_INTRA_STAFF_GAP = 12.0
# 선을 수평/수직으로 볼 허용 오차 (pt)
HORIZONTAL_TOLERANCE = 0.5
VERTICAL_TOLERANCE = 1.5
# 선으로 취급할 얇은 사각형의 두께 상한 (pt)
THIN_RECT_MAX = 3.0
# 좌측 시스템 브래킷·시작 마디선을 버리는 x 하한 (pt)
MIN_BARLINE_X = 40.0
# 겹세로선(double bar)을 한 마디선으로 병합할 간격 상한 (pt)
BARLINE_MERGE_GAP = 6.0
# 마디선이 두 staff 를 관통했다고 볼 허용 오차 (pt)
SPAN_TOLERANCE = 2.0
# 타브 최상단 선과 같은 선으로 볼 y 오차 (pt)
SAME_LINE_TOLERANCE = 1.0


@dataclass(frozen=True)
class HLine:
    y: float
    x0: float
    x1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0


@dataclass(frozen=True)
class VLine:
    x: float
    y0: float
    y1: float


@dataclass(frozen=True)
class Glyph:
    x: float          # baseline origin x
    y: float          # baseline origin y
    x_end: float      # 잉크 우측 끝 (bbox[2])
    char: str
    font: str
    size: float


@dataclass
class PageGeometry:
    hlines: list[HLine] = field(default_factory=list)
    vlines: list[VLine] = field(default_factory=list)
    glyphs: list[Glyph] = field(default_factory=list)


@dataclass(frozen=True)
class System:
    """frozen 이므로 좌표는 tuple 로 담는다 — list 면 append 로 내부가 바뀐다."""

    melody_ys: tuple[float, ...]
    tab_ys: tuple[float, ...]


def _iter_segments(page):
    """드로잉을 (x0, y0, x1, y1) 로 평탄화. 얇은 사각형도 선으로 취급한다."""
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                yield item[1].x, item[1].y, item[2].x, item[2].y
            elif item[0] == "re":
                rect = item[1]
                if rect.height <= THIN_RECT_MAX:
                    yield rect.x0, rect.y0, rect.x1, rect.y0
                elif rect.width <= THIN_RECT_MAX:
                    yield rect.x0, rect.y0, rect.x0, rect.y1


def load_page_geometry(page) -> PageGeometry:
    geo = PageGeometry()
    for x0, y0, x1, y1 in _iter_segments(page):
        if abs(y1 - y0) < HORIZONTAL_TOLERANCE:
            geo.hlines.append(HLine(y0, min(x0, x1), max(x0, x1)))
        elif abs(x1 - x0) < VERTICAL_TOLERANCE:
            geo.vlines.append(VLine(x0, min(y0, y1), max(y0, y1)))

    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for ch in span["chars"]:
                    if ch["c"].strip() == "":
                        continue
                    geo.glyphs.append(Glyph(
                        x=ch["origin"][0], y=ch["origin"][1], x_end=ch["bbox"][2],
                        char=ch["c"], font=span["font"], size=round(span["size"], 1),
                    ))
    return geo


def _staff_groups(geo: PageGeometry) -> list[list[float]]:
    ys = sorted({round(h.y, 1) for h in geo.hlines
                 if h.width > MIN_STAFF_LINE_WIDTH})
    if not ys:
        return []
    groups, current = [], [ys[0]]
    for y in ys[1:]:
        if y - current[-1] < MAX_INTRA_STAFF_GAP:
            current.append(y)
        else:
            groups.append(current)
            current = [y]
    groups.append(current)
    return groups


def find_systems(geo: PageGeometry) -> list[System]:
    """5선(멜로디) + 6선(타브) 인접쌍을 한 시스템으로 묶는다."""
    groups = _staff_groups(geo)
    return [System(melody_ys=tuple(a), tab_ys=tuple(b))
            for a, b in zip(groups, groups[1:])
            if len(a) == MELODY_LINE_COUNT and len(b) == TAB_LINE_COUNT]


def find_barlines(geo: PageGeometry, system: System) -> list[float]:
    """시스템의 멜로디 최상단 ~ 타브 최하단을 관통하는 세로선의 x."""
    top, bottom = system.melody_ys[0], system.tab_ys[-1]
    xs = sorted({round(v.x, 1) for v in geo.vlines
                 if v.x > MIN_BARLINE_X
                 and v.y0 <= top + SPAN_TOLERANCE
                 and v.y1 >= bottom - SPAN_TOLERANCE})
    merged: list[float] = []
    for x in xs:
        if not merged or x - merged[-1] > BARLINE_MERGE_GAP:
            merged.append(x)
    return merged


def tab_left_edge(geo: PageGeometry, system: System) -> float:
    """타브 최상단 선의 좌측 끝 — 첫 마디의 시작 경계."""
    return min((h.x0 for h in geo.hlines
                if abs(h.y - system.tab_ys[0]) < SAME_LINE_TOLERANCE),
               default=0.0)


def measure_bounds(geo: PageGeometry, system: System) -> list[tuple[float, float]]:
    """마디별 [x0, x1) 경계 목록.

    좌측 끝보다 앞에 검출된 세로선은 버린다 — 남기면 (50, 45) 같은 역방향 경계가
    되어 가짜 빈 마디가 생기고 이후 마디 번호가 밀린다.
    """
    left = tab_left_edge(geo, system)
    bars = [x for x in find_barlines(geo, system) if x > left]
    if not bars:
        return []
    edges = [left] + bars
    return [(edges[i], edges[i + 1]) for i in range(len(bars))]
