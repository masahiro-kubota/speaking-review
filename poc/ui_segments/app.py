from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


ROOT_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT_DIR / "poc" / "output"
STATIC_DIR = Path(__file__).resolve().parent / "static"
TRANSCRIPT_SUFFIX = ".diarized.transcript.json"
SPEAKER_ROLES_SUFFIX = ".speaker_roles.json"


app = FastAPI(title="Segment Review UI")
app.mount("/segment-static", StaticFiles(directory=STATIC_DIR), name="segment-static")


def available_transcript_paths() -> list[Path]:
    if not OUTPUT_DIR.exists():
        return []
    return sorted(
        OUTPUT_DIR.glob(f"*{TRANSCRIPT_SUFFIX}"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def transcript_path_from_name(name: str) -> Path:
    path = (OUTPUT_DIR / name).resolve()
    if path.parent != OUTPUT_DIR.resolve():
        raise HTTPException(status_code=400, detail="Invalid transcript path.")
    if not path.is_file() or not path.name.endswith(TRANSCRIPT_SUFFIX):
        raise HTTPException(status_code=404, detail="Transcript not found.")
    return path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def speaker_roles_path_for_transcript(transcript_path: Path) -> Path:
    return transcript_path.with_name(
        transcript_path.name.removesuffix(TRANSCRIPT_SUFFIX) + SPEAKER_ROLES_SUFFIX
    )


def load_speaker_roles(transcript_path: Path) -> dict[str, str]:
    roles_path = speaker_roles_path_for_transcript(transcript_path)
    if not roles_path.exists():
        return {}

    payload = load_json(roles_path)
    inference = payload.get("inference")
    if not isinstance(inference, dict):
        return {}

    speaker_roles = inference.get("speaker_roles")
    if not isinstance(speaker_roles, dict):
        return {}

    normalized: dict[str, str] = {}
    for speaker, role in speaker_roles.items():
        if not isinstance(speaker, str) or not isinstance(role, str):
            continue
        normalized[speaker.strip()] = role.strip()
    return normalized


def flatten_segments(transcript_payload: dict, speaker_roles: dict[str, str]) -> list[dict]:
    flattened: list[dict] = []
    chunks = transcript_payload.get("chunks")
    if not isinstance(chunks, list):
        return flattened

    for chunk_index, chunk in enumerate(chunks, start=1):
        chunk_start_seconds = float(chunk.get("start_seconds", 0))
        segments = chunk.get("segments")
        if not isinstance(segments, list):
            continue

        for segment_index, segment in enumerate(segments, start=1):
            raw_start = float(segment.get("start", 0))
            raw_end = float(segment.get("end", 0))
            speaker = str(segment.get("speaker", "")).strip()
            flattened.append(
                {
                    "id": f"c{chunk_index}s{segment_index}",
                    "chunk_index": chunk_index,
                    "segment_index": segment_index,
                    "speaker": speaker,
                    "role": speaker_roles.get(speaker, ""),
                    "text": str(segment.get("text", "")).strip(),
                    "start": round(raw_start, 3),
                    "end": round(raw_end, 3),
                    "absolute_start": round(chunk_start_seconds + raw_start, 3),
                    "absolute_end": round(chunk_start_seconds + raw_end, 3),
                }
            )

    return flattened


def build_transcript_payload(transcript_path: Path) -> dict:
    transcript_payload = load_json(transcript_path)
    source_file = Path(str(transcript_payload.get("source_file", "")))
    speaker_roles = load_speaker_roles(transcript_path)
    segments = flatten_segments(transcript_payload, speaker_roles)
    return {
        "name": transcript_path.name,
        "source_file": str(source_file),
        "source_file_name": source_file.name,
        "duration_seconds": transcript_payload.get("duration_seconds"),
        "speaker_roles": speaker_roles,
        "segments": segments,
    }


def build_transcript_summary(transcript_path: Path) -> dict:
    payload = build_transcript_payload(transcript_path)
    return {
        "name": payload["name"],
        "source_file_name": payload["source_file_name"],
        "duration_seconds": payload["duration_seconds"],
        "segment_count": len(payload["segments"]),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/transcripts")
def list_transcripts() -> dict:
    return {"items": [build_transcript_summary(path) for path in available_transcript_paths()]}


@app.get("/api/transcript")
def get_transcript(name: str) -> dict:
    return build_transcript_payload(transcript_path_from_name(name))


@app.get("/api/audio")
def get_audio(name: str) -> FileResponse:
    transcript_path = transcript_path_from_name(name)
    transcript_payload = load_json(transcript_path)
    source_path = Path(str(transcript_payload.get("source_file", ""))).resolve()
    if not source_path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found.")
    return FileResponse(source_path)
