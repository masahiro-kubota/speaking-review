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


app = FastAPI(title="Student Turn Review UI")
app.mount("/student-turn-static", StaticFiles(directory=STATIC_DIR), name="student-turn-static")


def available_student_turn_paths() -> list[Path]:
    if not OUTPUT_DIR.exists():
        return []
    return sorted(
        OUTPUT_DIR.glob(f"*{STUDENT_TURNS_SUFFIX}"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def student_turns_path_from_name(name: str) -> Path:
    path = (OUTPUT_DIR / name).resolve()
    if path.parent != OUTPUT_DIR.resolve():
        raise HTTPException(status_code=400, detail="Invalid student-turn path.")
    if not path.is_file() or not path.name.endswith(STUDENT_TURNS_SUFFIX):
        raise HTTPException(status_code=404, detail="Student-turn file not found.")
    return path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_audio_source(turns_payload: dict) -> Path:
    source_file = str(turns_payload.get("source_file", "")).strip()
    if source_file:
        source_path = Path(source_file).expanduser().resolve()
        if source_path.is_file():
            return source_path

    transcript_file = str(turns_payload.get("transcript_file", "")).strip()
    if transcript_file:
        transcript_path = Path(transcript_file).expanduser().resolve()
        if transcript_path.is_file():
            transcript_payload = load_json(transcript_path)
            transcript_source = Path(str(transcript_payload.get("source_file", ""))).expanduser().resolve()
            if transcript_source.is_file():
                return transcript_source

    raise HTTPException(status_code=404, detail="Audio file not found.")


def normalize_turns(turns_payload: dict) -> list[dict]:
    raw_turns = turns_payload.get("turns")
    if not isinstance(raw_turns, list):
        return []

    turns: list[dict] = []
    for index, turn in enumerate(raw_turns, start=1):
        turn_id = str(turn.get("turn_id", "")).strip() or f"student_turn_{index:03d}"
        start = round(float(turn.get("start", 0)), 3)
        end = round(float(turn.get("end", 0)), 3)
        duration_seconds = round(float(turn.get("duration_seconds", max(0.0, end - start))), 3)

        prompt_start = turn.get("prev_teacher_start")
        prompt_end = turn.get("prev_teacher_end")
        turns.append(
            {
                "turn_id": turn_id,
                "speaker_labels": [str(label).strip() for label in turn.get("speaker_labels", []) if str(label).strip()],
                "start": start,
                "end": end,
                "duration_seconds": duration_seconds,
                "text": str(turn.get("text", "")).strip(),
                "prev_teacher_text": str(turn.get("prev_teacher_text", "")).strip(),
                "prev_teacher_start": round(float(prompt_start), 3) if prompt_start is not None else None,
                "prev_teacher_end": round(float(prompt_end), 3) if prompt_end is not None else None,
            }
        )

    return turns


def build_student_turns_payload(turns_path: Path) -> dict:
    payload = load_json(turns_path)
    audio_path = resolve_audio_source(payload)
    turns = normalize_turns(payload)
    return {
        "name": turns_path.name,
        "source_file": str(audio_path),
        "source_file_name": audio_path.name,
        "merge_gap_seconds": payload.get("merge_gap_seconds"),
        "student_speakers": payload.get("student_speakers", []),
        "teacher_speakers": payload.get("teacher_speakers", []),
        "turn_count": len(turns),
        "duration_seconds": max((turn["end"] for turn in turns), default=0),
        "turns": turns,
    }


def build_student_turns_summary(turns_path: Path) -> dict:
    payload = build_student_turns_payload(turns_path)
    return {
        "name": payload["name"],
        "source_file_name": payload["source_file_name"],
        "turn_count": payload["turn_count"],
        "duration_seconds": payload["duration_seconds"],
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/student-turn-files")
def list_student_turn_files() -> dict:
    return {"items": [build_student_turns_summary(path) for path in available_student_turn_paths()]}


@app.get("/api/student-turns")
def get_student_turns(name: str) -> dict:
    return build_student_turns_payload(student_turns_path_from_name(name))


@app.get("/api/audio")
def get_audio(name: str) -> FileResponse:
    turns_path = student_turns_path_from_name(name)
    turns_payload = load_json(turns_path)
    return FileResponse(resolve_audio_source(turns_payload))
