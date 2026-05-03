#!/usr/bin/env python3
"""Review student turns and suggest speaking-focused improvements.

Requires:
    - OPENAI_API_KEY in the repository root .env file or environment

Example:
    uv run python poc/review_student_turns.py \
      "poc/output/2026年5月02日 12_30のレッスン.part1of2.student_turns.json"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = ROOT_DIR / ".env"
RESPONSES_API_URL = "https://api.openai.com/v1/responses"
REVIEW_MODEL = "gpt-4.1"
REQUEST_TIMEOUT_SECONDS = 180
DEFAULT_MIN_REVIEW_WORDS = 3


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
        description="Review student turns from a student_turns.json file.",
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to a student_turns JSON file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output JSON path. Defaults to <input>.student_turn_reviews.json",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional number of turns to review from the start of the file.",
    )
    parser.add_argument(
        "--min-review-words",
        type=int,
        default=DEFAULT_MIN_REVIEW_WORDS,
        help=f"Skip turns with fewer than this many words. Default: {DEFAULT_MIN_REVIEW_WORDS}",
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
    suffix = ".student_turns.json"
    if input_path.name.endswith(suffix):
        return input_path.with_name(
            input_path.name.removesuffix(suffix) + ".student_turn_reviews.json"
        )
    return input_path.with_name(input_path.stem + ".student_turn_reviews.json")


def load_student_turns(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    turns = payload.get("turns")
    if not isinstance(turns, list):
        raise ValueError(f"student turns were not found in: {path}")
    return payload


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text))


def build_skip_review(turn: dict, reason: str) -> dict:
    return {
        "turn_id": turn["turn_id"],
        "speaker_labels": turn.get("speaker_labels", []),
        "start": turn["start"],
        "end": turn["end"],
        "duration_seconds": turn["duration_seconds"],
        "prev_teacher_text": turn.get("prev_teacher_text"),
        "student_text": turn["text"],
        "review_status": "skipped",
        "skip_reason": reason,
        "error": None,
        "corrected_text": None,
        "natural_text": None,
        "overall_feedback_ja": None,
        "issues": [],
    }


def build_error_review(turn: dict, error: str) -> dict:
    return {
        "turn_id": turn["turn_id"],
        "speaker_labels": turn.get("speaker_labels", []),
        "start": turn["start"],
        "end": turn["end"],
        "duration_seconds": turn["duration_seconds"],
        "prev_teacher_text": turn.get("prev_teacher_text"),
        "student_text": turn["text"],
        "review_status": "error",
        "skip_reason": None,
        "error": error,
        "corrected_text": None,
        "natural_text": None,
        "overall_feedback_ja": None,
        "issues": [],
    }


def build_response_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "corrected_text",
            "natural_text",
            "overall_feedback_ja",
            "issues",
        ],
        "properties": {
            "corrected_text": {
                "type": "string",
            },
            "natural_text": {
                "type": "string",
            },
            "overall_feedback_ja": {
                "type": "string",
            },
            "issues": {
                "type": "array",
                "minItems": 0,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "category",
                        "original",
                        "suggestion",
                        "explanation_ja",
                    ],
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": [
                                "grammar",
                                "word_choice",
                                "expression",
                                "fluency",
                            ],
                        },
                        "original": {
                            "type": "string",
                        },
                        "suggestion": {
                            "type": "string",
                        },
                        "explanation_ja": {
                            "type": "string",
                        },
                    },
                },
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


def review_turn(turn: dict, api_key: str) -> dict:
    system_prompt = (
        "You are reviewing one student utterance from an English speaking lesson. "
        "Your job is to help the student improve spoken English. "
        "Use the teacher prompt only as context. Review only the student's utterance. "
        "Do not rewrite the answer into something much longer or more formal than needed. "
        "Prefer concise, natural spoken English. "
        "Return at most 3 high-impact issues. "
        "Write explanations in Japanese."
    )

    prompt_text = (
        f"Teacher prompt/context:\n{turn.get('prev_teacher_text') or '(none)'}\n\n"
        f"Student utterance to review:\n{turn['text']}\n\n"
        "Please provide:\n"
        "- corrected_text: minimal correction that fixes clear errors\n"
        "- natural_text: a more natural spoken-English version\n"
        "- overall_feedback_ja: short Japanese feedback for this turn\n"
        "- issues: up to 3 important issues only"
    )

    request_payload = {
        "model": REVIEW_MODEL,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt_text},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "student_turn_review",
                "description": "Speaking review for one student turn.",
                "strict": True,
                "schema": build_response_schema(),
            }
        },
    }

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
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"OpenAI API request failed: {exc.code} {exc.reason}\n{details}"
        ) from exc

    parsed = json.loads(extract_response_text(payload))
    return {
        "turn_id": turn["turn_id"],
        "speaker_labels": turn.get("speaker_labels", []),
        "start": turn["start"],
        "end": turn["end"],
        "duration_seconds": turn["duration_seconds"],
        "prev_teacher_text": turn.get("prev_teacher_text"),
        "student_text": turn["text"],
        "review_status": "reviewed",
        "skip_reason": None,
        "error": None,
        "corrected_text": parsed["corrected_text"],
        "natural_text": parsed["natural_text"],
        "overall_feedback_ja": parsed["overall_feedback_ja"],
        "issues": parsed["issues"],
    }


def build_output_payload(
    input_path: Path,
    student_turns_payload: dict,
    reviews: list[dict],
    generated_at: str,
    limit: int | None,
    min_review_words: int,
) -> dict:
    reviewed_count = sum(1 for review in reviews if review["review_status"] == "reviewed")
    skipped_count = sum(1 for review in reviews if review["review_status"] == "skipped")
    error_count = sum(1 for review in reviews if review["review_status"] == "error")
    return {
        "student_turns_file": str(input_path),
        "source_file": student_turns_payload.get("source_file"),
        "source_file_name": student_turns_payload.get("source_file_name"),
        "generated_at": generated_at,
        "model": REVIEW_MODEL,
        "min_review_words": min_review_words,
        "input_turn_count": len(student_turns_payload.get("turns", [])),
        "processed_turn_count": len(reviews),
        "limit": limit,
        "reviewed_count": reviewed_count,
        "skipped_count": skipped_count,
        "error_count": error_count,
        "reviews": reviews,
    }


def save_output(
    output_path: Path,
    input_path: Path,
    student_turns_payload: dict,
    reviews: list[dict],
    generated_at: str,
    limit: int | None,
    min_review_words: int,
) -> None:
    payload = build_output_payload(
        input_path=input_path,
        student_turns_payload=student_turns_payload,
        reviews=reviews,
        generated_at=generated_at,
        limit=limit,
        min_review_words=min_review_words,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
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
        input_path = resolve_target(args.input)
        student_turns_payload = load_student_turns(input_path)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    turns = student_turns_payload.get("turns", [])
    if not turns:
        print("No student turns were found in the input file.", file=sys.stderr)
        return 1

    output_path = args.output.expanduser().resolve() if args.output else default_output_path(input_path)
    selected_turns = turns[: args.limit] if args.limit is not None else turns
    generated_at = datetime.now(timezone.utc).isoformat()
    reviews: list[dict] = []

    print(f"Reviewing student turns with OpenAI model: {REVIEW_MODEL}", flush=True)
    print(f"Input file: {input_path}", flush=True)
    print(f"Turns to process: {len(selected_turns)} / {len(turns)}", flush=True)

    for index, turn in enumerate(selected_turns, start=1):
        text = str(turn.get("text", "")).strip()
        count = word_count(text)
        turn_id = str(turn.get("turn_id", f"turn_{index:03d}"))

        if count < args.min_review_words:
            print(
                f"[{index}/{len(selected_turns)}] Skipping {turn_id} ({count} words)",
                flush=True,
            )
            reviews.append(build_skip_review(turn, "too_short"))
            save_output(
                output_path=output_path,
                input_path=input_path,
                student_turns_payload=student_turns_payload,
                reviews=reviews,
                generated_at=generated_at,
                limit=args.limit,
                min_review_words=args.min_review_words,
            )
            continue

        print(
            f"[{index}/{len(selected_turns)}] Reviewing {turn_id} ({count} words)",
            flush=True,
        )
        try:
            review = review_turn(turn, api_key)
        except Exception as exc:  # noqa: BLE001
            print(f"  Error on {turn_id}: {exc}", flush=True)
            review = build_error_review(turn, str(exc))

        reviews.append(review)
        save_output(
            output_path=output_path,
            input_path=input_path,
            student_turns_payload=student_turns_payload,
            reviews=reviews,
            generated_at=generated_at,
            limit=args.limit,
            min_review_words=args.min_review_words,
        )

    payload = build_output_payload(
        input_path=input_path,
        student_turns_payload=student_turns_payload,
        reviews=reviews,
        generated_at=generated_at,
        limit=args.limit,
        min_review_words=args.min_review_words,
    )
    print(f"Reviewed: {payload['reviewed_count']}", flush=True)
    print(f"Skipped: {payload['skipped_count']}", flush=True)
    print(f"Errors: {payload['error_count']}", flush=True)
    print(f"Saved: {output_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
