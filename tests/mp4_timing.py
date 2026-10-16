"""Read finalized MP4 track metadata to check duration and A/V end alignment."""

import argparse
import json
from pathlib import Path
import struct


def boxes(stream, start, end):
    position = start
    while position + 8 <= end:
        stream.seek(position)
        size, kind = struct.unpack(">I4s", stream.read(8))
        header = 8
        if size == 1:
            size, = struct.unpack(">Q", stream.read(8))
            header = 16
        elif size == 0:
            size = end-position
        if size < header or position+size > end:
            raise ValueError("Invalid MP4 box bounds")
        yield kind, position+header, position+size
        position += size


def payload(stream, children, kind):
    _, start, end = next(item for item in children if item[0] == kind)
    stream.seek(start)
    return stream.read(end-start)


def probe(path):
    tracks = {}
    with path.open("rb") as stream:
        _, moov_start, moov_end = next(item for item in boxes(stream, 0, path.stat().st_size) if item[0] == b"moov")
        for kind, start, end in list(boxes(stream, moov_start, moov_end)):
            if kind != b"trak":
                continue
            track = list(boxes(stream, start, end))
            _, media_start, media_end = next(item for item in track if item[0] == b"mdia")
            media = list(boxes(stream, media_start, media_end))
            handler = payload(stream, media, b"hdlr")[8:12].decode("ascii")
            timing = payload(stream, media, b"mdhd")
            if timing[0] == 1:
                timescale, duration = struct.unpack_from(">IQ", timing, 20)
            else:
                timescale, duration = struct.unpack_from(">II", timing, 12)
            details = {"seconds": duration/timescale}
            if handler == "vide":
                width, height = struct.unpack(">II", payload(stream, track, b"tkhd")[-8:])
                details.update(width=width >> 16, height=height >> 16)
            tracks[handler] = details
    return tracks


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("--expect-seconds", type=float)
    options = parser.parse_args()
    tracks = probe(options.video)
    assert "vide" in tracks and "soun" in tracks, tracks
    assert abs(tracks["vide"]["seconds"]-tracks["soun"]["seconds"]) < 0.5, tracks
    if options.expect_seconds:
        assert abs(tracks["vide"]["seconds"]-options.expect_seconds) < 2, tracks
    print("PASS MP4 track timing: " + json.dumps(tracks))
