# PDF 기타 타브 악보 → Guitar Pro `.gp5` 변환 설계

- 작성일: 2026-08-31
- 상태: 승인됨 (구현 계획 대기)
- 기준 입력: `나는반딧불.pdf` (3페이지, Finale `.musx` → "Microsoft: Print To PDF")
- 개정: 변환 로직을 독립 CLI 대신 **guitar-pro-mcp 내부**에 넣도록 4·5·10절 반전

## 1. 문제와 범위

Finale로 조판된 기타 타브 PDF를 Guitar Pro가 읽는 파일로 변환한다. 변환은
guitar-pro-mcp 의 도구로 노출하고, 결과를 Guitar Pro 8에 바로 띄운다.

**범위 (승인된 A안)**: 타브(TAB) staff만 변환한다. 같은 PDF에 있는 5선 멜로디
staff와 한글 가사는 버린다. 기타 연주용으로는 타브가 전부이고, 오선 음높이
디코딩(조표·임시표 처리)은 작업량을 2배 이상으로 만들면서 목적에 기여하지 않는다.

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
| 구조 | 3페이지 × 4시스템 = 12시스템. 각 시스템 = 5선 멜로디 + 6선 타브 |
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
| `0xE0A4` | noteheadBlack | 367 |
| `0xE100` | **미상** | 241 |
| `0xE4A1` | **미상** | 44 |
| `0xE1E7` | augmentationDot | 39 |
| `0xE4E6` | rest8th | 32 |
| `0xE050` | gClef | 14 |
| `0xE0A3` | noteheadHalf | 11 |
| `0xE4E4` / `0xE4E7` | restHalf / rest16th | 9 / 9 |
| `0xE0A9` | **미상** | 8 |
| `0xE241` / `0xE240` | flag8thDown / flag8thUp | 7 / 5 |
| `0xE610`, `0xE612`, `0xE047`, `0xE048` | **미상** | 6 / 3 / 1 / 2 |
| `0xE4E3` / `0xE4E5` | restWhole / restQuarter | 4 / 2 |
| `0xE084` | timeSig4 | 2 |
| `0xE262` | accidentalSharp | 1 |

시스템1 마디1은 육안·기하 양쪽으로 교차 검증했다: 빔 1줄 × 4음 그룹 2개 =
4/4의 8분음표 8개, `(string, fret)` = `(5,3) (4,0) (1,0) (3,0) (6,0) (3,1) (1,0) (3,1)`.

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

정본은 `guitar-pro-mcp-main/` (프로젝트 내 vendored). 원격 저장소가 없는 zip 배포본이라
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
3. **노트 추출** — 타브 대역의 `CIDFont+F2` 9.3pt span에서 숫자를 읽고, y를 6개 줄
   앵커에 최근접 매칭해 줄 번호(1=고음 E … 6=저음 E)를, 텍스트를 프렛으로 삼는다.
4. **beat 클러스터링** — 노트를 x로 ±2pt 묶는다. 한 묶음이 화음 1개(beat)다.
5. **음길이 디코딩** — 6절 규칙.
6. **검산** — 마디별 길이 합을 박자와 비교한다.

**`src/utils/tab_pdf/build.py` — IR → `Song`**

IR을 받아 `pyguitarpro` 객체를 만든다. PDF를 전혀 모른다. 4절에서 확정한 API
제약(`NoteType.normal`, `BeatStatus.normal`, 부모 인자)을 여기 한 곳에 가둔다.

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
      "beats": [
        {"x": 79.2, "duration": 8, "dotted": false, "rest": false,
         "notes": [{"string": 5, "fret": 3}]}
      ]
    }
  ],
  "warnings": [
    {"measure": 7, "kind": "duration_mismatch", "detail": "합 3.5 / 목표 4.0"}
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
`warnings` 에 넣고 **계속 진행**한다. 12시스템 중 한 마디가 틀렸다고 나머지를
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

어떤 경우에도 조용히 넘기지 않는다. 경고는 IR의 `warnings` 와 `import_tab_pdf` 의
반환값 양쪽에 남는다.

## 8. 테스트

`test_tab_pdf.py` 하나. assert 기반, 프레임워크 없음. 실제 `나는반딧불.pdf` 를 입력으로 쓴다.
MCP 서버를 띄우지 않고 `extract` / `build` 를 직접 호출한다.

1. 마디 수가 12개다.
2. 마디1의 beat가 실측값과 정확히 일치한다 —
   `(5,3) (4,0) (1,0) (3,0) (6,0) (3,1) (1,0) (3,1)`, 전부 `duration=8`.
3. 모든 마디의 길이 합이 박자와 맞는다 (`warnings` 에 `duration_mismatch` 없음).
4. `build` 결과를 `.gp5` 로 쓰고 다시 파싱해 노트 수·튜닝·한글 제목이 IR과 일치한다
   (왕복 검증, `cp949`).

## 9. 리스크

| 리스크 | 대응 |
|---|---|
| 미상 글리프 6종, 특히 `0xE100` 이 241개 — 무시할 수 없는 양 | 구현 1단계에서 해당 좌표를 크롭 렌더해 정체를 규명한다. 기둥 글리프로 추정되며, 그렇다면 6절의 세로선 탐지를 글리프 기반으로 바꾼다 |
| 슬라이드 사선 (시스템1 마디3에서 육안 확인) | 1차 구현에서는 음정·리듬만 살리고 슬라이드는 `warnings` 에 기록. 효과 반영은 후속 |
| 타이·잇단음표(triplet) 미확인 | 검산이 잡는다. 합이 안 맞는 마디로 드러나면 규칙 추가 |
| 다른 PDF는 조판이 다를 수 있다 | 이 설계는 `나는반딧불.pdf` 기준. 악보 폴더의 나머지 8개 PDF는 같은 Finale 출력이면 그대로 동작할 가능성이 높고, 아니면 그때 대응 |
| vendored MCP 를 직접 수정 — upstream 변경 병합 불가 | 원격이 없는 zip 배포본이므로 수용한다. 수정 4곳은 이 문서 5절에 기록 |

## 10. 사용자 최종 절차

MCP 도구 호출 3~4회.

```
import_tab_pdf("나는반딧불.pdf")      → 12마디, 노트 N개, 경고 M개
get_track_tab(0)                      → 텍스트 타브로 원본 PDF와 대조
save_guitar_pro("나는반딧불.gp5")
open_in_guitar_pro("나는반딧불.gp5")   → GP8 에 즉시 표시
```

경고가 있으면 해당 마디를 IR JSON에서 확인한다. GP8 에서 대조 후 `.gp` 로 저장한다.
