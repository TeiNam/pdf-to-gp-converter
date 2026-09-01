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
