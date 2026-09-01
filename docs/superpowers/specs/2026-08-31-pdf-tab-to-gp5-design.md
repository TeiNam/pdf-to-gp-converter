# PDF 기타 타브 악보 → Guitar Pro `.gp5` 변환 설계

- 작성일: 2026-08-31
- 상태: 승인됨 (구현 계획 대기)
- 기준 입력: `pdf/나는반딧불.pdf` (3페이지, Finale `.musx` → "Microsoft: Print To PDF")
- 입력 PDF는 `pdf/`, 산출 `.gp5` 는 `gp/` 에 둔다. 둘 다 폴더째 gitignore 한다
  (상용 악보와 그 파생물). 레포에는 커밋되지 않는다
- 개정: 변환 로직을 독립 CLI 대신 **guitar-pro-mcp 내부**에 넣도록 4·5·10절 반전

## 1. 문제와 범위

Finale로 조판된 기타 타브 PDF를 Guitar Pro가 읽는 파일로 변환한다. 변환은
guitar-pro-mcp 의 도구로 노출하고, 결과를 Guitar Pro 8에 바로 띄운다.

**범위 (승인된 A안)**: 타브(TAB) staff만 변환한다. 같은 PDF에 있는 5선 멜로디
staff와 한글 가사는 버린다. 기타 연주용으로는 타브가 전부이고, 오선 음높이
디코딩(조표·임시표 처리)은 작업량을 2배 이상으로 만들면서 목적에 기여하지 않는다.

**표기법 두 종류를 모두 다룬다** (3절 참조). 프렛 숫자 타브는 숫자를 그대로 쓰고,
슬래시 스트러밍 구간은 코드네임 → 보이싱 표로 변환해 연주 가능한 프렛을 채운다.
슬래시가 곡의 43% 이므로 버릴 수 없다.

**범위 밖**
- 스캔 이미지 PDF (OCR/OMR). 이 설계는 벡터 텍스트 PDF만 다룬다.
- 멜로디 staff, 가사, 반복 구조(도돌이·코다), 코드 다이어그램.
- `.gpx` 직접 출력 — 2절 참조.
- 실행 중인 Guitar Pro 8 문서에 직접 쓰기 — 2절 참조.

## 2. 확정된 두 가지 불가능

### `.gpx` 는 출력할 수 없다

`pyguitarpro` 는 GP3/GP4/GP5만 쓴다. 설치된 패키지 전체에 `gpx` 문자열이 0건이고
`guitarpro.write()` docstring의 버전 예시는 `(5, 1, 0)` 이다. guitar-pro-mcp 의
`save_guitar_pro` 도 이 함수에 위임하므로 동일한 한계를 갖는다.

따라서 산출물은 `.gp5` 이고, 사용자는 Guitar Pro 8에서 열어 `.gp` 로 저장한다.
사용자의 악보 폴더에 이미 `.gp5`/`.gp4`/`.gpx` 가 섞여 있어 실사용에 문제가 없다.

### 실행 중인 Guitar Pro 8 에 직접 꽂을 수 없다

Guitar Pro 8 은 스크립팅 API를 제공하지 않는다. 확인한 근거:

- `Info.plist` 에 `NSAppleScriptEnabled` 없음, `OSAScriptingDefinition` 없음
- 앱 번들 안에 `.sdef` (AppleScript 용어사전) 파일 없음
- `Contents/Plugins/` 는 Qt 런타임 플러그인(platforms, imageformats, styles,
  printsupport 등)뿐 — 확장 SDK가 아니다

남는 수단은 접근성 API를 통한 UI 자동화뿐이고, 파일 경로가 동작하는 상황에서
그것을 쓸 이유가 없다.

