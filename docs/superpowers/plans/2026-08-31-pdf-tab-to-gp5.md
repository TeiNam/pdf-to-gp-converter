# PDF 기타 타브 → Guitar Pro `.gp5` 변환 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finale로 조판된 기타 타브 PDF를 `.gp5` 로 변환하는 기능을 guitar-pro-mcp 의 도구로 추가한다.

**Architecture:** PDF 기하 수집(`geometry.py`) → 중간표현 IR(`extract.py`) → `pyguitarpro` 조립(`build.py`) 3단 분리. 음길이는 **빔 기하가 아니라 x 간격 비례 + 합제약 스냅**으로 구한다 (2절). 슬래시 스트러밍 구간은 코드 보이싱 표(`chords.py`)로 채운다. MCP 도구 2개로 노출해 곡 전체가 tool call 1회에 파싱된다.

**Tech Stack:** Python 3.14 / uv / pymupdf / pyguitarpro / mcp<2 / pytest

**Spec:** `docs/superpowers/specs/2026-08-31-pdf-tab-to-gp5-design.md`

**개정 이력:** 1차 계획을 Claude + Codex 2way 리뷰로 검증해 blocker 5건·major 10건이 나와 재작성했다. 가장 큰 변경은 음길이 알고리즘 교체다. 전체 목록은 부록 A.

---

## 1. Global Constraints

- Python 실행·의존성은 **전부 `uv`**. `pip` 금지. 추가는 `uv add`, 실행은 `uv run`.
- 작업 디렉터리는 `guitar-pro-mcp-main/` (프로젝트 내 vendored MCP, 원격 없음).
- `mcp` 는 `>=0.2.0,<2` 고정. mcp 2.x 는 `FastMCP` → `MCPServer` 개명으로 이 코드베이스가 import 실패한다.
- `.gp5` 읽기·쓰기는 **반드시 `encoding="cp949"`**. 기본 `cp1252` 는 한글에서 쓰기 `UnicodeEncodeError`, 읽기는 조용한 mojibake.
- `pyguitarpro` 명시 필수: `NoteType.normal`, `BeatStatus.normal` (기본값은 `rest`, `empty`).
- 생성자는 부모 인자를 요구한다: `Track(song)`, `Measure(track, header)`, `Voice(measure)`, `Beat(voice)`, `Note(beat)`, `GuitarString(number, value)`.
- `Song()` 은 `tracks`/`measureHeaders` 를 기본값으로 채우므로 조립 전에 `.clear()`.
- 실제 클래스명은 `FileOperationsController` 다. `GuitarProFileMixin` 은 **존재하지 않는다**.
- `GuitarProController` 는 상속이 아니라 `__getattr__` 위임이며 호출 전후로 `current_song` 을 양방향 동기화한다. 따라서 `controller.current_song = song` 후 `controller.save_file(path)` 가 성립한다.
- MCP 도구 등록 함수는 `setup_mcp_tools(mcp, controller)` 다.
- **stdio 서버는 stdout 에 MCP 메시지 외 아무것도 쓰지 않는다.** 진단 출력은 stderr/logger 로.
- 입력은 `pdf/`, 산출은 `gp/`. 둘 다 gitignore. `gp/` 는 없으면 만든다.
- 폰트 이름에 의존하지 않는다. `CIDFont+F2` 같은 subset 이름은 PDF마다 다르게 배정된다. 프렛 숫자 판정은 **숫자 + 타브 선 스냅 + 크기 상한**으로 한다.
- 추측으로 값을 채우지 않는다. `VOICINGS` 에 없는 코드는 노트를 비우고 경고한다.
- 에러는 삼키지 않는다. 입력이 대상 아님 → 즉시 실패. 데이터 이상 → `warnings` 수집 후 진행.

### 검증된 기대값 (프로토타입 실측 — 테스트에 그대로 쓴다)

| 항목 | 값 |
|---|---|
| 타브 시스템 | **14** (p1 4, p2 5, p3 5) |
| 마디 | **58** (12시스템×4 + 2시스템×5) |
| 마디별 표기법 | fret 32, slash 25, mixed 1, empty 0 |
| 총 노트 | **1502** |
| 음길이 합제약 스냅 실패 | **0 / 58** |
| 코드네임 | `Cadd9`×20, `E7`×14, `Am`×14, `F`×14, `G`×14 (정확히 이 5종) |
| 시스템1 5선 y | 145.4 / 150.5 / 155.6 / 160.8 / 165.8 |
| 시스템1 6선 y | 206.9 / 214.6 / 222.2 / 229.9 / 237.6 / 245.3 |
| 시스템1 마디선 x | 198 / 324 / 450 / 576 |

**마디1 = 8 beat / 10 노트**, 전부 8분음표:

```
beat1 (5,3) | beat2 (3,0) | beat3 (1,0)+(2,3) | beat4 (3,0)
beat5 (6,0) | beat6 (3,1) | beat7 (1,0)+(2,3) | beat8 (3,1)
```

> 1차 계획은 이를 "노트 8개"로 잘못 적고 `(4,0)` 으로 오전사했다. y=222.5 는 타브 3선(222.2)에
> 스냅되므로 string **3** 이고, beat 3·7 은 2음 화음이다. 위가 정답이다.

---

## 2. 음길이 알고리즘 — 왜 빔 기하를 버렸는가

1차 계획은 빔(수평선) 개수로 음길이를 정하려 했다. 2way 리뷰에서 실행해 본 결과 **58마디 중 56개가
불일치**했다. 원인 세 가지를 실측으로 확인했다.

1. 기하 탐색이 페이지 전체를 x 만으로 훑어 다른 시스템의 빔·기둥이 섞였다.
2. 시스템 대역으로 좁혀도 **멜로디 staff 의 기둥**이 타브 것과 섞였다 (불일치 21개로만 줄었다).
3. 결정적으로 **Finale 는 빔을 기울어진 사각형으로 그린다.** `|dy| < 0.5` 수평선 필터가 기울어진
   빔을 전부 버렸다. 게다가 배경 삽화가 staff 를 덮고, 해머온 `H`·곡선 아티큘레이션도 섞여 있다.

대신 **조판된 악보에서 x 간격이 음길이에 비례한다**는 성질을 쓴다. 이미 안정적으로 뽑는 두 값
(마디선 x, 노트 x) 만으로 계산된다.

```
1. 마디 [x0, x1) 안의 beat x 목록 cl = (프렛 숫자 x) ∪ (슬래시 노트헤드 x), ±2pt 병합
2. gaps = [cl[1]-cl[0], …, cl[n-1]-cl[n-2], x1-cl[n-1]]
3. props[i] = 4.0 × gaps[i] / sum(gaps)        # 항상 정확히 4.0 으로 합해진다
4. 각 props[i] 를 legal 집합으로 스냅. 합이 목표(4/4 → 4.0)와 다르면
   "스냅 오차가 가장 적게 늘어나는 beat 하나"를 다른 legal 값으로 바꾸는 그리디를 반복
```

