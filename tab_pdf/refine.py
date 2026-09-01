"""1차 IR 을 AI 에게 보여주고 보정안을 받는다.

무엇을 보내는가 — 이미지가 아니라 **글리프 덤프**다. PDF 텍스트 레이어에 심볼의
정체(유니코드)와 정확한 좌표가 이미 다 들어 있어서, 페이지를 PNG 로 렌더해
읽히면 오히려 정보를 잃는다. 덕분에 이미지를 못 보는 로컬 소형 모델도 그대로
쓸 수 있고 토큰도 훨씬 싸다.

무엇을 맡기는가 — 코드가 **읽었지만 뜻을 모르는** 것만이다. 프렛·줄·리듬은 글리프
좌표에서 결정론적으로 나오므로 AI 에게 묻지 않는다. AI 는 연주법·가사 배치·
코드명·보이싱만 제안하고, 그 제안은 `corrections` 의 검증을 통과해야 IR 에 닿는다.
"""

import concurrent.futures
import dataclasses
import json
import re
from dataclasses import dataclass

from . import ai, chords, corrections

# 모델 출력을 그대로 터미널에 쓰면 ANSI/OSC 제어문자로 화면을 위조할 수 있다.
# 모델 입력은 PDF 에서 왔으므로 신뢰 경계 밖이다 — IR 에 넣기 전에 씻는다.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# 모델 메모가 길어지면 화면과 IR 을 덮는다
MAX_NOTE_LENGTH = 800

# 한 번에 보낼 마디 수. 시스템(악보 한 단)이 보통 4마디라 주석이 배치 안에서 닫힌다
BATCH_MEASURES = 4
# 배치는 서로 독립이라 겹쳐 보낸다. 실측 배치당 25초 × 15배치 = 6분이 순차인데
# 4개씩 겹치면 2분이 된다. 더 늘리면 서버 rate limit 에 걸린다.
MAX_CONCURRENCY = 4

