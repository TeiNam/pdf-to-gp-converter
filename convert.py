#!/usr/bin/env python3
"""Finale 조판 기타 타브 PDF → Guitar Pro `.gp5` 변환 CLI.

    uv run convert.py pdf/나는반딧불.pdf
    uv run convert.py pdf/x.pdf -o gp/y.gp5 --tempo 90 --ir gp/x.ir.json --open

Guitar Pro 8 은 스크립팅 API 가 없다 (AppleScript·sdef·플러그인 SDK 모두 없음).
`--open` 은 파일을 앱에 넘겨 열기만 요청한다 — 이미 열려 있는 문서를 갱신하지는
못하므로, 재변환했으면 GP8 에서 파일을 닫고 다시 열어야 한다.
"""

import argparse
import collections
import json
import os
import shutil
import subprocess
import sys

from tab_pdf import build, extract

GUITAR_PRO_APP = "Guitar Pro 8"
# 산출물 기본 폴더 — 입력 pdf/ 와 대칭
DEFAULT_OUTPUT_DIR = "gp"
# IR 디버그 파일 확장자 — 입력을 덮어쓰지 못하게 강제한다
IR_SUFFIX = ".json"
# 결함성 경고 — 하나라도 있으면 변환 결과를 믿을 수 없다
DEFECT_KINDS = frozenset({
    "duration_mismatch", "empty_measure", "empty_beat",
    "unknown_chord", "unsnapped_digit", "time_signature",
})


def default_output_path(pdf_path: str) -> str:
    """산출 경로를 정하고 폴더를 만든다.

    `<root>/pdf/x.pdf` → `<root>/gp/x.gp5`. 그 외에는 PDF 옆에 `gp/` 를 만든다
    — 입력 폴더 밖으로 나가지 않는다.
    """
    source_dir = os.path.dirname(os.path.abspath(pdf_path))
    base = (os.path.dirname(source_dir)
            if os.path.basename(source_dir) == "pdf" else source_dir)
    out_dir = os.path.join(base, DEFAULT_OUTPUT_DIR)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    return os.path.join(out_dir, f"{stem}.gp5")


def open_in_guitar_pro(file_path: str) -> None:
    """저장된 파일을 Guitar Pro 8 로 열도록 요청한다."""
    if shutil.which("open") is None:
        raise RuntimeError("macOS 의 open 명령을 찾을 수 없습니다")
    subprocess.run(["open", "-a", GUITAR_PRO_APP, file_path], check=True)


def _validate(args) -> str | None:
    """사용자 입력을 검증한다. 문제가 있으면 메시지를 돌려준다."""
    if not os.path.isfile(args.pdf):
        return f"PDF 파일이 없습니다: {args.pdf}"
    if args.ir is None:
        return None
    if not args.ir.lower().endswith(IR_SUFFIX):
        return f"--ir 는 {IR_SUFFIX} 여야 합니다: {args.ir}"
    if os.path.abspath(args.ir) == os.path.abspath(args.pdf):
        # 원본 PDF 를 JSON 으로 덮어쓰면 복구할 수 없다
        return f"--ir 가 입력 PDF 와 같습니다: {args.ir}"
    return None


def _report(ir: dict, output: str) -> int:
    """변환 요약을 출력하고 결함성 경고 개수를 돌려준다."""
    beats = sum(len(m["beats"]) for m in ir["measures"])
    notes = sum(len(b["notes"]) for m in ir["measures"] for b in m["beats"])
    kinds = collections.Counter(m["kind"] for m in ir["measures"])
    print(f"제목   : {ir['title']}"
          + (f" / {ir['artist']}" if ir["artist"] else ""))
    print(f"마디   : {len(ir['measures'])}  (표기법 {dict(kinds)})")
    print(f"beat   : {beats}   노트: {notes}")
    print(f"저장   : {output}")

    defects = [w for w in ir["warnings"] if w["kind"] in DEFECT_KINDS]
    informational = [w for w in ir["warnings"] if w["kind"] not in DEFECT_KINDS]
    if informational:
        summary = collections.Counter(w["kind"] for w in informational)
        print(f"미반영 : {sum(summary.values())}건 {dict(summary)}"
              " — 음정·리듬은 옮겼지만 아티큘레이션·연주법은 반영하지 못했다")
    if defects:
        print(f"\n결함성 경고 {len(defects)}건 — 결과를 그대로 믿지 말 것:",
              file=sys.stderr)
        for warning in defects:
            print(f"  m{warning['measure']} [{warning['kind']}] {warning['detail']}",
                  file=sys.stderr)
    return len(defects)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Finale 조판 기타 타브 PDF 를 Guitar Pro .gp5 로 변환한다.",
        epilog="GP8 에서 열어 원본 PDF 와 대조한 뒤 .gp 로 저장하면 된다.",
    )
    parser.add_argument("pdf", help="입력 PDF 경로")
    parser.add_argument("-o", "--output",
                        help="산출 .gp5 경로 (기본: 입력과 대칭인 gp/<이름>.gp5)")
    parser.add_argument("--ir", help="중간표현을 JSON 으로 저장할 경로 (디버그용)")
    parser.add_argument("--tempo", type=int, help="곡 템포 (기본 80)")
    parser.add_argument("--title", help="곡 제목 (기본: PDF 파일명)")
    parser.add_argument("--artist", help="아티스트")
    parser.add_argument("--open", action="store_true",
                        dest="open_app", help=f"저장 후 {GUITAR_PRO_APP} 로 열기")
    args = parser.parse_args(argv)

    problem = _validate(args)
    if problem:
        print(f"오류: {problem}", file=sys.stderr)
        return 2

    try:
        ir = extract.extract_ir(args.pdf, tempo=args.tempo,
                                title=args.title, artist=args.artist)
    except extract.NotATabPdf as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2

    output = args.output or default_output_path(args.pdf)
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    build.write_gp5(build.build_song(ir), output)
    if args.ir:
        with open(args.ir, "w", encoding="utf-8") as handle:
            json.dump(ir, handle, ensure_ascii=False, indent=2)

    defects = _report(ir, output)
    if args.open_app:
        open_in_guitar_pro(output)
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main())