`legal` = 4분음표 단위 길이 집합:

| Duration.value | dotted | quarters |
|---|---|---|
| 1 | | 4.0 |
| 2 | ● | 3.0 |
| 2 | | 2.0 |
| 4 | ● | 1.5 |
| 4 | | 1.0 |
| 8 | ● | 0.75 |
| 8 | | 0.5 |
| 16 | ● | 0.375 |
| 16 | | 0.25 |
| 32 | | 0.125 |

**프로토타입 결과: 58마디 전부 성공(실패 0), 마디1 = 8분음표 8개.** 빔·기둥·플래그·부점 글리프
탐지 코드 전체가 필요 없어진다.

### 한계 (정직하게)

- 휴리스틱이다. 조판이 비례적이라는 가정에 의존한다 (Finale 기본 동작).
- **쉼표를 구분하지 못한다.** 쉼표는 x 공간을 차지하지만 숫자가 없어 인접 gap 에 흡수된다.
  이 PDF 는 타브 staff 에 쉼표가 0개임을 확인했다 (56개 전부 멜로디 staff). 타브 대역에서 쉼표
  글리프가 발견되면 `unsupported_glyph` 경고를 남긴다.
- 타이·잇단음표도 같은 이유로 경고 대상이다.

---

## 3. File Structure

| 파일 | 책임 |
|---|---|
| `guitar-pro-mcp-main/pyproject.toml` | 수정: `pymupdf` 추가, dev 에 `pytest`·`pytest-cov` |
| `.../src/controllers/guitar_pro/file_operations.py` | 수정: `save_file`/`load_file` 에 `cp949` |
| `.../src/run_mcp_server.py` | 수정: `print()` → logger (stdout 오염 제거) |
| `.../src/utils/tab_pdf/__init__.py` | 신규: 패키지 마커 |
| `.../src/utils/tab_pdf/durations.py` | 신규: legal 집합 + 합제약 스냅 |
| `.../src/utils/tab_pdf/geometry.py` | 신규: 선·글리프 수집, 시스템·마디선 검출 |
| `.../src/utils/tab_pdf/chords.py` | 신규: 코드네임 → 보이싱 |
| `.../src/utils/tab_pdf/extract.py` | 신규: 위를 조합해 IR 생성 |
| `.../src/utils/tab_pdf/build.py` | 신규: IR → `pyguitarpro.Song` |
| `.../src/mcp_tools.py` | 수정: 도구 2개 등록 |
| `.../tests/test_tab_pdf.py` | 신규: 실제 PDF 기반 (없으면 skip) |
| `.../tests/test_synthetic.py` | 신규: **합성 PDF 기반 — 깨끗한 clone 에서도 실행된다** |
| `pdf/` `gp/` (gitignore) | 입력 / 산출 |

`durations.py` 를 분리하는 이유: 순수 함수라 PDF 없이 테스트되고, 합성 테스트의 토대가 된다.

---

## Task 1: 의존성·인코딩·stdout 오염 수정

**Files:**
- Modify: `pyproject.toml`, `src/controllers/guitar_pro/file_operations.py`, `src/run_mcp_server.py`
- Test: `tests/test_tab_pdf.py`

**Interfaces:**
- Consumes: 없음
- Produces: `pytest` 실행 가능. `.gp5` I/O 가 한글 제목을 왕복 보존. stdio 서버 stdout 청결.

- [ ] **Step 1: 의존성 추가**

```bash
cd guitar-pro-mcp-main && uv add pymupdf && uv add --dev pytest pytest-cov
```

`pytest` 는 `[project.optional-dependencies] dev` 에만 있어 `uv run pytest` 가 `ModuleNotFoundError`
로 죽는다. `pyproject.toml` 의 `addopts = "-v --cov=src"` 때문에 `pytest-cov` 도 필요하다.

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_tab_pdf.py` 신규:

```python
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
```

- [ ] **Step 3: 실패 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -v
```

기대: `test_save_and_load_preserve_korean` FAIL (`UnicodeEncodeError`),
`test_server_module_does_not_print_to_stdout` FAIL (`print(` 3건).

- [ ] **Step 4: 세 곳 수정**

`file_operations.py` — `load_file` 의 `parse(file_path)` 를 `parse(file_path, encoding="cp949")` 로,
`save_file` 의 `write(self.current_song, file_path)` 를
`write(self.current_song, file_path, encoding="cp949")` 로.

`run_mcp_server.py` — `mcp.run()` 앞의 `print(...)` 3개를 모두 `logger.info(...)` 로 바꾼다.
`logging.basicConfig` 는 기본이 stderr 이므로 stdout 이 깨끗해진다.

- [ ] **Step 5: 통과 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_tab_pdf.py -v
```

기대: 2 passed

- [ ] **Step 6: 커밋**

```bash
git add guitar-pro-mcp-main/pyproject.toml guitar-pro-mcp-main/uv.lock \
        guitar-pro-mcp-main/src/controllers/guitar_pro/file_operations.py \
        guitar-pro-mcp-main/src/run_mcp_server.py \
        guitar-pro-mcp-main/tests/test_tab_pdf.py
git commit -m "fix: cp949 인코딩·stdout 오염·테스트 의존성 수정"
```

---

## Task 2: 음길이 합제약 스냅 (`durations.py`)

PDF 없이 테스트되는 순수 함수다. 먼저 만들어 합성 테스트의 토대로 쓴다.

**Files:**
- Create: `src/utils/tab_pdf/__init__.py`, `src/utils/tab_pdf/durations.py`
- Test: `tests/test_synthetic.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `durations.LegalDuration(value: int, dotted: bool, quarters: float)` — frozen dataclass
  - `durations.LEGAL: tuple[LegalDuration, ...]`
  - `durations.target_quarters(numerator: int, denominator: int) -> float`
  - `durations.proportions(beat_xs: list[float], measure_end_x: float, target: float) -> list[float]`
  - `durations.fit_durations(props: list[float], target: float) -> tuple[list[LegalDuration], bool]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_synthetic.py` 신규:

```python
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
```

- [ ] **Step 2: 실패 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_synthetic.py -v
```

기대: FAIL — `ModuleNotFoundError: No module named 'utils.tab_pdf'`

- [ ] **Step 3: 구현**

`src/utils/tab_pdf/__init__.py`:

```python
"""Finale 조판 기타 타브 PDF → Guitar Pro 변환."""
```

`src/utils/tab_pdf/durations.py`:

```python
"""음길이를 4분음표 단위로 다루고, 마디 합 제약 아래 legal 값으로 스냅한다.

PDF 를 모르는 순수 계산 모듈이다. x 간격이 음길이에 비례한다는 조판 성질만 쓴다.
빔 기하를 쓰지 않는 이유는 계획 문서 2절 참조.
"""

from dataclasses import dataclass

