"""실제 PDF(`pdf/나는반딧불.pdf`) 기반 테스트.

`pdf/` 는 gitignore 되므로 입력이 없는 clone 에서는 skip 된다.
입력 없이도 도는 테스트는 `test_synthetic.py` 에 있다.
"""

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
