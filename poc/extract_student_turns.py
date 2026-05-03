#!/usr/bin/env python3
"""Extract student turns from a diarized transcript and speaker role mapping.

Example:
    uv run python poc/extract_student_turns.py \
      "poc/output/2026年5月02日 12_30のレッスン.part1of2.diarized.transcript.json" \
      "poc/output/2026年5月02日 12_30のレッスン.part1of2.speaker_roles.json"
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_MERGE_GAP_SECONDS = 1.5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract student turns from a diarized transcript JSON and speaker role mapping.",
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to a diarized transcript JSON file.",
    )
    parser.add_argument(
        "roles",
        type=Path,
        help="Path to a speaker roles JSON file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output JSON path. Defaults to <input>.student_turns.json",
    )
    parser.add_argument(
        "--merge-gap-seconds",
        type=float,
        default=DEFAULT_MERGE_GAP_SECONDS,
        help=f"Merge consecutive same-role segments when the gap is at most this many seconds. Default: {DEFAULT_MERGE_GAP_SECONDS}",
    )
    return parser.parse_args()


def resolve_target(path: Path) -> Path:
    target = path.expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(f"Input file not found: {target}")
    if target.suffix.lower() != ".json":
        raise ValueError(f"Only JSON input is supported in this PoC: {target}")
    return target


def default_output_path(input_path: Path) -> Path:
    suffix = ".diarized.transcript.json"
    if input_path.name.endswith(suffix):
        return input_path.with_name(input_path.name.removesuffix(suffix) + ".student_turns.json")
    return input_path.with_name(input_path.stem + ".student_turns.json")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_transcript(path: Path) -> dict:
    payload = load_json(path)
    chunks = payload.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise ValueError(f"Diarized transcript chunks were not found in: {path}")
    return payload


def load_speaker_roles(path: Path) -> tuple[dict[str, str], list[str], list[str]]:
    payload = load_json(path)

    speaker_roles = payload.get("speaker_roles")
    if not isinstance(speaker_roles, dict):
        inference = payload.get("inference")
        if isinstance(inference, dict):
            speaker_roles = inference.get("speaker_roles")

    if not isinstance(speaker_roles, dict) or not speaker_roles:
        raise ValueError(f"speaker_roles mapping was not found in: {path}")

    normalized_roles: dict[str, str] = {}
    for raw_speaker, role in speaker_roles.items():
        speaker = str(raw_speaker).strip()
        normalized_role = str(role).strip().lower()
        if not speaker or normalized_role not in {"student", "teacher"}:
            raise ValueError(f"Invalid speaker role mapping in {path}: {raw_speaker} -> {role}")
        normalized_roles[speaker] = normalized_role

    student_speakers = sorted([speaker for speaker, role in normalized_roles.items() if role == "student"])
    teacher_speakers = sorted([speaker for speaker, role in normalized_roles.items() if role == "teacher"])
    if not student_speakers:
        raise ValueError(f"No student speakers were found in: {path}")

    return normalized_roles, student_speakers, teacher_speakers


def flatten_segments(payload: dict) -> list[dict]:
    segments: list[dict] = []
    for chunk_index, chunk in enumerate(payload.get("chunks", []), start=1):
        chunk_start_seconds = float(chunk.get("start_seconds", 0))
        raw_segments = chunk.get("segments")
        if not isinstance(raw_segments, list):
            continue

        for segment_index, segment in enumerate(raw_segments, start=1):
            text = str(segment.get("text", "")).strip()
            raw_speaker = str(segment.get("speaker", "")).strip()
            if not text or not raw_speaker:
                continue

            relative_start = float(segment.get("start", 0))
            relative_end = float(segment.get("end", 0))
            absolute_start = round(chunk_start_seconds + relative_start, 3)
            absolute_end = round(chunk_start_seconds + relative_end, 3)
            segments.append(
                {
                    "segment_id": f"c{chunk_index}s{segment_index}",
                    "raw_segment_id": str(segment.get("id", "")).strip() or None,
                    "speaker": raw_speaker,
                    "text": text,
                    "start": absolute_start,
                    "end": absolute_end,
                    "duration_seconds": round(max(0.0, absolute_end - absolute_start), 3),
                }
            )

    return sorted(segments, key=lambda segment: (segment["start"], segment["end"], segment["segment_id"]))


def stitch_text(left: str, right: str) -> str:
    left = left.strip()
    right = right.strip()
    if not left:
        return right
    if not right:
        return left
    return f"{left} {right}"


def merge_role_turns(
    segments: list[dict],
    speaker_roles: dict[str, str],
    merge_gap_seconds: float,
) -> list[dict]:
    if merge_gap_seconds < 0:
        raise ValueError("merge_gap_seconds must be non-negative")

    turns: list[dict] = []
    missing_speakers: set[str] = set()

    for segment in segments:
        role = speaker_roles.get(segment["speaker"])
        if role is None:
            missing_speakers.add(segment["speaker"])
            continue

        if (
            turns
            and turns[-1]["role"] == role
            and segment["start"] - turns[-1]["end"] <= merge_gap_seconds
        ):
            turns[-1]["text"] = stitch_text(turns[-1]["text"], segment["text"])
            turns[-1]["end"] = segment["end"]
            turns[-1]["duration_seconds"] = round(turns[-1]["end"] - turns[-1]["start"], 3)
            if segment["speaker"] not in turns[-1]["speaker_labels"]:
                turns[-1]["speaker_labels"].append(segment["speaker"])
            turns[-1]["segment_ids"].append(segment["segment_id"])
            if segment["raw_segment_id"]:
                turns[-1]["raw_segment_ids"].append(segment["raw_segment_id"])
            continue

        turns.append(
            {
                "role": role,
                "speaker_labels": [segment["speaker"]],
                "start": segment["start"],
                "end": segment["end"],
                "duration_seconds": segment["duration_seconds"],
                "segment_ids": [segment["segment_id"]],
                "raw_segment_ids": [segment["raw_segment_id"]] if segment["raw_segment_id"] else [],
                "text": segment["text"],
            }
        )

    if missing_speakers:
        missing = ", ".join(sorted(missing_speakers))
        raise ValueError(f"speaker_roles.json is missing mappings for speakers: {missing}")

    return turns


def build_student_turns(role_turns: list[dict]) -> list[dict]:
    student_turns: list[dict] = []
    previous_teacher_turn: dict | None = None

    for turn in role_turns:
        if turn["role"] == "teacher":
            previous_teacher_turn = turn
            continue

        turn_id = f"student_turn_{len(student_turns) + 1:03d}"
        student_turns.append(
            {
                "turn_id": turn_id,
                "speaker_labels": turn["speaker_labels"],
                "start": turn["start"],
                "end": turn["end"],
                "duration_seconds": turn["duration_seconds"],
                "segment_ids": turn["segment_ids"],
                "raw_segment_ids": turn["raw_segment_ids"],
                "text": turn["text"],
                "prev_teacher_text": previous_teacher_turn["text"] if previous_teacher_turn else None,
                "prev_teacher_start": previous_teacher_turn["start"] if previous_teacher_turn else None,
                "prev_teacher_end": previous_teacher_turn["end"] if previous_teacher_turn else None,
            }
        )

    return student_turns


def main() -> int:
    args = parse_args()

    transcript_path = resolve_target(args.input)
    speaker_roles_path = resolve_target(args.roles)
    output_path = (args.output.expanduser().resolve() if args.output else default_output_path(transcript_path))

    transcript = load_transcript(transcript_path)
    speaker_roles, student_speakers, teacher_speakers = load_speaker_roles(speaker_roles_path)
    segments = flatten_segments(transcript)
    role_turns = merge_role_turns(segments, speaker_roles, args.merge_gap_seconds)
    student_turns = build_student_turns(role_turns)

    result = {
        "transcript_file": str(transcript_path),
        "speaker_roles_file": str(speaker_roles_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "merge_gap_seconds": args.merge_gap_seconds,
        "student_speakers": student_speakers,
        "teacher_speakers": teacher_speakers,
        "turns": student_turns,
    }
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Transcript: {transcript_path}")
    print(f"Speaker roles: {speaker_roles_path}")
    print(f"Student speakers: {', '.join(student_speakers)}")
    print(f"Teacher speakers: {', '.join(teacher_speakers) if teacher_speakers else '(none)'}")
    print(f"Student turns: {len(student_turns)}")
    print(f"Saved: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