# 그리디 보정 반복 상한 — beat 수가 수십 개여도 넉넉하다
MAX_REPAIR_STEPS = 200
EPSILON = 1e-9


@dataclass(frozen=True)
class LegalDuration:
    value: int          # pyguitarpro Duration.value (1/2/4/8/16/32)
    dotted: bool
    quarters: float     # 4분음표 단위 길이


LEGAL: tuple[LegalDuration, ...] = (
    LegalDuration(1, False, 4.0),
    LegalDuration(2, True, 3.0),
    LegalDuration(2, False, 2.0),
    LegalDuration(4, True, 1.5),
    LegalDuration(4, False, 1.0),
    LegalDuration(8, True, 0.75),
    LegalDuration(8, False, 0.5),
    LegalDuration(16, True, 0.375),
    LegalDuration(16, False, 0.25),
    LegalDuration(32, False, 0.125),
)


def target_quarters(numerator: int, denominator: int) -> float:
    """박자표의 마디 길이를 4분음표 단위로."""
    return 4.0 * numerator / denominator


def proportions(beat_xs: list[float], measure_end_x: float,
                target: float) -> list[float]:
    """beat x 간격을 target 으로 정규화한 길이 목록. 합은 정확히 target."""
    if not beat_xs:
        return []
    gaps = [beat_xs[i + 1] - beat_xs[i] for i in range(len(beat_xs) - 1)]
    gaps.append(measure_end_x - beat_xs[-1])
    span = sum(gaps)
    if span <= 0:
        # 좌표가 퇴화했다 — 균등 분배로 떨어뜨리되 합은 유지한다
        return [target / len(beat_xs)] * len(beat_xs)
    return [target * gap / span for gap in gaps]


def _nearest(prop: float) -> LegalDuration:
    return min(LEGAL, key=lambda legal: abs(legal.quarters - prop))


def fit_durations(props: list[float],
                  target: float) -> tuple[list[LegalDuration], bool]:
    """비례값을 legal 값으로 스냅하되 합이 정확히 target 이 되게 보정한다.

    독립 스냅은 반올림 때문에 합이 어긋난다. 스냅 오차가 가장 적게 늘어나는
    beat 하나를 다른 legal 값으로 바꾸는 그리디를 합이 맞을 때까지 반복한다.

    Returns: (스냅 결과, 합이 정확히 맞았는지)
    """
    if not props:
        return [], True
    current = [_nearest(prop) for prop in props]
    for _ in range(MAX_REPAIR_STEPS):
        diff = target - sum(legal.quarters for legal in current)
        if abs(diff) < EPSILON:
            return current, True
        best = None
        for index, prop in enumerate(props):
            for candidate in LEGAL:
                if candidate is current[index]:
                    continue
                remaining = diff - (candidate.quarters - current[index].quarters)
                if abs(remaining) >= abs(diff) - EPSILON:
                    continue          # 차이를 줄이지 못하는 후보
                cost = (abs(candidate.quarters - prop)
                        - abs(current[index].quarters - prop))
                if best is None or cost < best[0]:
                    best = (cost, index, candidate)
        if best is None:
            return current, False     # 더 줄일 수 없다 — 거짓말하지 않고 실패 보고
        current[best[1]] = best[2]
    return current, False
```

- [ ] **Step 4: 통과 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_synthetic.py -v
```

기대: 5 passed

- [ ] **Step 5: 커밋**

```bash
git add guitar-pro-mcp-main/src/utils/tab_pdf guitar-pro-mcp-main/tests/test_synthetic.py
git commit -m "feat: 음길이 합제약 스냅 (x간격 비례 기반, PDF 비의존 순수 함수)"
```

---

## Task 3: 기하 수집과 시스템·마디선 검출

**Files:**
- Create: `src/utils/tab_pdf/geometry.py`
- Test: `tests/test_synthetic.py`, `tests/test_tab_pdf.py`

**Interfaces:**
- Consumes: pymupdf
- Produces:
  - `geometry.HLine(y, x0, x1)` — `.width` 프로퍼티
  - `geometry.VLine(x, y0, y1)`
  - `geometry.Glyph(x, y, x_end, char, font, size)` — `x`/`y` 는 baseline origin, `x_end` 는 잉크 우측 끝(`bbox[2]`). **`x_end` 는 코드네임 토큰화에 필수** (부록 A-2)
  - `geometry.PageGeometry(hlines, vlines, glyphs)`
  - `geometry.System(melody_ys, tab_ys)`
  - `geometry.load_page_geometry(page) -> PageGeometry`
  - `geometry.find_systems(geo) -> list[System]`
  - `geometry.find_barlines(geo, system) -> list[float]`
  - `geometry.tab_left_edge(geo, system) -> float`
  - `geometry.measure_bounds(geo, system) -> list[tuple[float, float]]`

- [ ] **Step 1: 합성 PDF 테스트 작성 (clean clone 에서도 도는 테스트)**

`tests/test_synthetic.py` 에 추가:

```python
# 합성 악보 좌표 — 실제 PDF 시스템1 배치를 축약 재현
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
```

`tests/test_tab_pdf.py` 에 추가:

```python
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
```

