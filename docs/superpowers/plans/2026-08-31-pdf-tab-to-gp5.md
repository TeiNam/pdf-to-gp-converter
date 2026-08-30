# PDF 기타 타브 → Guitar Pro `.gp5` 변환 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finale로 조판된 기타 타브 PDF를 `.gp5` 로 변환하는 기능을 guitar-pro-mcp 의 도구로 추가한다.

**Architecture:** PDF 기하 추출(`extract.py`) → 중간표현(IR) → `pyguitarpro` 객체 조립(`build.py`) 의 3단 분리. 코드네임 보이싱 표(`chords.py`)는 슬래시 스트러밍 구간을 연주 가능한 프렛으로 채운다. MCP 도구 2개(`import_tab_pdf`, `open_in_guitar_pro`)로 노출해 곡 전체가 tool call 1회에 파싱된다.

**Tech Stack:** Python 3.14 / uv / pymupdf / pyguitarpro / mcp<2 / pytest

**Spec:** `docs/superpowers/specs/2026-08-31-pdf-tab-to-gp5-design.md`

## Global Constraints

- Python 실행·의존성은 **전부 `uv`** 로 한다. `pip` 를 쓰지 않는다. 의존성 추가는 `uv add`, 실행은 `uv run`.
- 작업 디렉터리는 `guitar-pro-mcp-main/` (vendored MCP). 모든 `uv` 명령은 `--directory guitar-pro-mcp-main` 또는 그 안에서 실행한다.
- `mcp` 는 `>=0.2.0,<2` 로 고정. mcp 2.x 는 `FastMCP` → `MCPServer` 개명으로 이 코드베이스가 import 실패한다.
- `.gp5` 읽기·쓰기는 **반드시 `encoding="cp949"`**. 기본 `cp1252` 는 한글에서 쓰기 실패·읽기 mojibake.
- `pyguitarpro` 객체 생성 시 명시 필수: `NoteType.normal`, `BeatStatus.normal`. 기본값은 각각 `rest`, `empty` 다.
- 생성자는 부모를 요구한다: `Track(song)`, `Measure(track, header)`, `Voice(measure)`, `Beat(voice)`, `Note(beat)`, `GuitarString(number, value)`.
- `Song()` 은 `tracks`/`measureHeaders` 를 기본값으로 채우므로 직접 조립 전에 `.clear()` 한다.
- 기준 입력은 `pdf/나는반딧불.pdf`, 산출물은 `gp/<원본이름>.gp5`. `pdf/` 와 `gp/` 는 둘 다 gitignore 되므로 **입력 파일이 없으면 테스트는 skip** 한다 (실패 아님). `gp/` 는 없으면 만든다.
- 확정 기대값: **타브 시스템 14개**, **마디 58개**, 마디1 = `(5,3) (4,0) (1,0) (3,0) (6,0) (3,1) (1,0) (3,1)` 전부 8분음표.
- 줄 번호는 1=고음 E … 6=저음 E. 표준 튜닝 MIDI = `[64, 59, 55, 50, 45, 40]`.
- 길이는 4분음표 단위로 검산한다: beat = `4/duration × (1.5 if dotted else 1)`, 마디 목표 = `4 × numerator/denominator`.
- 에러는 삼키지 않는다. 입력이 대상 아님 → 즉시 실패. 데이터 이상 → `warnings` 수집 후 진행.
- 추측으로 값을 채우지 않는다. `VOICINGS` 에 없는 코드는 노트를 비우고 경고한다.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `guitar-pro-mcp-main/pyproject.toml` | 수정: `mcp<2` 핀(적용됨), `pymupdf` 추가 |
| `guitar-pro-mcp-main/src/controllers/guitar_pro/file_operations.py` | 수정: `save_file`/`load_file` 에 `cp949` |
| `guitar-pro-mcp-main/src/utils/tab_pdf/__init__.py` | 신규: 패키지 마커 + 공개 API 재노출 |
| `guitar-pro-mcp-main/src/utils/tab_pdf/geometry.py` | 신규: PDF 도형·텍스트 저수준 수집 (staff·마디선·빔·기둥·글리프) |
| `guitar-pro-mcp-main/src/utils/tab_pdf/extract.py` | 신규: geometry 를 조합해 IR 생성 (표기법 판정·노트·음길이·검산) |
| `guitar-pro-mcp-main/src/utils/tab_pdf/chords.py` | 신규: 코드네임 → `(string, fret)` 보이싱 표 |
| `guitar-pro-mcp-main/src/utils/tab_pdf/build.py` | 신규: IR → `pyguitarpro.Song` |
| `guitar-pro-mcp-main/src/mcp_tools.py` | 수정: `import_tab_pdf`, `open_in_guitar_pro` 도구 등록 |
| `guitar-pro-mcp-main/tests/test_tab_pdf.py` | 신규: assert 기반 pytest 테스트 (기존 dev 의존성 재사용) |
| `pdf/` (gitignore) | 입력 PDF |
| `gp/` (gitignore) | 산출 `.gp5` 와 디버그 IR JSON |

`geometry.py` 를 `extract.py` 에서 분리하는 이유: PDF 좌표 수집과 음악적 해석은 바뀌는 이유가 다르다. 다른 PDF 조판에 대응할 때 `geometry.py` 만 손본다.

---

## Task 1: 인코딩 버그 수정 + pymupdf 의존성

**Files:**
- Modify: `guitar-pro-mcp-main/pyproject.toml`
- Modify: `guitar-pro-mcp-main/src/controllers/guitar_pro/file_operations.py:44-47` (`save_file`), `:10-14` (`load_file`)
- Test: `guitar-pro-mcp-main/tests/test_tab_pdf.py`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces: `guitarpro` 파일 I/O 가 한글 제목을 손실 없이 왕복한다. 이후 모든 태스크의 `.gp5` 쓰기가 이에 의존한다.

- [ ] **Step 1: pymupdf 추가**

```bash
cd guitar-pro-mcp-main && uv add pymupdf
```

- [ ] **Step 2: 실패하는 테스트 작성**

`guitar-pro-mcp-main/tests/test_tab_pdf.py` 신규 생성:

```python
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import guitarpro as gp
from controllers.guitar_pro.file_operations import GuitarProFileMixin


class _Ctl(GuitarProFileMixin):
    def __init__(self):
        self.current_song = None

    def _ensure_song_loaded(self):
        assert self.current_song is not None


def _korean_song():
    from guitarpro.models import (
        Song, Track, GuitarString, MeasureHeader, TimeSignature,
        Measure, Voice, Beat, Note, Duration, BeatStatus, NoteType,
    )
    song = Song(title="나는 반딧불", artist="황가람", tempo=80)
    song.tracks.clear()
    song.measureHeaders.clear()
    track = Track(song, name="Guitar")
    track.strings = [GuitarString(i + 1, v) for i, v in enumerate([64, 59, 55, 50, 45, 40])]
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
    """save_file/load_file 이 한글 제목을 손실 없이 왕복해야 한다."""
    out = tmp_path / "한글제목.gp5"
    ctl = _Ctl()
    ctl.current_song = _korean_song()
    ctl.save_file(str(out))          # cp1252 면 UnicodeEncodeError 로 죽는다

    ctl2 = _Ctl()
    ctl2.load_file(str(out))
    assert ctl2.current_song.title == "나는 반딧불"
    assert ctl2.current_song.artist == "황가람"
```

- [ ] **Step 3: 실패 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -v
```

기대: FAIL — `UnicodeEncodeError: 'charmap' codec can't encode characters` (cp1252 가 한글 못 씀)

- [ ] **Step 4: 최소 수정**

`file_operations.py` 의 두 곳:

```python
    def load_file(self, file_path: str) -> None:
        """Load a Guitar Pro file."""
        try:
            logger.info(f"Loading Guitar Pro file: {file_path}")
            self.current_song = parse(file_path, encoding="cp949")
```

```python
    def save_file(self, file_path: str) -> None:
        """Save the current song to a Guitar Pro file."""
        self._ensure_song_loaded()
        write(self.current_song, file_path, encoding="cp949")
```

- [ ] **Step 5: 통과 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -v
```

기대: PASS

- [ ] **Step 6: 커밋**

```bash
git add guitar-pro-mcp-main/pyproject.toml guitar-pro-mcp-main/uv.lock \
        guitar-pro-mcp-main/src/controllers/guitar_pro/file_operations.py \
        guitar-pro-mcp-main/tests/test_tab_pdf.py