SYSTEM_PROMPT = """\
당신은 기타 타브 악보 전사(transcription) 검수자다. PDF 에서 결정론적으로 추출한
1차 결과와, 그 과정에서 뜻을 몰라 남긴 글리프 목록을 받는다. 할 일은 놓친 표기를
찾아 보정안(JSON)으로 돌려주는 것이다.

## 좌표계
- string 1 = 가장 높은 E(1번줄) … string 6 = 가장 낮은 E(6번줄)
- fret 0 = 개방현
- `measure` 는 입력에 적힌 값을 그대로 쓴다 (0부터 시작한다)
- `beat` 는 그 마디 `beats` 배열의 0부터 시작하는 인덱스다

## 글리프 대역(band)
- `chord`  : 멜로디 5선 위 — 코드명이 있는 줄
- `melody` : 멜로디 5선 안 — **노래 선율**이다. 기타 파트가 아니다
- `between`: 멜로디 5선과 타브 6선 사이 — 가사와 H/P/S 연주법 표기가 섞여 있다
- `tab`    : 타브 6선 — **기타 파트**다

## 글리프 항목 읽는 법
평범한 문자는 `char` 로 온다. 음악 기호는 유니코드 사설 영역이라 글자로는 읽을 수
없어서 `codepoint`(예: "U+E4A1")·`symbol`(구획 분류)·`smufl_name`(표준 SMuFL 이름,
모르면 null)으로 온다. **`smufl_name` 이 있으면 그것을 근거로 판단하고**, null 이면
`codepoint` 로 SMuFL 표준을 떠올려 판단한다. 확신이 없으면 생략한다.

자주 나오는 것들:
- `articAccentBelow`/`articAccentAbove` (U+E4A0/E4A1) → `accent`
- `articMarcatoAbove`/`Below` → `heavy_accent`
- `articStaccatoAbove`/`Below`, `articStaccatissimo*` → `staccato`
- `noteheadXBlack` (U+E0A9) 이 `tab` 대역에 있으면 뮤트 노트 → `dead`
- `stringsHarmonic` → `harmonic`
- `augmentationDot`(점음표의 점)·`flag*`(꼬리)·`notehead*`·`rest*`·`gClef`·
  `timeSig*`·`accidental*` 은 음정·리듬 표기다 → **연주법이 아니므로 보정하지 않는다**
- `segno`/`coda` 는 곡 진행 지시다 → 4가지 보정 대상이 아니다

`band` 를 꼭 함께 본다 — `tab` 대역의 아티큘레이션은 기타 파트의 것이지만,
`melody` 대역의 것은 노래 선율에 붙은 것이라 기타 노트로 옮기면 안 된다.

`beat` 는 그 글리프에 x 좌표가 가장 가까운 beat 인덱스다. `tab` 대역 글리프에는
`string`(1~6, 어느 선에도 안 붙으면 null)이 함께 온다 — 줄이 뜻인 기호(X 음표머리,
하모닉스)는 이 값을 쓴다.

## 한국 타브 악보 표기 관례
`H`=해머온, `P`=풀오프, `S`=슬라이드, 뒤에 붙는 `.D`/`.U`=방향(내림/올림),
`x`=뮤트 노트, `~`=비브라토, `P.M.`=팜뮤트, `<>`=하모닉스, `T`=태핑,
`b`=벤드, `let ring`/`L.R.`=여음.
표기는 보통 대상 음의 **오른쪽**에 놓이고 효과는 **왼쪽(앞) 음**이 갖는다.

## 낼 수 있는 보정 (이 4가지뿐)
1. 연주법 — `{"op":"technique","measure":M,"beat":B,"kind":K,"string":S}`
   `kind` 는 다음 중 하나여야 한다:
   hammer(해머온·풀오프 공용), slide, slide_out_down, slide_out_up,
   slide_in_below, slide_in_above, bend, vibrato, harmonic, palm_mute,
   let_ring, staccato, dead, accent, heavy_accent, ghost

   **`string` 은 특정 줄 하나에만 걸리는 표기일 때만 쓴다.** 그 beat 의 `notes` 에
   실제로 있는 줄이어야 한다. H/P/S·벤드·하모닉스처럼 타브의 한 줄에 붙어 있는
   표기가 그렇다 — 표기 x 좌표 왼쪽의 가장 가까운 음이 대상이다.

   **악보 위·아래에 그려지는 아티큘레이션은 `string` 을 생략한다.** 악센트
   (`articAccent*`)·마르카토·스타카토는 한 줄이 아니라 **그 박 전체**에 붙는
   표기다. 생략하면 그 beat 의 모든 음에 적용된다. 줄을 짐작해서 적지 말 것 —
   스트럼 화음 6음 중 하나만 악센트가 되면 원본과 다른 악보가 된다.
2. 가사 재배치 — `{"op":"lyric","measure":M,"beat":B,"text":"음절"}`
   그 마디의 가사를 옮길 때는 **그 마디의 모든 음절을 빠짐없이 다시 나열**한다.
   글자를 더하거나 빼거나 바꾸면 그 마디의 가사 보정은 전부 폐기된다.
3. 코드명 — `{"op":"chord","measure":M,"beat":B,"name":"Am7"}`
4. 보이싱 — `{"op":"voicing","name":"Am7","frets":[0,1,0,2,0,-1]}`
   `frets` 는 **1번줄부터 6번줄 순서**의 6개 배열, `-1` 은 안 쓰는 줄이다.

## 하지 말 것
- 음(프렛·줄)을 추가·삭제·변경하지 말 것. 리듬(duration)도 건드리지 말 것.
- `melody` 대역의 음표를 기타 노트로 옮기지 말 것 — 그건 노래 선율이다.
- 가사를 새로 쓰거나 다듬지 말 것. 입력에 있는 음절만 옮길 수 있다.
- 글리프에 근거가 없는 추측은 내지 말 것. 확실하지 않으면 그 항목을 생략한다.

## 출력
설명·머리말·코드펜스 없이 JSON 객체 하나만 낸다:
{"corrections": [...], "notes": "판단 근거나 의심스러운 점을 한국어 한두 문장으로"}
보정할 것이 없으면 `{"corrections": [], "notes": "..."}` 를 낸다.
"""