- [ ] **Step 2: 실패 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/ -v
```

기대: geometry 관련 전부 FAIL (`No module named 'utils.tab_pdf.geometry'`)

- [ ] **Step 3: 구현**

`src/utils/tab_pdf/geometry.py`:

```python
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
    melody_ys: list[float]
    tab_ys: list[float]


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
    return [System(melody_ys=list(a), tab_ys=list(b))
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
    """마디별 [x0, x1) 경계 목록."""
    bars = find_barlines(geo, system)
    if not bars:
        return []
    edges = [tab_left_edge(geo, system)] + bars
    return [(edges[i], edges[i + 1]) for i in range(len(bars))]
```

- [ ] **Step 4: 통과 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/ -v
```

기대: 합성 3개 + 실제 2개 PASS (실제 PDF 없으면 2개 skip)

- [ ] **Step 5: 커밋**

```bash
git add guitar-pro-mcp-main/src/utils/tab_pdf/geometry.py guitar-pro-mcp-main/tests
git commit -m "feat: PDF 기하 수집과 시스템·마디선 검출 (14시스템/58마디)"
```

---

## Task 4: 코드네임 → 보이싱 (`chords.py`)

**Files:**
- Create: `src/utils/tab_pdf/chords.py`
- Test: `tests/test_synthetic.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `chords.VOICINGS: dict[str, tuple[tuple[int, int], ...]]`
  - `chords.voicing_for(name: str | None) -> tuple[tuple[int, int], ...] | None`
  - `chords.looks_like_chord(token: str) -> bool` — 페이지 번호(`2`,`3`)·해머온(`H`) 을 거른다

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_synthetic.py` 에 추가:

```python
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
```

- [ ] **Step 2: 실패 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_synthetic.py -k chord -v
```

기대: FAIL — `No module named 'utils.tab_pdf.chords'`

- [ ] **Step 3: 구현**

`src/utils/tab_pdf/chords.py`:

```python
"""코드네임 → 기타 프렛 보이싱.

이 곡(`나는반딧불`)에 실제로 쓰인 5개만 넣는다. 표에 없으면 None 을 돌려 호출자가
경고를 남기게 한다 — 추측으로 채우면 틀린 악보가 조용히 나온다.
string 1 = 고음 E, 6 = 저음 E. fret 0 = 개방현.
"""

import re

# 코드명 후보 판정 — A~G 로 시작하고 두 번째 글자가 숫자가 아니어야 한다.
# 'H'(해머온), '2'/'3'(페이지 번호) 를 거르면서 미등록 코드는 통과시킨다.
_CHORD_PATTERN = re.compile(r"^[A-G](?:[#b])?(?:[A-Za-z][A-Za-z0-9#b/+()-]*|[0-9]+|)$")

VOICINGS: dict[str, tuple[tuple[int, int], ...]] = {
    "Cadd9": ((5, 3), (4, 2), (3, 0), (2, 3), (1, 3)),
    "E7":    ((6, 0), (5, 2), (4, 0), (3, 1), (2, 0), (1, 0)),
    "Am":    ((5, 0), (4, 2), (3, 2), (2, 1), (1, 0)),
    "F":     ((4, 3), (3, 2), (2, 1), (1, 1)),
    "G":     ((6, 3), (5, 2), (4, 0), (3, 0), (2, 0), (1, 3)),
}


def looks_like_chord(token: str) -> bool:
    """코드명 형태인지. 미등록 코드도 True 여야 경고 경로가 살아난다."""
    return bool(_CHORD_PATTERN.match(token.strip()))


def voicing_for(name: str | None) -> tuple[tuple[int, int], ...] | None:
    """코드명의 보이싱. 모르는 코드는 None."""
    if name is None:
        return None
    return VOICINGS.get(name.strip())
```

- [ ] **Step 4: 통과 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/test_synthetic.py -k chord -v
```

기대: 3 passed. `looks_like_chord("H")` 가 True 로 나오면 정규식의 `[A-G]` 범위를 확인한다
(`H` 는 A–G 밖이라 매치되지 않아야 한다).

- [ ] **Step 5: 커밋**

```bash
git add guitar-pro-mcp-main/src/utils/tab_pdf/chords.py guitar-pro-mcp-main/tests/test_synthetic.py
git commit -m "feat: 코드 보이싱 표와 코드명 형태 판정 (미등록 코드는 None)"
```

---

## Task 5: IR 추출 (`extract.py`)

**Files:**
- Create: `src/utils/tab_pdf/extract.py`
- Test: `tests/test_tab_pdf.py`, `tests/test_synthetic.py`

**Interfaces:**
- Consumes: `geometry`, `durations`, `chords`
- Produces:
  - `extract.NotATabPdf(ValueError)`
  - `extract.extract_ir(pdf_path, tempo=None, title=None, artist=None) -> dict`
  - IR 키: `title`, `artist`, `tempo`, `tuning`, `measures`, `warnings`
  - `measures[i]`: `index`, `time_sig`, `kind`(`fret`|`slash`|`mixed`|`empty`), `beats`
  - `beats[j]`: `x`, `duration`, `dotted`, `chord`, `stroke`(`down`|`up`|`None`), `notes`
  - `notes[k]`: `string`, `fret`
  - `warnings[n]`: `measure`, `kind`, `detail`. `kind` ∈ `duration_mismatch`, `empty_measure`, `unknown_chord`, `unsnapped_digit`, `unsupported_glyph`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_tab_pdf.py` 에 추가:

```python
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
    assert kinds == {"fret": 32, "slash": 25, "mixed": 1}
    notes = sum(len(b["notes"]) for m in ir["measures"] for b in m["beats"])
    assert notes == 1502


@needs_pdf
def test_ir_every_measure_sums_to_its_time_signature():
    """58마디 전부 합제약 스냅 성공 — duration_mismatch·empty_measure 0건."""
    from utils.tab_pdf import extract

    ir = extract.extract_ir(str(PDF), tempo=80)
    assert [w for w in ir["warnings"] if w["kind"] == "duration_mismatch"] == []
    assert [w for w in ir["warnings"] if w["kind"] == "empty_measure"] == []


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
    """PDF 메타 제목은 "Ÿfl˘'‹.musx" 다. 파일명에서 제목을 얻어야 한다."""
    from utils.tab_pdf import extract

    ir = extract.extract_ir(str(PDF))
    assert ir["title"] == "나는반딧불"
    assert ".musx" not in ir["title"]
    override = extract.extract_ir(str(PDF), title="반딧불", artist="황가람")
    assert override["title"] == "반딧불"
    assert override["artist"] == "황가람"
```

`tests/test_synthetic.py` 에 추가:

```python
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
```

- [ ] **Step 2: 실패 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/ -v
```

기대: extract 관련 FAIL (`No module named 'utils.tab_pdf.extract'`)

- [ ] **Step 3: 구현**

`src/utils/tab_pdf/extract.py`:

```python
"""PDF 기하를 음악적 중간표현(IR)으로 해석한다."""

import os
from dataclasses import dataclass, field

import pymupdf

from . import chords, durations, geometry

STANDARD_TUNING = (64, 59, 55, 50, 45, 40)      # 1=고음 E … 6=저음 E
DEFAULT_TEMPO = 80
DEFAULT_TIME_SIG = (4, 4)

# 프렛 숫자로 인정할 글리프 크기 상한 (pt). 'T','A','B' 세로 라벨은 14.4pt 로 더 크다
MAX_FRET_GLYPH_SIZE = 11.0
# 숫자 baseline 이 이 거리 안이면 그 타브 선에 속한다 (선 간격 7.7 의 절반 미만)
MAX_STRING_SNAP_DISTANCE = 4.0
# 같은 beat(화음)로 묶을 x 허용 오차 (pt)
BEAT_CLUSTER_TOLERANCE = 2.0
# 글리프가 타브 staff 에 속한다고 볼 상하 여유 (pt)
TAB_BAND_MARGIN = 8.0
# 코드 행 대역: 멜로디 5선 위쪽 이만큼 (pt). 제목·부제를 배제한다
CHORD_BAND_HEIGHT = 20.0
# 코드명 문자를 한 토큰으로 이을 간격 상한 (pt). 직전 문자의 잉크 끝 기준.
# 실측: 코드명 내부 최대 1.41, 코드명 사이 최소 40.32
CHORD_CHAR_GAP = 5.0
# 코드가 beat 에 적용된다고 볼 x 여유 (pt)
CHORD_APPLY_SLACK = 5.0
# 스트로크 기호가 beat 에 속한다고 볼 x 거리 (pt)
STROKE_X_WINDOW = 6.0

SMUFL_SLASH_RANGE = (0xE100, 0xE10F)            # SMuFL Slash noteheads
SMUFL_REST_RANGE = (0xE4E0, 0xE4FF)             # 타브 대역에 나오면 경고
SMUFL_STROKE_DOWN = ""
SMUFL_STROKE_UP = ""


class NotATabPdf(ValueError):
    """타브 악보로 해석할 수 없는 입력."""


@dataclass
class _Warnings:
    items: list[dict] = field(default_factory=list)

    def add(self, measure: int, kind: str, detail: str) -> None:
        self.items.append({"measure": measure, "kind": kind, "detail": detail})


def _in_range(char: str, bounds: tuple[int, int]) -> bool:
    low, high = bounds
    return low <= ord(char) <= high


def _in_tab_band(glyph: geometry.Glyph, system: geometry.System) -> bool:
    return (system.tab_ys[0] - TAB_BAND_MARGIN <= glyph.y
            <= system.tab_ys[-1] + TAB_BAND_MARGIN)


def _snap_to_string(y: float, tab_ys: list[float]) -> int | None:
    """baseline y 를 가장 가까운 타브 선에 붙여 줄 번호(1..6)를 돌려준다."""
    index = min(range(len(tab_ys)), key=lambda i: abs(y - tab_ys[i]))
    if abs(y - tab_ys[index]) > MAX_STRING_SNAP_DISTANCE:
        return None
    return index + 1        # tab_ys 는 위→아래, 위가 고음 E = string 1


def _fret_glyphs(geo, system, x0, x1):
    """폰트 이름에 의존하지 않는다 — 숫자 + 타브 대역 + 크기 상한."""
    return [g for g in geo.glyphs
            if x0 <= g.x < x1 and g.char.isdigit()
            and g.size <= MAX_FRET_GLYPH_SIZE and _in_tab_band(g, system)]


def _slash_xs(geo, system, x0, x1) -> list[float]:
    return [g.x for g in geo.glyphs
            if x0 <= g.x < x1 and _in_range(g.char, SMUFL_SLASH_RANGE)
            and _in_tab_band(g, system)]


def _chord_tokens(geo, system, x_limit: float) -> list[tuple[float, str]]:
    """이 시스템의 코드 행에서 (x, 코드명) 을 복원한다.

    직전 문자의 잉크 끝(`x_end`) 기준으로 이어붙인다. origin 기준으로는 'Cadd9' 가
    'C' + 'add9' 로 쪼개진다 (실측 C→a origin 간격 7.68pt, 잉크끝 기준 0.90pt).
    """
    top = system.melody_ys[0]
    candidates = sorted(
        (g for g in geo.glyphs
         if top - CHORD_BAND_HEIGHT <= g.y < top and g.x < x_limit),
        key=lambda g: (round(g.y, 1), g.x),
    )
    tokens: list[tuple[float, str]] = []
    text, start_x, prev_end, prev_y = "", None, None, None
    for glyph in candidates:
        if (prev_end is None or round(glyph.y, 1) != prev_y
                or glyph.x - prev_end > CHORD_CHAR_GAP):
            if text:
                tokens.append((start_x, text))
            text, start_x = "", glyph.x
        text += glyph.char
        prev_end, prev_y = glyph.x_end, round(glyph.y, 1)
    if text:
        tokens.append((start_x, text))
    return [(x, name) for x, name in tokens if chords.looks_like_chord(name)]


def _chord_at(tokens: list[tuple[float, str]], x: float) -> str | None:
    """x 이전(또는 같은 위치)의 가장 가까운 코드명."""
    current = None
    for token_x, name in tokens:
        if token_x <= x + CHORD_APPLY_SLACK:
            current = name
        else:
            break
    return current


def _stroke_at(geo, x: float) -> str | None:
    for glyph in geo.glyphs:
        if abs(glyph.x - x) > STROKE_X_WINDOW:
            continue
        if glyph.char == SMUFL_STROKE_DOWN:
            return "down"
        if glyph.char == SMUFL_STROKE_UP:
            return "up"
    return None


def _cluster(xs: list[float]) -> list[float]:
    clustered: list[float] = []
    for x in sorted(xs):
        if not clustered or x - clustered[-1] > BEAT_CLUSTER_TOLERANCE:
            clustered.append(x)
    return clustered


def _classify(fret_glyphs, slash_xs) -> str:
    if fret_glyphs and slash_xs:
        return "mixed"
    if fret_glyphs:
        return "fret"
    if slash_xs:
        return "slash"
    return "empty"


def _warn_unsupported(geo, system, x0, x1, index, warn) -> None:
    for glyph in geo.glyphs:
        if not (x0 <= glyph.x < x1) or not _in_tab_band(glyph, system):
            continue
        if _in_range(glyph.char, SMUFL_REST_RANGE):
            warn.add(index, "unsupported_glyph",
                     f"타브 대역 쉼표 {hex(ord(glyph.char))} at x={glyph.x:.1f} "
                     f"— x간격 기반 음길이가 틀어질 수 있다")


def _beat_notes(fret_glyphs, beat_x, system, index, warn) -> list[dict]:
    notes = []
    for glyph in fret_glyphs:
        if abs(glyph.x - beat_x) > BEAT_CLUSTER_TOLERANCE:
            continue
        string = _snap_to_string(glyph.y, system.tab_ys)
        if string is None:
            warn.add(index, "unsnapped_digit",
                     f"숫자 {glyph.char!r} at ({glyph.x:.1f}, {glyph.y:.1f}) "
                     f"가 어느 줄에도 스냅되지 않았다")
            continue
        notes.append({"string": string, "fret": int(glyph.char)})
    return notes


def _build_measure(geo, system, bounds, index, tokens, warn) -> dict:
    x0, x1 = bounds
    fret_glyphs = _fret_glyphs(geo, system, x0, x1)
    slash_xs = _slash_xs(geo, system, x0, x1)
    kind = _classify(fret_glyphs, slash_xs)
    _warn_unsupported(geo, system, x0, x1, index, warn)

    beat_xs = _cluster([g.x for g in fret_glyphs] + slash_xs)
    measure = {"index": index, "time_sig": list(DEFAULT_TIME_SIG),
               "kind": kind, "beats": []}
    if not beat_xs:
        warn.add(index, "empty_measure", f"판정 {kind}, x {x0:.1f}..{x1:.1f}")
        return measure

    target = durations.target_quarters(*DEFAULT_TIME_SIG)
    fitted, exact = durations.fit_durations(
        durations.proportions(beat_xs, x1, target), target)
    if not exact:
        total = sum(d.quarters for d in fitted)
        warn.add(index, "duration_mismatch",
                 f"합 {total:.3f} / 목표 {target:.3f}, "
                 f"durs={[d.value for d in fitted]}")

    for beat_x, duration in zip(beat_xs, fitted):
        notes = _beat_notes(fret_glyphs, beat_x, system, index, warn)
        chord = stroke = None
        if not notes:                   # 슬래시 beat — 코드 보이싱으로 채운다
            chord = _chord_at(tokens, beat_x)
            voicing = chords.voicing_for(chord)
            if chord is None:
                warn.add(index, "unknown_chord",
                         f"x={beat_x:.1f} 슬래시 beat 앞에 코드명이 없다")
            elif voicing is None:
                warn.add(index, "unknown_chord", f"{chord} — VOICINGS 에 없음")
            notes = [{"string": s, "fret": f} for s, f in (voicing or ())]
            stroke = _stroke_at(geo, beat_x)

        measure["beats"].append({
            "x": round(beat_x, 2),
            "duration": duration.value,
            "dotted": duration.dotted,
            "chord": chord,
            "stroke": stroke,
            "notes": notes,
        })
    return measure


def extract_ir(pdf_path: str, tempo: int | None = None,
               title: str | None = None, artist: str | None = None) -> dict:
    """PDF 전체를 IR 로 만든다.

    제목은 PDF 메타데이터를 쓰지 않는다 — 대상 PDF 의 메타 제목은 mojibake 된
    "Ÿfl˘'‹.musx" 다. 파일명 stem 을 쓰고 인자로 덮어쓸 수 있다.
    """
    document = pymupdf.open(pdf_path)
    warn = _Warnings()
    measures: list[dict] = []
    saw_text = False

    for page in document:
        geo = geometry.load_page_geometry(page)
        if geo.glyphs:
            saw_text = True
        for system in geometry.find_systems(geo):
            all_bounds = geometry.measure_bounds(geo, system)
            if not all_bounds:
                continue
            tokens = _chord_tokens(geo, system, all_bounds[-1][1] + 1.0)
            for bounds in all_bounds:
                measures.append(_build_measure(
                    geo, system, bounds, len(measures), tokens, warn))

    if not saw_text:
        raise NotATabPdf(
            f"{pdf_path}: 텍스트 레이어가 없다. 스캔 이미지 PDF 는 변환할 수 없다")
    if not measures:
        raise NotATabPdf(f"{pdf_path}: 6줄 타브 staff 를 찾지 못했다")

    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    return {
        "title": title if title is not None else stem,
        "artist": artist if artist is not None else "",
        "tempo": tempo or DEFAULT_TEMPO,
        "tuning": list(STANDARD_TUNING),
        "measures": measures,
        "warnings": warn.items,
    }
```

- [ ] **Step 4: 통과 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/ -v
```

기대: 전부 PASS. 노트 수(1502)나 kind 분포가 어긋나면 `MAX_FRET_GLYPH_SIZE`·`TAB_BAND_MARGIN`·
`MAX_STRING_SNAP_DISTANCE` 를 조정한다. 1절 표가 정답이다.

- [ ] **Step 5: 커밋**

```bash
git add guitar-pro-mcp-main/src/utils/tab_pdf/extract.py guitar-pro-mcp-main/tests
git commit -m "feat: IR 추출 — x간격 음길이, 슬래시 보이싱, 경고 수집"
```

---

## Task 6: IR → `.gp5` (`build.py`)

**Files:**
- Create: `src/utils/tab_pdf/build.py`
- Test: `tests/test_tab_pdf.py`, `tests/test_synthetic.py`

**Interfaces:**
- Consumes: Task 5 의 IR dict
- Produces:
  - `build.build_song(ir: dict) -> guitarpro.models.Song`
  - `build.write_gp5(song, file_path: str) -> None` — `encoding="cp949"`, `version=(5,1,0)`

- [ ] **Step 1: 스트로크 enum 이름 확인**

```bash
cd guitar-pro-mcp-main && uv run python -c "
import guitarpro.models as m
print([n for n in dir(m) if 'Stroke' in n])
print(m.BeatEffect().stroke)
"
```

이 출력에 나온 실제 이름을 아래 구현에 쓴다. 계획은 `BeatStrokeDirection.down/up` 을 가정한다.

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_tab_pdf.py` 에 추가:

```python
@needs_pdf
def test_gp5_roundtrip_preserves_everything(tmp_path):
    from utils.tab_pdf import build, extract

    ir = extract.extract_ir(str(PDF), tempo=80, artist="황가람")
    song = build.build_song(ir)
    out = tmp_path / "나는반딧불.gp5"
    build.write_gp5(song, str(out))

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
    assert gp_notes == ir_notes == 1502

    first = [b for v in track.measures[0].voices for b in v.beats]
    assert [b.duration.value for b in first] == [8] * 8
    assert [sorted((n.string, n.value) for n in b.notes) for b in first] == \
           [sorted(group) for group in EXPECTED_MEASURE1]


@needs_pdf
def test_gp5_records_strum_direction(tmp_path):
    """추출한 다운/업 스트로크가 .gp5 에 남아야 한다."""
    from utils.tab_pdf import build, extract

    ir = extract.extract_ir(str(PDF))
    assert [b for m in ir["measures"] for b in m["beats"] if b["stroke"]], \
        "IR 에 스트로크가 하나도 없다"

    out = tmp_path / "stroke.gp5"
    build.write_gp5(build.build_song(ir), str(out))
    reparsed = gp.parse(str(out), encoding="cp949")
    recorded = [b for m in reparsed.tracks[0].measures for v in m.voices
                for b in v.beats if b.effect.stroke.value]
    assert recorded, ".gp5 에 스트로크가 기록되지 않았다"
```

`tests/test_synthetic.py` 에 추가:

```python
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
    assert [sorted((n.string, n.value) for n in b.notes) for b in first] == \
           [[(3, 0)]] * 4
```

- [ ] **Step 3: 실패 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/ -v
```

기대: build 관련 FAIL (`No module named 'utils.tab_pdf.build'`)

- [ ] **Step 4: 구현**

`src/utils/tab_pdf/build.py`:

```python
"""IR → pyguitarpro Song. PDF 를 전혀 모른다."""

import guitarpro as gp
from guitarpro.models import (
    Beat, BeatStatus, BeatStrokeDirection, Duration, GuitarString, Measure,
    MeasureHeader, Note, NoteType, Song, TimeSignature, Track, Voice,
)

# GP5 는 8비트 charset — 한글 보존에 필요
GP5_ENCODING = "cp949"
GP5_VERSION = (5, 1, 0)
# GP5 는 마디당 voice 슬롯 2개를 기대한다
GP5_VOICE_SLOTS = 2
DEFAULT_VELOCITY = 95
NYLON_GUITAR_MIDI_PROGRAM = 24
# 스트로크 속도. 0 은 "스트로크 없음" 이라 1 이상을 준다
STROKE_VALUE = 1

_STROKE_DIRECTIONS = {
    "down": BeatStrokeDirection.down,
    "up": BeatStrokeDirection.up,
}


def _apply_stroke(beat: Beat, stroke: str | None) -> None:
    direction = _STROKE_DIRECTIONS.get(stroke or "")
    if direction is None:
        return
    beat.effect.stroke.direction = direction
    beat.effect.stroke.value = STROKE_VALUE


def _make_header(measure_ir: dict) -> MeasureHeader:
    header = MeasureHeader(number=measure_ir["index"] + 1)
    numerator, denominator = measure_ir["time_sig"]
    signature = TimeSignature()
    signature.numerator = numerator
    signature.denominator.value = denominator
    header.timeSignature = signature
    return header


def build_song(ir: dict) -> Song:
    song = Song(title=ir.get("title", ""), artist=ir.get("artist", ""),
                tempo=ir.get("tempo", 80))
    song.tracks.clear()
    song.measureHeaders.clear()

    track = Track(song, name="Guitar")
    track.channel.instrument = NYLON_GUITAR_MIDI_PROGRAM
    track.strings = [GuitarString(i + 1, value)
                     for i, value in enumerate(ir["tuning"])]
    track.measures.clear()

    for measure_ir in ir["measures"]:
        header = _make_header(measure_ir)
        song.measureHeaders.append(header)

        measure = Measure(track, header)
        measure.voices.clear()
        voice = Voice(measure)
        for beat_ir in measure_ir["beats"]:
            beat = Beat(
                voice,
                duration=Duration(value=beat_ir["duration"],
                                  isDotted=beat_ir["dotted"]),
                status=BeatStatus.normal,
            )
            for note_ir in beat_ir["notes"]:
                beat.notes.append(Note(
                    beat, value=note_ir["fret"], string=note_ir["string"],
                    velocity=DEFAULT_VELOCITY, type=NoteType.normal,
                ))
            _apply_stroke(beat, beat_ir.get("stroke"))
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
cd guitar-pro-mcp-main && uv run pytest tests/ -v
```

기대: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add guitar-pro-mcp-main/src/utils/tab_pdf/build.py guitar-pro-mcp-main/tests
git commit -m "feat: IR -> .gp5 조립 (cp949, 스트로크 방향 기록, 58마디 왕복 검증)"
```

---

## Task 7: MCP 도구 노출

**Files:**
- Modify: `src/mcp_tools.py`
- Test: `tests/test_tab_pdf.py`, `tests/test_synthetic.py`

**Interfaces:**
- Consumes: `extract.extract_ir`, `extract.NotATabPdf`, `build.build_song`
- Produces:
  - `mcp_tools.default_output_path(pdf_path) -> str`
  - `mcp_tools.import_tab_pdf_impl(controller, pdf_path, tempo=None, title=None, artist=None, ir_path=None) -> dict`
  - `mcp_tools.open_in_guitar_pro_impl(file_path) -> dict`
  - MCP 도구 `import_tab_pdf`, `open_in_guitar_pro`. 둘 다 예외를 던지지 않고 `{"status": ...}` 반환

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_synthetic.py` 에 추가:

```python
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
```

`tests/test_tab_pdf.py` 에 추가:

```python
@needs_pdf
def test_import_tool_end_to_end(tmp_path):
    import mcp_tools
    from controllers import GuitarProController

    controller = GuitarProController()
    result = mcp_tools.import_tab_pdf_impl(
        controller, str(PDF), tempo=80, artist="황가람",
        ir_path=str(tmp_path / "ir.json"))
    assert result["status"] == "success"
    assert result["data"]["measures"] == 58
    assert result["data"]["notes"] == 1502
    assert result["data"]["warnings"] == []

    out = tmp_path / "out.gp5"
    controller.save_file(str(out))
    assert gp.parse(str(out), encoding="cp949").title == "나는반딧불"
```

- [ ] **Step 2: 실패 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/ -v
```

기대: FAIL — `module 'mcp_tools' has no attribute 'default_output_path'`

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


def default_output_path(pdf_path: str) -> str:
    """산출 경로를 정하고 폴더를 만든다.

    `<root>/pdf/x.pdf` → `<root>/gp/x.gp5`. 그 외에는 PDF 옆에 `gp/` 를 만든다.
    """
    source_dir = os.path.dirname(os.path.abspath(pdf_path))
    base = (os.path.dirname(source_dir)
            if os.path.basename(source_dir) == "pdf" else source_dir)
    out_dir = os.path.join(base, DEFAULT_OUTPUT_DIR)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    return os.path.join(out_dir, f"{stem}.gp5")


def import_tab_pdf_impl(controller, pdf_path: str, tempo: int | None = None,
                        title: str | None = None, artist: str | None = None,
                        ir_path: str | None = None) -> dict:
    """PDF 타브를 파싱해 controller.current_song 에 적재한다.

    실패 시 controller 상태를 바꾸지 않는다 — 부수효과를 모두 성공 확정 후로 미룬다.
    """
    if not os.path.isfile(pdf_path):
        return {"status": "error", "message": f"PDF 파일이 없습니다: {pdf_path}"}

    try:
        ir = extract.extract_ir(pdf_path, tempo=tempo, title=title, artist=artist)
        song = build.build_song(ir)
        if ir_path:
            with open(ir_path, "w", encoding="utf-8") as handle:
                json.dump(ir, handle, ensure_ascii=False, indent=2)
        suggested = default_output_path(pdf_path)
    except extract.NotATabPdf as exc:
        return {"status": "error", "message": str(exc)}
    except OSError as exc:
        return {"status": "error", "message": f"파일 처리 실패: {exc}"}
    except Exception as exc:
        return {"status": "error", "message": f"변환 실패: {exc}"}

    controller.current_song = song          # 성공 확정 후에만 상태 변경
    return {
        "status": "success",
        "data": {
            "title": ir["title"],
            "suggested_output": suggested,
            "measures": len(ir["measures"]),
            "notes": sum(len(b["notes"])
                         for m in ir["measures"] for b in m["beats"]),
            "notation_kinds": sorted({m["kind"] for m in ir["measures"]}),
            "warnings": ir["warnings"],
        },
    }


def open_in_guitar_pro_impl(file_path: str) -> dict:
    """저장된 파일을 Guitar Pro 8 로 띄운다."""
    if not os.path.isfile(file_path):
        return {"status": "error", "message": f"파일이 없습니다: {file_path}"}
    if shutil.which("open") is None:
        return {"status": "error", "message": "macOS 의 open 명령을 찾을 수 없습니다"}
    try:
        subprocess.run(["open", "-a", GUITAR_PRO_APP, file_path], check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        return {"status": "error", "message": f"{GUITAR_PRO_APP} 실행 실패: {exc}"}
    return {"status": "success",
            "message": f"{GUITAR_PRO_APP} 로 열었습니다: {file_path}"}
```

`setup_mcp_tools(mcp, controller)` 안, 다른 `@mcp.tool(...)` 들과 같은 위치에 추가:

```python
    @mcp.tool("import_tab_pdf")
    def import_tab_pdf(ctx: Context, pdf_path: str, tempo: int = None,
                       title: str = None, artist: str = None,
                       ir_path: str = None) -> Dict[str, Any]:
        """Parse a Finale-engraved guitar tab PDF into the current song."""
        return import_tab_pdf_impl(controller, pdf_path, tempo, title,
                                   artist, ir_path)

    @mcp.tool("open_in_guitar_pro")
    def open_in_guitar_pro(ctx: Context, file_path: str) -> Dict[str, Any]:
        """Open a saved Guitar Pro file in the Guitar Pro 8 desktop app."""
        return open_in_guitar_pro_impl(file_path)
```

- [ ] **Step 4: 통과 확인**

```bash
cd guitar-pro-mcp-main && uv run pytest tests/ -v
```

기대: 전부 PASS

- [ ] **Step 5: MCP 서버 도구 목록 + stdout 청결성 확인**

```bash
cd guitar-pro-mcp-main && printf '%s\n%s\n%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
 '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 | uv run -m src.run_mcp_server 2>/dev/null \
 | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    assert line.startswith('{'), f'stdout 오염: {line[:80]!r}'
    data = json.loads(line)
    if data.get('id') == 2:
        names = [t['name'] for t in data['result']['tools']]
        print('도구', len(names), '개')
        print('신규:', sorted(n for n in names
                              if n in ('import_tab_pdf', 'open_in_guitar_pro')))
"
```

기대: `도구 37 개`, `신규: ['import_tab_pdf', 'open_in_guitar_pro']`, stdout assert 통과

- [ ] **Step 6: 실제 변환 end-to-end**

```bash
cd guitar-pro-mcp-main && uv run python -c "
import sys; sys.path.insert(0, 'src')
from controllers import GuitarProController
import mcp_tools
controller = GuitarProController()
result = mcp_tools.import_tab_pdf_impl(
    controller, '../pdf/나는반딧불.pdf', tempo=80, artist='황가람',
    ir_path='../gp/나는반딧불.ir.json')
print('status :', result['status'])
data = result['data']
print('마디   :', data['measures'], '노트:', data['notes'])
print('표기법 :', data['notation_kinds'])
print('경고   :', len(data['warnings']))
out = data['suggested_output']
controller.save_file(out)
print('저장   :', out)
"
```

기대: `status: success`, 마디 58, 노트 1502, 경고 0, `gp/나는반딧불.gp5` 생성

- [ ] **Step 7: 커밋**

```bash
git add guitar-pro-mcp-main/src/mcp_tools.py guitar-pro-mcp-main/tests
git commit -m "feat: import_tab_pdf / open_in_guitar_pro MCP 도구 추가"
```

---

## 부록 A: 2way 리뷰 수정 목록

Claude 자체 리뷰 + Codex(`openai.gpt-5.6-sol`, Bedrock) 독립 리뷰의 교차 결과. 15건 전부 반영됐다.

| # | 심각도 | 발견 | 발견자 | 반영 |
|---|---|---|---|---|
| 1 | blocker | 음길이 기하 탐색이 시스템 y 범위를 무시 → 58마디 중 56개 불일치 | Codex(실행) | 2절, 알고리즘 교체 |
| 2 | blocker | `CHORD_CHAR_GAP` 을 origin 기준 7.0 으로 둬 `Cadd9` 가 분해 | 양쪽 | `x_end` 기준 5.0 (Task 3·5) |
| 3 | blocker | `GuitarProFileMixin` 부재 (실제는 `FileOperationsController`) | 양쪽 | 1절, Task 1 |
| 4 | blocker | `pytest` 미설치, `--cov=src` 용 `pytest-cov` 도 없음 | 양쪽 | Task 1 Step 1 |
| 5 | blocker | stdio 서버가 stdout 에 `print` → MCP 프로토콜 오염 | Codex | Task 1, Task 7 Step 5 |
| 6 | major | 코드명 탐색 y 대역이 위쪽 전 시스템·제목까지 포함 | 양쪽 | `CHORD_BAND_HEIGHT` (Task 5) |
| 7 | major | `SMUFL_RESTS` 죽은 상수 | 양쪽 | 제거. 타브 쉼표는 `unsupported_glyph` 경고 |
| 8 | major | `unknown_chord` 경고 도달 불가 | 양쪽 | `looks_like_chord` 로 분리 (Task 4) |
| 9 | major | 줄 스냅 실패 숫자를 조용히 버림 | Codex | `unsnapped_digit` 경고 |
| 10 | major | IR 의 `stroke` 가 `.gp5` 에서 소실 | Codex | `_apply_stroke` (Task 6) |
| 11 | major | PDF 메타 제목이 mojibake `"Ÿfl˘'‹.musx"` | Codex | 파일명 stem + 인자 override |
| 12 | major | 모든 테스트가 gitignore 된 PDF 의존 → CI 가 0줄 실행 | Codex | `tests/test_synthetic.py` |
| 13 | major | 테스트가 개수·비어있지않음만 검증 | Codex | 마디1 정확값·코드 5종·노트 1502 |
| 14 | major | import 도구가 비원자적, `makedirs` 가 try 밖 | Codex | 부수효과를 성공 후로 (Task 7) |
| 15 | minor | 슬라이드·미지 글리프가 경고 없이 소실 | 양쪽 | `unsupported_glyph` 경고 |

### A-1. 잘못 적었던 기대값

1차 계획의 "마디1 노트 8개 `(5,3) (4,0) (1,0) (3,0) (6,0) (3,1) (1,0) (3,1)`" 은 틀렸다.
beat 수와 노트 수를 혼동했고 `(4,0)` 은 오전사였다. 정답은 **8 beat / 10 노트** 이며 1절에 고정했다.

### A-2. 왜 `x_end` 가 필요한가

코드명 문자 간격 실측 (시스템1 코드 행, baseline y=134.3):

| 전이 | origin 간격 | 잉크끝 기준 간격 |
|---|---|---|
| `C` → `a` (Cadd9 내부) | 7.68 | **0.90** |
| `a` → `d` | 4.48 | 0.35 |
| `9` → `E` (코드명 사이) | 45.00 | **40.32** |

origin 기준은 내부(7.68)와 경계(45.00) 사이 임계값이 좁아 7.0 을 쓰면 `Cadd9` 가 쪼개진다.
잉크끝 기준은 내부 최대 1.41 / 경계 최소 40.32 로 30배 여유가 있다.

### A-3. 남은 미검증 사항

- `BeatStrokeDirection` enum 이름은 Task 6 Step 1 에서 실제 확인한다. Codex 가 `BeatEffect.stroke`
  존재까지는 확인했다.
- 합성 PDF 는 SMuFL 음악 폰트를 쓰지 않으므로 슬래시·스트로크 경로는 실제 PDF 테스트에서만
  검증된다. 합성 테스트는 기하·음길이·프렛·조립·왕복을 덮는다.