git commit -m "fix: .gp5 입출력에 cp949 적용해 한글 제목 손실 방지

- save_file: cp1252 기본값이 한글에서 UnicodeEncodeError 로 크래시
- load_file: cp1252 로 읽으면 한글 제목이 조용히 mojibake
- pymupdf 의존성 추가"
```

---

## Task 2: staff·시스템 클러스터링

**Files:**
- Create: `guitar-pro-mcp-main/src/utils/tab_pdf/__init__.py`, `geometry.py`
- Test: `guitar-pro-mcp-main/tests/test_tab_pdf.py`

**Interfaces:**
- Consumes: Task 1 의 `pymupdf` 의존성
- Produces:
  - `geometry.load_page_geometry(page) -> PageGeometry` — `PageGeometry` 는 `hlines: list[HLine]`, `vlines: list[VLine]`, `chars: list[Glyph]` 를 갖는 dataclass
  - `HLine(y: float, x0: float, x1: float)`, `VLine(x: float, y0: float, y1: float)`, `Glyph(x: float, y: float, char: str, font: str, size: float)` — `x`/`y` 는 baseline origin
  - `geometry.find_systems(geo: PageGeometry) -> list[System]` — `System(melody_ys: list[float], tab_ys: list[float])`
  - 상수 `TAB_LINE_COUNT = 6`, `MELODY_LINE_COUNT = 5`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_tab_pdf.py` 에 추가:

```python
import pytest
import pymupdf

PDF = pathlib.Path(__file__).resolve().parents[2] / "pdf" / "나는반딧불.pdf"
needs_pdf = pytest.mark.skipif(not PDF.exists(), reason=f"입력 PDF 없음: {PDF}")


@needs_pdf
def test_finds_14_tab_systems():
    """3페이지 합쳐 타브 시스템 14개 (p1 4, p2 5, p3 5)."""
    from utils.tab_pdf import geometry

    doc = pymupdf.open(PDF)
    per_page = []
    for page in doc:
        geo = geometry.load_page_geometry(page)
        per_page.append(len(geometry.find_systems(geo)))
    assert per_page == [4, 5, 5]
    assert sum(per_page) == 14


@needs_pdf
def test_system1_staff_line_positions():
    """시스템1 의 5선·6선 y 좌표가 실측값과 일치."""
    from utils.tab_pdf import geometry

    doc = pymupdf.open(PDF)
    geo = geometry.load_page_geometry(doc[0])
    sys1 = geometry.find_systems(geo)[0]
    assert [round(y, 1) for y in sys1.melody_ys] == [145.4, 150.5, 155.6, 160.8, 165.8]
    assert [round(y, 1) for y in sys1.tab_ys] == [206.9, 214.6, 222.2, 229.9, 237.6, 245.3]
```

- [ ] **Step 2: 실패 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -k systems -v
```

기대: FAIL — `ModuleNotFoundError: No module named 'utils.tab_pdf'`

- [ ] **Step 3: 구현**

`src/utils/tab_pdf/__init__.py`:

```python
"""Finale 조판 기타 타브 PDF → Guitar Pro 변환."""
```

`src/utils/tab_pdf/geometry.py`:

```python
"""PDF 저수준 기하 수집. 음악적 해석은 하지 않는다."""

from dataclasses import dataclass, field

MELODY_LINE_COUNT = 5
TAB_LINE_COUNT = 6

# 수평선을 staff 선으로 인정할 최소 길이 (pt)
MIN_STAFF_LINE_WIDTH = 50.0
# 같은 staff 묶음으로 볼 인접 선 간격 상한 (pt) — 타브 간격 ≈7.7, 시스템 간격은 40 이상
MAX_INTRA_STAFF_GAP = 12.0
# 선을 수평/수직으로 볼 허용 오차 (pt)
AXIS_TOLERANCE = 0.5
VLINE_AXIS_TOLERANCE = 1.5


@dataclass(frozen=True)
class HLine:
    y: float
    x0: float
    x1: float


@dataclass(frozen=True)
class VLine:
    x: float
    y0: float
    y1: float


@dataclass(frozen=True)
class Glyph:
    x: float          # baseline origin x
    y: float          # baseline origin y
    char: str
    font: str
    size: float


@dataclass
class PageGeometry:
    hlines: list[HLine] = field(default_factory=list)
    vlines: list[VLine] = field(default_factory=list)
    chars: list[Glyph] = field(default_factory=list)


@dataclass(frozen=True)
class System:
    melody_ys: list[float]
    tab_ys: list[float]


def _iter_segments(page):
    """드로잉 아이템을 (x0, y0, x1, y1) 로 평탄화."""
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                yield item[1].x, item[1].y, item[2].x, item[2].y
            elif item[0] == "re":
                r = item[1]
                # 얇은 사각형은 선으로 취급 (Finale 는 빔을 사각형으로 그린다)
                if r.height <= 3:
                    yield r.x0, r.y0, r.x1, r.y0
                elif r.width <= 3:
                    yield r.x0, r.y0, r.x0, r.y1


def load_page_geometry(page) -> PageGeometry:
    """페이지에서 수평선·수직선·글리프를 수집한다."""
    geo = PageGeometry()
    for x0, y0, x1, y1 in _iter_segments(page):
        if abs(y1 - y0) < AXIS_TOLERANCE:
            geo.hlines.append(HLine(y0, min(x0, x1), max(x0, x1)))
        elif abs(x1 - x0) < VLINE_AXIS_TOLERANCE:
            geo.vlines.append(VLine(x0, min(y0, y1), max(y0, y1)))

    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for ch in span["chars"]:
                    if ch["c"].strip() == "":
                        continue
                    geo.chars.append(
                        Glyph(ch["origin"][0], ch["origin"][1],
                              ch["c"], span["font"], round(span["size"], 1))
                    )
    return geo


def _staff_groups(geo: PageGeometry) -> list[list[float]]:
    """긴 수평선의 y 를 묶어 staff 후보 그룹을 만든다."""
    ys = sorted({
        round(h.y, 1) for h in geo.hlines
        if h.x1 - h.x0 > MIN_STAFF_LINE_WIDTH
    })
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
    """5선(멜로디) + 6선(타브) 인접쌍을 하나의 시스템으로 묶는다."""
    groups = _staff_groups(geo)
    systems = []
    for a, b in zip(groups, groups[1:]):
        if len(a) == MELODY_LINE_COUNT and len(b) == TAB_LINE_COUNT:
            systems.append(System(melody_ys=list(a), tab_ys=list(b)))
    return systems
```

- [ ] **Step 4: 통과 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -k systems -v
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -k staff_line -v
```

기대: 둘 다 PASS

- [ ] **Step 5: 커밋**

```bash
git add guitar-pro-mcp-main/src/utils/tab_pdf guitar-pro-mcp-main/tests/test_tab_pdf.py
git commit -m "feat: PDF staff·시스템 클러스터링 (타브 시스템 14개 검출)"
```

---

## Task 3: 마디 분할

**Files:**
- Modify: `guitar-pro-mcp-main/src/utils/tab_pdf/geometry.py`
- Test: `guitar-pro-mcp-main/tests/test_tab_pdf.py`

**Interfaces:**
- Consumes: `geometry.PageGeometry`, `geometry.System`, `geometry.VLine` (Task 2)
- Produces: `geometry.find_barlines(geo: PageGeometry, system: System) -> list[float]` — 시스템을 관통하는 마디선 x 의 오름차순 리스트. 좌측 브래킷·시작선은 제외하고, 겹세로선은 하나로 병합한다. 마디 개수 = `len(결과)`.

- [ ] **Step 1: 실패하는 테스트 추가**

```python
@needs_pdf
def test_finds_58_measures_total():
    """전체 마디 58개. 시스템별로는 12×4 + 2×5."""
    from utils.tab_pdf import geometry

    doc = pymupdf.open(PDF)
    counts = []
    for page in doc:
        geo = geometry.load_page_geometry(page)
        for system in geometry.find_systems(geo):
            counts.append(len(geometry.find_barlines(geo, system)))
    assert counts == [4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 5, 5, 4]
    assert sum(counts) == 58


@needs_pdf
def test_system1_barline_positions():
    """시스템1 마디선 x — 좌측 브래킷(33/36)은 제외돼야 한다."""
    from utils.tab_pdf import geometry

    doc = pymupdf.open(PDF)
    geo = geometry.load_page_geometry(doc[0])
    system = geometry.find_systems(geo)[0]
    xs = [round(x) for x in geometry.find_barlines(geo, system)]
    assert xs == [198, 324, 450, 576]
```

