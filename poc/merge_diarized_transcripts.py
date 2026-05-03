#!/usr/bin/env python3
"""Merge two overlapping diarized transcript JSON files into one transcript.

This is the diarized counterpart of `merge_transcripts.py`.

It:
    - detects overlap from the expected overlap time window
    - compares the left overlap suffix against the right overlap prefix
    - trims the duplicate prefix from the right-side segments
    - uses the split manifest to place each part on the original absolute timeline

Example:
    uv run python poc/merge_diarized_transcripts.py \
      "poc/output/lesson.part1of3.diarized.transcript.json" \
      "poc/output/lesson.part2of3.diarized.transcript.json" \
      "poc/output/lesson.split_manifest.json"
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

DEFAULT_SEARCH_HEAD_CHARS = 4000
DEFAULT_MAX_OVERLAP_TOKENS = 160
DEFAULT_MIN_OVERLAP_TOKENS = 6
DEFAULT_MIN_SCORE = 0.72
DEFAULT_MIN_COVERAGE = 0.6
TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?")
FILLER_TOKENS = {
    "ah",
    "bye",
    "cool",
    "great",
    "hello",
    "hey",
    "hi",
    "hmm",
    "mm",
    "nice",
    "oh",
    "okay",
    "ok",
    "right",
    "so",
    "sure",
    "thanks",
    "thank",
    "uh",
    "um",
    "well",
    "yeah",
    "yep",
}


@dataclass(frozen=True)
class PartInfo:
    index: int
    label: str
    path: Path
    logical_start_seconds: float
    logical_end_seconds: float
    extract_start_seconds: float
    extract_end_seconds: float
    left_overlap_seconds: float
    right_overlap_seconds: float


@dataclass(frozen=True)
class Token:
    normalized: str
    start: int
    end: int


@dataclass(frozen=True)
class WindowTokenRef:
    normalized: str
    absolute_segment_index: int
    local_start: int
    local_end: int


@dataclass(frozen=True)
class OverlapCandidate:
    left_start_idx: int
    right_end_idx: int
    left_token_count: int
    right_token_count: int
    text_overlap_score: float
    ratio: float
    coverage: float
    left_coverage: float
    right_coverage: float
    content_coverage: float
    matched_tokens: int
    matched_content_tokens: int
    right_cut_char: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge two overlapping diarized transcript JSON files.",
    )
    parser.add_argument("left", type=Path, help="Earlier diarized transcript JSON path.")
    parser.add_argument("right", type=Path, help="Later diarized transcript JSON path.")
    parser.add_argument("manifest", type=Path, help="Split manifest JSON path.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output JSON path. Defaults to poc/output/<left>__<right>.merged.diarized.transcript.json",
    )
    parser.add_argument(
        "--debug-output",
        type=Path,
        help="Optional debug JSON path. Defaults to <output>.debug.json",
    )
    parser.add_argument(
        "--search-head-chars",
        type=int,
        default=DEFAULT_SEARCH_HEAD_CHARS,
        help=f"Only the first N characters of the right overlap window are searched. Default: {DEFAULT_SEARCH_HEAD_CHARS}",
    )
    parser.add_argument(
        "--min-overlap-tokens",
        type=int,
        default=DEFAULT_MIN_OVERLAP_TOKENS,
        help=f"Minimum token count considered as a plausible overlap. Default: {DEFAULT_MIN_OVERLAP_TOKENS}",
    )
    parser.add_argument(
        "--max-overlap-tokens",
        type=int,
        default=DEFAULT_MAX_OVERLAP_TOKENS,
        help=f"Maximum token count considered for overlap comparison. Default: {DEFAULT_MAX_OVERLAP_TOKENS}",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=DEFAULT_MIN_SCORE,
        help=f"Minimum text overlap score for trusting the text-based cut. Default: {DEFAULT_MIN_SCORE}",
    )
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=DEFAULT_MIN_COVERAGE,
        help=f"Minimum coverage for trusting the text-based cut. Default: {DEFAULT_MIN_COVERAGE}",
    )
    return parser.parse_args()


def resolve_json(path: Path) -> Path:
    target = path.expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(f"JSON file not found: {target}")
    if target.suffix.lower() != ".json":
        raise ValueError(f"Only JSON input is supported in this PoC: {target}")
    return target


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_token(token_text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", token_text.lower())


def tokenize_with_spans(text: str) -> list[Token]:
    tokens: list[Token] = []
    for match in TOKEN_RE.finditer(text):
        normalized = normalize_token(match.group())
        if normalized:
            tokens.append(Token(normalized=normalized, start=match.start(), end=match.end()))
    return tokens


def count_content_tokens(normalized_tokens: list[str]) -> int:
    return sum(token not in FILLER_TOKENS for token in normalized_tokens)


def stitch_merged_text(left_text: str, right_remainder: str) -> str:
    left = left_text.rstrip()
    right = right_remainder.lstrip()
    if not right:
        return left
    separator = "" if left.endswith((" ", "\n")) or right.startswith((".", ",", "!", "?")) else " "
    return f"{left}{separator}{right}"


def stitch_segment_text(segments: list[dict]) -> str:
    text = ""
    for segment in segments:
        text = stitch_merged_text(text, str(segment.get("text", "")))
    return text.strip()


def append_text_fragment(base_text: str, fragment: str) -> str:
    fragment = fragment.strip()
    if not fragment:
        return base_text
    return stitch_merged_text(base_text, fragment)


def extend_cut_char(text: str, cut_char: int) -> int:
    while cut_char < len(text) and text[cut_char] in "\"'”)]}":
        cut_char += 1
    while cut_char < len(text) and text[cut_char] in ".!?":
        cut_char += 1
    while cut_char < len(text) and text[cut_char].isspace():
        cut_char += 1
    return cut_char


def extend_local_cut_char(text: str, cut_char: int) -> int:
    while cut_char < len(text) and text[cut_char] in "\"'”)]}":
        cut_char += 1
    while cut_char < len(text) and text[cut_char] in ".!?":
        cut_char += 1
    while cut_char < len(text) and text[cut_char].isspace():
        cut_char += 1
    return cut_char


def extract_token_span_text(text: str, tokens: list[Token], start_idx: int, end_idx: int) -> str:
    if not tokens or start_idx < 0 or end_idx <= start_idx:
        return ""
    return text[tokens[start_idx].start : tokens[end_idx - 1].end].strip()


def load_transcript_payload(path: Path) -> dict:
    payload = load_json(path)
    text = payload.get("text")
    chunks = payload.get("chunks")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"Transcript text was not found in: {path}")
    if not isinstance(chunks, list) or not chunks:
        raise ValueError(f"Transcript chunks were not found in: {path}")
    return payload


def load_manifest(path: Path) -> tuple[dict, list[PartInfo]]:
    payload = load_json(path)
    parts = payload.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError(f"Manifest parts were not found in: {path}")

    parsed_parts: list[PartInfo] = []
    for raw_part in parts:
        parsed_parts.append(
            PartInfo(
                index=int(raw_part["index"]),
                label=str(raw_part["label"]),
                path=Path(str(raw_part["path"])).expanduser().resolve(),
                logical_start_seconds=float(raw_part["logical_start_seconds"]),
                logical_end_seconds=float(raw_part["logical_end_seconds"]),
                extract_start_seconds=float(raw_part["extract_start_seconds"]),
                extract_end_seconds=float(raw_part["extract_end_seconds"]),
                left_overlap_seconds=float(raw_part.get("left_overlap_seconds", 0.0)),
                right_overlap_seconds=float(raw_part.get("right_overlap_seconds", 0.0)),
            )
        )
    return payload, parsed_parts


def infer_part(payload: dict, parts: list[PartInfo]) -> PartInfo | None:
    source_file = str(payload.get("source_file", "")).strip()
    if not source_file:
        return None
    source_path = Path(source_file).expanduser().resolve()
    for part in parts:
        if part.path == source_path:
            return part
    matching_by_name = [part for part in parts if part.path.name == source_path.name]
    if len(matching_by_name) == 1:
        return matching_by_name[0]
    return None


def normalize_segment(segment: dict) -> dict:
    return {
        "type": str(segment.get("type", "transcript.text.segment")),
        "text": str(segment.get("text", "")),
        "speaker": str(segment.get("speaker", "")),
        "start": float(segment.get("start", 0.0)),
        "end": float(segment.get("end", 0.0)),
        "id": str(segment.get("id", "")).strip() or None,
    }


def canonical_chunk_base(payload: dict, part: PartInfo | None) -> float:
    if payload.get("chunk_time_basis") == "absolute":
        return min(float(chunk.get("start_seconds", 0.0)) for chunk in payload.get("chunks", []))
    if part is None:
        raise ValueError("A non-merged transcript could not be matched to a manifest part.")
    return part.extract_start_seconds


def namespace_speaker(source_part_label: str | None, speaker: str) -> tuple[str, str]:
    raw_speaker = speaker.strip()
    if not raw_speaker:
        return "", ""
    if not source_part_label:
        return raw_speaker, raw_speaker
    prefix = f"{source_part_label}:"
    if raw_speaker.startswith(prefix):
        return raw_speaker, raw_speaker[len(prefix) :].strip()
    return f"{prefix}{raw_speaker}", raw_speaker


def flatten_payload_segments(payload: dict, part: PartInfo | None) -> tuple[list[dict], float]:
    absolute_chunks = payload.get("chunk_time_basis") == "absolute"
    base_start = canonical_chunk_base(payload, part)
    flattened: list[dict] = []

    for chunk_index, chunk in enumerate(payload.get("chunks", []), start=1):
        chunk_start = float(chunk.get("start_seconds", 0.0))
        chunk_base = chunk_start if absolute_chunks else part.extract_start_seconds + chunk_start
        source_part_label = str(chunk.get("source_part_label", "")).strip() if absolute_chunks else part.label
        raw_segments = chunk.get("segments")
        if not isinstance(raw_segments, list):
            continue

        for segment_index, raw_segment in enumerate(raw_segments, start=1):
            segment = normalize_segment(raw_segment)
            text = segment["text"].strip()
            speaker = segment["speaker"].strip()
            if not text or not speaker:
                continue
            namespaced_speaker, raw_speaker = namespace_speaker(source_part_label, speaker)
            absolute_start = round(chunk_base + segment["start"], 3)
            absolute_end = round(chunk_base + segment["end"], 3)
            flattened.append(
                {
                    "flat_id": f"c{chunk_index}s{segment_index}",
                    "type": segment["type"],
                    "text": text,
                    "speaker": namespaced_speaker,
                    "raw_speaker": raw_speaker,
                    "source_part_label": source_part_label or None,
                    "absolute_start": absolute_start,
                    "absolute_end": absolute_end,
                    "duration_seconds": round(max(0.0, absolute_end - absolute_start), 3),
                    "original_id": segment["id"],
                    "local_start": segment["start"],
                    "local_end": segment["end"],
                }
            )

    return flattened, base_start


def build_canonical_chunk(
    label: str,
    source_payload_file: Path,
    source_part_label: str | None,
    base_start_seconds: float,
    absolute_segments: list[dict],
) -> dict:
    relative_segments: list[dict] = []
    for index, segment in enumerate(absolute_segments, start=1):
        relative_start = round(segment["absolute_start"] - base_start_seconds, 3)
        relative_end = round(segment["absolute_end"] - base_start_seconds, 3)
        segment_id = segment.get("original_id") or f"{label}_seg_{index:03d}"
        relative_segments.append(
            {
                "type": segment["type"],
                "text": segment["text"],
                "speaker": segment["speaker"],
                "raw_speaker": segment.get("raw_speaker", segment["speaker"]),
                "source_part_label": segment.get("source_part_label"),
                "start": relative_start,
                "end": relative_end,
                "id": segment_id,
            }
        )

    chunk_text = stitch_segment_text(relative_segments)
    chunk_end_seconds = base_start_seconds
    if relative_segments:
        chunk_end_seconds = round(base_start_seconds + max(segment["end"] for segment in relative_segments), 3)

    chunk = {
        "label": label,
        "source_payload_file": str(source_payload_file),
        "start_seconds": round(base_start_seconds, 3),
        "end_seconds": chunk_end_seconds,
        "duration_seconds": round(max(0.0, chunk_end_seconds - base_start_seconds), 3),
        "text": chunk_text,
        "segments": relative_segments,
    }
    if source_part_label is not None:
        chunk["source_part_label"] = source_part_label
    return chunk


def select_segment_entries_in_time_window(
    absolute_segments: list[dict],
    start_seconds: float,
    end_seconds: float,
) -> list[tuple[int, dict]]:
    selected: list[tuple[int, dict]] = []
    for index, segment in enumerate(absolute_segments):
        if segment["absolute_end"] <= start_seconds:
            continue
        if segment["absolute_start"] >= end_seconds:
            continue
        selected.append((index, segment))
    return selected


def build_overlap_window(
    left_absolute_segments: list[dict],
    right_absolute_segments: list[dict],
    right_part: PartInfo,
) -> tuple[str, str, list[tuple[int, dict]], dict]:
    overlap_start_seconds = right_part.extract_start_seconds
    overlap_end_seconds = right_part.logical_start_seconds + right_part.left_overlap_seconds
    left_window_entries = select_segment_entries_in_time_window(
        absolute_segments=left_absolute_segments,
        start_seconds=overlap_start_seconds,
        end_seconds=overlap_end_seconds,
    )
    right_window_entries = select_segment_entries_in_time_window(
        absolute_segments=right_absolute_segments,
        start_seconds=overlap_start_seconds,
        end_seconds=overlap_end_seconds,
    )
    left_window_text = stitch_segment_text([segment for _, segment in left_window_entries])
    right_window_text = stitch_segment_text([segment for _, segment in right_window_entries])
    return (
        left_window_text,
        right_window_text,
        right_window_entries,
        {
            "overlap_start_seconds": round(overlap_start_seconds, 3),
            "overlap_end_seconds": round(overlap_end_seconds, 3),
            "left_window_segment_count": len(left_window_entries),
            "right_window_segment_count": len(right_window_entries),
        },
    )


def score_overlap_candidate(left_tokens: list[str], right_tokens: list[str]) -> tuple[float, float, float, float, float, int, int]:
    matcher = SequenceMatcher(None, left_tokens, right_tokens, autojunk=False)
    ratio = matcher.ratio()
    matched_tokens = 0
    matched_content_tokens = 0

    for block in matcher.get_matching_blocks():
        if not block.size:
            continue
        matched_tokens += block.size
        matched_content_tokens += sum(
            token not in FILLER_TOKENS for token in left_tokens[block.a : block.a + block.size]
        )

    left_coverage = matched_tokens / max(1, len(left_tokens))
    right_coverage = matched_tokens / max(1, len(right_tokens))
    coverage = min(left_coverage, right_coverage)
    min_content_tokens = max(1, min(count_content_tokens(left_tokens), count_content_tokens(right_tokens)))
    content_coverage = matched_content_tokens / min_content_tokens
    text_overlap_score = (ratio * 0.45) + (coverage * 0.35) + (content_coverage * 0.20)
    return (
        text_overlap_score,
        ratio,
        coverage,
        left_coverage,
        right_coverage,
        matched_tokens,
        matched_content_tokens,
    )


def find_overlap_candidate(
    left_text: str,
    right_text: str,
    search_head_chars: int,
    min_score: float,
    min_coverage: float,
    min_overlap_tokens: int,
    max_overlap_tokens: int,
) -> tuple[OverlapCandidate, str, str]:
    right_search_text = right_text[: min(len(right_text), search_head_chars)] if search_head_chars > 0 else right_text
    left_tokens_all = tokenize_with_spans(left_text)
    right_tokens_all = tokenize_with_spans(right_search_text)
    if len(left_tokens_all) < min_overlap_tokens:
        raise ValueError("Left overlap window is too short to search for a stable suffix.")
    if len(right_tokens_all) < min_overlap_tokens:
        raise ValueError("Right overlap window is too short to search for a stable prefix.")

    max_left_tokens = min(len(left_tokens_all), max_overlap_tokens)
    max_right_tokens = min(len(right_tokens_all), max_overlap_tokens)
    left_start_min = len(left_tokens_all) - max_left_tokens
    left_start_max = len(left_tokens_all) - min_overlap_tokens

    candidates: list[OverlapCandidate] = []
    for left_start_idx in range(left_start_min, left_start_max + 1):
        left_suffix_tokens = left_tokens_all[left_start_idx:]
        left_normalized = [token.normalized for token in left_suffix_tokens]
        if len(left_normalized) < min_overlap_tokens or len(left_normalized) > max_overlap_tokens:
            continue

        for right_end_idx in range(min_overlap_tokens, max_right_tokens + 1):
            right_prefix_tokens = right_tokens_all[:right_end_idx]
            right_normalized = [token.normalized for token in right_prefix_tokens]
            (
                text_overlap_score,
                ratio,
                coverage,
                left_coverage,
                right_coverage,
                matched_tokens,
                matched_content_tokens,
            ) = score_overlap_candidate(left_normalized, right_normalized)
            content_coverage = matched_content_tokens / max(
                1,
                min(count_content_tokens(left_normalized), count_content_tokens(right_normalized)),
            )
            candidates.append(
                OverlapCandidate(
                    left_start_idx=left_start_idx,
                    right_end_idx=right_end_idx,
                    left_token_count=len(left_normalized),
                    right_token_count=len(right_normalized),
                    text_overlap_score=text_overlap_score,
                    ratio=ratio,
                    coverage=coverage,
                    left_coverage=left_coverage,
                    right_coverage=right_coverage,
                    content_coverage=content_coverage,
                    matched_tokens=matched_tokens,
                    matched_content_tokens=matched_content_tokens,
                    right_cut_char=extend_cut_char(right_search_text, right_prefix_tokens[-1].end),
                )
            )

    if not candidates:
        raise ValueError("No overlap candidates were generated from the overlap windows.")

    high_confidence = [
        candidate
        for candidate in candidates
        if candidate.text_overlap_score >= min_score
        and candidate.coverage >= min_coverage
        and candidate.content_coverage >= min_coverage
        and candidate.matched_content_tokens >= max(4, min_overlap_tokens - 1)
    ]
    pool = high_confidence or candidates
    chosen = max(
        pool,
        key=lambda candidate: (
            candidate.text_overlap_score,
            candidate.coverage,
            candidate.content_coverage,
            candidate.matched_content_tokens,
            -abs(candidate.left_token_count - candidate.right_token_count),
            candidate.left_token_count,
        ),
    )

    left_overlap_suffix_text = extract_token_span_text(
        text=left_text,
        tokens=left_tokens_all,
        start_idx=chosen.left_start_idx,
        end_idx=len(left_tokens_all),
    )
    right_overlap_prefix_text = right_search_text[: chosen.right_cut_char].strip()
    return chosen, left_overlap_suffix_text, right_overlap_prefix_text


def build_window_token_refs(window_segment_entries: list[tuple[int, dict]]) -> list[WindowTokenRef]:
    token_refs: list[WindowTokenRef] = []
    for absolute_segment_index, segment in window_segment_entries:
        for token in tokenize_with_spans(segment["text"]):
            token_refs.append(
                WindowTokenRef(
                    normalized=token.normalized,
                    absolute_segment_index=absolute_segment_index,
                    local_start=token.start,
                    local_end=token.end,
                )
            )
    return token_refs


def split_absolute_segment(segment: dict, cut_char: int, new_id_suffix: str) -> dict | None:
    text = segment["text"]
    cut_char = max(0, min(len(text), cut_char))
    remainder_text = text[cut_char:].strip()
    if not remainder_text:
        return None

    duration = segment["absolute_end"] - segment["absolute_start"]
    ratio = max(0.0, min(1.0, cut_char / max(1, len(text))))
    new_start = round(segment["absolute_start"] + (duration * ratio), 3)
    if new_start >= segment["absolute_end"]:
        return None

    return {
        **segment,
        "text": remainder_text,
        "absolute_start": new_start,
        "duration_seconds": round(max(0.0, segment["absolute_end"] - new_start), 3),
        "original_id": f"{segment.get('original_id') or segment['flat_id']}{new_id_suffix}",
    }


def trim_segments_by_logical_boundary(
    absolute_segments: list[dict],
    logical_start_seconds: float,
) -> tuple[list[dict], dict]:
    kept: list[dict] = []
    trimmed_ids: list[str] = []
    split_segment_id: str | None = None
    cut_segment_id: str | None = None
    cut_char_offset: int | None = None
    removed_prefix_text = ""

    for segment in absolute_segments:
        segment_id = segment.get("original_id") or segment["flat_id"]
        if segment["absolute_end"] <= logical_start_seconds:
            trimmed_ids.append(segment_id)
            removed_prefix_text = append_text_fragment(removed_prefix_text, segment["text"])
            continue
        if segment["absolute_start"] < logical_start_seconds < segment["absolute_end"]:
            ratio = (logical_start_seconds - segment["absolute_start"]) / max(
                0.001,
                segment["absolute_end"] - segment["absolute_start"],
            )
            cut_char_offset = extend_local_cut_char(segment["text"], int(len(segment["text"]) * ratio))
            tail = split_absolute_segment(segment, cut_char_offset, "__logical_tail")
            cut_segment_id = segment_id
            trimmed_ids.append(segment_id)
            removed_prefix_text = append_text_fragment(
                removed_prefix_text,
                segment["text"][:cut_char_offset],
            )
            if tail is not None:
                kept.append(tail)
                split_segment_id = tail["original_id"]
            continue
        kept.append(segment)

    debug = {
        "segment_trim_method": "logical_boundary_fallback",
        "trimmed_right_segment_ids": trimmed_ids,
        "split_right_segment_id": split_segment_id,
        "cut_segment_id": cut_segment_id,
        "cut_char_offset": cut_char_offset,
        "logical_boundary_seconds": round(logical_start_seconds, 3),
        "removed_prefix_text": removed_prefix_text,
    }
    return kept, debug


def trim_segments_by_candidate_boundary(
    absolute_segments: list[dict],
    window_token_refs: list[WindowTokenRef],
    candidate: OverlapCandidate,
    logical_start_seconds: float,
) -> tuple[list[dict], dict]:
    if not window_token_refs or candidate.right_end_idx > len(window_token_refs):
        return trim_segments_by_logical_boundary(absolute_segments, logical_start_seconds)

    boundary_token = window_token_refs[candidate.right_end_idx - 1]
    boundary_segment_index = boundary_token.absolute_segment_index
    boundary_local_end = boundary_token.local_end

    kept: list[dict] = []
    trimmed_ids: list[str] = []
    split_segment_id: str | None = None
    removed_prefix_text = ""
    cut_segment_id: str | None = None
    cut_char_offset: int | None = None

    for segment_index, segment in enumerate(absolute_segments):
        segment_id = segment.get("original_id") or segment["flat_id"]
        if segment_index < boundary_segment_index:
            trimmed_ids.append(segment_id)
            removed_prefix_text = append_text_fragment(removed_prefix_text, segment["text"])
            continue
        if segment_index > boundary_segment_index:
            kept.append(segment)
            continue

        cut_segment_id = segment_id
        cut_char_offset = extend_local_cut_char(segment["text"], boundary_local_end)
        trimmed_ids.append(segment_id)
        removed_prefix_text = append_text_fragment(
            removed_prefix_text,
            segment["text"][:cut_char_offset],
        )
        tail = split_absolute_segment(segment, cut_char_offset, "__overlap_tail")
        if tail is not None:
            kept.append(tail)
            split_segment_id = tail["original_id"]

    debug = {
        "segment_trim_method": "text_cut",
        "trimmed_right_segment_ids": trimmed_ids,
        "split_right_segment_id": split_segment_id,
        "cut_segment_id": cut_segment_id,
        "cut_char_offset": cut_char_offset,
        "logical_boundary_seconds": round(logical_start_seconds, 3),
        "removed_prefix_text": removed_prefix_text,
    }
    return kept, debug


def compute_time_alignment_metrics(
    trimmed_right_segments: list[dict],
    right_part: PartInfo,
    overlap_window_debug: dict,
) -> dict:
    overlap_start_seconds = float(overlap_window_debug["overlap_start_seconds"])
    overlap_end_seconds = float(overlap_window_debug["overlap_end_seconds"])
    overlap_duration_seconds = max(0.001, overlap_end_seconds - overlap_start_seconds)
    logical_boundary_seconds = right_part.logical_start_seconds

    if trimmed_right_segments:
        trim_boundary_seconds = min(segment["absolute_start"] for segment in trimmed_right_segments)
    else:
        trim_boundary_seconds = right_part.extract_end_seconds

    boundary_distance_seconds = trim_boundary_seconds - logical_boundary_seconds
    if overlap_start_seconds <= trim_boundary_seconds <= overlap_end_seconds:
        distance_to_window_seconds = 0.0
        trim_boundary_in_overlap_window = True
    elif trim_boundary_seconds < overlap_start_seconds:
        distance_to_window_seconds = overlap_start_seconds - trim_boundary_seconds
        trim_boundary_in_overlap_window = False
    else:
        distance_to_window_seconds = trim_boundary_seconds - overlap_end_seconds
        trim_boundary_in_overlap_window = False

    time_overlap_score = max(
        0.0,
        1.0 - (abs(boundary_distance_seconds) / overlap_duration_seconds),
    )
    time_window_score = max(
        0.0,
        1.0 - (distance_to_window_seconds / overlap_duration_seconds),
    )
    time_alignment_score = (time_overlap_score * 0.7) + (time_window_score * 0.3)

    return {
        "trim_boundary_seconds": round(trim_boundary_seconds, 3),
        "boundary_distance_seconds": round(boundary_distance_seconds, 3),
        "trim_boundary_in_overlap_window": trim_boundary_in_overlap_window,
        "distance_to_overlap_window_seconds": round(distance_to_window_seconds, 3),
        "time_overlap_score": round(time_overlap_score, 4),
        "time_window_score": round(time_window_score, 4),
        "time_alignment_score": round(time_alignment_score, 4),
    }


def default_output_path(left_path: Path, right_path: Path) -> Path:
    return left_path.parent / (
        f"{compact_stem(left_path)}__{compact_stem(right_path)}.merged.diarized.transcript.json"
    )


def compact_stem(path: Path, max_length: int = 60) -> str:
    stem = path.stem
    for suffix in (".merged.diarized.transcript", ".diarized.transcript"):
        if stem.endswith(suffix):
            stem = stem.removesuffix(suffix)
    stem = re.sub(r"\s+", "_", stem)
    stem = re.sub(r"_+", "_", stem).strip("_")
    if len(stem) <= max_length:
        return stem
    return stem[:max_length].rstrip("_")


def default_debug_output_path(output_path: Path) -> Path:
    name = output_path.name.removesuffix(".json") + ".debug.json"
    return output_path.with_name(name)


def merge_usages(usages: list[dict | None]) -> dict | None:
    normalized = [usage for usage in usages if isinstance(usage, dict)]
    if len(normalized) != len(usages):
        return None

    usage_types = {usage.get("type") for usage in normalized}
    if usage_types == {"tokens"}:
        merged = {
            "type": "tokens",
            "input_tokens": sum(int(usage.get("input_tokens", 0) or 0) for usage in normalized),
            "output_tokens": sum(int(usage.get("output_tokens", 0) or 0) for usage in normalized),
            "total_tokens": sum(int(usage.get("total_tokens", 0) or 0) for usage in normalized),
        }
        audio_tokens = sum(
            int(usage.get("input_token_details", {}).get("audio_tokens", 0) or 0)
            for usage in normalized
        )
        text_tokens = sum(
            int(usage.get("input_token_details", {}).get("text_tokens", 0) or 0)
            for usage in normalized
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
            "seconds": sum(float(usage.get("seconds", 0) or 0) for usage in normalized),
        }

    return None


def main() -> int:
    args = parse_args()

    left_path = resolve_json(args.left)
    right_path = resolve_json(args.right)
    manifest_path = resolve_json(args.manifest)
    output_path = args.output.expanduser().resolve() if args.output else default_output_path(left_path, right_path)
    debug_output_path = (
        args.debug_output.expanduser().resolve() if args.debug_output else default_debug_output_path(output_path)
    )

    manifest_payload, manifest_parts = load_manifest(manifest_path)
    left_payload = load_transcript_payload(left_path)
    right_payload = load_transcript_payload(right_path)

    left_part = infer_part(left_payload, manifest_parts)
    right_part = infer_part(right_payload, manifest_parts)
    if right_part is None:
        raise ValueError("Right transcript could not be matched to a manifest part.")

    left_absolute_segments, left_base_start = flatten_payload_segments(left_payload, left_part)
    right_absolute_segments, right_base_start = flatten_payload_segments(right_payload, right_part)

    left_text = stitch_segment_text(left_absolute_segments)
    right_text = stitch_segment_text(right_absolute_segments)
    (
        left_overlap_window_text,
        right_overlap_window_text,
        right_window_entries,
        overlap_window_debug,
    ) = build_overlap_window(
        left_absolute_segments=left_absolute_segments,
        right_absolute_segments=right_absolute_segments,
        right_part=right_part,
    )

    candidate, left_overlap_suffix_text, right_overlap_prefix_text = find_overlap_candidate(
        left_text=left_overlap_window_text or left_text,
        right_text=right_overlap_window_text or right_text,
        search_head_chars=args.search_head_chars,
        min_score=args.min_score,
        min_coverage=args.min_coverage,
        min_overlap_tokens=args.min_overlap_tokens,
        max_overlap_tokens=args.max_overlap_tokens,
    )

    text_high_confidence = (
        candidate.text_overlap_score >= args.min_score
        and candidate.coverage >= args.min_coverage
        and candidate.content_coverage >= args.min_coverage
    )
    if text_high_confidence:
        trimmed_right_segments, trim_debug = trim_segments_by_candidate_boundary(
            absolute_segments=right_absolute_segments,
            window_token_refs=build_window_token_refs(right_window_entries),
            candidate=candidate,
            logical_start_seconds=right_part.logical_start_seconds,
        )
    else:
        trimmed_right_segments, trim_debug = trim_segments_by_logical_boundary(
            absolute_segments=right_absolute_segments,
            logical_start_seconds=right_part.logical_start_seconds,
        )

    time_alignment = compute_time_alignment_metrics(
        trimmed_right_segments=trimmed_right_segments,
        right_part=right_part,
        overlap_window_debug=overlap_window_debug,
    )
    if text_high_confidence and not time_alignment["trim_boundary_in_overlap_window"]:
        trimmed_right_segments, trim_debug = trim_segments_by_logical_boundary(
            absolute_segments=right_absolute_segments,
            logical_start_seconds=right_part.logical_start_seconds,
        )
        time_alignment = compute_time_alignment_metrics(
            trimmed_right_segments=trimmed_right_segments,
            right_part=right_part,
            overlap_window_debug=overlap_window_debug,
        )

    right_removed_text = trim_debug.get("removed_prefix_text", "") or right_overlap_prefix_text
    right_remainder_text = stitch_segment_text(trimmed_right_segments)
    merged_text = stitch_merged_text(left_text, right_remainder_text)

    left_label = left_part.label if left_part is not None else left_path.stem
    right_label = right_part.label

    left_chunk = build_canonical_chunk(
        label=left_label,
        source_payload_file=left_path,
        source_part_label=left_part.label if left_part is not None else None,
        base_start_seconds=left_base_start,
        absolute_segments=left_absolute_segments,
    )
    right_chunk = build_canonical_chunk(
        label=right_label,
        source_payload_file=right_path,
        source_part_label=right_part.label,
        base_start_seconds=right_base_start,
        absolute_segments=trimmed_right_segments,
    )

    chunks = [left_chunk]
    if right_chunk["segments"]:
        chunks.append(right_chunk)

    for index, chunk in enumerate(chunks, start=1):
        chunk["index"] = index

    merged_duration_seconds = round(
        max((float(chunk["end_seconds"]) for chunk in chunks), default=0.0),
        3,
    )
    result = {
        "source_file": str(Path(str(manifest_payload.get("source_file", ""))).expanduser().resolve()),
        "source_file_name": str(manifest_payload.get("source_file_name", "")),
        "split_manifest_file": str(manifest_path),
        "left_file": str(left_path),
        "right_file": str(right_path),
        "model": right_payload.get("model") or left_payload.get("model"),
        "language": right_payload.get("language") or left_payload.get("language"),
        "response_format": right_payload.get("response_format") or left_payload.get("response_format"),
        "chunk_time_basis": "absolute",
        "duration_seconds": merged_duration_seconds,
        "text": merged_text,
        "chunks": chunks,
        "merge": {
            "anchor_text": left_overlap_suffix_text,
            "matched_text": right_overlap_prefix_text,
            "left_overlap_suffix_text": left_overlap_suffix_text,
            "right_overlap_prefix_text": right_overlap_prefix_text,
            "right_overlap_text": right_removed_text,
            "right_removed_text": right_removed_text,
            "right_remainder_text": right_remainder_text,
            "score": round(candidate.text_overlap_score, 4),
            "text_overlap_score": round(candidate.text_overlap_score, 4),
            "ratio": round(candidate.ratio, 4),
            "coverage": round(candidate.coverage, 4),
            "left_coverage": round(candidate.left_coverage, 4),
            "right_coverage": round(candidate.right_coverage, 4),
            "content_coverage": round(candidate.content_coverage, 4),
            "matched_tokens": candidate.matched_tokens,
            "matched_content_tokens": candidate.matched_content_tokens,
            "left_token_count": candidate.left_token_count,
            "right_token_count": candidate.right_token_count,
            "cut_char": candidate.right_cut_char,
            "text_high_confidence": text_high_confidence,
            "used_text_boundary": trim_debug["segment_trim_method"] == "text_cut",
            **trim_debug,
            **time_alignment,
        },
    }

    merged_usage = merge_usages([left_payload.get("usage"), right_payload.get("usage")])
    if merged_usage is not None:
        result["usage"] = merged_usage

    debug_payload = {
        "left_file": str(left_path),
        "right_file": str(right_path),
        "split_manifest_file": str(manifest_path),
        "anchor_text": left_overlap_suffix_text,
        "matched_text": right_overlap_prefix_text,
        "left_overlap_suffix_text": left_overlap_suffix_text,
        "right_overlap_prefix_text": right_overlap_prefix_text,
        "right_removed_text": right_removed_text,
        "right_remainder_text": right_remainder_text,
        "candidate": {
            "score": round(candidate.text_overlap_score, 4),
            "text_overlap_score": round(candidate.text_overlap_score, 4),
            "ratio": round(candidate.ratio, 4),
            "coverage": round(candidate.coverage, 4),
            "left_coverage": round(candidate.left_coverage, 4),
            "right_coverage": round(candidate.right_coverage, 4),
            "content_coverage": round(candidate.content_coverage, 4),
            "matched_tokens": candidate.matched_tokens,
            "matched_content_tokens": candidate.matched_content_tokens,
            "left_token_count": candidate.left_token_count,
            "right_token_count": candidate.right_token_count,
            "right_cut_char": candidate.right_cut_char,
            "text_high_confidence": text_high_confidence,
        },
        "trim": trim_debug,
        "time_alignment": time_alignment,
        "left_chunk_label": left_label,
        "right_chunk_label": right_label,
        "right_logical_start_seconds": round(right_part.logical_start_seconds, 3),
        "right_extract_start_seconds": round(right_part.extract_start_seconds, 3),
        "left_segment_count": len(left_absolute_segments),
        "right_segment_count_before_trim": len(right_absolute_segments),
        "right_segment_count_after_trim": len(trimmed_right_segments),
        "overlap_window": overlap_window_debug,
        "overlap_window_texts": {
            "left": left_overlap_window_text,
            "right": right_overlap_window_text,
        },
        "used_time_window_for_search": bool(right_overlap_window_text),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    debug_output_path.write_text(json.dumps(debug_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Left overlap suffix: {left_overlap_suffix_text}", flush=True)
    print(f"Right overlap prefix: {right_overlap_prefix_text}", flush=True)
    print(
        "Text overlap score: "
        f"{candidate.text_overlap_score:.4f} "
        f"(ratio={candidate.ratio:.4f}, coverage={candidate.coverage:.4f}, "
        f"left={candidate.left_coverage:.4f}, right={candidate.right_coverage:.4f})",
        flush=True,
    )
    print(
        "Time alignment score: "
        f"{time_alignment['time_alignment_score']:.4f} "
        f"(boundary={time_alignment['time_overlap_score']:.4f}, window={time_alignment['time_window_score']:.4f})",
        flush=True,
    )
    print(f"Segment trim method: {trim_debug['segment_trim_method']}", flush=True)
    print(f"Saved merged diarized transcript to: {output_path}", flush=True)
    print(f"Saved merge debug output to: {debug_output_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
