#!/usr/bin/env python3
"""Build a full lesson review bundle from one mp3.

This orchestrator runs the README flow 1 and 2 end-to-end:
    1. split -> diarize -> pairwise merge
    2. infer speaker roles -> extract student turns -> group exchanges -> review exchanges

All generated artifacts are written under:
    poc/output/<source mp3 stem>/

Reuse is explicit per phase. When reuse for a phase is true, every required
file for that phase must already exist. Missing files are treated as errors.

Example:
    uv run python poc/build_lesson_review_bundle.py \
      "data/2026年5月02日 12_30のレッスン.mp3" \
      --reuse-split false \
      --reuse-diarize false \
      --reuse-merge false \
      --reuse-speaker-roles false \
      --reuse-turns false \
      --reuse-exchanges false \
      --reuse-reviews false
"""

from __future__ import annotations

import argparse
import concurrent.futures
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a full lesson review bundle from one mp3.",
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
    parser.add_argument(
        "--reuse-speaker-roles",
        type=parse_bool,
        default=False,
        metavar="true|false",
        help="Reuse merged speaker role inference if it already exists. Default: false",
    )
    parser.add_argument(
        "--reuse-turns",
        type=parse_bool,
        default=False,
        metavar="true|false",
        help="Reuse merged student turns if they already exist. Default: false",
    )
    parser.add_argument(
        "--reuse-exchanges",
        type=parse_bool,
        default=False,
        metavar="true|false",
        help="Reuse merged student exchanges if they already exist. Default: false",
    )
    parser.add_argument(
        "--reuse-reviews",
        type=parse_bool,
        default=False,
        metavar="true|false",
        help="Reuse merged student exchange reviews if they already exist. Default: false",
    )
    return parser.parse_args()


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("Expected true or false.")


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


def merged_transcript_path(output_dir: Path) -> Path:
    return output_dir / "merged.diarized.transcript.json"


def merged_debug_path(output_dir: Path) -> Path:
    return output_dir / "merged.diarized.debug.json"


def merged_speaker_roles_path(output_dir: Path) -> Path:
    return output_dir / "merged.speaker_roles.json"


def merged_student_turns_path(output_dir: Path) -> Path:
    return output_dir / "merged.student_turns.json"


def merged_student_exchanges_path(output_dir: Path) -> Path:
    return output_dir / "merged.student_exchanges.json"


def merged_student_exchange_reviews_path(output_dir: Path) -> Path:
    return output_dir / "merged.student_exchange_reviews.json"


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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_reuse_dependencies(args: argparse.Namespace) -> None:
    phase_flags = [
        ("split", args.reuse_split),
        ("diarize", args.reuse_diarize),
        ("merge", args.reuse_merge),
        ("speaker_roles", args.reuse_speaker_roles),
        ("turns", args.reuse_turns),
        ("exchanges", args.reuse_exchanges),
        ("reviews", args.reuse_reviews),
    ]
    saw_false = False
    false_phase = ""
    for phase_name, reuse in phase_flags:
        if not reuse:
            saw_false = True
            false_phase = phase_name
            continue
        if saw_false:
            raise ValueError(
                f"Invalid reuse combination: reuse-{phase_name}=true cannot be used after "
                f"reuse-{false_phase}=false. Downstream reuse requires all upstream phases to be reused."
            )


def run_command(args: list[str]) -> None:
    subprocess.run(args, check=True)


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


def transcribe_part_task(audio_path: Path, transcript_path: Path, api_key: str) -> Path:
    print(f"Diarizing: {audio_path}", flush=True)
    result = transcribe_file(file_path=audio_path, api_key=api_key)
    save_named_transcript(result=result, output_path=transcript_path)
    print(f"Saved diarized transcript to: {transcript_path}", flush=True)
    return transcript_path


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

    print(f"Running diarize phase for all parts in parallel ({DEFAULT_PART_COUNT} workers).", flush=True)
    futures: dict[concurrent.futures.Future[Path], tuple[int, Path]] = {}
    completed_paths: dict[int, Path] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=DEFAULT_PART_COUNT) as executor:
        for index, transcript_path in enumerate(transcript_paths, start=1):
            audio_path = part_audio_path(output_dir, index, DEFAULT_PART_COUNT)
            future = executor.submit(transcribe_part_task, audio_path, transcript_path, api_key)
            futures[future] = (index, transcript_path)

        try:
            for future in concurrent.futures.as_completed(futures):
                index, transcript_path = futures[future]
                completed_paths[index] = future.result()
                print(f"Completed diarize part {index}/{DEFAULT_PART_COUNT}: {transcript_path.name}", flush=True)
        except Exception:
            for pending in futures:
                pending.cancel()
            raise

    return [completed_paths[index] for index in range(1, DEFAULT_PART_COUNT + 1)], "executed"


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
    final_output = merged_transcript_path(output_dir)
    final_debug = merged_debug_path(output_dir)

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


def run_speaker_roles_phase(output_dir: Path, reuse: bool) -> tuple[Path, str]:
    transcript_path = merged_transcript_path(output_dir)
    output_path = merged_speaker_roles_path(output_dir)
    if reuse:
        ensure_files_exist([transcript_path, output_path], "speaker role phase")
        print(f"Reusing speaker roles from: {output_path}", flush=True)
        return output_path, "reused"

    print("Running speaker role inference phase.", flush=True)
    run_command(
        [
            sys.executable,
            str(ROOT_DIR / "poc" / "infer_student_speaker.py"),
            str(transcript_path),
            "--output",
            str(output_path),
        ]
    )
    return output_path, "executed"


