from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


ROOT_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT_DIR / "poc" / "output"
STATIC_DIR = Path(__file__).resolve().parent / "static"
STUDENT_EXCHANGES_FILE_NAME = "merged.student_exchanges.json"
STUDENT_EXCHANGE_REVIEWS_FILE_NAME = "merged.student_exchange_reviews.json"


app = FastAPI(title="Student Speech Review UI")
app.mount("/student-turn-static", StaticFiles(directory=STATIC_DIR), name="student-turn-static")

def available_lesson_dirs() -> list[Path]:
    if not OUTPUT_DIR.exists():
        return []
    return sorted(
        [
            path
            for path in OUTPUT_DIR.iterdir()
            if path.is_dir() and (path / STUDENT_EXCHANGES_FILE_NAME).is_file()
        ],
        key=lambda path: (path / STUDENT_EXCHANGES_FILE_NAME).stat().st_mtime,
        reverse=True,
    )


def lesson_dir_from_name(name: str) -> Path:
    path = (OUTPUT_DIR / name).resolve()
    if path.parent != OUTPUT_DIR.resolve():
        raise HTTPException(status_code=400, detail="Invalid lesson path.")
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="Lesson directory not found.")
    if not (path / STUDENT_EXCHANGES_FILE_NAME).is_file():
        raise HTTPException(status_code=404, detail="Student exchange file not found.")
    return path


def payload_path_for_lesson(lesson_dir: Path) -> Path:
    payload_path = lesson_dir / STUDENT_EXCHANGES_FILE_NAME
    if not payload_path.is_file():
        raise HTTPException(status_code=404, detail="Student exchange file not found.")
    return payload_path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_reviews(lesson_dir: Path) -> tuple[dict[str, dict], dict]:
    review_path = lesson_dir / STUDENT_EXCHANGE_REVIEWS_FILE_NAME
    if not review_path.exists():
        return {}, {
            "available": False,
            "review_file_name": None,
            "reviewed_count": 0,
            "skipped_count": 0,
            "error_count": 0,
            "processed_count": 0,
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
            "processed_count": 0,
        }

    review_map: dict[str, dict] = {}
    for review in reviews:
        item_id = str(review.get("exchange_id", "")).strip()
        if item_id:
            review_map[item_id] = review

    return review_map, {
        "available": True,
        "review_file_name": review_path.name,
        "reviewed_count": int(payload.get("reviewed_count", 0) or 0),
        "skipped_count": int(payload.get("skipped_count", 0) or 0),
        "error_count": int(payload.get("error_count", 0) or 0),
        "processed_count": int(
            payload.get(
                "processed_exchange_count",
                payload.get("processed_turn_count", len(review_map)),
            )
            or 0
        ),
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


def normalize_items(payload: dict, review_map: dict[str, dict]) -> list[dict]:
    raw_items = payload.get("exchanges")
    if not isinstance(raw_items, list):
        return []

    items: list[dict] = []
    for index, item in enumerate(raw_items, start=1):
        item_id = str(item.get("exchange_id", "")).strip() or f"student_exchange_{index:03d}"
        start = round(float(item.get("start", 0)), 3)
        end = round(float(item.get("end", 0)), 3)
        duration_seconds = round(float(item.get("duration_seconds", max(0.0, end - start))), 3)
        review = review_map.get(item_id, {})

        prompt_text = str(item.get("teacher_prompt_text", "")).strip()
        prompt_start = item.get("teacher_prompt_start")
        prompt_end = item.get("teacher_prompt_end")
        student_text = str(item.get("student_response_text", "")).strip()

        items.append(
            {
                "id": item_id,
                "unit_type": "exchange",
                "speaker_labels": [str(label).strip() for label in item.get("speaker_labels", []) if str(label).strip()],
                "start": start,
                "end": end,
                "duration_seconds": duration_seconds,
                "student_text": student_text,
                "prompt_text": prompt_text,
                "prompt_start": round(float(prompt_start), 3) if prompt_start is not None else None,
                "prompt_end": round(float(prompt_end), 3) if prompt_end is not None else None,
                "source_unit_count": int(item.get("source_turn_count", 1) or 1),
                "source_turn_ids": item.get("turn_ids", [item_id]),
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


def build_payload(lesson_dir: Path) -> dict:
    payload_path = payload_path_for_lesson(lesson_dir)
    payload = load_json(payload_path)
    audio_path = resolve_audio_source(payload)
    review_map, review_summary = load_reviews(lesson_dir)
    items = normalize_items(payload, review_map)
    return {
        "name": lesson_dir.name,
        "unit_type": "exchange",
        "unit_label": "exchange",
        "payload_file_name": payload_path.name,
        "source_file": str(audio_path),
        "source_file_name": audio_path.name,
        "student_speakers": payload.get("student_speakers", []),
        "teacher_speakers": payload.get("teacher_speakers", []),
        "exchange_count": len(items),
        "item_count": len(items),
        "duration_seconds": max((item["end"] for item in items), default=0),
        "review_summary": review_summary,
        "items": items,
    }


def build_summary(lesson_dir: Path) -> dict:
    payload = build_payload(lesson_dir)
    return {
        "name": payload["name"],
        "unit_type": "exchange",
        "source_file_name": payload["source_file_name"],
        "item_count": payload["item_count"],
        "duration_seconds": payload["duration_seconds"],
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/lessons")
def list_lessons() -> dict:
    return {"items": [build_summary(path) for path in available_lesson_dirs()]}


@app.get("/api/student-speech")
def get_student_speech(lesson: str) -> dict:
    return build_payload(lesson_dir_from_name(lesson))


@app.get("/api/audio")
def get_audio(lesson: str) -> FileResponse:
    payload_path = payload_path_for_lesson(lesson_dir_from_name(lesson))
    payload = load_json(payload_path)
    return FileResponse(resolve_audio_source(payload))
