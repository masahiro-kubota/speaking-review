#!/usr/bin/env python3
"""Run split -> diarize -> pairwise merge for a single mp3.

This orchestrator writes all generated artifacts under:
    poc/output/<source mp3 stem>/

Reuse is explicit per phase. When reuse for a phase is true, the orchestrator
expects every required file for that phase to already exist. If anything is
missing, it stops with an error instead of filling gaps implicitly.

Example:
    uv run python poc/diarize_split_manifest_and_merge.py \
      "data/2026年5月02日 12_30のレッスン.mp3"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from split_mp3_with_overlap import (
    DEFAULT_OVERLAP_SECONDS,
    DEFAULT_PART_COUNT,
    ROOT_DIR,
    build_manifest,
    build_ranges,
    cut_audio_part,
    get_audio_duration_seconds,
    resolve_target as resolve_mp3_target,
    validate_part_constraints,
)
from transcribe_mp3_gpt4o_diarize import (
    DEFAULT_ENV_PATH,
    load_env_file,
    transcribe_file,
)


DEFAULT_OUTPUT_ROOT = ROOT_DIR / "poc" / "output"


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("Expected true or false.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run split -> diarize -> pairwise merge for a single mp3.",
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to an mp3 file.",
    )
    parser.add_argument(
        "--reuse-split",
        type=parse_bool,
        default=False,
        metavar="true|false",
        help="Reuse split artifacts if they already exist. Default: false",
    )
    parser.add_argument(
        "--reuse-diarize",
        type=parse_bool,
        default=False,
        metavar="true|false",
        help="Reuse diarized transcript artifacts if they already exist. Default: false",
    )
    parser.add_argument(
        "--reuse-merge",
        type=parse_bool,
        default=False,
        metavar="true|false",
        help="Reuse merge artifacts if they already exist. Default: false",
    )
    return parser.parse_args()


def build_output_dir(source_path: Path) -> Path:
    return DEFAULT_OUTPUT_ROOT / source_path.stem


def part_audio_path(output_dir: Path, index: int, part_count: int) -> Path:
    return output_dir / f"part{index}of{part_count}.mp3"


def part_diarized_path(output_dir: Path, index: int, part_count: int) -> Path:
    return output_dir / f"part{index}of{part_count}.diarized.transcript.json"


def split_manifest_path(output_dir: Path) -> Path:
    return output_dir / "split_manifest.json"


def intermediate_merge_path(output_dir: Path) -> Path:
    return output_dir / "merge_part1of3_part2of3.diarized.transcript.json"


def intermediate_merge_debug_path(output_dir: Path) -> Path:
    return output_dir / "merge_part1of3_part2of3.debug.json"


def final_merge_path(output_dir: Path) -> Path:
    return output_dir / "merged.diarized.transcript.json"


def final_merge_debug_path(output_dir: Path) -> Path:
    return output_dir / "merged.diarized.debug.json"


def pipeline_manifest_path(output_dir: Path) -> Path:
    return output_dir / "pipeline_manifest.json"


def ensure_files_exist(paths: list[Path], phase_name: str) -> None:
    missing = [path for path in paths if not path.exists()]
    if missing:
        joined = "\n".join(str(path) for path in missing)
        raise FileNotFoundError(
            f"Cannot reuse {phase_name}. The following required files are missing:\n{joined}"
        )


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_split_phase(source_path: Path, output_dir: Path, reuse: bool) -> tuple[dict, str]:
    manifest_path = split_manifest_path(output_dir)
    part_paths = [part_audio_path(output_dir, index, DEFAULT_PART_COUNT) for index in range(1, DEFAULT_PART_COUNT + 1)]

    if reuse:
        ensure_files_exist([manifest_path, *part_paths], "split phase")
        print(f"Reusing split artifacts from: {output_dir}", flush=True)
        return load_json(manifest_path), "reused"

    duration_seconds = get_audio_duration_seconds(source_path)
    source_file_size_bytes = source_path.stat().st_size
    parts = build_ranges(
        duration_seconds=duration_seconds,
        part_count=DEFAULT_PART_COUNT,
        overlap_seconds=DEFAULT_OVERLAP_SECONDS,
    )

    print(
        f"Running split phase: {DEFAULT_PART_COUNT} part(s) with {DEFAULT_OVERLAP_SECONDS:.3f}s overlap",
        flush=True,
    )
    for part in parts:
        output_path = part_audio_path(output_dir, part["index"], DEFAULT_PART_COUNT)
        cut_audio_part(
            source_path=source_path,
            output_path=output_path,
            start_seconds=part["extract_start_seconds"],
            duration_seconds=part["requested_duration_seconds"],
        )
        actual_duration_seconds = get_audio_duration_seconds(output_path)
        validate_part_constraints(output_path, actual_duration_seconds)
        part["path"] = str(output_path)
        part["file_size_bytes"] = output_path.stat().st_size
        part["duration_seconds"] = round(actual_duration_seconds, 3)

    manifest = build_manifest(
        source_path=source_path,
        output_dir=output_dir,
        duration_seconds=duration_seconds,
        source_file_size_bytes=source_file_size_bytes,
        part_count=DEFAULT_PART_COUNT,
        overlap_seconds=DEFAULT_OVERLAP_SECONDS,
        parts=parts,
    )
    manifest["output_dir"] = str(output_dir)
    write_json(manifest_path, manifest)
    print(f"Saved split manifest to: {manifest_path}", flush=True)
    return manifest, "executed"


def save_named_transcript(result: dict, output_path: Path) -> None:
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def run_diarize_phase(output_dir: Path, reuse: bool) -> tuple[list[Path], str]:
    transcript_paths = [
        part_diarized_path(output_dir, index, DEFAULT_PART_COUNT)
        for index in range(1, DEFAULT_PART_COUNT + 1)
    ]
    if reuse:
        ensure_files_exist(transcript_paths, "diarize phase")
        print(f"Reusing diarized transcripts from: {output_dir}", flush=True)
        return transcript_paths, "reused"

    load_env_file(DEFAULT_ENV_PATH)
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Add it to .env or the environment.")

    print("Running diarize phase for all parts.", flush=True)
    for index, transcript_path in enumerate(transcript_paths, start=1):
        audio_path = part_audio_path(output_dir, index, DEFAULT_PART_COUNT)
        print(f"Diarizing: {audio_path}", flush=True)
        result = transcribe_file(file_path=audio_path, api_key=api_key)
        save_named_transcript(result=result, output_path=transcript_path)
        print(f"Saved diarized transcript to: {transcript_path}", flush=True)

    return transcript_paths, "executed"


def run_merge_command(
    left_path: Path,
    right_path: Path,
    manifest_path: Path,
    output_path: Path,
    debug_output_path: Path,
) -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT_DIR / "poc" / "merge_diarized_transcripts.py"),
            str(left_path),
            str(right_path),
            str(manifest_path),
            "--output",
            str(output_path),
            "--debug-output",
            str(debug_output_path),
        ],
        check=True,
    )


def run_merge_phase(output_dir: Path, split_manifest: dict, reuse: bool) -> tuple[dict, str]:
    split_manifest_file = split_manifest_path(output_dir)
    part1 = part_diarized_path(output_dir, 1, DEFAULT_PART_COUNT)
    part2 = part_diarized_path(output_dir, 2, DEFAULT_PART_COUNT)
    part3 = part_diarized_path(output_dir, 3, DEFAULT_PART_COUNT)
    pair_output = intermediate_merge_path(output_dir)
    pair_debug = intermediate_merge_debug_path(output_dir)
    final_output = final_merge_path(output_dir)
    final_debug = final_merge_debug_path(output_dir)

    if reuse:
        ensure_files_exist(
            [pair_output, pair_debug, final_output, final_debug],
            "merge phase",
        )
        print(f"Reusing merge artifacts from: {output_dir}", flush=True)
        return {
            "pair_output": pair_output,
            "pair_debug": pair_debug,
            "final_output": final_output,
            "final_debug": final_debug,
        }, "reused"

    print("Running merge phase: part1of3 + part2of3", flush=True)
    run_merge_command(
        left_path=part1,
        right_path=part2,
        manifest_path=split_manifest_file,
        output_path=pair_output,
        debug_output_path=pair_debug,
    )
    print("Running merge phase: (part1of3 + part2of3) + part3of3", flush=True)
    run_merge_command(
        left_path=pair_output,
        right_path=part3,
        manifest_path=split_manifest_file,
        output_path=final_output,
        debug_output_path=final_debug,
    )
    return {
        "pair_output": pair_output,
        "pair_debug": pair_debug,
        "final_output": final_output,
        "final_debug": final_debug,
    }, "executed"


def build_pipeline_manifest(
    source_path: Path,
    output_dir: Path,
    split_manifest: dict,
    reuse_split: bool,
    reuse_diarize: bool,
    reuse_merge: bool,
    split_status: str,
    diarize_status: str,
    merge_status: str,
    transcript_paths: list[Path],
    merge_outputs: dict,
) -> dict:
    return {
        "source_file": str(source_path),
        "source_file_name": source_path.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(output_dir),
        "part_count": DEFAULT_PART_COUNT,
        "overlap_seconds": DEFAULT_OVERLAP_SECONDS,
        "reuse": {
            "split": reuse_split,
            "diarize": reuse_diarize,
            "merge": reuse_merge,
        },
        "status": {
            "split": split_status,
            "diarize": diarize_status,
            "merge": merge_status,
        },
        "split_manifest_file": str(split_manifest_path(output_dir)),
        "parts": [
            {
                "index": index,
                "audio_file": str(part_audio_path(output_dir, index, DEFAULT_PART_COUNT)),
                "diarized_transcript_file": str(transcript_paths[index - 1]),
            }
            for index in range(1, DEFAULT_PART_COUNT + 1)
        ],
        "merge_steps": [
            {
                "left": str(transcript_paths[0]),
                "right": str(transcript_paths[1]),
                "output": str(merge_outputs["pair_output"]),
                "debug_output": str(merge_outputs["pair_debug"]),
            },
            {
                "left": str(merge_outputs["pair_output"]),
                "right": str(transcript_paths[2]),
                "output": str(merge_outputs["final_output"]),
                "debug_output": str(merge_outputs["final_debug"]),
            },
        ],
        "final_merged_transcript_file": str(merge_outputs["final_output"]),
        "final_merged_debug_file": str(merge_outputs["final_debug"]),
        "split_manifest_summary": {
            "duration_seconds": split_manifest.get("duration_seconds"),
            "source_file_size_bytes": split_manifest.get("source_file_size_bytes"),
        },
    }


def main() -> int:
    args = parse_args()

    try:
        source_path = resolve_mp3_target(args.input)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    output_dir = build_output_dir(source_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Source file: {source_path}", flush=True)
    print(f"Output dir: {output_dir}", flush=True)
    print(
        "Reuse flags: "
        f"split={str(args.reuse_split).lower()}, "
        f"diarize={str(args.reuse_diarize).lower()}, "
        f"merge={str(args.reuse_merge).lower()}",
        flush=True,
    )

    try:
        split_manifest, split_status = run_split_phase(
            source_path=source_path,
            output_dir=output_dir,
            reuse=args.reuse_split,
        )
        transcript_paths, diarize_status = run_diarize_phase(
            output_dir=output_dir,
            reuse=args.reuse_diarize,
        )
        merge_outputs, merge_status = run_merge_phase(
            output_dir=output_dir,
            split_manifest=split_manifest,
            reuse=args.reuse_merge,
        )
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    pipeline_manifest = build_pipeline_manifest(
        source_path=source_path,
        output_dir=output_dir,
        split_manifest=split_manifest,
        reuse_split=args.reuse_split,
        reuse_diarize=args.reuse_diarize,
        reuse_merge=args.reuse_merge,
        split_status=split_status,
        diarize_status=diarize_status,
        merge_status=merge_status,
        transcript_paths=transcript_paths,
        merge_outputs=merge_outputs,
    )
    manifest_path = pipeline_manifest_path(output_dir)
    write_json(manifest_path, pipeline_manifest)

    print(f"Saved pipeline manifest to: {manifest_path}", flush=True)
    print(f"Final merged transcript: {merge_outputs['final_output']}", flush=True)
    print(f"Final merge debug output: {merge_outputs['final_debug']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