**대신 파일로 연결한다.** GP8 이 등록한 문서 타입은 gp, gp3, gp4, gp5, gpx, mid,
midi, mxl, ptb, txt, xml 이다. 검증 중 생성한 `.gp5` 는 macOS 가 이미
`com.arobas-music.guitarpro5.document` 로 인식했다. 따라서 저장 직후
`open -a "Guitar Pro 8" <path>` 로 GP8 에 즉시 띄울 수 있다.

## 3. 입력 PDF에서 확인된 사실

측정으로 확인한 것만 적는다. 추측은 9절 리스크로 분리했다.

| 항목 | 확인된 값 |
|---|---|
| PDF 성격 | 완전 벡터. 페이지당 이미지 0개 → OCR 불필요 |
| 페이지 크기 | 612 × 792 pt |
| 구조 | 타브 시스템 14개 (p1 4개, p2 5개, p3 5개). 각 시스템 = 5선 멜로디 + 6선 타브 |
| 마디 수 | **58** (12시스템 × 4마디 + 2시스템 × 5마디). 마디선 x를 세어 확인 |
| 시스템1 멜로디 5선 y | 145.4 / 150.5 / 155.6 / 160.8 / 165.8 (간격 ≈5.1) |
| 시스템1 타브 6선 y | 206.9 / 214.6 / 222.2 / 229.9 / 237.6 / 245.3 (간격 ≈7.7) |
| 타브 숫자 폰트 | `CIDFont+F2` 9.3pt. 전 3페이지 합 약 300개 |
| 음악 기호 폰트 | `CIDFont+F1` 20.5pt, SMuFL 코드포인트 |
| 마디선 | 두 staff를 관통하는 전체높이 세로선 (예: x=198.1, y 145.4..245.4, 길이 100.0) |
| 빔 | 가로선 길이 ≈45pt. 시스템1에 8개, 각각 4개 note x를 덮음 |
| 코드네임 | 텍스트로 추출 가능 (Cadd9, E7, Am, F, G) |
| 화음 | 같은 x에 숫자 복수 (예: x=109.0 에 `0`(y207.1), `3`(y214.8)) |

추출된 SMuFL 글리프 (3페이지 전체):

| 코드포인트 | 이름 | 개수 |
|---|---|---|
| `0xE0A4` | noteheadBlack (멜로디 staff) | 367 |
| `0xE100` | **슬래시 노트헤드** — SMuFL E100–E10F = Slash noteheads | 241 |
| `0xE4A1` | 악센트 (articAccent) | 44 |
| `0xE1E7` | augmentationDot | 39 |
| `0xE4E6` | rest8th | 32 |
| `0xE050` | gClef | 14 |
| `0xE0A3` | noteheadHalf | 11 |
| `0xE4E4` / `0xE4E7` | restHalf / rest16th | 9 / 9 |
| `0xE0A9` | 노트헤드 계열 (저빈도) | 8 |
| `0xE241` / `0xE240` | flag8thDown / flag8thUp | 7 / 5 |
| `0xE610` / `0xE612` | **다운스트로크 / 업스트로크** (`∏` / `V`) | 6 / 3 |
| `0xE047` / `0xE048` | 시스템 브래킷 계열 | 1 / 2 |
| `0xE4E3` / `0xE4E5` | restWhole / restQuarter | 4 / 2 |
| `0xE084` | timeSig4 | 2 |
| `0xE262` | accidentalSharp | 1 |

시스템1 마디1은 육안·기하 양쪽으로 교차 검증했다: 빔 1줄 × 4음 그룹 2개 =
4/4의 8분음표 8개, `(string, fret)` = `(5,3) (4,0) (1,0) (3,0) (6,0) (3,1) (1,0) (3,1)`.

### 이 악보는 표기법이 두 종류다

`0xE100` 을 크롭 렌더해 규명한 결과, 이 곡의 타브는 두 방식이 섞여 있다.