- [ ] **Step 2: 실패 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -k "measures_total or barline" -v
```

기대: FAIL — `AttributeError: module 'utils.tab_pdf.geometry' has no attribute 'find_barlines'`

- [ ] **Step 3: 구현**

`geometry.py` 상수 블록에 추가:

```python
# 좌측 시스템 브래킷·시작 마디선을 버리는 x 하한 (pt)
MIN_BARLINE_X = 40.0
# 겹세로선(double bar)을 한 마디선으로 병합할 간격 상한 (pt)
BARLINE_MERGE_GAP = 6.0
# 마디선이 두 staff 를 관통했다고 볼 허용 오차 (pt)
SPAN_TOLERANCE = 2.0
```

`geometry.py` 끝에 추가:

```python
def find_barlines(geo: PageGeometry, system: System) -> list[float]:
    """시스템의 멜로디 최상단 ~ 타브 최하단을 관통하는 세로선의 x."""
    top = system.melody_ys[0]
    bottom = system.tab_ys[-1]
    xs = sorted({
        round(v.x, 1) for v in geo.vlines
        if v.x > MIN_BARLINE_X
        and v.y0 <= top + SPAN_TOLERANCE
        and v.y1 >= bottom - SPAN_TOLERANCE
    })
    merged: list[float] = []
    for x in xs:
        if not merged or x - merged[-1] > BARLINE_MERGE_GAP:
            merged.append(x)
    return merged
```

- [ ] **Step 4: 통과 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -k "measures_total or barline" -v
```

기대: PASS

- [ ] **Step 5: 커밋**

```bash
git add guitar-pro-mcp-main/src/utils/tab_pdf/geometry.py guitar-pro-mcp-main/tests/test_tab_pdf.py
git commit -m "feat: 마디선 검출 (총 58마디, 브래킷·겹세로선 처리)"
```

---

## Task 4: 프렛 노트 추출과 줄 매핑

**Files:**
- Create: `guitar-pro-mcp-main/src/utils/tab_pdf/extract.py`
- Test: `guitar-pro-mcp-main/tests/test_tab_pdf.py`

**Interfaces:**
- Consumes: `geometry.load_page_geometry`, `geometry.find_systems`, `geometry.find_barlines`, `geometry.Glyph`, `geometry.System` (Task 2·3)
- Produces:
  - `extract.TabNote(string: int, fret: int, x: float)` — `string` 은 1(고음 E)~6(저음 E)
  - `extract.fret_notes(geo, system, x0: float, x1: float) -> list[TabNote]` — `x0 <= x < x1` 구간의 프렛 노트를 x 오름차순으로
  - 상수 `FRET_FONT = "CIDFont+F2"`, `MAX_FRET_FONT_SIZE = 11.0`, `MAX_STRING_SNAP_DISTANCE = 4.0`

- [ ] **Step 1: 실패하는 테스트 추가**

```python
@needs_pdf
def test_measure1_fret_notes():
    """시스템1 마디1 = 실측 8개 노트, x 순서대로."""
    from utils.tab_pdf import geometry, extract

    doc = pymupdf.open(PDF)
    geo = geometry.load_page_geometry(doc[0])
    system = geometry.find_systems(geo)[0]
    bars = geometry.find_barlines(geo, system)
    notes = extract.fret_notes(geo, system, 0.0, bars[0])
    assert [(n.string, n.fret) for n in notes] == [
        (5, 3), (4, 0), (1, 0), (3, 0), (6, 0), (3, 1), (1, 0), (3, 1)
    ]


@needs_pdf
def test_chord_shares_one_x():
    """같은 x 에 놓인 두 숫자는 화음이다 — x=109 에 (1,0)+(2,3)."""
    from utils.tab_pdf import geometry, extract

    doc = pymupdf.open(PDF)
    geo = geometry.load_page_geometry(doc[0])
    system = geometry.find_systems(geo)[0]
    notes = extract.fret_notes(geo, system, 105.0, 115.0)
    assert len(notes) == 2
    assert {n.string for n in notes} == {1, 2}
```

> 주: 3절 실측에서 x=109.0 의 두 숫자는 y=207.1(타브 1선=고음 E, string 1) 과
> y=214.8(2선=B, string 2) 이다. 프렛은 각각 `0`, `3`.

- [ ] **Step 2: 실패 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -k "fret_notes or chord_shares" -v
```

기대: FAIL — `ModuleNotFoundError: No module named 'utils.tab_pdf.extract'`

- [ ] **Step 3: 구현**

`src/utils/tab_pdf/extract.py`:

```python
"""PDF 기하를 음악적 중간표현(IR)으로 해석한다."""

from dataclasses import dataclass

from . import geometry

# 타브 프렛 숫자 폰트 — 코드네임(CIDFont+F3)·타이틀과 구별된다
FRET_FONT = "CIDFont+F2"
# 'T','A','B' 세로 라벨은 14.4pt 로 더 크다. 프렛 숫자는 9.3pt
MAX_FRET_FONT_SIZE = 11.0
# 숫자 baseline 이 이 거리 안에 있으면 그 줄에 속한 것으로 본다 (선 간격 7.7 의 절반 미만)
MAX_STRING_SNAP_DISTANCE = 4.0


@dataclass(frozen=True)
class TabNote:
    string: int      # 1 = 고음 E … 6 = 저음 E
    fret: int
    x: float


def _snap_to_string(y: float, tab_ys: list[float]) -> int | None:
    """baseline y 를 가장 가까운 타브 선에 붙여 줄 번호를 돌려준다."""
    best_index, best_distance = None, None
    for index, line_y in enumerate(tab_ys):
        distance = abs(y - line_y)
        if best_distance is None or distance < best_distance:
            best_index, best_distance = index, distance
    if best_distance is None or best_distance > MAX_STRING_SNAP_DISTANCE:
        return None
    return best_index + 1          # tab_ys 는 위→아래, 위가 고음 E = string 1


def fret_notes(geo: geometry.PageGeometry, system: geometry.System,
               x0: float, x1: float) -> list[TabNote]:
    """[x0, x1) 구간 타브 staff 의 프렛 숫자를 노트로 뽑는다."""
    notes = []
    for glyph in geo.chars:
        if not (x0 <= glyph.x < x1):
            continue
        if glyph.font != FRET_FONT or glyph.size > MAX_FRET_FONT_SIZE:
            continue
        if not glyph.char.isdigit():
            continue
        string = _snap_to_string(glyph.y, system.tab_ys)
        if string is None:
            continue
        notes.append(TabNote(string=string, fret=int(glyph.char), x=glyph.x))
    notes.sort(key=lambda n: (n.x, n.string))
    return notes
```

- [ ] **Step 4: 통과 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -k "fret_notes or chord_shares" -v
```

기대: PASS

- [ ] **Step 5: 커밋**

```bash
git add guitar-pro-mcp-main/src/utils/tab_pdf/extract.py guitar-pro-mcp-main/tests/test_tab_pdf.py
git commit -m "feat: 프렛 숫자 추출과 줄 매핑 (마디1 실측값 일치)"
```

---

## Task 5: 음길이 디코딩과 마디 검산

**Files:**
- Modify: `guitar-pro-mcp-main/src/utils/tab_pdf/extract.py`
- Test: `guitar-pro-mcp-main/tests/test_tab_pdf.py`

**Interfaces:**
- Consumes: `extract.TabNote`, `extract.fret_notes` (Task 4), `geometry.HLine`/`VLine` (Task 2)
- Produces:
  - `extract.Beat(x: float, duration: int, dotted: bool, rest: bool, notes: list[TabNote], chord: str | None, stroke: str | None)`
  - `extract.decode_durations(geo, system, notes: list[TabNote]) -> list[Beat]` — x 로 화음 묶고 각 묶음에 음길이를 붙인다
  - `extract.measure_length(beats: list[Beat]) -> float` — 4분음표 단위 합계
  - 상수 `BEAT_CLUSTER_TOLERANCE = 2.0`, `SMUFL_RESTS`, `SMUFL_FLAGS`, `SMUFL_AUG_DOT = ""`