def _batch_payload(ir: dict, measures: list[dict]) -> str:
    """한 배치를 모델이 읽을 JSON 으로 만든다."""
    indexes = {measure["index"] for measure in measures}
    payload = {
        "tuning_midi_high_to_low": ir["tuning"],
        "measures": [
            {
                "measure": measure["index"],
                "time_sig": measure["time_sig"],
                "notation": measure["kind"],
                "beats": [
                    {
                        "beat": position,
                        "duration": beat["duration"],
                        "dotted": beat["dotted"],
                        "notes": beat["notes"],
                        "chord": beat.get("chord"),
                        "stroke": beat.get("stroke"),
                        "techniques": beat.get("techniques", []),
                        "lyric": beat.get("lyric"),
                    }
                    for position, beat in enumerate(measure["beats"])
                ],
                "unread_glyphs": measure.get("glyphs", []),
            }
            for measure in measures
        ],
        "extractor_warnings": [
            warning for warning in ir.get("warnings", [])
            if warning.get("measure") in indexes
        ],
        "known_voicings": sorted(_known_voicing_names(ir)),
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


def _known_voicing_names(ir: dict) -> set[str]:
    return set(chords.VOICINGS) | set(ir.get("ai_voicings", {}))


def _clean_note(text: str) -> str:
    """모델 메모를 IR·화면에 넣기 전에 씻는다."""
    return _CONTROL_CHARS.sub("", text).strip()[:MAX_NOTE_LENGTH]


def _batches(measures: list[dict], size: int) -> list[list[dict]]:
    return [measures[start:start + size] for start in range(0, len(measures), size)]


@dataclass(frozen=True)
class _BatchResult:
    """한 배치의 결과. 실패도 값으로 들고 온다 — 예외로 전체를 멈추지 않는다."""

    span: tuple[int, int]
    proposals: tuple[dict, ...] = ()
    strays: tuple[dict, ...] = ()
    note: str | None = None
    failure: str | None = None


def _scope(proposals: list, measures: list[dict]) -> tuple[list, list]:
    """이 배치의 마디를 가리키는 보정만 남긴다.

    배치는 자기가 본 마디만 고칠 수 있다. 범위를 안 막으면 한 배치의 환각이나
    PDF 텍스트에 심어둔 프롬프트 인젝션이 보지도 않은 마디를 고칠 수 있다.
    `voicing` 은 마디에 매이지 않으므로 통과시킨다.
    """
    allowed = {measure["index"] for measure in measures}
    kept, strays = [], []
    for proposal in proposals:
        if not isinstance(proposal, dict) or proposal.get("op") == "voicing":
            kept.append(proposal)       # 형식은 corrections 가 판정한다
            continue
        target = proposal.get("measure")
        # 정수가 아니면 set 조회에서 TypeError 로 refinement 전체가 죽는다.
        # 타입 판정은 corrections 가 제 메시지로 하도록 넘긴다.
        if not isinstance(target, int) or isinstance(target, bool):
            kept.append(proposal)
        elif target in allowed:
            kept.append(proposal)
        else:
            strays.append(proposal)
    return kept, strays


def _run_batch(ask, config: ai.Config, ir: dict,
               measures: list[dict]) -> _BatchResult:
    span = (measures[0]["index"], measures[-1]["index"])
    try:
        answer = ask(config, SYSTEM_PROMPT, _batch_payload(ir, measures))
    except ai.AiUnavailable as exc:
        return _BatchResult(span, failure=str(exc))
    proposals = answer.get("corrections")
    if not isinstance(proposals, list):
        return _BatchResult(span, failure=f"corrections 가 배열이 아니다: "
                                          f"{type(proposals).__name__}")
    kept, strays = _scope(proposals, measures)
    note = answer.get("notes")
    cleaned = _clean_note(note) if isinstance(note, str) else ""
    return _BatchResult(span, proposals=tuple(kept), strays=tuple(strays),
                        note=cleaned or None)


def _gather(ask, config: ai.Config, ir: dict, batches: list[list[dict]],
            workers: int, on_batch) -> list[_BatchResult]:
    """배치를 병렬로 돌리고 **입력 순서대로** 결과를 돌려준다."""
    if not batches:
        return []
    done = 0
    results: list[_BatchResult | None] = [None] * len(batches)
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, min(workers, len(batches)))) as pool:
        futures = {pool.submit(_run_batch, ask, config, ir, measures): number
                   for number, measures in enumerate(batches)}
        for future in concurrent.futures.as_completed(futures):
            number = futures[future]
            results[number] = future.result()
            done += 1
            if on_batch is not None:
                on_batch(done, len(batches), *results[number].span)
    return [result for result in results if result is not None]


