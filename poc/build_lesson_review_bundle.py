#!/usr/bin/env python3
"""Build a full lesson review bundle from one mp3.

This orchestrator runs the README flow 1 and 2 end-to-end:
    1. split -> diarize -> pairwise merge
    2. infer speaker roles -> extract student turns -> group utterances -> review utterances

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
      --reuse-utterances false \
      --reuse-reviews false
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from diarize_split_manifest_and_merge import build_output_dir, parse_bool, pipeline_manifest_path
from split_mp3_with_overlap import ROOT_DIR, resolve_target as resolve_mp3_target


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
        "--reuse-utterances",
        type=parse_bool,
        default=False,
        metavar="true|false",
        help="Reuse merged student utterances if they already exist. Default: false",
    )
    parser.add_argument(
        "--reuse-reviews",
        type=parse_bool,
        default=False,
        metavar="true|false",
        help="Reuse merged student utterance reviews if they already exist. Default: false",
    )
    return parser.parse_args()


def merged_transcript_path(output_dir: Path) -> Path:
    return output_dir / "merged.diarized.transcript.json"


def merged_debug_path(output_dir: Path) -> Path:
    return output_dir / "merged.diarized.debug.json"


def merged_speaker_roles_path(output_dir: Path) -> Path:
    return output_dir / "merged.speaker_roles.json"


def merged_student_turns_path(output_dir: Path) -> Path:
    return output_dir / "merged.student_turns.json"


def merged_student_utterances_path(output_dir: Path) -> Path:
    return output_dir / "merged.student_utterances.json"


def merged_student_utterance_reviews_path(output_dir: Path) -> Path:
    return output_dir / "merged.student_utterance_reviews.json"


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
        ("utterances", args.reuse_utterances),
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


def run_split_diarize_merge_phase(source_path: Path, args: argparse.Namespace) -> tuple[dict, str]:
    command = [
        sys.executable,
        str(ROOT_DIR / "poc" / "diarize_split_manifest_and_merge.py"),
        str(source_path),
        "--reuse-split",
        str(args.reuse_split).lower(),
        "--reuse-diarize",
        str(args.reuse_diarize).lower(),
        "--reuse-merge",
        str(args.reuse_merge).lower(),
    ]
    run_command(command)
    output_dir = build_output_dir(source_path)
    manifest_path = pipeline_manifest_path(output_dir)
    if not manifest_path.exists():
        raise FileNotFoundError(f"Pipeline manifest was not generated: {manifest_path}")
    return load_json(manifest_path), "executed"


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


def run_utterances_phase(output_dir: Path, reuse: bool) -> tuple[Path, str]:
    turns_path = merged_student_turns_path(output_dir)
    output_path = merged_student_utterances_path(output_dir)
    if reuse:
        ensure_files_exist([turns_path, output_path], "student utterance phase")
        print(f"Reusing student utterances from: {output_path}", flush=True)
        return output_path, "reused"

    print("Running student utterance grouping phase.", flush=True)
    run_command(
        [
            sys.executable,
            str(ROOT_DIR / "poc" / "group_student_utterances.py"),
            str(turns_path),
            "--output",
            str(output_path),
        ]
    )
    return output_path, "executed"


def run_reviews_phase(output_dir: Path, reuse: bool) -> tuple[Path, str]:
    utterances_path = merged_student_utterances_path(output_dir)
    output_path = merged_student_utterance_reviews_path(output_dir)
    if reuse:
        ensure_files_exist([utterances_path, output_path], "student utterance review phase")
        print(f"Reusing student utterance reviews from: {output_path}", flush=True)
        return output_path, "reused"

    print("Running student utterance review phase.", flush=True)
    run_command(
        [
            sys.executable,
            str(ROOT_DIR / "poc" / "review_student_utterances.py"),
            str(utterances_path),
            "--output",
            str(output_path),
        ]
    )
    return output_path, "executed"


def build_pipeline_manifest(
    source_path: Path,
    output_dir: Path,
    split_diarize_merge_manifest: dict,
    args: argparse.Namespace,
    speaker_roles_status: str,
    turns_status: str,
    utterances_status: str,
    reviews_status: str,
) -> dict:
    manifest = dict(split_diarize_merge_manifest)
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    manifest["source_file"] = str(source_path)
    manifest["source_file_name"] = source_path.name
    manifest["output_dir"] = str(output_dir)
    manifest["reuse"] = {
        "split": args.reuse_split,
        "diarize": args.reuse_diarize,
        "merge": args.reuse_merge,
        "speaker_roles": args.reuse_speaker_roles,
        "turns": args.reuse_turns,
        "utterances": args.reuse_utterances,
        "reviews": args.reuse_reviews,
    }
    status = dict(manifest.get("status", {}))
    status.update(
        {
            "speaker_roles": speaker_roles_status,
            "turns": turns_status,
            "utterances": utterances_status,
            "reviews": reviews_status,
        }
    )
    manifest["status"] = status
    manifest["merged_transcript_file"] = str(merged_transcript_path(output_dir))
    manifest["merged_debug_file"] = str(merged_debug_path(output_dir))
    manifest["merged_speaker_roles_file"] = str(merged_speaker_roles_path(output_dir))
    manifest["merged_student_turns_file"] = str(merged_student_turns_path(output_dir))
    manifest["merged_student_utterances_file"] = str(merged_student_utterances_path(output_dir))
    manifest["merged_student_utterance_reviews_file"] = str(
        merged_student_utterance_reviews_path(output_dir)
    )
    return manifest


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
        f"utterances={str(args.reuse_utterances).lower()}, "
        f"reviews={str(args.reuse_reviews).lower()}",
        flush=True,
    )

    try:
        split_diarize_merge_manifest, _ = run_split_diarize_merge_phase(source_path, args)
        _, speaker_roles_status = run_speaker_roles_phase(output_dir, args.reuse_speaker_roles)
        _, turns_status = run_turns_phase(output_dir, args.reuse_turns)
        _, utterances_status = run_utterances_phase(output_dir, args.reuse_utterances)
        _, reviews_status = run_reviews_phase(output_dir, args.reuse_reviews)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    manifest = build_pipeline_manifest(
        source_path=source_path,
        output_dir=output_dir,
        split_diarize_merge_manifest=split_diarize_merge_manifest,
        args=args,
        speaker_roles_status=speaker_roles_status,
        turns_status=turns_status,
        utterances_status=utterances_status,
        reviews_status=reviews_status,
    )
    manifest_path = pipeline_manifest_path(output_dir)
    write_json(manifest_path, manifest)

    print(f"Saved pipeline manifest to: {manifest_path}", flush=True)
    print(f"Merged transcript: {merged_transcript_path(output_dir)}", flush=True)
    print(f"Merged speaker roles: {merged_speaker_roles_path(output_dir)}", flush=True)
    print(f"Merged student turns: {merged_student_turns_path(output_dir)}", flush=True)
    print(f"Merged student utterances: {merged_student_utterances_path(output_dir)}", flush=True)
    print(
        f"Merged student utterance reviews: {merged_student_utterance_reviews_path(output_dir)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
