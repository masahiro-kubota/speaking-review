#!/usr/bin/env python3
"""Transcribe a single mp3 file with OpenAI gpt-4o-transcribe-diarize.

Requires:
    - OPENAI_API_KEY in the repository root .env file or environment
    - ffprobe available on PATH for audio duration inspection
    - ffmpeg available on PATH for splitting long audio files

Example:
    uv run python poc/transcribe_mp3_gpt4o_diarize.py data/2026_4_24_9_00.mp3
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT_DIR / "poc" / "output"
DEFAULT_ENV_PATH = ROOT_DIR / ".env"
API_URL = "https://api.openai.com/v1/audio/transcriptions"
TRANSCRIPTION_MODEL = "gpt-4o-transcribe-diarize"
TRANSCRIPTION_LANGUAGE = "en"
MAX_MODEL_DURATION_SECONDS = 1400
DEFAULT_CHUNK_DURATION_SECONDS = 180
MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 600


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
        description="Transcribe a single mp3 file with speaker diarization.",
    )
    parser.add_argument(
        "input",
        help="Path to an mp3 file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where diarized transcript JSON files are written.",
    )
    return parser.parse_args()


def resolve_target(user_input: str) -> Path:
    target = Path(user_input).expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(f"Input file not found: {target}")
    if target.suffix.lower() != ".mp3":
        raise ValueError(f"Only mp3 files are supported in this PoC: {target}")
    return target


def build_multipart_body(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = "----CodexSpeakingReviewBoundary"
    parts: list[bytes] = []

    for name, value in fields.items():
        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )

    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    file_bytes = file_path.read_bytes()
    parts.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{file_path.name}"\r\n'
            ).encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )

    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def get_audio_duration_seconds(file_path: Path) -> float:
    print(f"Inspecting audio duration with ffprobe: {file_path}", flush=True)
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


def split_audio_file(file_path: Path, output_dir: Path, chunk_duration_seconds: int) -> list[Path]:
    output_pattern = output_dir / f"{file_path.stem}.part%03d{file_path.suffix.lower()}"
    print(
        f"Splitting long audio with ffmpeg into {chunk_duration_seconds}s chunks: {file_path}",
        flush=True,
    )

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(file_path),
                "-vn",
                "-f",
                "segment",
                "-segment_time",
                str(chunk_duration_seconds),
                "-c",
                "copy",
                "-reset_timestamps",
                "1",
                str(output_pattern),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required to split long audio files.") from exc
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() or exc.stdout.strip()
        raise RuntimeError(f"Failed to split {file_path.name} into chunks: {details}") from exc

    chunk_paths = sorted(output_dir.glob(f"{file_path.stem}.part*{file_path.suffix.lower()}"))
    if not chunk_paths:
        raise RuntimeError(f"ffmpeg did not produce any chunks for {file_path.name}.")
    print(f"Created {len(chunk_paths)} chunk(s).", flush=True)
    return chunk_paths


def assert_file_size_within_limit(file_path: Path, label: str) -> None:
    file_size_bytes = file_path.stat().st_size
    print(f"Input file size for {label}: {file_size_bytes / (1024 * 1024):.2f} MiB", flush=True)
    if file_size_bytes > MAX_UPLOAD_SIZE_BYTES:
        raise ValueError(
            f"{label} exceeds the current Audio API upload limit of 25 MiB. "
            "Lower the split duration before using this PoC."
        )


def transcribe_chunk(file_path: Path, api_key: str, chunk_label: str | None = None) -> dict:
    label = chunk_label or file_path.name
    assert_file_size_within_limit(file_path, label)
    body, content_type = build_multipart_body(
        fields={
            "model": TRANSCRIPTION_MODEL,
            "language": TRANSCRIPTION_LANGUAGE,
            "response_format": "diarized_json",
            "chunking_strategy": "auto",
        },
        file_field="file",
        file_path=file_path,
    )

    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
        },
        method="POST",
    )

    print(f"Uploading audio to OpenAI transcription API: {label}", flush=True)
    print("Requested response_format=diarized_json with chunking_strategy=auto", flush=True)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            print(f"Received API response: {label} (HTTP {response.status})", flush=True)
            payload = response.read().decode("utf-8")
            return json.loads(payload)
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"OpenAI API request failed for {file_path.name}: {exc.code} {exc.reason}\n{details}"
        ) from exc


def merge_usage(chunk_results: list[dict]) -> dict | None:
    usages = [result.get("usage") for result in chunk_results if isinstance(result.get("usage"), dict)]
    if len(usages) != len(chunk_results):
        return None

    usage_types = {usage.get("type") for usage in usages}
    if usage_types == {"tokens"}:
        merged = {
            "type": "tokens",
            "input_tokens": sum(usage.get("input_tokens", 0) for usage in usages),
            "output_tokens": sum(usage.get("output_tokens", 0) for usage in usages),
            "total_tokens": sum(usage.get("total_tokens", 0) for usage in usages),
        }

        audio_tokens = sum(
            usage.get("input_token_details", {}).get("audio_tokens", 0) for usage in usages
        )
        text_tokens = sum(
            usage.get("input_token_details", {}).get("text_tokens", 0) for usage in usages
        )
        if audio_tokens or text_tokens:
            merged["input_token_details"] = {
                "audio_tokens": audio_tokens,
                "text_tokens": text_tokens,
            }
        return merged

    if usage_types == {"duration"}:
        return {
            "type": "duration",
            "seconds": sum(usage.get("seconds", 0) for usage in usages),
        }

    return None


def combine_chunk_results(
    source_path: Path,
    duration_seconds: float,
    chunk_duration_seconds: int,
    chunk_results: list[dict],
) -> dict:
    combined = {
        "source_file": str(source_path),
        "model": TRANSCRIPTION_MODEL,
        "language": TRANSCRIPTION_LANGUAGE,
        "response_format": "diarized_json",
        "duration_seconds": duration_seconds,
        "text": "\n\n".join(result.get("text", "").strip() for result in chunk_results if result.get("text")),
        "chunking": {
            "applied": len(chunk_results) > 1,
            "chunk_count": len(chunk_results),
            "chunk_duration_seconds": chunk_duration_seconds,
            "max_model_duration_seconds": MAX_MODEL_DURATION_SECONDS,
        },
        "chunks": [],
    }

    for index, result in enumerate(chunk_results):
        chunk = {
            "index": index + 1,
            "start_seconds": index * chunk_duration_seconds,
            "end_seconds": min((index + 1) * chunk_duration_seconds, duration_seconds),
            "duration_seconds": result.get("duration"),
            "text": result.get("text", ""),
            "segments": result.get("segments", []),
        }
        if result.get("usage") is not None:
            chunk["usage"] = result["usage"]
        if result.get("task") is not None:
            chunk["task"] = result["task"]
        combined["chunks"].append(chunk)

    merged_usage = merge_usage(chunk_results)
    if merged_usage is not None:
        combined["usage"] = merged_usage

    return combined


def transcribe_file(file_path: Path, api_key: str) -> dict:
    duration_seconds = get_audio_duration_seconds(file_path)
    print(f"Audio duration: {duration_seconds:.1f}s", flush=True)
    if duration_seconds <= MAX_MODEL_DURATION_SECONDS:
        print("Audio fits in a single diarization request.", flush=True)
        result = transcribe_chunk(file_path=file_path, api_key=api_key)
        return combine_chunk_results(
            source_path=file_path,
            duration_seconds=duration_seconds,
            chunk_duration_seconds=DEFAULT_CHUNK_DURATION_SECONDS,
            chunk_results=[result],
        )

    with tempfile.TemporaryDirectory(prefix="transcribe-diarize-chunks-") as temp_dir:
        chunk_paths = split_audio_file(
            file_path=file_path,
            output_dir=Path(temp_dir),
            chunk_duration_seconds=DEFAULT_CHUNK_DURATION_SECONDS,
        )
        chunk_results = []
        for index, chunk_path in enumerate(chunk_paths, start=1):
            print(f"Processing chunk {index}/{len(chunk_paths)}: {chunk_path.name}", flush=True)
            chunk_results.append(
                transcribe_chunk(
                    file_path=chunk_path,
                    api_key=api_key,
                    chunk_label=f"chunk {index}/{len(chunk_paths)}",
                )
            )

    return combine_chunk_results(
        source_path=file_path,
        duration_seconds=duration_seconds,
        chunk_duration_seconds=DEFAULT_CHUNK_DURATION_SECONDS,
        chunk_results=chunk_results,
    )


def save_transcript(result: dict, source_path: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{source_path.stem}.diarized.transcript.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def main() -> int:
    args = parse_args()
    load_env_file(DEFAULT_ENV_PATH)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY is not set. Add it to .env or the environment.", file=sys.stderr)
        return 1

    try:
        target = resolve_target(args.input)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Transcribing with diarization: {target}", flush=True)
    try:
        result = transcribe_file(file_path=target, api_key=api_key)
        output_path = save_transcript(result, target, args.output_dir)
        print(f"Saved diarized transcript to: {output_path}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
