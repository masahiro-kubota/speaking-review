from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


ROOT_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT_DIR / "poc" / "output"
STATIC_DIR = Path(__file__).resolve().parent / "static"
STUDENT_TURNS_SUFFIX = ".student_turns.json"
STUDENT_UTTERANCES_SUFFIX = ".student_utterances.json"
STUDENT_TURN_REVIEWS_SUFFIX = ".student_turn_reviews.json"
STUDENT_UTTERANCE_REVIEWS_SUFFIX = ".student_utterance_reviews.json"


app = FastAPI(title="Student Turn Review UI")
app.mount("/student-turn-static", StaticFiles(directory=STATIC_DIR), name="student-turn-static")


def normalize_unit(unit: str) -> str:
    if unit not in {"turn", "utterance"}:
        raise HTTPException(status_code=400, detail="Invalid unit type.")
    return unit


def available_paths(unit: str) -> list[Path]:
    unit = normalize_unit(unit)
    if not OUTPUT_DIR.exists():
        return []
    suffix = STUDENT_TURNS_SUFFIX if unit == "turn" else STUDENT_UTTERANCES_SUFFIX
    return sorted(
        OUTPUT_DIR.glob(f"*{suffix}"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def payload_path_from_name(name: str, unit: str) -> Path:
    unit = normalize_unit(unit)
    path = (OUTPUT_DIR / name).resolve()
    if path.parent != OUTPUT_DIR.resolve():
        raise HTTPException(status_code=400, detail="Invalid student-speech path.")
    suffix = STUDENT_TURNS_SUFFIX if unit == "turn" else STUDENT_UTTERANCES_SUFFIX
    if not path.is_file() or not path.name.endswith(suffix):
        raise HTTPException(status_code=404, detail="Student-speech file not found.")
    return path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def review_path_for_payload(payload_path: Path, unit: str) -> Path:
    unit = normalize_unit(unit)
    if unit == "turn":
        return payload_path.with_name(
            payload_path.name.removesuffix(STUDENT_TURNS_SUFFIX) + STUDENT_TURN_REVIEWS_SUFFIX
        )
    return payload_path.with_name(
        payload_path.name.removesuffix(STUDENT_UTTERANCES_SUFFIX) + STUDENT_UTTERANCE_REVIEWS_SUFFIX
    )


def load_reviews(payload_path: Path, unit: str) -> tuple[dict[str, dict], dict]:
    unit = normalize_unit(unit)
    review_path = review_path_for_payload(payload_path, unit)
    if not review_path.exists():
        return {}, {
            "available": False,
            "review_file_name": None,
            "reviewed_count": 0,
            "skipped_count": 0,
            "error_count": 0,
            "processed_turn_count": 0,
        }

    payload = load_json(review_path)
    reviews = payload.get("reviews")
    if not isinstance(reviews, list):
        return {}, {
            "available": False,
            "review_file_name": review_path.name,
            "reviewed_count": 0,
            "skipped_count": 0,
            "error_count": 0,
            "processed_turn_count": 0,
        }

    review_map: dict[str, dict] = {}
    item_id_key = "turn_id" if unit == "turn" else "utterance_id"
    for review in reviews:
        item_id = str(review.get(item_id_key, "")).strip()
        if item_id:
            review_map[item_id] = review

    return review_map, {
        "available": True,
        "review_file_name": review_path.name,
        "reviewed_count": int(payload.get("reviewed_count", 0) or 0),
        "skipped_count": int(payload.get("skipped_count", 0) or 0),
        "error_count": int(payload.get("error_count", 0) or 0),
        "processed_turn_count": int(payload.get("processed_turn_count", len(review_map)) or 0),
    }


def resolve_audio_source(payload: dict) -> Path:
    source_file = str(payload.get("source_file", "")).strip()
    if source_file:
        source_path = Path(source_file).expanduser().resolve()
        if source_path.is_file():
            return source_path

    transcript_file = str(payload.get("transcript_file", "") or payload.get("student_turns_file", "")).strip()
    if transcript_file:
        transcript_path = Path(transcript_file).expanduser().resolve()
        if transcript_path.is_file():
            transcript_payload = load_json(transcript_path)
            transcript_source = Path(str(transcript_payload.get("source_file", ""))).expanduser().resolve()
            if transcript_source.is_file():
                return transcript_source

    raise HTTPException(status_code=404, detail="Audio file not found.")


def normalize_items(payload: dict, review_map: dict[str, dict], unit: str) -> list[dict]:
    unit = normalize_unit(unit)
    key = "turns" if unit == "turn" else "utterances"
    raw_items = payload.get(key)
    if not isinstance(raw_items, list):
        return []

    items: list[dict] = []
    id_key = "turn_id" if unit == "turn" else "utterance_id"
    id_prefix = "student_turn" if unit == "turn" else "student_utterance"

    for index, item in enumerate(raw_items, start=1):
        item_id = str(item.get(id_key, "")).strip() or f"{id_prefix}_{index:03d}"
        start = round(float(item.get("start", 0)), 3)
        end = round(float(item.get("end", 0)), 3)
        duration_seconds = round(float(item.get("duration_seconds", max(0.0, end - start))), 3)
        review = review_map.get(item_id, {})

        prompt_start = item.get("prev_teacher_start")
        prompt_end = item.get("prev_teacher_end")
        items.append(
            {
                "id": item_id,
                "unit_type": unit,
                "speaker_labels": [str(label).strip() for label in item.get("speaker_labels", []) if str(label).strip()],
                "start": start,
                "end": end,
                "duration_seconds": duration_seconds,
                "text": str(item.get("text", "")).strip(),
                "prev_teacher_text": str(item.get("prev_teacher_text", "")).strip(),
                "prev_teacher_start": round(float(prompt_start), 3) if prompt_start is not None else None,
                "prev_teacher_end": round(float(prompt_end), 3) if prompt_end is not None else None,
                "source_unit_count": int(item.get("source_turn_count", 1) or 1),
                "source_turn_ids": item.get("turn_ids", [item_id]) if unit == "utterance" else [item_id],
                "review_status": str(review.get("review_status", "")).strip() or "not_reviewed",
                "skip_reason": review.get("skip_reason"),
                "error": review.get("error"),
                "corrected_text": review.get("corrected_text"),
                "natural_text": review.get("natural_text"),
                "overall_feedback_ja": review.get("overall_feedback_ja"),
                "issues": review.get("issues", []),
            }
        )

    return items


def build_payload(payload_path: Path, unit: str) -> dict:
    unit = normalize_unit(unit)
    payload = load_json(payload_path)
    audio_path = resolve_audio_source(payload)
    review_map, review_summary = load_reviews(payload_path, unit)
    items = normalize_items(payload, review_map, unit)
    item_label = "turn" if unit == "turn" else "utterance"
    count_key = "turn_count" if unit == "turn" else "utterance_count"
    return {
        "name": payload_path.name,
        "unit_type": unit,
        "unit_label": item_label,
        "source_file": str(audio_path),
        "source_file_name": audio_path.name,
        "merge_gap_seconds": payload.get("merge_gap_seconds"),
        "student_speakers": payload.get("student_speakers", []),
        "teacher_speakers": payload.get("teacher_speakers", []),
        count_key: len(items),
        "item_count": len(items),
        "duration_seconds": max((item["end"] for item in items), default=0),
        "review_summary": review_summary,
        "items": items,
    }


def build_summary(payload_path: Path, unit: str) -> dict:
    unit = normalize_unit(unit)
    payload = build_payload(payload_path, unit)
    return {
        "name": payload["name"],
        "unit_type": unit,
        "source_file_name": payload["source_file_name"],
        "item_count": payload["item_count"],
        "duration_seconds": payload["duration_seconds"],
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/student-speech-files")
def list_student_speech_files(unit: str = "turn") -> dict:
    return {"items": [build_summary(path, unit) for path in available_paths(unit)]}


@app.get("/api/student-speech")
def get_student_speech(name: str, unit: str = "turn") -> dict:
    return build_payload(payload_path_from_name(name, unit), unit)


@app.get("/api/audio")
def get_audio(name: str, unit: str = "turn") -> FileResponse:
    payload_path = payload_path_from_name(name, unit)
    payload = load_json(payload_path)
    return FileResponse(resolve_audio_source(payload))