| 표기 | 시스템 | 내용 |
|---|---|---|
| **프렛 숫자 타브** | p1 sys1–4, p2 sys1–2, p3 sys4–5 (8개) | 프렛 숫자 294개. 핑거스타일 |
| **슬래시 스트러밍** | p2 sys3–5, p3 sys1–3 (6개) | 슬래시 노트헤드 232개. 프렛 숫자가 **없고** 코드네임으로만 음이 정해진다. `∏`/`V` 로 다운/업 스트로크, `>` 로 악센트 표시 |

p3 sys4 는 프렛 36개 + 슬래시 9개로 전환 구간이다.

슬래시 구간은 곡의 약 43% 다. 조용히 버리면 안 된다.

### 코드네임은 5개뿐

`Cadd9` ×20, `E7` ×14, `Am` ×14, `F` ×14, `G` ×14. 그 외 `H`(해머온) ×3,
`S.D`·`.P` 같은 테크닉 표기가 소수 있다.

5개짜리 보이싱 표만 있으면 슬래시 구간을 실제 연주 가능한 프렛으로 변환할 수 있다.

## 4. 검증된 출력 경로와 API 함정

`pyguitarpro` 직접 호출로 end-to-end 증명을 마쳤다. 위 마디1 실측값으로 `.gp5` 를
쓰고 다시 파싱해 노트·튜닝·박자·한글 제목이 모두 일치했다 (1511 bytes).

증명 과정에서 확정된 API 사실:

- `Duration.value` 상수: `whole=1, half=2, quarter=4, eighth=8, sixteenth=16`.
  빔 개수에서 직접 매핑된다.
- `Note.type` 기본값이 `NoteType.rest` 이므로 실음은 `NoteType.normal` 을 **명시**해야 한다.
- `Beat.status` 기본값이 `BeatStatus.empty` 이므로 `BeatStatus.normal` 을 **명시**해야 한다.
- 생성자는 부모를 요구한다: `Track(song)`, `Measure(track, header)`, `Voice(measure)`,
  `Beat(voice)`, `Note(beat)`, `GuitarString(number, value)`.
- `Song()` 은 `tracks` / `measureHeaders` 에 기본 항목을 채워 넣으므로 직접 구성 시 먼저 비운다.
- GP5 는 8비트 charset이다. 한글은 `encoding="cp949"` 가 필요하다. 기본 `cp1252` 는
  쓰기에서 `UnicodeEncodeError` 로 실패하고, 읽기에서는 조용히 mojibake가 된다.

## 5. guitar-pro-mcp 수정

정본은 `mcp/` (프로젝트 내 vendored). 원격 저장소가 없는 zip 배포본이라
직접 수정한다.

### 기존 버그 수정 4곳

| 파일 | 문제 | 수정 |
|---|---|---|
| `pyproject.toml` | `mcp>=0.2.0` — mcp 2.x 에서 `FastMCP` 가 `MCPServer` 로 개명돼 import 실패 | `mcp>=0.2.0,<2` + `pymupdf` 추가 |
| `src/controllers/guitar_pro/base_controller.py` | `repo_root/PyGuitarPro` 소스 체크아웃이 없으면 `ImportError` | 가드 제거 (`pyguitarpro` 가 이미 의존성) |
| `file_operations.py::save_file` | `write(song, path)` — encoding 미지정 → **한글 제목 크래시** | `encoding="cp949"` |
| `file_operations.py::load_file` | `parse(path)` — encoding 미지정 → 한글 제목 mojibake | `encoding="cp949"` |

앞의 2개는 서버 기동 자체를 막던 것으로 이미 적용했다. 뒤의 2개는 사용자의 악보
폴더가 전부 한글 제목이라 실사용에서 바로 걸린다.

### `json_export.py` 는 손대지 않는다

이 파일은 양방향 모두 깨져 있다 (`song_to_json` 은 존재하지 않는 `song.author` 참조와
`gp` 미import, `json_to_song` 은 `Track()` 무인자 호출·`GuitarString()` 무인자 호출·
존재하지 않는 `Duration.isRest` 설정). 고칠 곳이 6군데다.

