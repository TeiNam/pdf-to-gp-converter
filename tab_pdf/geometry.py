"""PDF 저수준 기하 수집. 음악적 해석은 하지 않는다."""

from collections import Counter
from dataclasses import dataclass, field
from itertools import pairwise

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
# 아래 문턱들이 전제한 staff 선 간격 (pt) — 실측 대상 악보의 인접 선 간격 중앙값.
# 다른 배율로 내보낸 PDF 는 좌표를 이 간격에 맞춰 되돌린 뒤 해석한다
# (실측: 125% 확대본은 정규화 없이 음 1564개 중 291개를 잃었다)
REFERENCE_STAFF_GAP = 7.7
# 아래 문턱들이 전제한 프렛 숫자 크기 (pt) — 실측 대상 악보의 숫자 크기 최빈값
REFERENCE_DIGIT_SIZE = 9.3
# 선 간격과 숫자 크기로 잰 두 배율이 이만큼 안에서 맞아야 페이지 배율로 믿는다.
# 확대·축소 내보내기는 둘을 함께 바꾸고, 조판 차이(좁은 타브 간격)는 한쪽만 바꾼다
SCALE_AGREEMENT = 0.1
# 프렛 숫자의 baseline 은 자기 줄에서 선 간격의 절반 안에 있다 (실측 3.3pt / 7.7pt)
FRET_ON_LINE_RATIO = 0.6
# 배율을 모를 때 staff 묶음을 가르는 선 간격 배수 — 선 사이는 1배, staff 사이는 4배 이상
STAFF_GROUP_RATIO = 1.6
# 이 안의 배율 차이는 문턱들의 여유로 흡수된다 — 좌표를 건드리지 않는다
# (합성 악보의 8pt 타브 간격은 기준보다 4% 넓지만 그대로 읽힌다)
SCALE_TOLERANCE = 0.05


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
class Beam:
    """두께를 걷어낸 빔 중심선. 기울어진 빔도 그대로 보존한다."""

    x0: float
    y0: float
    x1: float
    y1: float

    def y_at(self, x: float) -> float:
        return self.y0 + (self.y1 - self.y0) * (x - self.x0) / (self.x1 - self.x0)


@dataclass(frozen=True)
class Curve:
    """타이·슬러의 양 끝점과 호가 부푼 방향."""

    x0: float
    y0: float
    x1: float
    y1: float
    above: bool | None = None


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
    curves: list[Curve] = field(default_factory=list)
    beams: list[Beam] = field(default_factory=list)


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


def _beam_polygon(points, scale: float = 1.0) -> Beam | None:
    """검은 사각형의 좌우 변을 잇는다. 삽화·슬러의 임의 경로는 받지 않는다."""
    x0, x1 = min(p.x for p in points), max(p.x for p in points)
    if x1 - x0 < 3.0 * scale:
        return None
    left = [p.y for p in points if abs(p.x - x0) < 0.5 * scale]
    right = [p.y for p in points if abs(p.x - x1) < 0.5 * scale]
    if len(left) + len(right) != len(points) or not left or not right:
        return None
    if not all(0.8 * scale <= max(ys) - min(ys) <= 4.0 * scale for ys in (left, right)):
        return None
    y0, y1 = (min(left) + max(left)) / 2, (min(right) + max(right)) / 2
    if abs(y1 - y0) > (x1 - x0) / 2:
        return None
    return Beam(x0, y0, x1, y1)


def _drawing_beams(drawing, scale: float = 1.0):
    """빔은 검게 채운 사각형/평행사변형 또는 두꺼운 선이다. 문턱은 배율을 따른다."""
    items = drawing["items"]
    fill = drawing.get("fill")
    if fill is not None and all(c <= 0.1 for c in fill):
        for item in items:
            if item[0] == "re":
                rect = item[1]
                beam = _beam_polygon([rect.tl, rect.tr, rect.bl, rect.br], scale)
                if beam is not None:
                    yield beam
            elif item[0] == "qu":
                beam = _beam_polygon(list(item[1]), scale)
                if beam is not None:
                    yield beam
        if len(items) == 4 and all(item[0] == "l" for item in items):
            beam = _beam_polygon([item[1] for item in items], scale)
            if beam is not None:
                yield beam
    elif (fill is None and drawing.get("color") is not None
          and all(c <= 0.1 for c in drawing["color"])
          and 0.8 * scale <= (drawing.get("width") or 0) <= 4.0 * scale):
        for item in items:
            if item[0] != "l":
                continue
            a, b = sorted(item[1:], key=lambda p: p.x)
            if b.x - a.x >= 3.0 * scale and abs(b.y - a.y) <= (b.x - a.x) / 2:
                yield Beam(a.x, a.y, b.x, b.y)