def refine_ir(ir: dict, *, config: ai.Config | None = None,
              ask=ai.ask_json, batch_size: int = BATCH_MEASURES,
              limit: int | None = None, workers: int = MAX_CONCURRENCY,
              on_batch=None) -> tuple[dict, corrections.Outcome]:
    """AI 보정을 적용한 새 IR 과 적용 내역을 돌려준다. 원본 IR 은 그대로다.

    배치 하나가 실패해도 나머지는 계속한다 — 58마디를 다시 돌리는 비용이 크고,
    실패는 경고로 남아 사용자가 볼 수 있다.

    배치는 병렬로 보내지만 결과는 **배치 순서대로** 합친다. 도착 순서로 합치면
    같은 보정의 중복 판정이 실행마다 달라져 결과가 재현되지 않는다.

    `ask` 는 테스트에서 갈아끼운다. 기본값이 유일한 네트워크 접점이다.
    """
    if config is None:
        config = ai.load_config()

    batches = _batches(ir["measures"], batch_size)
    if limit is not None:
        batches = batches[:limit]

    results = _gather(ask, config, ir, batches, workers, on_batch)
    proposals = [proposal for batch in results for proposal in batch.proposals]
    failures = [{"measures": list(batch.span), "reason": batch.failure}
                for batch in results if batch.failure]
    notes = [f"m{batch.span[0]}-{batch.span[1]}: {batch.note}"
             for batch in results if batch.note]

    result, outcome = corrections.apply_corrections(ir, proposals)
    strays = tuple(
        {"correction": proposal,
         "reason": f"배치(마디 {batch.span[0]}..{batch.span[1]}) 밖의 마디 "
                   f"{proposal.get('measure')!r} 를 고치려 했다"}
        for batch in results for proposal in batch.strays)
    outcome = dataclasses.replace(outcome, rejected=outcome.rejected + strays)
    result["refinement"] = {
        "backend": config.label,
        "batches": len(batches),
        "proposed": len(proposals) + len(strays),
        "realized_beats": outcome.realized,
        "applied": list(outcome.applied),
        "rejected": list(outcome.rejected),
        "failed_batches": failures,
        "model_notes": notes,
    }
    _add_warnings(result, outcome, failures)
    return result, outcome


def _add_warnings(ir: dict, outcome: corrections.Outcome,
                  failures: list[dict]) -> None:
    """폐기된 보정과 실패한 배치를 경고로 남긴다 — 조용히 버리지 않는다."""
    warnings = ir.setdefault("warnings", [])
    for failure in failures:
        warnings.append({"measure": failure["measures"][0],
                         "kind": "ai_batch_failed",
                         "detail": failure["reason"]})
    for entry in outcome.rejected:
        correction = entry["correction"]
        warnings.append({
            "measure": correction.get("measure", 0),
            "kind": "ai_rejected",
            "detail": f"{correction.get('op', '?')} 보정을 버렸다: {entry['reason']}",
        })