그러나 파서가 서버 안에서 `Song` 객체를 직접 만들어 `current_song` 에 넣으므로 JSON
계층을 경유할 이유가 없다. 고치지 않고 우회한다. `export_to_json` / `import_from_json`
도구는 깨진 상태로 남으며, 이 설계는 그것을 쓰지 않는다.

### 추가 모듈 2개

Guitar Pro 개념과 PDF 기하를 섞지 않는다.

**`src/utils/tab_pdf/extract.py` — PDF → IR**

PyMuPDF 로 텍스트 span과 도형을 읽는다. Guitar Pro 를 전혀 모르고 PDF 기하만 다룬다.

1. **staff 클러스터링** — 긴 수평선(길이 > 50pt)을 y로 모아 간격이 좁은 5줄
   묶음(멜로디)과 넓은 6줄 묶음(타브)으로 분류하고, 인접 쌍을 한 시스템으로 묶는다.
2. **마디 분할** — 두 staff를 관통하는 전체높이 세로선의 x로 시스템을 자른다.
3. **시스템 표기법 판정** — 타브 대역의 프렛 숫자 개수와 슬래시 노트헤드(`0xE100`
   대역) 개수를 비교해 `fret` / `slash` 로 분류한다. 둘 다 있으면 전환 구간이므로
   beat 단위로 각각 처리한다 (p3 sys4 가 이 경우다).
4. **프렛 노트 추출** (`fret` beat) — `CIDFont+F2` 9.3pt span에서 숫자를 읽고, y를
   6개 줄 앵커에 최근접 매칭해 줄 번호(1=고음 E … 6=저음 E)를, 텍스트를 프렛으로 삼는다.
5. **슬래시 beat 추출** (`slash` beat) — 슬래시 노트헤드의 x를 beat 위치로 쓰고,
   프렛은 비운 채 그 x 이전의 가장 가까운 코드네임을 `chord` 로 붙인다.
   `0xE610`/`0xE612` 로 다운/업 스트로크를 기록한다.
6. **beat 클러스터링** — 노트를 x로 ±2pt 묶는다. 한 묶음이 화음 1개(beat)다.
7. **음길이 디코딩** — 6절 규칙. 프렛·슬래시 공통이다 (둘 다 기둥·빔을 쓴다).
8. **검산** — 마디별 길이 합을 박자와 비교한다.

**`src/utils/tab_pdf/chords.py` — 코드네임 → 보이싱**

5개 코드의 프렛 보이싱 표. `(string, fret)` 리스트를 돌려준다. 이 곡에 필요한 것만
넣고, 표에 없는 코드는 경고로 남긴다 — 추측으로 채우지 않는다.

```python
VOICINGS = {
    "Cadd9": [(5, 3), (4, 2), (3, 0), (2, 3), (1, 3)],
    "E7":    [(6, 0), (5, 2), (4, 0), (3, 1), (2, 0), (1, 0)],
    "Am":    [(5, 0), (4, 2), (3, 2), (2, 1), (1, 0)],
    "F":     [(4, 3), (3, 2), (2, 1), (1, 1)],
    "G":     [(6, 3), (5, 2), (4, 0), (3, 0), (2, 0), (1, 3)],
}
```

**`src/utils/tab_pdf/build.py` — IR → `Song`**

IR을 받아 `pyguitarpro` 객체를 만든다. PDF를 전혀 모른다. 4절에서 확정한 API
제약(`NoteType.normal`, `BeatStatus.normal`, 부모 인자)을 여기 한 곳에 가둔다.
`slash` beat 는 `chords.py` 의 보이싱으로 노트를 채운다.

### 추가 도구 2개