- [ ] **Step 1: 실패하는 테스트 추가**

```python
@needs_pdf
def test_measure1_all_eighths():
    """마디1 은 8분음표 8개 — 빔 1줄 × 4음 그룹 2개."""
    from utils.tab_pdf import geometry, extract

    doc = pymupdf.open(PDF)
    geo = geometry.load_page_geometry(doc[0])
    system = geometry.find_systems(geo)[0]
    bars = geometry.find_barlines(geo, system)
    notes = extract.fret_notes(geo, system, 0.0, bars[0])
    beats = extract.decode_durations(geo, system, notes)
    assert len(beats) == 8
    assert all(b.duration == 8 and not b.dotted for b in beats)


@needs_pdf
def test_measure1_length_is_four_quarters():
    """4/4 마디는 4분음표 단위로 정확히 4.0 이어야 한다."""
    from utils.tab_pdf import geometry, extract

    doc = pymupdf.open(PDF)
    geo = geometry.load_page_geometry(doc[0])
    system = geometry.find_systems(geo)[0]
    bars = geometry.find_barlines(geo, system)
    notes = extract.fret_notes(geo, system, 0.0, bars[0])
    beats = extract.decode_durations(geo, system, notes)
    assert extract.measure_length(beats) == 4.0
```

- [ ] **Step 2: 실패 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -k "all_eighths or four_quarters" -v
```

기대: FAIL — `AttributeError: module 'utils.tab_pdf.extract' has no attribute 'decode_durations'`

- [ ] **Step 3: 구현**

`extract.py` 상수 블록에 추가:

```python
# 같은 beat(화음)로 묶을 x 허용 오차 (pt)
BEAT_CLUSTER_TOLERANCE = 2.0
# 기둥(stem)으로 인정할 세로선 최소 길이 (pt)
MIN_STEM_LENGTH = 3.0
# 기둥 x 와 beat x 가 같다고 볼 오차 (pt) — Finale 는 노트헤드 우측에 기둥을 붙인다
STEM_X_TOLERANCE = 4.0
# 빔이 기둥 끝에 닿았다고 볼 오차 (pt)
BEAM_TOUCH_TOLERANCE = 3.0
# 빔으로 인정할 수평선 길이 범위 (pt) — staff 선(>50)과 구별
MIN_BEAM_WIDTH, MAX_BEAM_WIDTH = 2.0, 60.0
# 부점이 beat 에 속한다고 볼 x 거리 (pt)
AUG_DOT_X_WINDOW = 12.0

SMUFL_AUG_DOT = ""
SMUFL_RESTS = {
    "": 1,    # restWhole
    "": 2,    # restHalf
    "": 4,    # restQuarter
    "": 8,    # rest8th
    "": 16,   # rest16th
}
SMUFL_FLAGS = {"", ""}          # flag8thUp / flag8thDown
SMUFL_NOTEHEAD_HALF = ""
SMUFL_NOTEHEAD_WHOLE = ""

# 빔 개수 → Duration.value
BEAMS_TO_DURATION = {1: 8, 2: 16, 3: 32}
DEFAULT_DURATION = 4
```

`extract.py` 에 `Beat` 와 디코딩 함수 추가:

```python
@dataclass
class Beat:
    x: float
    duration: int                    # 1/2/4/8/16/32
    dotted: bool = False
    rest: bool = False
    notes: list[TabNote] = None
    chord: str | None = None
    stroke: str | None = None        # "down" | "up" | None

    def __post_init__(self):
        if self.notes is None:
            self.notes = []


def _cluster_by_x(notes: list[TabNote]) -> list[list[TabNote]]:
    """x 가 BEAT_CLUSTER_TOLERANCE 안이면 한 화음으로 묶는다."""
    clusters: list[list[TabNote]] = []
    for note in notes:
        if clusters and abs(note.x - clusters[-1][0].x) <= BEAT_CLUSTER_TOLERANCE:
            clusters[-1].append(note)
        else:
            clusters.append([note])
    return clusters


def _stems_near(geo: geometry.PageGeometry, x: float) -> list[geometry.VLine]:
    return [
        v for v in geo.vlines
        if abs(v.x - x) <= STEM_X_TOLERANCE and (v.y1 - v.y0) >= MIN_STEM_LENGTH
    ]


def _count_beams(geo: geometry.PageGeometry, stem: geometry.VLine) -> int:
    """기둥의 양 끝에 닿는 빔(짧은 수평선) 개수."""
    count = 0
    for h in geo.hlines:
        width = h.x1 - h.x0
        if not (MIN_BEAM_WIDTH <= width <= MAX_BEAM_WIDTH):
            continue
        if not (h.x0 - BEAM_TOUCH_TOLERANCE <= stem.x <= h.x1 + BEAM_TOUCH_TOLERANCE):
            continue
        if (abs(h.y - stem.y0) <= BEAM_TOUCH_TOLERANCE
                or abs(h.y - stem.y1) <= BEAM_TOUCH_TOLERANCE):
            count += 1
    return count


def _glyphs_near(geo: geometry.PageGeometry, x: float, window: float) -> list[geometry.Glyph]:
    return [g for g in geo.chars if abs(g.x - x) <= window]


def _duration_for(geo: geometry.PageGeometry, x: float) -> int:
    """beat x 의 기둥·빔·플래그·노트헤드로 Duration.value 를 정한다."""
    stems = _stems_near(geo, x)
    if stems:
        beams = max((_count_beams(geo, s) for s in stems), default=0)
        if beams:
            return BEAMS_TO_DURATION.get(beams, 32)
        if any(g.char in SMUFL_FLAGS for g in _glyphs_near(geo, x, STEM_X_TOLERANCE)):
            return 8
        return DEFAULT_DURATION
    for glyph in _glyphs_near(geo, x, STEM_X_TOLERANCE):
        if glyph.char == SMUFL_NOTEHEAD_HALF:
            return 2
        if glyph.char == SMUFL_NOTEHEAD_WHOLE:
            return 1
    return DEFAULT_DURATION


def decode_durations(geo: geometry.PageGeometry, system: geometry.System,
                     notes: list[TabNote]) -> list[Beat]:
    """노트를 화음으로 묶고 각 묶음의 음길이를 붙인다."""
    beats = []
    for cluster in _cluster_by_x(notes):
        x = cluster[0].x
        dotted = any(
            g.char == SMUFL_AUG_DOT
            for g in _glyphs_near(geo, x, AUG_DOT_X_WINDOW)
            if g.x >= x
        )
        beats.append(Beat(x=x, duration=_duration_for(geo, x),
                          dotted=dotted, notes=cluster))
    return beats


def measure_length(beats: list[Beat]) -> float:
    """4분음표 단위 합계."""
    total = 0.0
    for beat in beats:
        length = 4.0 / beat.duration
        if beat.dotted:
            length *= 1.5
        total += length
    return total
```

- [ ] **Step 4: 통과 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -k "all_eighths or four_quarters" -v
```

기대: PASS

- [ ] **Step 5: 커밋**

```bash
git add guitar-pro-mcp-main/src/utils/tab_pdf/extract.py guitar-pro-mcp-main/tests/test_tab_pdf.py
git commit -m "feat: 빔·기둥 기반 음길이 디코딩과 4분음표 단위 검산"
```

---

## Task 6: 슬래시 스트러밍 구간과 코드 보이싱

**Files:**
- Create: `guitar-pro-mcp-main/src/utils/tab_pdf/chords.py`
- Modify: `guitar-pro-mcp-main/src/utils/tab_pdf/extract.py`
- Test: `guitar-pro-mcp-main/tests/test_tab_pdf.py`

**Interfaces:**
- Consumes: `extract.Beat`, `extract.fret_notes`, `extract.decode_durations` (Task 4·5)
- Produces:
  - `chords.VOICINGS: dict[str, list[tuple[int, int]]]` — 코드명 → `[(string, fret), ...]`
  - `chords.voicing_for(name: str) -> list[tuple[int, int]] | None` — 없으면 `None` (추측 금지)
  - `extract.chord_names(geo, system, x0, x1) -> list[tuple[float, str]]` — `(x, 코드명)` x 오름차순
  - `extract.slash_beats(geo, system, x0, x1) -> list[Beat]` — 슬래시 노트헤드를 beat 로. `chord` 채우고 `notes` 는 보이싱으로 채움
  - `extract.system_notation(geo, system, x0, x1) -> str` — `"fret"` | `"slash"` | `"mixed"` | `"empty"`
  - 상수 `SMUFL_SLASH_RANGE = (0xE100, 0xE10F)`, `SMUFL_STROKE_DOWN = ""`, `SMUFL_STROKE_UP = ""`, `CHORD_FONT_PREFIX = "CIDFont+F3"`