def page_scale(hlines: list[HLine], glyphs: list[Glyph]) -> float:
    """staff 선 간격과 숫자 크기가 함께 말하는 페이지 배율. 모르면 1.

    선 간격만 믿으면 타브 간격이 좁게 조판된 악보를 축소본으로 오인해 9.3pt
    프렛을 11.9pt 로 키우고, 프렛 크기 상한에 걸려 음이 전부 사라진다.
    """
    ys = sorted({round(h.y, 1) for h in hlines if h.width > MIN_STAFF_LINE_WIDTH})
    gaps = sorted(b - a for a, b in pairwise(ys))
    if not gaps:
        return 1.0
    # 시스템 하나에 선 사이 간격 9개, 시스템 사이 간격 1~2개 — 중앙값은 선 간격이다
    scale = gaps[len(gaps) // 2] / REFERENCE_STAFF_GAP
    if abs(scale - 1.0) < SCALE_TOLERANCE:
        return 1.0
    # 타브(6선) 줄 위의 숫자(프렛)만 센다 — 머리글 날짜·오선 위 운지 숫자가
    # 최빈값을 차지하면 배율을 잘못 확증한다. 묶음은 선 간격에 비례해 가른다
    gap = gaps[len(gaps) // 2]
    groups, current = [], [ys[0]]
    for y in ys[1:]:
        if y - current[-1] <= STAFF_GROUP_RATIO * gap:
            current.append(y)
        else:
            groups.append(current)
            current = [y]
    groups.append(current)
    tab_lines = [y for group in groups if len(group) == TAB_LINE_COUNT for y in group]
    sizes = Counter(g.size for g in glyphs if g.char.isdigit() and tab_lines
                    and min(abs(g.y - y) for y in tab_lines) <= FRET_ON_LINE_RATIO * gap)
    if not sizes:
        return 1.0      # 확인할 숫자가 없다 — 조판 차이일 수 있어 건드리지 않는다
    digit_scale = sizes.most_common(1)[0][0] / REFERENCE_DIGIT_SIZE
    return scale if abs(digit_scale / scale - 1.0) <= SCALE_AGREEMENT else 1.0


def _normalized(geo: PageGeometry, scale: float) -> PageGeometry:
    """모든 좌표·글자 크기를 기준 배율로 되돌린 새 기하."""
    if scale == 1.0:
        return geo
    return PageGeometry(
        hlines=[HLine(h.y / scale, h.x0 / scale, h.x1 / scale) for h in geo.hlines],
        vlines=[VLine(v.x / scale, v.y0 / scale, v.y1 / scale) for v in geo.vlines],
        glyphs=[Glyph(g.x / scale, g.y / scale, g.x_end / scale, g.char, g.font,
                      round(g.size / scale, 1)) for g in geo.glyphs],
        curves=[Curve(c.x0 / scale, c.y0 / scale, c.x1 / scale, c.y1 / scale, c.above)
                for c in geo.curves],
        beams=[Beam(b.x0 / scale, b.y0 / scale, b.x1 / scale, b.y1 / scale)
               for b in geo.beams],
    )


def load_page_geometry(page) -> PageGeometry:
    geo = PageGeometry()
    for x0, y0, x1, y1 in _iter_segments(page):
        if abs(y1 - y0) < HORIZONTAL_TOLERANCE:
            geo.hlines.append(HLine(y0, min(x0, x1), max(x0, x1)))
        elif abs(x1 - x0) < VERTICAL_TOLERANCE:
            geo.vlines.append(VLine(x0, min(y0, y1), max(y0, y1)))
    geo.glyphs.extend(_page_glyphs(page))
    scale = page_scale(geo.hlines, geo.glyphs)

    # 곡선(타이·슬러 호). 한 호가 베지어 여러 조각이라 드로잉 단위로 모아
    # 좌우 끝점만 남긴다 — 음악적 해석(타이인지)은 extract 의 몫이다.
    for drawing in page.get_drawings():
        geo.beams.extend(_drawing_beams(drawing, scale))
        points = [point for item in drawing["items"] if item[0] == "c"
                  for point in (item[1], item[4])]
        if points:
            left = min(points, key=lambda p: p.x)
            right = max(points, key=lambda p: p.x)
            controls = [p.y for item in drawing["items"] if item[0] == "c"
                        for p in (item[2], item[3])]
            above = sum(controls) / len(controls) < (left.y + right.y) / 2
            geo.curves.append(Curve(left.x, left.y, right.x, right.y, above))

    return _normalized(geo, scale)


def _page_glyphs(page) -> list[Glyph]:
    return [Glyph(x=ch["origin"][0], y=ch["origin"][1], x_end=ch["bbox"][2],
                  char=ch["c"], font=span["font"], size=round(span["size"], 1))
            for block in page.get_text("rawdict")["blocks"]
            for line in block.get("lines", [])
            for span in line["spans"]
            for ch in span["chars"] if ch["c"].strip() != ""]


def staff_groups(geo: PageGeometry) -> list[list[float]]:
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
    groups = staff_groups(geo)
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