| 도구 | 동작 |
|---|---|
| `import_tab_pdf(pdf_path, tempo=None)` | `extract` → `build` → `controller.current_song` 에 적재. 요약(마디 수, 노트 수, 경고 목록) 반환. **곡 전체가 tool call 1회** |
| `open_in_guitar_pro(file_path)` | `open -a "Guitar Pro 8" <path>`. 경로 존재 검증 후 실행 |

기존 도구 중 `get_track_tab`, `get_song_statistics` 로 결과를 원본 PDF와 대조하고,
`save_guitar_pro` 로 `.gp5` 를 쓴다. 노트 300개를 `add_gp_note` 로 개별 호출하는 문제는
파싱이 서버 안에서 한 번에 끝나므로 발생하지 않는다.

### IR 스키마

`import_tab_pdf` 는 IR을 `current_song` 으로만 변환하지만, 디버그를 위해 IR을 파일로
남길 수 있어야 한다. 리듬 디코딩이 틀렸을 때 잡을 곳이 여기뿐이다.

```json
{
  "title": "나는 반딧불",
  "artist": "황가람",
  "tempo": 80,
  "tuning": [64, 59, 55, 50, 45, 40],
  "measures": [
    {
      "index": 0,
      "time_sig": [4, 4],
      "kind": "fret",
      "beats": [
        {"x": 79.2, "duration": 8, "dotted": false, "rest": false,
         "notes": [{"string": 5, "fret": 3}]},
        {"x": 94.0, "duration": 8, "dotted": false, "rest": false,
         "chord": "Cadd9", "stroke": "down",
         "notes": [{"string": 5, "fret": 3}, {"string": 4, "fret": 2}]}
      ]
    }
  ],
  "warnings": [
    {"measure": 7, "kind": "duration_mismatch", "detail": "합 3.5 / 목표 4.0"},
    {"measure": 31, "kind": "unknown_chord", "detail": "Bm7 — VOICINGS 에 없음"}
  ]
}
```

## 6. 음길이 디코딩 규칙

이 설계의 유일한 실질 난이도이고 버그가 날 곳이다.

- beat x에 세로선(기둥)이 있는지 본다. 기둥 끝 y에 닿는 수평 빔의 **개수**로
  결정한다: 1줄 = 8분(`value=8`), 2줄 = 16분(16), 3줄 = 32분(32).
- 기둥이 있고 빔이 없으면 `flag8thUp`/`flag8thDown` 글리프 유무를 본다.
  있으면 8분, 없으면 4분(4).
- 기둥이 없으면 노트헤드 글리프로 구분한다: `noteheadHalf` → 2, `noteheadWhole` → 1.
- `augmentationDot` 글리프가 beat x 근처에 있으면 `dotted = true`.
- 쉼표 글리프(`restWhole`/`restHalf`/`restQuarter`/`rest8th`/`rest16th`)는
  노트 없는 rest beat로 넣는다.

**검산이 규칙의 일부다.** 길이는 **4분음표 단위**로 계산한다 — beat 하나는
`4 / duration × (1.5 if dotted else 1)`, 마디 목표값은 `4 × numerator / denominator`
(4/4 → 4.0). 8분음표 8개는 `8 × 4/8 = 4.0` 으로 맞는다. 어긋나면 그 마디를
`warnings` 에 넣고 **계속 진행**한다. 58마디 중 한 마디가 틀렸다고 나머지를
버릴 이유가 없다.

## 7. 에러 처리

경고로 수집하고 부분 결과를 살리는 것과, 즉시 실패하는 것을 구분한다. MCP 도구는
예외를 던지지 않고 `{"status": "error", "message": ...}` 를 반환하는 기존 관례를 따른다.

**즉시 실패** (입력이 이 도구의 대상이 아님)
- PDF에 텍스트 span이 없다 (스캔 이미지) → 이 도구로는 불가하다고 명시하고 종료.
- 6줄 타브 staff를 하나도 못 찾았다 → 타브 악보가 아니거나 클러스터링 실패.
- `pdf_path` 가 없거나 PDF가 아니다.

