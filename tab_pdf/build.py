"""IR → pyguitarpro Song. PDF 를 전혀 모른다."""

import guitarpro as gp
from guitarpro.models import (
    Beat, BeatStatus, BeatStrokeDirection, Chord, Duration, GuitarString,
    Measure, MeasureHeader, Note, NoteType, SlideType, Song, TimeSignature,
    Track, Voice,
)

from . import chords

# GP5 는 8비트 charset — 한글 보존에 필요
GP5_ENCODING = "cp949"
GP5_VERSION = (5, 1, 0)
# GP5 는 마디당 voice 슬롯 2개를 기대한다
GP5_VOICE_SLOTS = 2
DEFAULT_VELOCITY = 95
NYLON_GUITAR_MIDI_PROGRAM = 24
# 스트로크 속도. 0 은 "스트로크 없음" 이라 1 이상을 준다
STROKE_VALUE = 1
GUITAR_STRINGS = 6
# Chord.strings 에서 안 쓰는 줄을 나타내는 값
UNUSED_STRING = -1

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


def _apply_techniques(beat: Beat, techniques: list[dict]) -> None:
    """연주법을 해당 줄의 노트에 붙인다. GP 는 해머온/풀오프를 한 플래그로 다룬다."""
    for technique in techniques:
        for note in beat.notes:
            if note.string != technique["string"]:
                continue
            if technique["kind"] == "hammer":
                note.effect.hammer = True
            elif technique["kind"] == "slide":
                note.effect.slides = [SlideType.shiftSlideTo]


def _make_chord_diagram(name: str) -> Chord | None:
    """코드명에 다이어그램을 만든다. 보이싱을 모르면 None.

    GP5 는 다이어그램을 beat 에 붙인다. 트랙 단위 코드 목록(첫 페이지 상단에 모아
    보여주는 것)은 GP7/8 의 DiagramCollection 기능이라 GP5 포맷에는 자리가 없다.

    `Chord.strings` 는 인덱스 0 = 1번줄(고음 E) … 5 = 6번줄이고 미사용은 -1 이다.
    """
    voicing = chords.voicing_for(name)
    if not voicing:
        return None
    by_string = dict(voicing)
    strings = [by_string.get(i + 1, UNUSED_STRING) for i in range(GUITAR_STRINGS)]
    fretted = [fret for fret in strings if fret > 0]
    return Chord(
        length=GUITAR_STRINGS, name=name, firstFret=min(fretted, default=1),
        strings=strings, show=True, newFormat=True,
    )


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

    previous_chord = None
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
            _apply_techniques(beat, beat_ir.get("techniques", ()))
            chord_name = beat_ir.get("chord")
            if chord_name and chord_name != previous_chord:
                diagram = _make_chord_diagram(chord_name)
                if diagram is not None:
                    beat.effect.chord = diagram
                previous_chord = chord_name
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