- [ ] **Step 1: 실패하는 테스트 추가**

```python
def test_voicing_lookup_has_five_chords():
    """이 곡에 쓰인 5개 코드가 표에 있고, 모르는 코드는 None."""
    from utils.tab_pdf import chords

    for name in ("Cadd9", "E7", "Am", "F", "G"):
        voicing = chords.voicing_for(name)
        assert voicing, f"{name} 보이싱 없음"
        assert all(1 <= s <= 6 and f >= 0 for s, f in voicing)
    assert chords.voicing_for("Bm7b5") is None


@needs_pdf
def test_notation_kinds_across_document():
    """프렛 8시스템 + 슬래시 6시스템. mixed 를 fret 로 세면 8개."""
    from utils.tab_pdf import geometry, extract

    doc = pymupdf.open(PDF)
    kinds = []
    for page in doc:
        geo = geometry.load_page_geometry(page)
        for system in geometry.find_systems(geo):
            bars = geometry.find_barlines(geo, system)
            kinds.append(extract.system_notation(geo, system, 0.0, bars[-1]))
    assert len(kinds) == 14
    assert sum(1 for k in kinds if k in ("fret", "mixed")) == 8
    assert sum(1 for k in kinds if k == "slash") == 6


@needs_pdf
def test_slash_beats_get_chord_voicings():
    """슬래시 구간 첫 시스템(p2 sys3)의 beat 는 코드 보이싱으로 채워진다."""
    from utils.tab_pdf import geometry, extract

    doc = pymupdf.open(PDF)
    geo = geometry.load_page_geometry(doc[1])
    system = geometry.find_systems(geo)[2]
    bars = geometry.find_barlines(geo, system)
    beats = extract.slash_beats(geo, system, 0.0, bars[0])
    assert beats, "슬래시 beat 를 못 찾았다"
    assert all(b.chord for b in beats), "코드명이 안 붙은 beat 가 있다"
    assert all(b.notes for b in beats), "보이싱이 안 채워진 beat 가 있다"
```

- [ ] **Step 2: 실패 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -k "voicing or notation_kinds or slash_beats" -v
```

기대: FAIL — `ModuleNotFoundError: No module named 'utils.tab_pdf.chords'`

- [ ] **Step 3: chords.py 구현**

```python
"""코드네임 → 기타 프렛 보이싱.

이 곡(`나는반딧불`)에 실제로 쓰인 5개만 넣는다. 표에 없는 코드는 None 을 돌려
호출자가 경고를 남기게 한다 — 추측으로 채우면 틀린 악보가 조용히 나온다.
string 1 = 고음 E, 6 = 저음 E. fret 0 = 개방현.
"""

VOICINGS: dict[str, list[tuple[int, int]]] = {
    "Cadd9": [(5, 3), (4, 2), (3, 0), (2, 3), (1, 3)],
    "E7":    [(6, 0), (5, 2), (4, 0), (3, 1), (2, 0), (1, 0)],
    "Am":    [(5, 0), (4, 2), (3, 2), (2, 1), (1, 0)],
    "F":     [(4, 3), (3, 2), (2, 1), (1, 1)],
    "G":     [(6, 3), (5, 2), (4, 0), (3, 0), (2, 0), (1, 3)],
}


def voicing_for(name: str) -> list[tuple[int, int]] | None:
    """코드명의 보이싱. 모르는 코드는 None."""
    return VOICINGS.get(name.strip())
```

- [ ] **Step 4: extract.py 에 슬래시 처리 추가**

상수 블록에 추가:

```python
SMUFL_SLASH_RANGE = (0xE100, 0xE10F)        # SMuFL Slash noteheads
SMUFL_STROKE_DOWN = ""
SMUFL_STROKE_UP = ""
CHORD_FONT_PREFIX = "CIDFont+F3"
# 슬래시 노트헤드가 타브 staff 에 속한다고 볼 상하 여유 (pt)
SLASH_BAND_MARGIN = 8.0
# 코드명 문자들을 한 토큰으로 이을 x 간격 상한 (pt)
CHORD_CHAR_GAP = 7.0
# 스트로크 기호가 beat 에 속한다고 볼 x 거리 (pt)
STROKE_X_WINDOW = 6.0
```

`extract.py` 끝에 추가:

```python
from . import chords


def _is_slash(glyph: geometry.Glyph) -> bool:
    lo, hi = SMUFL_SLASH_RANGE
    return lo <= ord(glyph.char) <= hi


def _in_tab_band(glyph: geometry.Glyph, system: geometry.System) -> bool:
    top = system.tab_ys[0] - SLASH_BAND_MARGIN
    bottom = system.tab_ys[-1] + SLASH_BAND_MARGIN
    return top <= glyph.y <= bottom


def chord_names(geo: geometry.PageGeometry, system: geometry.System,
                x0: float, x1: float) -> list[tuple[float, str]]:
    """타브 staff 위쪽의 코드명을 (x, 이름) 으로 복원한다."""
    above = system.tab_ys[0]
    candidates = sorted(
        (g for g in geo.chars
         if g.font.startswith(CHORD_FONT_PREFIX) and x0 <= g.x < x1 and g.y < above),
        key=lambda g: (round(g.y, 1), g.x),
    )
    tokens: list[tuple[float, str]] = []
    start_x, text, prev_x, prev_y = None, "", None, None
    for glyph in candidates:
        broke = (
            prev_x is None
            or round(glyph.y, 1) != prev_y
            or glyph.x - prev_x > CHORD_CHAR_GAP
        )
        if broke:
            if text:
                tokens.append((start_x, text))
            start_x, text = glyph.x, ""
        text += glyph.char
        prev_x, prev_y = glyph.x, round(glyph.y, 1)
    if text:
        tokens.append((start_x, text))
    return sorted(
        [(x, t) for x, t in tokens if chords.voicing_for(t) is not None],
        key=lambda pair: pair[0],
    )


def _chord_at(names: list[tuple[float, str]], x: float) -> str | None:
    """x 이전(또는 같은 위치)의 가장 가까운 코드명."""
    current = None
    for chord_x, name in names:
        if chord_x <= x + CHORD_CHAR_GAP:
            current = name
        else:
            break
    return current


def _stroke_at(geo: geometry.PageGeometry, x: float) -> str | None:
    for glyph in _glyphs_near(geo, x, STROKE_X_WINDOW):
        if glyph.char == SMUFL_STROKE_DOWN:
            return "down"
        if glyph.char == SMUFL_STROKE_UP:
            return "up"
    return None


def slash_beats(geo: geometry.PageGeometry, system: geometry.System,
                x0: float, x1: float) -> list[Beat]:
    """슬래시 노트헤드를 beat 로 만들고 코드 보이싱으로 노트를 채운다."""
    slashes = sorted(
        (g for g in geo.chars
         if _is_slash(g) and x0 <= g.x < x1 and _in_tab_band(g, system)),
        key=lambda g: g.x,
    )
    names = chord_names(geo, system, 0.0, x1)
    beats: list[Beat] = []
    for glyph in slashes:
        x = glyph.x
        if beats and abs(x - beats[-1].x) <= BEAT_CLUSTER_TOLERANCE:
            continue                      # 같은 beat 의 중복 슬래시
        chord = _chord_at(names, x)
        voicing = chords.voicing_for(chord) if chord else None
        dotted = any(
            g.char == SMUFL_AUG_DOT
            for g in _glyphs_near(geo, x, AUG_DOT_X_WINDOW) if g.x >= x
        )
        beats.append(Beat(
            x=x,
            duration=_duration_for(geo, x),
            dotted=dotted,
            notes=[TabNote(string=s, fret=f, x=x) for s, f in (voicing or [])],
            chord=chord,
            stroke=_stroke_at(geo, x),
        ))
    return beats


def system_notation(geo: geometry.PageGeometry, system: geometry.System,
                    x0: float, x1: float) -> str:
    """시스템의 표기법: fret / slash / mixed / empty."""
    frets = len(fret_notes(geo, system, x0, x1))
    slashes = sum(
        1 for g in geo.chars
        if _is_slash(g) and x0 <= g.x < x1 and _in_tab_band(g, system)
    )
    if frets and slashes:
        return "mixed"
    if frets:
        return "fret"
    if slashes:
        return "slash"
    return "empty"
