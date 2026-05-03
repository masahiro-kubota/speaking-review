#!/usr/bin/env python3
"""Group student turns into larger utterances with hybrid rules and LLM decisions.

A "student turn" in this PoC is a rough intermediate unit produced by
`extract_student_turns.py`.

An "utterance" here means:
    - a larger student-side unit that should feel like one answer or one thought
    - it may contain multiple adjacent student turns
    - ambiguous boundaries are resolved with a mix of rules and LLM judgments

This utterance unit is intended to be closer to the final review unit than the
raw student turn unit.

Requires:
    - OPENAI_API_KEY in the repository root .env file or environment

Example:
    uv run python poc/group_student_utterances.py \
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
GROUPING_MODEL = "gpt-4.1"
REQUEST_TIMEOUT_SECONDS = 180
MAX_CANDIDATE_GAP_SECONDS = 8.0
MAX_SHORT_REACTION_DURATION_SECONDS = 2.5
MAX_SHORT_REACTION_WORDS = 3
MAX_BOUNDARIES_PER_REQUEST = 20
SHORT_TEACHER_REACTION_KEYS = {
    "ah",
    "gotit",
    "hmm",
    "hm",
    "isee",
    "mhm",
    "mmhmm",
    "oh",
    "okay",
    "ok",
    "really",
    "right",
    "uhhuh",
    "uhuh",
    "wow",
    "yeah",
    "yep",
    "yes",
}


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
        description="Group student turns into larger student utterances.",
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to a student_turns JSON file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output JSON path. Defaults to <input>.student_utterances.json",
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
            input_path.name.removesuffix(suffix) + ".student_utterances.json"
        )
    return input_path.with_name(input_path.stem + ".student_utterances.json")


def load_student_turns(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    turns = payload.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ValueError(f"student turns were not found in: {path}")
    return payload


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text))


def normalize_text_key(text: str) -> str:
    return re.sub(r"[^a-z]+", "", text.lower())


def normalize_prompt_text(text: str | None) -> str:
    return " ".join((text or "").split())


def teacher_prompt_signature(turn: dict) -> tuple[float | None, float | None, str]:
    return (
        turn.get("prev_teacher_start"),
        turn.get("prev_teacher_end"),
        normalize_prompt_text(turn.get("prev_teacher_text")),
    )


def is_short_teacher_reaction(turn: dict) -> bool:
    text = normalize_prompt_text(turn.get("prev_teacher_text"))
    if not text:
        return False

    start = turn.get("prev_teacher_start")
    end = turn.get("prev_teacher_end")
    if start is None or end is None:
        return False

    duration = float(end) - float(start)
    if duration > MAX_SHORT_REACTION_DURATION_SECONDS:
        return False
    if word_count(text) > MAX_SHORT_REACTION_WORDS:
        return False
    return normalize_text_key(text) in SHORT_TEACHER_REACTION_KEYS


def truncate_text(text: str, limit: int = 240) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def stitch_text(left: str, right: str) -> str:
    left = left.strip()
    right = right.strip()
    if not left:
        return right
    if not right:
        return left
    return f"{left} {right}"


def classify_boundaries(turns: list[dict]) -> tuple[list[dict], list[dict]]:
    boundary_decisions: list[dict] = []
    candidate_boundaries: list[dict] = []

    for index in range(len(turns) - 1):
        left = turns[index]
        right = turns[index + 1]
        gap_seconds = round(float(right["start"]) - float(left["end"]), 3)
        boundary_id = f"boundary_{index + 1:03d}"

        same_prompt = teacher_prompt_signature(left) == teacher_prompt_signature(right)
        short_reaction = is_short_teacher_reaction(right)
        boundary_kind = "same_prompt" if same_prompt else ("short_teacher_reaction" if short_reaction else "hard_split")

        decision = {
            "boundary_id": boundary_id,
            "left_turn_id": left["turn_id"],
            "right_turn_id": right["turn_id"],
            "gap_seconds": gap_seconds,
            "boundary_kind": boundary_kind,
        }

        if gap_seconds > MAX_CANDIDATE_GAP_SECONDS:
            decision.update(
                {
                    "decision": "keep_split",
                    "method": "rule",
                    "reason": f"Gap {gap_seconds:.3f}s exceeds max candidate gap.",
                }
            )
            boundary_decisions.append(decision)
            continue

        if same_prompt or short_reaction:
            candidate = {
                **decision,
                "shared_prompt": normalize_prompt_text(right.get("prev_teacher_text")),
                "left_text": left["text"],
                "right_text": right["text"],
            }
            candidate_boundaries.append(candidate)
            continue

        decision.update(
            {
                "decision": "keep_split",
                "method": "rule",
                "reason": "Teacher prompt/context changed in a substantive way.",
            }
        )
        boundary_decisions.append(decision)

    return boundary_decisions, candidate_boundaries


def build_response_schema(boundary_ids: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["decisions"],
        "properties": {
            "decisions": {
                "type": "array",
                "minItems": len(boundary_ids),
                "maxItems": len(boundary_ids),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["boundary_id", "decision", "reason"],
                    "properties": {
                        "boundary_id": {
                            "type": "string",
                            "enum": boundary_ids,
                        },
                        "decision": {
                            "type": "string",
                            "enum": ["merge", "keep_split"],
                        },
                        "reason": {
                            "type": "string",
                        },
                    },
                },
            }
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


def infer_boundary_decisions(candidates: list[dict], api_key: str) -> list[dict]:
    if not candidates:
        return []

    boundary_sections = []
    for candidate in candidates:
        boundary_sections.append(
            "\n".join(
                [
                    f"Boundary ID: {candidate['boundary_id']}",
                    f"Kind: {candidate['boundary_kind']}",
                    f"Gap seconds: {candidate['gap_seconds']}",
                    f"Shared or latest teacher context: {candidate['shared_prompt'] or '(none)'}",
                    f"Left turn ({candidate['left_turn_id']}): {truncate_text(candidate['left_text'])}",
                    f"Right turn ({candidate['right_turn_id']}): {truncate_text(candidate['right_text'])}",
                ]
            )
        )

    system_prompt = (
        "You are grouping adjacent student turns from an English speaking lesson into larger utterances. "
        "Each boundary is already pre-filtered as ambiguous. "
        "Decide MERGE only if the right turn is clearly a continuation of the same student utterance or thought. "
        "Keep split if the student has started a new answer, restarted after a meaningful pause, or if the short teacher reaction meaningfully changed the flow. "
        "Use semantic continuity, unfinished phrasing, discourse markers, and whether the teacher text looks like a mere acknowledgment. "
        "Do not rewrite any text. Only decide merge vs keep_split."
    )

    user_prompt = "\n\n".join(
        [
            "Decide each boundary independently.",
            "Boundaries:",
            "\n\n".join(boundary_sections),
        ]
    )

    boundary_ids = [candidate["boundary_id"] for candidate in candidates]
    request_payload = {
        "model": GROUPING_MODEL,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "student_turn_boundary_decisions",
                "description": "Merge or keep_split decisions for ambiguous student-turn boundaries.",
                "strict": True,
                "schema": build_response_schema(boundary_ids),
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
    decisions = parsed.get("decisions")
    if not isinstance(decisions, list):
        raise RuntimeError("Boundary grouping response did not contain decisions.")

    by_id = {decision["boundary_id"]: decision for decision in decisions}
    missing = [boundary_id for boundary_id in boundary_ids if boundary_id not in by_id]
    if missing:
        raise RuntimeError(f"Missing boundary decisions for: {', '.join(missing)}")

    return [by_id[boundary_id] for boundary_id in boundary_ids]


def resolve_candidate_boundaries(candidates: list[dict], api_key: str) -> list[dict]:
    resolved: list[dict] = []
    for offset in range(0, len(candidates), MAX_BOUNDARIES_PER_REQUEST):
        batch = candidates[offset : offset + MAX_BOUNDARIES_PER_REQUEST]
        print(
            f"Grouping ambiguous boundaries with OpenAI model: {GROUPING_MODEL} "
            f"({offset + 1}-{offset + len(batch)} of {len(candidates)})",
            flush=True,
        )
        decisions = infer_boundary_decisions(batch, api_key)
        for candidate, decision in zip(batch, decisions, strict=True):
            resolved.append(
                {
                    **candidate,
                    "decision": decision["decision"],
                    "method": "llm",
                    "reason": decision["reason"],
                }
            )
    return resolved


def build_utterances(turns: list[dict], boundary_decisions: list[dict]) -> list[dict]:
    decisions_by_left_turn = {decision["left_turn_id"]: decision for decision in boundary_decisions}
    utterances: list[dict] = []

    current = {
        "turn_ids": [turns[0]["turn_id"]],
        "speaker_labels": list(turns[0].get("speaker_labels", [])),
        "start": turns[0]["start"],
        "end": turns[0]["end"],
        "duration_seconds": turns[0]["duration_seconds"],
        "text": turns[0]["text"],
        "prev_teacher_text": turns[0].get("prev_teacher_text"),
        "prev_teacher_start": turns[0].get("prev_teacher_start"),
        "prev_teacher_end": turns[0].get("prev_teacher_end"),
    }

    for index in range(len(turns) - 1):
        left = turns[index]
        right = turns[index + 1]
        decision = decisions_by_left_turn.get(left["turn_id"])
        should_merge = decision is not None and decision["decision"] == "merge"

        if should_merge:
            current["turn_ids"].append(right["turn_id"])
            current["end"] = right["end"]
            current["duration_seconds"] = round(current["end"] - current["start"], 3)
            current["text"] = stitch_text(current["text"], right["text"])
            for label in right.get("speaker_labels", []):
                if label not in current["speaker_labels"]:
                    current["speaker_labels"].append(label)
            continue

        utterance_id = f"student_utterance_{len(utterances) + 1:03d}"
        utterances.append(
            {
                "utterance_id": utterance_id,
                "turn_ids": current["turn_ids"],
                "speaker_labels": current["speaker_labels"],
                "start": current["start"],
                "end": current["end"],
                "duration_seconds": current["duration_seconds"],
                "text": current["text"],
                "prev_teacher_text": current["prev_teacher_text"],
                "prev_teacher_start": current["prev_teacher_start"],
                "prev_teacher_end": current["prev_teacher_end"],
                "source_turn_count": len(current["turn_ids"]),
            }
        )
        current = {
            "turn_ids": [right["turn_id"]],
            "speaker_labels": list(right.get("speaker_labels", [])),
            "start": right["start"],
            "end": right["end"],
            "duration_seconds": right["duration_seconds"],
            "text": right["text"],
            "prev_teacher_text": right.get("prev_teacher_text"),
            "prev_teacher_start": right.get("prev_teacher_start"),
            "prev_teacher_end": right.get("prev_teacher_end"),
        }

    utterance_id = f"student_utterance_{len(utterances) + 1:03d}"
    utterances.append(
        {
            "utterance_id": utterance_id,
            "turn_ids": current["turn_ids"],
            "speaker_labels": current["speaker_labels"],
            "start": current["start"],
            "end": current["end"],
            "duration_seconds": current["duration_seconds"],
            "text": current["text"],
            "prev_teacher_text": current["prev_teacher_text"],
            "prev_teacher_start": current["prev_teacher_start"],
            "prev_teacher_end": current["prev_teacher_end"],
            "source_turn_count": len(current["turn_ids"]),
        }
    )

    return utterances


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
    if len(turns) < 2:
        print("There are not enough student turns to group.", file=sys.stderr)
        return 1

    output_path = args.output.expanduser().resolve() if args.output else default_output_path(input_path)
    rule_decisions, candidates = classify_boundaries(turns)
    print(f"Input turns: {len(turns)}", flush=True)
    print(f"Hard-split boundaries: {len(rule_decisions)}", flush=True)
    print(f"Ambiguous boundaries for LLM: {len(candidates)}", flush=True)

    try:
        llm_decisions = resolve_candidate_boundaries(candidates, api_key)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    boundary_decisions = sorted(
        [*rule_decisions, *llm_decisions],
        key=lambda decision: decision["boundary_id"],
    )
    utterances = build_utterances(turns, boundary_decisions)

    payload = {
        "student_turns_file": str(input_path),
        "source_file": student_turns_payload.get("source_file"),
        "source_file_name": student_turns_payload.get("source_file_name"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": GROUPING_MODEL,
        "max_candidate_gap_seconds": MAX_CANDIDATE_GAP_SECONDS,
        "input_turn_count": len(turns),
        "candidate_boundary_count": len(candidates),
        "utterance_count": len(utterances),
        "boundary_decisions": boundary_decisions,
        "utterances": utterances,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    merged = sum(1 for decision in boundary_decisions if decision["decision"] == "merge")
    kept = sum(1 for decision in boundary_decisions if decision["decision"] == "keep_split")
    print(f"Merged boundaries: {merged}", flush=True)
    print(f"Kept split boundaries: {kept}", flush=True)
    print(f"Utterances: {len(utterances)}", flush=True)
    print(f"Saved: {output_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
