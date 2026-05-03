#!/usr/bin/env python3
"""Infer which diarized speaker labels belong to the student or teacher.

Requires:
    - OPENAI_API_KEY in the repository root .env file or environment

Example:
    uv run python poc/infer_student_speaker.py \
      "poc/output/2026年5月02日 12_30のレッスン.diarized.transcript.json"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = ROOT_DIR / ".env"
RESPONSES_API_URL = "https://api.openai.com/v1/responses"
INFERENCE_MODEL = "gpt-4.1"
TURN_MERGE_GAP_SECONDS = 1.0
MAX_TURNS_PER_SPEAKER = 6
MAX_CONVERSATION_TURNS = 18
REQUEST_TIMEOUT_SECONDS = 180


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Infer which diarized speaker labels belong to the student.",
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to a diarized transcript JSON file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output JSON path. Defaults to <input>.speaker_roles.json",
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
        return input_path.with_name(input_path.name.removesuffix(suffix) + ".speaker_roles.json")
    return input_path.with_name(input_path.stem + ".speaker_roles.json")


def load_transcript(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    chunks = payload.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise ValueError(f"Diarized transcript chunks were not found in: {path}")
    return payload


def flatten_segments(payload: dict) -> list[dict]:
    segments: list[dict] = []
    for chunk_index, chunk in enumerate(payload.get("chunks", []), start=1):
        chunk_start_seconds = float(chunk.get("start_seconds", 0))
        raw_segments = chunk.get("segments")
        if not isinstance(raw_segments, list):
            continue

        for segment_index, segment in enumerate(raw_segments, start=1):
            text = str(segment.get("text", "")).strip()
            speaker = str(segment.get("speaker", "")).strip()
            if not text or not speaker:
                continue

            start = float(segment.get("start", 0))
            end = float(segment.get("end", 0))
            segments.append(
                {
                    "id": f"c{chunk_index}s{segment_index}",
                    "speaker": speaker,
                    "text": text,
                    "start": round(chunk_start_seconds + start, 3),
                    "end": round(chunk_start_seconds + end, 3),
                    "duration_seconds": round(max(0.0, end - start), 3),
                }
            )
    return segments


def stitch_text(left: str, right: str) -> str:
    left = left.strip()
    right = right.strip()
    if not left:
        return right
    if not right:
        return left
    if left.endswith((".", "?", "!", ",")):
        return f"{left} {right}"
    return f"{left} {right}"


def merge_turns(segments: list[dict]) -> list[dict]:
    turns: list[dict] = []
    for segment in segments:
        if (
            turns
            and turns[-1]["speaker"] == segment["speaker"]
            and segment["start"] - turns[-1]["end"] <= TURN_MERGE_GAP_SECONDS
        ):
            turns[-1]["text"] = stitch_text(turns[-1]["text"], segment["text"])
            turns[-1]["end"] = segment["end"]
            turns[-1]["duration_seconds"] = round(turns[-1]["end"] - turns[-1]["start"], 3)
            turns[-1]["segment_ids"].append(segment["id"])
            continue

        turns.append(
            {
                "speaker": segment["speaker"],
                "text": segment["text"],
                "start": segment["start"],
                "end": segment["end"],
                "duration_seconds": segment["duration_seconds"],
                "segment_ids": [segment["id"]],
            }
        )
    return turns


def format_seconds(value: float) -> str:
    minutes = int(value // 60)
    seconds = value - (minutes * 60)
    return f"{minutes:02d}:{seconds:06.3f}"


def truncate_text(text: str, limit: int = 240) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def select_evenly_spaced(items: list[dict], count: int) -> list[dict]:
    if len(items) <= count:
        return items
    step = (len(items) - 1) / (count - 1)
    indexes = {round(index * step) for index in range(count)}
    return [items[index] for index in sorted(indexes)]


def select_representative_turns(turns: list[dict], count: int) -> list[dict]:
    if len(turns) <= count:
        return turns

    ranked = sorted(
        turns,
        key=lambda turn: (
            -len(turn["text"]),
            -turn["duration_seconds"],
            turn["start"],
        ),
    )
    selected = sorted(ranked[:count], key=lambda turn: turn["start"])
    return selected


def build_speaker_stats(segments: list[dict], turns: list[dict]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for segment in segments:
        speaker = segment["speaker"]
        speaker_stats = stats.setdefault(
            speaker,
            {
                "segment_count": 0,
                "turn_count": 0,
                "total_duration_seconds": 0.0,
            },
        )
        speaker_stats["segment_count"] += 1
        speaker_stats["total_duration_seconds"] += segment["duration_seconds"]

    for turn in turns:
        stats[turn["speaker"]]["turn_count"] += 1

    for speaker_stats in stats.values():
        speaker_stats["total_duration_seconds"] = round(speaker_stats["total_duration_seconds"], 3)

    return stats


def build_prompt(transcript_path: Path, segments: list[dict], turns: list[dict]) -> tuple[str, str]:
    observed_speakers = sorted({segment["speaker"] for segment in segments})
    stats = build_speaker_stats(segments, turns)
    speaker_sections: list[str] = []

    for speaker in observed_speakers:
        speaker_turns = [turn for turn in turns if turn["speaker"] == speaker]
        selected_turns = select_representative_turns(speaker_turns, MAX_TURNS_PER_SPEAKER)
        examples = "\n".join(
            f"- [{format_seconds(turn['start'])}-{format_seconds(turn['end'])}] {truncate_text(turn['text'])}"
            for turn in selected_turns
        )
        speaker_sections.append(
            "\n".join(
                [
                    f"Speaker {speaker}",
                    f"segment_count={stats[speaker]['segment_count']}",
                    f"turn_count={stats[speaker]['turn_count']}",
                    f"total_duration_seconds={stats[speaker]['total_duration_seconds']}",
                    "Representative turns:",
                    examples or "- (no examples)",
                ]
            )
        )

    conversation_turns = select_evenly_spaced(turns, MAX_CONVERSATION_TURNS)
    conversation_section = "\n".join(
        f"- [{turn['speaker']}] [{format_seconds(turn['start'])}-{format_seconds(turn['end'])}] {truncate_text(turn['text'])}"
        for turn in conversation_turns
    )

    system_prompt = (
        "You are analyzing an English lesson transcript that already has diarization labels like A, B, or C. "
        "There are exactly two real people in the conversation: one English teacher and one student. "
        "The diarization labels may be over-segmented, so multiple raw labels can belong to the same real person. "
        "Assign every observed raw speaker label to exactly one role: student or teacher. "
        "Use the transcript content only. Prefer signals like who asks guided lesson questions, who explains corrections "
        "or manages the lesson, and who talks about their own plans or produces less natural learner English. "
        "If the evidence is weak, still make the best assignment but set needs_review=true."
    )

    user_prompt = "\n\n".join(
        [
            f"Transcript file: {transcript_path.name}",
            f"Observed raw speakers: {', '.join(observed_speakers)}",
            "Speaker summaries:",
            "\n\n".join(speaker_sections),
            "Conversation sample:",
            conversation_section or "- (no turns)",
        ]
    )
    return system_prompt, user_prompt


def build_response_schema(observed_speakers: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["speaker_roles", "confidence", "needs_review", "reasoning"],
        "properties": {
            "speaker_roles": {
                "type": "object",
                "additionalProperties": False,
                "required": observed_speakers,
                "properties": {
                    speaker: {
                        "type": "string",
                        "enum": ["student", "teacher"],
                    }
                    for speaker in observed_speakers
                },
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
            },
            "needs_review": {
                "type": "boolean",
            },
            "reasoning": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 5,
            },
        },
    }


def extract_response_text(payload: dict) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    outputs = payload.get("output")
    if not isinstance(outputs, list):
        raise RuntimeError("OpenAI response did not contain output text.")

    text_parts: list[str] = []
    for item in outputs:
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                text_parts.append(content.get("text", ""))
            if content.get("type") == "refusal":
                raise RuntimeError(f"Model refused the request: {content.get('refusal', '')}")

    text = "\n".join(part for part in text_parts if part)
    if not text.strip():
        raise RuntimeError("OpenAI response text was empty.")
    return text


def infer_speaker_roles(transcript_path: Path, segments: list[dict], turns: list[dict], api_key: str) -> dict:
    observed_speakers = sorted({segment["speaker"] for segment in segments})
    system_prompt, user_prompt = build_prompt(transcript_path, segments, turns)
    request_payload = {
        "model": INFERENCE_MODEL,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "speaker_role_inference",
                "description": "Maps each raw diarization label to either student or teacher.",
                "strict": True,
                "schema": build_response_schema(observed_speakers),
            }
        },
    }

    print(f"Inferring speaker roles with OpenAI model: {INFERENCE_MODEL}", flush=True)
    request = urllib.request.Request(
        RESPONSES_API_URL,
        data=json.dumps(request_payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            print(f"Received API response (HTTP {response.status})", flush=True)
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"OpenAI API request failed: {exc.code} {exc.reason}\n{details}"
        ) from exc

    parsed = json.loads(extract_response_text(payload))
    role_map = parsed["speaker_roles"]
    student_speakers = sorted(
        speaker for speaker, role in role_map.items() if role == "student"
    )
    teacher_speakers = sorted(
        speaker for speaker, role in role_map.items() if role == "teacher"
    )

    needs_review = parsed["needs_review"]
    if not student_speakers or not teacher_speakers:
        needs_review = True

    return {
        "speaker_roles": role_map,
        "student_speakers": student_speakers,
        "teacher_speakers": teacher_speakers,
        "confidence": parsed["confidence"],
        "needs_review": needs_review,
        "reasoning": parsed["reasoning"],
    }


def save_output(
    output_path: Path,
    transcript_path: Path,
    segments: list[dict],
    turns: list[dict],
    inference: dict,
) -> None:
    speaker_stats = build_speaker_stats(segments, turns)
    payload = {
        "transcript_file": str(transcript_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": INFERENCE_MODEL,
        "observed_speakers": sorted(speaker_stats),
        "speaker_stats": speaker_stats,
        "inference": inference,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    load_env_file(DEFAULT_ENV_PATH)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY is not set. Add it to .env or the environment.", file=sys.stderr)
        return 1

    try:
        transcript_path = resolve_target(args.input)
        transcript_payload = load_transcript(transcript_path)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    segments = flatten_segments(transcript_payload)
    if not segments:
        print("No usable diarized segments were found in the transcript.", file=sys.stderr)
        return 1

    turns = merge_turns(segments)
    try:
        inference = infer_speaker_roles(
            transcript_path=transcript_path,
            segments=segments,
            turns=turns,
            api_key=api_key,
        )
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    output_path = args.output or default_output_path(transcript_path)
    save_output(
        output_path=output_path,
        transcript_path=transcript_path,
        segments=segments,
        turns=turns,
        inference=inference,
    )

    print(f"Observed speakers: {', '.join(sorted(inference['speaker_roles']))}")
    print(f"Student speakers: {', '.join(inference['student_speakers']) or '(none)'}")
    print(f"Teacher speakers: {', '.join(inference['teacher_speakers']) or '(none)'}")
    print(f"Confidence: {inference['confidence']:.2f}")
    print(f"Needs review: {inference['needs_review']}")
    print(f"Saved speaker-role inference to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