```

- [ ] **Step 5: 통과 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -k "voicing or notation_kinds or slash_beats" -v
```

기대: PASS. `test_notation_kinds_across_document` 가 8/6 으로 안 나오면 `SLASH_BAND_MARGIN` 을 조정한다 — 3절 실측이 정답(프렛 8, 슬래시 6)이다.

- [ ] **Step 6: 커밋**

```bash
git add guitar-pro-mcp-main/src/utils/tab_pdf guitar-pro-mcp-main/tests/test_tab_pdf.py
git commit -m "feat: 슬래시 스트러밍 구간을 코드 보이싱으로 변환

- chords.py: 이 곡의 5개 코드 보이싱 표, 모르는 코드는 None
- 표기법 판정(fret/slash/mixed/empty), 다운/업 스트로크 기록"
```

---

## Task 7: 문서 전체 IR 조립과 `.gp5` 출력

**Files:**
- Modify: `guitar-pro-mcp-main/src/utils/tab_pdf/extract.py`
- Create: `guitar-pro-mcp-main/src/utils/tab_pdf/build.py`
- Test: `guitar-pro-mcp-main/tests/test_tab_pdf.py`

**Interfaces:**
- Consumes: 앞선 모든 `extract`/`chords` 함수
- Produces:
  - `extract.extract_ir(pdf_path: str, tempo: int | None = None) -> dict` — spec 5절 IR 스키마. 키: `title`, `artist`, `tempo`, `tuning`, `measures`, `warnings`. `measures[i]` = `{"index", "time_sig", "kind", "beats"}`, `beats[j]` = `{"x", "duration", "dotted", "rest", "chord", "stroke", "notes"}`, `notes[k]` = `{"string", "fret"}`
  - `build.build_song(ir: dict)` → `guitarpro.models.Song`
  - `build.write_gp5(song, file_path: str) -> None` — `encoding="cp949"` 고정
  - 예외 `extract.NotATabPdf(ValueError)` — 텍스트 없음 / 타브 staff 없음

- [ ] **Step 1: 실패하는 테스트 추가**

```python
@needs_pdf
def test_extract_ir_shape():
    """IR 이 58마디를 담고, 빈 마디가 없고, 길이 검산 경고가 없다."""
    from utils.tab_pdf import extract

    ir = extract.extract_ir(str(PDF), tempo=80)
    assert len(ir["measures"]) == 58
    assert ir["tuning"] == [64, 59, 55, 50, 45, 40]
    assert all(m["beats"] for m in ir["measures"]), "빈 마디가 있다"
    mismatches = [w for w in ir["warnings"] if w["kind"] == "duration_mismatch"]
    assert not mismatches, f"길이 불일치 마디: {mismatches}"


@needs_pdf
def test_ir_measure1_matches_measured_values():
    """IR 첫 마디가 3절 실측값과 일치."""
    from utils.tab_pdf import extract

    ir = extract.extract_ir(str(PDF), tempo=80)
    beats = ir["measures"][0]["beats"]
    got = [(n["string"], n["fret"]) for b in beats for n in b["notes"]]
    assert got == [(5, 3), (4, 0), (1, 0), (3, 0), (6, 0), (3, 1), (1, 0), (3, 1)]
    assert all(b["duration"] == 8 for b in beats)


@needs_pdf
def test_gp5_roundtrip(tmp_path):
    """IR → Song → .gp5 → 재파싱에서 노트 수·튜닝·한글 제목이 보존된다."""
    from utils.tab_pdf import extract, build

    ir = extract.extract_ir(str(PDF), tempo=80)
    song = build.build_song(ir)
    out = tmp_path / "나는반딧불.gp5"
    build.write_gp5(song, str(out))

    reparsed = gp.parse(str(out), encoding="cp949")
    track = reparsed.tracks[0]
    assert reparsed.title == ir["title"]
    assert [s.value for s in track.strings] == ir["tuning"]
    assert len(track.measures) == 58
    ir_notes = sum(len(b["notes"]) for m in ir["measures"] for b in m["beats"])
    gp_notes = sum(len(b.notes) for m in track.measures for v in m.voices for b in v.beats)
    assert gp_notes == ir_notes


def test_rejects_non_tab_pdf(tmp_path):
    """타브 staff 가 없는 PDF 는 즉시 실패한다 — 조용히 빈 결과를 내지 않는다."""
    import pymupdf
    from utils.tab_pdf import extract

    plain = tmp_path / "plain.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "hello, not a score")
    doc.save(str(plain))

    with pytest.raises(extract.NotATabPdf):
        extract.extract_ir(str(plain))
```

- [ ] **Step 2: 실패 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -k "extract_ir or ir_measure1 or roundtrip or non_tab" -v
```

기대: FAIL — `AttributeError: ... has no attribute 'extract_ir'`

- [ ] **Step 3: extract_ir 구현**

`extract.py` 에 추가 (파일 상단 import 에 `import pymupdf` 추가):

```python
STANDARD_TUNING = [64, 59, 55, 50, 45, 40]      # 1=고음 E … 6=저음 E
DEFAULT_TEMPO = 80
DEFAULT_TIME_SIG = (4, 4)
# 검산 허용 오차 — 부동소수 누적 대비
LENGTH_EPSILON = 1e-6


class NotATabPdf(ValueError):
    """타브 악보로 해석할 수 없는 입력."""


def _target_length(time_sig: tuple[int, int]) -> float:
    numerator, denominator = time_sig
    return 4.0 * numerator / denominator


def extract_ir(pdf_path: str, tempo: int | None = None) -> dict:
    """PDF 전체를 IR 로 만든다."""
    doc = pymupdf.open(pdf_path)
    warnings: list[dict] = []
    measures: list[dict] = []
    saw_text = False

    for page in doc:
        geo = geometry.load_page_geometry(page)
        if geo.chars:
            saw_text = True
        for system in geometry.find_systems(geo):
            bars = geometry.find_barlines(geo, system)
            if not bars:
                continue
            starts = [0.0 if not measures else system.tab_ys and bars[0]] and None
            # 마디 경계: 시스템 좌측 끝(첫 마디선 이전) ~ 각 마디선
            left = min((h.x0 for h in geo.hlines
                        if abs(h.y - system.tab_ys[0]) < 1.0), default=0.0)
            bounds = [left] + list(bars)
            for i in range(len(bars)):
                x0, x1 = bounds[i], bounds[i + 1]
                kind = system_notation(geo, system, x0, x1)
                if kind in ("fret", "mixed"):
                    beats = decode_durations(
                        geo, system, fret_notes(geo, system, x0, x1))
                    if kind == "mixed":
                        beats = sorted(
                            beats + slash_beats(geo, system, x0, x1),
                            key=lambda b: b.x)
                elif kind == "slash":
                    beats = slash_beats(geo, system, x0, x1)
                else:
                    beats = []

                index = len(measures)
                length = measure_length(beats)
                target = _target_length(DEFAULT_TIME_SIG)
                if beats and abs(length - target) > LENGTH_EPSILON:
                    warnings.append({
                        "measure": index,
                        "kind": "duration_mismatch",
                        "detail": f"합 {length:.3f} / 목표 {target:.3f}",
                    })
                if not beats:
                    warnings.append({
                        "measure": index,
                        "kind": "empty_measure",
                        "detail": f"표기법 판정 {kind}, x {x0:.1f}..{x1:.1f}",
                    })
                for beat in beats:
                    if beat.chord and not beat.notes:
                        warnings.append({
                            "measure": index,
                            "kind": "unknown_chord",
                            "detail": f"{beat.chord} — VOICINGS 에 없음",
                        })

                measures.append({
                    "index": index,
                    "time_sig": list(DEFAULT_TIME_SIG),
                    "kind": kind,
                    "beats": [{
                        "x": round(b.x, 2),
                        "duration": b.duration,
                        "dotted": b.dotted,
                        "rest": b.rest,
                        "chord": b.chord,
                        "stroke": b.stroke,
                        "notes": [{"string": n.string, "fret": n.fret} for n in b.notes],
                    } for b in beats],
                })

    if not saw_text:
        raise NotATabPdf(
            f"{pdf_path}: 텍스트 레이어가 없다. 스캔 이미지 PDF 는 이 도구로 변환할 수 없다")
    if not measures:
        raise NotATabPdf(f"{pdf_path}: 6줄 타브 staff 를 찾지 못했다")

    return {
        "title": doc.metadata.get("title") or "",
        "artist": "",
        "tempo": tempo or DEFAULT_TEMPO,
        "tuning": list(STANDARD_TUNING),
        "measures": measures,
        "warnings": warnings,
    }
