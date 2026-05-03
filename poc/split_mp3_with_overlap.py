#!/usr/bin/env python3
"""Split a single mp3 into overlapping parts for later diarization.

This PoC creates:
    - overlapping mp3 chunk files
    - a split manifest JSON that records each part's logical and extracted range

The intended use is:
    1. split a large lesson recording into safe upload-sized parts
    2. diarize each part independently
    3. merge the diarized transcripts later using the overlap

Example:
    uv run python poc/split_mp3_with_overlap.py \
      "data/2026年5月02日 12_30のレッスン.mp3"
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT_DIR / "poc" / "output"
DEFAULT_PART_COUNT = 3
DEFAULT_OVERLAP_SECONDS = 30.0
MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024
MAX_MODEL_DURATION_SECONDS = 1400


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split a single mp3 into overlapping parts for later diarization.",
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to an mp3 file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where split mp3 files and the manifest are written.",
    )
    parser.add_argument(
        "--part-count",
        type=int,
        default=DEFAULT_PART_COUNT,
        help=f"How many overlapping parts to create. Default: {DEFAULT_PART_COUNT}",
    )
    parser.add_argument(
        "--overlap-seconds",
        type=float,
        default=DEFAULT_OVERLAP_SECONDS,
        help=f"How many seconds neighboring parts should overlap. Default: {DEFAULT_OVERLAP_SECONDS}",
    )
    return parser.parse_args()


def resolve_target(path: Path) -> Path:
    target = path.expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(f"Input file not found: {target}")
    if target.suffix.lower() != ".mp3":
        raise ValueError(f"Only mp3 files are supported in this PoC: {target}")
    return target


def get_audio_duration_seconds(file_path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ffprobe is required to inspect audio duration.") from exc
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() or exc.stdout.strip()
        raise RuntimeError(f"Failed to inspect audio duration for {file_path.name}: {details}") from exc

    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"Could not parse audio duration for {file_path.name}.") from exc


def format_seconds(value: float) -> str:
    return f"{value:.3f}"


def build_manifest_path(source_path: Path, output_dir: Path) -> Path:
    return output_dir / f"{source_path.stem}.split_manifest.json"


def build_part_path(source_path: Path, output_dir: Path, index: int, part_count: int) -> Path:
    return output_dir / f"{source_path.stem}.part{index}of{part_count}{source_path.suffix.lower()}"


def build_ranges(duration_seconds: float, part_count: int, overlap_seconds: float) -> list[dict]:
    if part_count < 2:
        raise ValueError("part_count must be at least 2.")
    if overlap_seconds < 0:
        raise ValueError("overlap_seconds must be non-negative.")

    logical_width = duration_seconds / part_count
    parts: list[dict] = []

    for zero_based_index in range(part_count):
        index = zero_based_index + 1
        logical_start = logical_width * zero_based_index
        logical_end = duration_seconds if index == part_count else logical_width * index
        extract_start = max(0.0, logical_start - overlap_seconds)
        extract_end = min(duration_seconds, logical_end + overlap_seconds)
        left_overlap = logical_start - extract_start
        right_overlap = extract_end - logical_end

        parts.append(
            {
                "index": index,
                "label": f"part{index}of{part_count}",
                "logical_start_seconds": round(logical_start, 3),
                "logical_end_seconds": round(logical_end, 3),
                "extract_start_seconds": round(extract_start, 3),
                "extract_end_seconds": round(extract_end, 3),
                "left_overlap_seconds": round(left_overlap, 3),
                "right_overlap_seconds": round(right_overlap, 3),
                "requested_duration_seconds": round(max(0.0, extract_end - extract_start), 3),
            }
        )

    return parts


def cut_audio_part(source_path: Path, output_path: Path, start_seconds: float, duration_seconds: float) -> None:
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source_path),
                "-ss",
                format_seconds(start_seconds),
                "-t",
                format_seconds(duration_seconds),
                "-vn",
                "-acodec",
                "copy",
                "-y",
                str(output_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required to split mp3 files.") from exc
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() or exc.stdout.strip()
        raise RuntimeError(f"Failed to split {source_path.name}: {details}") from exc


def validate_part_constraints(output_path: Path, duration_seconds: float) -> None:
    file_size_bytes = output_path.stat().st_size
    if file_size_bytes > MAX_UPLOAD_SIZE_BYTES:
        raise RuntimeError(
            f"Split part exceeds the current Audio API upload limit of 25 MiB: {output_path.name} "
            f"({file_size_bytes / (1024 * 1024):.2f} MiB)"
        )
    if duration_seconds > MAX_MODEL_DURATION_SECONDS:
        raise RuntimeError(
            f"Split part exceeds the current diarization model duration limit of 1400s: "
            f"{output_path.name} ({duration_seconds:.3f}s)"
        )


def build_manifest(
    source_path: Path,
    output_dir: Path,
    duration_seconds: float,
    source_file_size_bytes: int,
    part_count: int,
    overlap_seconds: float,
    parts: list[dict],
) -> dict:
    return {
        "source_file": str(source_path),
        "source_file_name": source_path.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(duration_seconds, 3),
        "source_file_size_bytes": source_file_size_bytes,
        "part_count": part_count,
        "overlap_seconds": round(overlap_seconds, 3),
        "max_upload_size_bytes": MAX_UPLOAD_SIZE_BYTES,
        "max_model_duration_seconds": MAX_MODEL_DURATION_SECONDS,
        "output_dir": str(output_dir),
        "parts": parts,
    }


def main() -> int:
    args = parse_args()

    source_path = resolve_target(args.input)
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    duration_seconds = get_audio_duration_seconds(source_path)
    source_file_size_bytes = source_path.stat().st_size

    print(f"Source file: {source_path}", flush=True)
    print(f"Duration: {duration_seconds:.3f}s", flush=True)
    print(f"File size: {source_file_size_bytes / (1024 * 1024):.2f} MiB", flush=True)
    print(
        f"Splitting into {args.part_count} part(s) with {args.overlap_seconds:.3f}s overlap.",
        flush=True,
    )

    parts = build_ranges(
        duration_seconds=duration_seconds,
        part_count=args.part_count,
        overlap_seconds=args.overlap_seconds,
    )

    for part in parts:
        output_path = build_part_path(
            source_path=source_path,
            output_dir=output_dir,
            index=part["index"],
            part_count=args.part_count,
        )
        cut_audio_part(
            source_path=source_path,
            output_path=output_path,
            start_seconds=part["extract_start_seconds"],
            duration_seconds=part["requested_duration_seconds"],
        )
        actual_duration_seconds = get_audio_duration_seconds(output_path)
        validate_part_constraints(output_path, actual_duration_seconds)
        part["path"] = str(output_path)
        part["file_size_bytes"] = output_path.stat().st_size
        part["duration_seconds"] = round(actual_duration_seconds, 3)
        print(
            f"{part['label']}: "
            f"logical={part['logical_start_seconds']:.3f}-{part['logical_end_seconds']:.3f}s, "
            f"extract={part['extract_start_seconds']:.3f}-{part['extract_end_seconds']:.3f}s, "
            f"size={part['file_size_bytes'] / (1024 * 1024):.2f} MiB, "
            f"duration={part['duration_seconds']:.3f}s",
            flush=True,
        )

    manifest = build_manifest(
        source_path=source_path,
        output_dir=output_dir,
        duration_seconds=duration_seconds,
        source_file_size_bytes=source_file_size_bytes,
        part_count=args.part_count,
        overlap_seconds=args.overlap_seconds,
        parts=parts,
    )
    manifest_path = build_manifest_path(source_path, output_dir)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved split manifest to: {manifest_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