def run_turns_phase(output_dir: Path, reuse: bool) -> tuple[Path, str]:
    transcript_path = merged_transcript_path(output_dir)
    roles_path = merged_speaker_roles_path(output_dir)
    output_path = merged_student_turns_path(output_dir)
    if reuse:
        ensure_files_exist([transcript_path, roles_path, output_path], "student turn phase")
        print(f"Reusing student turns from: {output_path}", flush=True)
        return output_path, "reused"

    print("Running student turn extraction phase.", flush=True)
    run_command(
        [
            sys.executable,
            str(ROOT_DIR / "poc" / "extract_student_turns.py"),
            str(transcript_path),
            str(roles_path),
            "--output",
            str(output_path),
        ]
    )
    return output_path, "executed"


def run_exchanges_phase(output_dir: Path, reuse: bool) -> tuple[Path, str]:
    turns_path = merged_student_turns_path(output_dir)
    output_path = merged_student_exchanges_path(output_dir)
    if reuse:
        ensure_files_exist([turns_path, output_path], "student exchange phase")
        print(f"Reusing student exchanges from: {output_path}", flush=True)
        return output_path, "reused"

    print("Running student exchange grouping phase.", flush=True)
    run_command(
        [
            sys.executable,
            str(ROOT_DIR / "poc" / "group_student_exchanges.py"),
            str(turns_path),
            "--output",
            str(output_path),
        ]
    )
    return output_path, "executed"


def run_reviews_phase(output_dir: Path, reuse: bool) -> tuple[Path, str]:
    exchanges_path = merged_student_exchanges_path(output_dir)
    output_path = merged_student_exchange_reviews_path(output_dir)
    if reuse:
        ensure_files_exist([exchanges_path, output_path], "student exchange review phase")
        print(f"Reusing student exchange reviews from: {output_path}", flush=True)
        return output_path, "reused"

    print("Running student exchange review phase.", flush=True)
    run_command(
        [
            sys.executable,
            str(ROOT_DIR / "poc" / "review_student_exchanges.py"),
            str(exchanges_path),
            "--output",
            str(output_path),
        ]
    )
    return output_path, "executed"


def build_pipeline_manifest(
    source_path: Path,
    output_dir: Path,
    split_manifest: dict,
    args: argparse.Namespace,
    split_status: str,
    diarize_status: str,
    merge_status: str,
    transcript_paths: list[Path],
    merge_outputs: dict,
    speaker_roles_status: str,
    turns_status: str,
    exchanges_status: str,
    reviews_status: str,
) -> dict:
    return {
        "source_file": str(source_path),
        "source_file_name": source_path.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(output_dir),
        "part_count": DEFAULT_PART_COUNT,
        "overlap_seconds": DEFAULT_OVERLAP_SECONDS,
        "reuse": {
            "split": args.reuse_split,
            "diarize": args.reuse_diarize,
            "merge": args.reuse_merge,
            "speaker_roles": args.reuse_speaker_roles,
            "turns": args.reuse_turns,
            "exchanges": args.reuse_exchanges,
            "reviews": args.reuse_reviews,
        },
        "status": {
            "split": split_status,
            "diarize": diarize_status,
            "merge": merge_status,
            "speaker_roles": speaker_roles_status,
            "turns": turns_status,
            "exchanges": exchanges_status,
            "reviews": reviews_status,
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
        "merged_transcript_file": str(merged_transcript_path(output_dir)),
        "merged_debug_file": str(merged_debug_path(output_dir)),
        "merged_speaker_roles_file": str(merged_speaker_roles_path(output_dir)),
        "merged_student_turns_file": str(merged_student_turns_path(output_dir)),
        "merged_student_exchanges_file": str(merged_student_exchanges_path(output_dir)),
        "merged_student_exchange_reviews_file": str(
            merged_student_exchange_reviews_path(output_dir)
        ),
    }


def main() -> int:
    args = parse_args()

    try:
        validate_reuse_dependencies(args)
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
        f"merge={str(args.reuse_merge).lower()}, "
        f"speaker_roles={str(args.reuse_speaker_roles).lower()}, "
        f"turns={str(args.reuse_turns).lower()}, "
        f"exchanges={str(args.reuse_exchanges).lower()}, "
        f"reviews={str(args.reuse_reviews).lower()}",
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
        _, speaker_roles_status = run_speaker_roles_phase(output_dir, args.reuse_speaker_roles)
        _, turns_status = run_turns_phase(output_dir, args.reuse_turns)
        _, exchanges_status = run_exchanges_phase(output_dir, args.reuse_exchanges)
        _, reviews_status = run_reviews_phase(output_dir, args.reuse_reviews)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    manifest = build_pipeline_manifest(
        source_path=source_path,
        output_dir=output_dir,
        split_manifest=split_manifest,
        args=args,
        split_status=split_status,
        diarize_status=diarize_status,
        merge_status=merge_status,
        transcript_paths=transcript_paths,
        merge_outputs=merge_outputs,
        speaker_roles_status=speaker_roles_status,
        turns_status=turns_status,
        exchanges_status=exchanges_status,
        reviews_status=reviews_status,
    )
    manifest_path = pipeline_manifest_path(output_dir)
    write_json(manifest_path, manifest)

    print(f"Saved pipeline manifest to: {manifest_path}", flush=True)
    print(f"Merged transcript: {merged_transcript_path(output_dir)}", flush=True)
    print(f"Merged speaker roles: {merged_speaker_roles_path(output_dir)}", flush=True)
    print(f"Merged student turns: {merged_student_turns_path(output_dir)}", flush=True)
    print(f"Merged student exchanges: {merged_student_exchanges_path(output_dir)}", flush=True)
    print(
        f"Merged student exchange reviews: {merged_student_exchange_reviews_path(output_dir)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