```

> `starts = ...` 로 시작하는 죽은 줄은 넣지 말 것. 위 코드에서 삭제하고
> `left`/`bounds` 만 사용한다. (계획 검토에서 잡힌 흔적 — 구현 시 제외)

- [ ] **Step 4: build.py 구현**

```python
"""IR → pyguitarpro Song. PDF 를 전혀 모른다."""

import guitarpro as gp
from guitarpro.models import (
    Song, Track, GuitarString, MeasureHeader, TimeSignature,
    Measure, Voice, Beat, Note, Duration, BeatStatus, NoteType,
)

# GP5 는 8비트 charset — 한글 보존에 필요
GP5_ENCODING = "cp949"
GP5_VERSION = (5, 1, 0)
# GP5 는 트랙당 voice 슬롯 2개를 기대한다
GP5_VOICE_SLOTS = 2
DEFAULT_VELOCITY = 95
NYLON_GUITAR_MIDI_PROGRAM = 24


def build_song(ir: dict) -> Song:
    """IR 을 Song 객체로 조립한다."""
    song = Song(title=ir.get("title", ""), artist=ir.get("artist", ""),
                tempo=ir.get("tempo", 80))
    song.tracks.clear()
    song.measureHeaders.clear()

    track = Track(song, name="Guitar")
    track.channel.instrument = NYLON_GUITAR_MIDI_PROGRAM
    track.strings = [GuitarString(i + 1, v) for i, v in enumerate(ir["tuning"])]
    track.measures.clear()

    for measure_ir in ir["measures"]:
        header = MeasureHeader(number=measure_ir["index"] + 1)
        header.timeSignature = TimeSignature()
        song.measureHeaders.append(header)

        measure = Measure(track, header)
        measure.voices.clear()
        voice = Voice(measure)
        for beat_ir in measure_ir["beats"]:
            beat = Beat(
                voice,
                duration=Duration(value=beat_ir["duration"],
                                  isDotted=beat_ir["dotted"]),
                status=BeatStatus.rest if beat_ir["rest"] else BeatStatus.normal,
            )
            for note_ir in beat_ir["notes"]:
                beat.notes.append(Note(
                    beat, value=note_ir["fret"], string=note_ir["string"],
                    velocity=DEFAULT_VELOCITY, type=NoteType.normal,
                ))
            voice.beats.append(beat)
        measure.voices.append(voice)
        while len(measure.voices) < GP5_VOICE_SLOTS:
            measure.voices.append(Voice(measure))
        track.measures.append(measure)

    song.tracks.append(track)
    return song


def write_gp5(song: Song, file_path: str) -> None:
    """.gp5 로 쓴다. 인코딩은 고정 — 한글 제목이 깨지면 안 된다."""
    gp.write(song, file_path, version=GP5_VERSION, encoding=GP5_ENCODING)
```

- [ ] **Step 5: 통과 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -v
```

기대: 전부 PASS. `duration_mismatch` 가 남으면 Task 5 의 빔 탐지 상수(`BEAM_TOUCH_TOLERANCE`, `STEM_X_TOLERANCE`)를 조정한다. 경고 내용이 어느 마디인지 알려주므로 그 마디만 크롭 렌더해 눈으로 확인한다.

- [ ] **Step 6: 커밋**

```bash
git add guitar-pro-mcp-main/src/utils/tab_pdf guitar-pro-mcp-main/tests/test_tab_pdf.py
git commit -m "feat: 문서 전체 IR 조립과 .gp5 출력 (58마디 왕복 검증)"
```

---

## Task 8: MCP 도구 노출

**Files:**
- Modify: `guitar-pro-mcp-main/src/mcp_tools.py`
- Test: `guitar-pro-mcp-main/tests/test_tab_pdf.py`

**Interfaces:**
- Consumes: `extract.extract_ir`, `extract.NotATabPdf`, `build.build_song`, `build.write_gp5` (Task 7)
- Produces: MCP 도구 `import_tab_pdf(pdf_path, tempo=None, ir_path=None)` 와 `open_in_guitar_pro(file_path)`. 둘 다 기존 관례대로 `{"status": "success"|"error", ...}` 를 돌려주고 예외를 던지지 않는다.

- [ ] **Step 1: 실패하는 테스트 추가**

```python
GP_APP = "/Applications/Guitar Pro 8.app"


@needs_pdf
def test_import_tab_pdf_tool_loads_song(tmp_path):
    """도구가 current_song 을 채우고 요약을 돌려준다."""
    from controllers import GuitarProController
    import mcp_tools

    controller = GuitarProController()
    result = mcp_tools._import_tab_pdf_impl(
        controller, str(PDF), tempo=80, ir_path=str(tmp_path / "ir.json"))
    assert result["status"] == "success"
    assert result["data"]["measures"] == 58
    assert result["data"]["notes"] > 0
    suggested = result["data"]["suggested_output"]
    assert suggested.endswith("gp/나는반딧불.gp5"), suggested
    assert pathlib.Path(suggested).parent.is_dir(), "gp/ 폴더가 생성되지 않았다"
    assert controller.current_song is not None
    assert len(controller.current_song.tracks[0].measures) == 58
    assert (tmp_path / "ir.json").exists()


def test_import_tab_pdf_tool_reports_error_for_missing_file():
    """없는 경로는 예외가 아니라 error 상태로."""
    from controllers import GuitarProController
    import mcp_tools

    controller = GuitarProController()
    result = mcp_tools._import_tab_pdf_impl(controller, "/nope/none.pdf")
    assert result["status"] == "error"
    assert "없" in result["message"] or "not" in result["message"].lower()


def test_default_output_path_rules(tmp_path):
    """pdf/ 안이면 형제 gp/, 밖이면 PDF 옆 gp/."""
    import mcp_tools

    inside = tmp_path / "pdf" / "song.pdf"
    inside.parent.mkdir()
    inside.touch()
    assert mcp_tools._default_output_path(str(inside)) == str(tmp_path / "gp" / "song.gp5")

    outside = tmp_path / "loose" / "song.pdf"
    outside.parent.mkdir()
    outside.touch()
    assert mcp_tools._default_output_path(str(outside)) == str(
        tmp_path / "loose" / "gp" / "song.gp5")


def test_open_in_guitar_pro_validates_path():
    """없는 파일이면 앱을 띄우지 않고 error."""
    import mcp_tools

    result = mcp_tools._open_in_guitar_pro_impl("/nope/none.gp5")
    assert result["status"] == "error"
```

- [ ] **Step 2: 실패 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -k "import_tab_pdf or open_in_guitar" -v
```

기대: FAIL — `AttributeError: module 'mcp_tools' has no attribute '_import_tab_pdf_impl'`

- [ ] **Step 3: 구현**

`mcp_tools.py` 상단 import 아래에 추가:

```python
import json
import os
import shutil
import subprocess

from utils.tab_pdf import build, extract

GUITAR_PRO_APP = "Guitar Pro 8"
# 산출물 기본 폴더 — 입력 pdf/ 와 대칭
DEFAULT_OUTPUT_DIR = "gp"


def _default_output_path(pdf_path: str) -> str:
    """산출 경로를 정하고 폴더를 만든다.

    입력이 `<root>/pdf/x.pdf` 면 `<root>/gp/x.gp5` (pdf/ 와 대칭).
    그 외에는 PDF 옆에 `gp/` 를 만든다 — 입력 폴더 밖으로 나가지 않는다.
    """
    source_dir = os.path.dirname(os.path.abspath(pdf_path))
    if os.path.basename(source_dir) == "pdf":
        out_dir = os.path.join(os.path.dirname(source_dir), DEFAULT_OUTPUT_DIR)
    else:
        out_dir = os.path.join(source_dir, DEFAULT_OUTPUT_DIR)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    return os.path.join(out_dir, f"{stem}.gp5")


