#!/usr/bin/env python3
"""Transcribe a single mp3 file with OpenAI gpt-4o-transcribe-diarize.

Requires:
    - OPENAI_API_KEY in the repository root .env file or environment
    - Input audio file size must be within the Audio API upload limit

Example:
    uv run python poc/transcribe_mp3_gpt4o_diarize.py data/2026_4_24_9_00.mp3
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT_DIR / "poc" / "output"
DEFAULT_ENV_PATH = ROOT_DIR / ".env"
API_URL = "https://api.openai.com/v1/audio/transcriptions"
TRANSCRIPTION_MODEL = "gpt-4o-transcribe-diarize"
TRANSCRIPTION_LANGUAGE = "en"
MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024


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


def assert_file_size_within_limit(file_path: Path) -> None:
    file_size_bytes = file_path.stat().st_size
    print(f"Input file size: {file_size_bytes / (1024 * 1024):.2f} MiB", flush=True)
    if file_size_bytes > MAX_UPLOAD_SIZE_BYTES:
        raise ValueError(
            "Input file exceeds the current Audio API upload limit of 25 MiB. "
            "Re-encode or split the file before using this PoC."
        )


def transcribe_file(file_path: Path, api_key: str) -> dict:
    assert_file_size_within_limit(file_path)
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

    print(f"Uploading audio to OpenAI transcription API: {file_path.name}", flush=True)
    print("Requested response_format=diarized_json with chunking_strategy=auto", flush=True)
    try:
        with urllib.request.urlopen(request) as response:
            print(f"Received API response: {file_path.name} (HTTP {response.status})", flush=True)
            payload = response.read().decode("utf-8")
            result = json.loads(payload)
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"OpenAI API request failed for {file_path.name}: {exc.code} {exc.reason}\n{details}"
        ) from exc

    return {
        "source_file": str(file_path),
        "model": TRANSCRIPTION_MODEL,
        "language": TRANSCRIPTION_LANGUAGE,
        "response_format": "diarized_json",
        **result,
    }


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