**경고 수집 후 진행**
- 마디 길이 합 불일치.
- 미지 글리프 발견 → 코드포인트와 좌표를 기록.
- 줄 앵커 최근접 매칭의 y 오차가 임계값 초과 → 해당 노트를 기록.
- 슬래시 beat 의 코드가 `VOICINGS` 에 없다 → 노트를 비우고 기록. 추측하지 않는다.

어떤 경우에도 조용히 넘기지 않는다. 경고는 IR의 `warnings` 와 `import_tab_pdf` 의
반환값 양쪽에 남는다.

## 8. 테스트

`test_tab_pdf.py` 하나. assert 기반, 프레임워크 없음. 실제 `pdf/나는반딧불.pdf` 를
입력으로 쓴다. MCP 서버를 띄우지 않고 `extract` / `build` 를 직접 호출한다.

**전제**: `pdf/` 는 gitignore 되므로 입력 PDF가 레포에 없다. 파일이 없으면 테스트는
실패가 아니라 skip 하고 그 사실을 출력한다 — 없는 파일 때문에 붉은 실패가 뜨면
안 된다.

1. 타브 시스템 14개, 마디 58개를 찾는다.
2. 마디1의 beat가 실측값과 정확히 일치한다 —
   `(5,3) (4,0) (1,0) (3,0) (6,0) (3,1) (1,0) (3,1)`, 전부 `duration=8`.
3. 모든 마디의 길이 합이 박자와 맞는다 (`warnings` 에 `duration_mismatch` 없음).
   슬래시 구간 6개 시스템이 코드 보이싱으로 채워져 빈 마디가 없다.
4. `build` 결과를 `.gp5` 로 쓰고 다시 파싱해 노트 수·튜닝·한글 제목이 IR과 일치한다
   (왕복 검증, `cp949`).

## 9. 리스크

| 리스크 | 대응 |
|---|---|
| ~~미상 글리프 `0xE100`~~ | **해소됨.** 슬래시 노트헤드로 규명. 표기법이 두 종류라는 사실이 드러나 1·3·5절에 반영 |
| 코드 보이싱 표가 원 편곡과 다를 수 있다 | 5개 코드뿐이므로 GP8에서 눈으로 확인 가능. 표는 한 곳(`chords.py`)에 모아 수정이 쉽게 한다 |
| 슬라이드 사선 (시스템1 마디3에서 육안 확인) | 1차 구현에서는 음정·리듬만 살리고 슬라이드는 `warnings` 에 기록. 효과 반영은 후속 |
| 타이·잇단음표(triplet) 미확인 | 검산이 잡는다. 합이 안 맞는 마디로 드러나면 규칙 추가 |
| 다른 PDF는 조판이 다를 수 있다 | 이 설계는 `pdf/나는반딧불.pdf` 기준. 악보 폴더의 나머지 8개 PDF는 같은 Finale 출력이면 그대로 동작할 가능성이 높고, 아니면 그때 대응 |
| vendored MCP 를 직접 수정 — upstream 변경 병합 불가 | 원격이 없는 zip 배포본이므로 수용한다. 수정 4곳은 이 문서 5절에 기록 |

## 10. 사용자 최종 절차

MCP 도구 호출 3~4회.

```
import_tab_pdf("pdf/나는반딧불.pdf")   → 58마디, 497beat, 1560노트, 경고 26(정보성)
                                       suggested_output = "gp/나는반딧불.gp5"
get_track_tab(0)                       → 텍스트 타브로 원본 PDF와 대조
save_guitar_pro("gp/나는반딧불.gp5")
open_in_guitar_pro("gp/나는반딧불.gp5") → GP8 에 즉시 표시
```

경고가 있으면 해당 마디를 IR JSON에서 확인한다. GP8 에서 대조 후 `.gp` 로 저장한다.