def _import_tab_pdf_impl(controller, pdf_path: str, tempo: int | None = None,
                         ir_path: str | None = None) -> dict:
    """PDF 타브를 파싱해 controller.current_song 에 적재한다."""
    if not os.path.isfile(pdf_path):
        return {"status": "error", "message": f"PDF 파일이 없습니다: {pdf_path}"}
    try:
        ir = extract.extract_ir(pdf_path, tempo=tempo)
    except extract.NotATabPdf as exc:
        return {"status": "error", "message": str(exc)}
    except Exception as exc:
        return {"status": "error", "message": f"PDF 파싱 실패: {exc}"}

    try:
        controller.current_song = build.build_song(ir)
    except Exception as exc:
        return {"status": "error", "message": f"Song 조립 실패: {exc}"}

    if ir_path:
        try:
            with open(ir_path, "w", encoding="utf-8") as handle:
                json.dump(ir, handle, ensure_ascii=False, indent=2)
        except OSError as exc:
            return {"status": "error", "message": f"IR 저장 실패: {exc}"}

    return {
        "status": "success",
        "data": {
            "title": ir["title"],
            "suggested_output": _default_output_path(pdf_path),
            "measures": len(ir["measures"]),
            "notes": sum(len(b["notes"]) for m in ir["measures"] for b in m["beats"]),
            "notation_kinds": sorted({m["kind"] for m in ir["measures"]}),
            "warnings": ir["warnings"],
        },
    }


def _open_in_guitar_pro_impl(file_path: str) -> dict:
    """저장된 파일을 Guitar Pro 8 로 띄운다."""
    if not os.path.isfile(file_path):
        return {"status": "error", "message": f"파일이 없습니다: {file_path}"}
    if shutil.which("open") is None:
        return {"status": "error", "message": "macOS 의 open 명령을 찾을 수 없습니다"}
    try:
        subprocess.run(["open", "-a", GUITAR_PRO_APP, file_path], check=True)
    except subprocess.CalledProcessError as exc:
        return {"status": "error",
                "message": f"{GUITAR_PRO_APP} 실행 실패 (exit {exc.returncode})"}
    return {"status": "success", "message": f"{GUITAR_PRO_APP} 로 열었습니다: {file_path}"}
```

기존 도구 등록 함수 안(다른 `@mcp.tool(...)` 들과 같은 위치)에 추가:

```python
    @mcp.tool("import_tab_pdf")
    def import_tab_pdf(ctx: Context, pdf_path: str, tempo: int = None,
                       ir_path: str = None) -> Dict[str, Any]:
        """Parse a Finale-engraved guitar tab PDF into the current song."""
        return _import_tab_pdf_impl(controller, pdf_path, tempo, ir_path)

    @mcp.tool("open_in_guitar_pro")
    def open_in_guitar_pro(ctx: Context, file_path: str) -> Dict[str, Any]:
        """Open a saved Guitar Pro file in the Guitar Pro 8 desktop app."""
        return _open_in_guitar_pro_impl(file_path)
```

- [ ] **Step 4: 통과 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -v
```

기대: 전부 PASS

- [ ] **Step 5: MCP 서버에서 도구가 보이는지 확인**

```bash
cd guitar-pro-mcp-main && printf '%s\n%s\n%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
 '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 | uv run -m src.run_mcp_server 2>/dev/null \
 | python3 -c "import sys,json; [print(sorted(n for n in (t['name'] for t in json.loads(l)['result']['tools']) if 'tab_pdf' in n or 'guitar_pro' in n)) for l in sys.stdin if '\"id\": 2' in l or '\"id\":2' in l]"
```

기대: `['import_tab_pdf', 'load_guitar_pro', 'open_in_guitar_pro', 'save_guitar_pro']` — 도구 총 37개

- [ ] **Step 6: 실제 변환 end-to-end 확인**

```bash
cd guitar-pro-mcp-main && uv run python -c "
import sys; sys.path.insert(0, 'src')
from controllers import GuitarProController
import mcp_tools
c = GuitarProController()
r = mcp_tools._import_tab_pdf_impl(c, '../pdf/나는반딧불.pdf', tempo=80, ir_path='../gp/나는반딧불.ir.json')
print('status:', r['status'])
print('마디:', r['data']['measures'], '노트:', r['data']['notes'])
print('표기법:', r['data']['notation_kinds'])
print('경고:', len(r['data']['warnings']))
out = r['data']['suggested_output']
c.save_file(out)
print('저장 완료:', out)
"
```

기대: `status: success`, 마디 58, 경고 0건, `gp/나는반딧불.gp5` 생성

- [ ] **Step 7: 커밋**

```bash
git add guitar-pro-mcp-main/src/mcp_tools.py guitar-pro-mcp-main/tests/test_tab_pdf.py
git commit -m "feat: import_tab_pdf / open_in_guitar_pro MCP 도구 추가

- PDF 타브 파싱을 tool call 1회로. 노트 300개 개별 호출 문제 없음
- Guitar Pro 8 은 스크립팅 API 가 없어 open -a 로 연결
- 두 도구 모두 예외 대신 status/message 반환 (기존 관례)"
```

---

## Self-Review

**1. Spec coverage**

| spec 절 | 담당 태스크 |
|---|---|
| 2절 `.gpx` 불가 → `.gp5` | Task 7 `build.write_gp5` (GP5_VERSION 고정) |
| 2절 GP8 파일 연결 | Task 8 `open_in_guitar_pro` |
| 3절 staff 구조·14시스템 | Task 2 |
| 3절 58마디 | Task 3 |
| 3절 표기법 두 종류 | Task 6 `system_notation` |
| 3절 코드 5개 | Task 6 `chords.VOICINGS` |
| 4절 API 함정 | Task 7 `build.py` 한 곳에 격리 |
| 5절 인코딩 버그 2개 | Task 1 |
| 5절 `pymupdf` 의존성 | Task 1 |
| 5절 `json_export.py` 미수정 | 어느 태스크도 건드리지 않음 (의도적) |
| 5절 extract 8단계 | Task 2·3·4·5·6·7 에 분배 |
| 5절 IR 스키마 | Task 7 `extract_ir` |
| 5절 도구 2개 | Task 8 |
| 6절 음길이 규칙·검산 | Task 5 |
| 7절 즉시 실패 vs 경고 | Task 7 `NotATabPdf` / `warnings` |
| 8절 테스트 4개 | Task 2(14시스템)·3(58마디)·4(마디1)·5(검산)·6(슬래시)·7(왕복) |
| 9절 리스크 | Task 6 Step 5 / Task 7 Step 5 에 조정 지침 |
| 10절 사용자 절차 | Task 8 Step 6 |

빠진 것 없음. `json_export.py` 는 의도적 미수정으로 spec 과 일치.

**2. Placeholder scan**

Task 7 Step 3 의 `starts = [0.0 if not measures else ...] and None` 은 죽은 줄이다. 같은 Step 안에 삭제 지시를 명시했다. 그 외 TBD/TODO/"적절히 처리" 없음. 모든 코드 스텝에 실제 코드가 있다.

**3. Type consistency**

- `TabNote(string, fret, x)` — Task 4 정의, Task 5·6 에서 동일 필드로 사용 ✓
- `Beat(x, duration, dotted, rest, notes, chord, stroke)` — Task 5 정의, Task 6 에서 `chord`/`stroke` 채움, Task 7 에서 전 필드 직렬화 ✓
- `System(melody_ys, tab_ys)` — Task 2 정의, Task 3·4·6 에서 사용 ✓
- `Glyph(x, y, char, font, size)` — Task 2 정의, Task 4·6 에서 `.font`/`.size`/`.char`/`.x`/`.y` 사용 ✓
- `find_barlines(geo, system)` — Task 3 정의, Task 6·7 호출 시그니처 일치 ✓
- `_duration_for(geo, x)` — Task 5 정의(private), Task 6 `slash_beats` 에서 재사용 ✓
- `_glyphs_near(geo, x, window)` — Task 5 정의, Task 6 에서 재사용 ✓
- `chords.voicing_for(name)` — Task 6 정의, `chord_names` 필터와 `slash_beats` 양쪽에서 사용 ✓
- `extract_ir` 반환 dict 키 — Task 7 정의, Task 8 에서 `ir["measures"]`, `ir["warnings"]`, `ir["title"]` 사용 ✓

`geometry.py` 는 Task 2 에서 만들고 Task 3 에서 `find_barlines` 를 추가한다. 상수 이름 중복 없음.
