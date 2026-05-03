#!/usr/bin/env python3
"""Merge overlapping transcript chunks by finding the last sentence(s) of A in the head of B."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path


TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?")
SENTENCE_RE = re.compile(r"[^.!?]+(?:[.!?]+|$)", re.DOTALL)
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
class SentenceSpan:
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class Token:
    normalized: str
    start: int
    end: int


@dataclass(frozen=True)
class MatchCandidate:
    start_idx: int
    end_idx: int
    score: float
    ratio: float
    coverage: float
    matched_content_tokens: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge two overlapping transcript JSON files by trimming the duplicate prefix from B.",
    )
    parser.add_argument("left", type=Path, help="Earlier transcript JSON path.")
    parser.add_argument("right", type=Path, help="Later transcript JSON path.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output JSON path. Defaults to poc/output/<left>__<right>.merged.json",
    )
    parser.add_argument(
        "--max-anchor-sentences",
        type=int,
        default=3,
        help="How many trailing sentences from A may be used as the anchor.",
    )
    parser.add_argument(
        "--min-anchor-tokens",
        type=int,
        default=8,
        help="Minimum normalized token count for the anchor before stopping sentence expansion.",
    )
    parser.add_argument(
        "--search-head-chars",
        type=int,
        default=4000,
        help="Only the first N characters of B are searched for the anchor.",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.72,
        help="Minimum fuzzy score for accepting a candidate as high confidence.",
    )
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=0.6,
        help="Minimum token coverage for accepting a candidate as high confidence.",
    )
    return parser.parse_args()


def load_transcript_text(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"Transcript text was not found in: {path}")
    return text.strip()


def sentence_spans(text: str) -> list[SentenceSpan]:
    spans: list[SentenceSpan] = []
    for match in SENTENCE_RE.finditer(text):
        sentence = match.group().strip()
        if not sentence:
            continue
        spans.append(SentenceSpan(text=sentence, start=match.start(), end=match.end()))
    return spans


def normalize_token(token_text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", token_text.lower())


def tokenize_with_spans(text: str) -> list[Token]:
    tokens: list[Token] = []
    for match in TOKEN_RE.finditer(text):
        normalized = normalize_token(match.group())
        if normalized:
            tokens.append(Token(normalized=normalized, start=match.start(), end=match.end()))
    return tokens


def count_content_tokens(tokens: list[Token]) -> int:
    return sum(token.normalized not in FILLER_TOKENS for token in tokens)


def pick_anchor(left_text: str, max_sentences: int, min_anchor_tokens: int) -> tuple[str, list[SentenceSpan]]:
    spans = sentence_spans(left_text)
    if not spans:
        fallback = left_text[-300:].strip()
        return fallback, [SentenceSpan(text=fallback, start=max(0, len(left_text) - len(fallback)), end=len(left_text))]

    chosen: list[SentenceSpan] = []
    chosen_tokens = 0
    chosen_content_tokens = 0

    for span in reversed(spans):
        chosen.append(span)
        sentence_tokens = tokenize_with_spans(span.text)
        chosen_tokens += len(sentence_tokens)
        chosen_content_tokens += count_content_tokens(sentence_tokens)
        if len(chosen) >= max_sentences:
            break
        if chosen_tokens >= min_anchor_tokens and chosen_content_tokens >= max(4, min_anchor_tokens // 2):
            break

    chosen.reverse()
    anchor_text = " ".join(span.text for span in chosen).strip()
    return anchor_text, chosen


def score_candidate(anchor_tokens: list[str], window_tokens: list[str]) -> tuple[float, float, float, int]:
    matcher = SequenceMatcher(None, anchor_tokens, window_tokens, autojunk=False)
    ratio = matcher.ratio()
    matched_tokens = 0
    matched_content_tokens = 0

    for block in matcher.get_matching_blocks():
        if not block.size:
            continue
        matched_tokens += block.size
        matched_content_tokens += sum(
            token not in FILLER_TOKENS for token in anchor_tokens[block.a : block.a + block.size]
        )

    coverage = matched_tokens / max(1, len(anchor_tokens))
    score = (ratio * 0.6) + (coverage * 0.4)
    return score, ratio, coverage, matched_content_tokens


def find_overlap_candidate(
    anchor_text: str,
    right_text: str,
    search_head_chars: int,
    min_score: float,
    min_coverage: float,
) -> tuple[MatchCandidate, str, list[Token]]:
    right_head = right_text[: min(len(right_text), search_head_chars)]
    right_tokens = tokenize_with_spans(right_head)
    anchor_tokens = tokenize_with_spans(anchor_text)
    anchor_normalized = [token.normalized for token in anchor_tokens]
    if not anchor_normalized:
        raise ValueError("Anchor text did not contain searchable tokens.")

    min_window = max(3, len(anchor_normalized) - 4)
    max_window = min(len(right_tokens), len(anchor_normalized) + 8)
    if min_window > max_window:
        raise ValueError("Right transcript is too short to search for the anchor.")

    candidates: list[MatchCandidate] = []
    for start_idx in range(len(right_tokens)):
        remaining = len(right_tokens) - start_idx
        if remaining < min_window:
            break

        for window_size in range(min_window, min(max_window, remaining) + 1):
            window = right_tokens[start_idx : start_idx + window_size]
            score, ratio, coverage, matched_content_tokens = score_candidate(
                anchor_normalized,
                [token.normalized for token in window],
            )
            candidates.append(
                MatchCandidate(
                    start_idx=start_idx,
                    end_idx=start_idx + window_size,
                    score=score,
                    ratio=ratio,
                    coverage=coverage,
                    matched_content_tokens=matched_content_tokens,
                )
            )

    high_confidence = [
        candidate
        for candidate in candidates
        if candidate.score >= min_score
        and candidate.coverage >= min_coverage
        and candidate.matched_content_tokens >= max(4, len(anchor_normalized) // 3)
    ]
    if high_confidence:
        chosen = min(
            high_confidence,
            key=lambda candidate: (
                candidate.start_idx,
                -candidate.score,
                -candidate.coverage,
                candidate.end_idx - candidate.start_idx,
            ),
        )
    else:
        chosen = max(
            candidates,
            key=lambda candidate: (
                candidate.score,
                candidate.coverage,
                candidate.matched_content_tokens,
                -candidate.start_idx,
            ),
        )

    matched_text = right_head[right_tokens[chosen.start_idx].start : right_tokens[chosen.end_idx - 1].end]
    return chosen, matched_text, right_tokens


def stitch_text(left_text: str, right_remainder: str) -> str:
    left = left_text.rstrip()
    right = right_remainder.lstrip()
    if not right:
        return left
    separator = "" if left.endswith((" ", "\n")) or right.startswith((".", ",", "!", "?")) else " "
    return f"{left}{separator}{right}"


def extend_cut_char(text: str, cut_char: int) -> int:
    while cut_char < len(text) and text[cut_char] in "\"'”)]}":
        cut_char += 1
    while cut_char < len(text) and text[cut_char] in ".!?":
        cut_char += 1
    while cut_char < len(text) and text[cut_char].isspace():
        cut_char += 1
    return cut_char


def default_output_path(left_path: Path, right_path: Path) -> Path:
    output_dir = left_path.parent
    return output_dir / f"{left_path.stem}__{right_path.stem}.merged.json"


def main() -> int:
    args = parse_args()
    left_text = load_transcript_text(args.left)
    right_text = load_transcript_text(args.right)

    anchor_text, anchor_spans = pick_anchor(
        left_text=left_text,
        max_sentences=args.max_anchor_sentences,
        min_anchor_tokens=args.min_anchor_tokens,
    )
    candidate, matched_text, right_tokens = find_overlap_candidate(
        anchor_text=anchor_text,
        right_text=right_text,
        search_head_chars=args.search_head_chars,
        min_score=args.min_score,
        min_coverage=args.min_coverage,
    )

    cut_char = extend_cut_char(right_text, right_tokens[candidate.end_idx - 1].end)
    right_overlap_text = right_text[:cut_char].strip()
    right_remainder = right_text[cut_char:]
    merged_text = stitch_text(left_text, right_remainder)

    result = {
        "left_file": str(args.left),
        "right_file": str(args.right),
        "anchor_text": anchor_text,
        "anchor_sentence_count": len(anchor_spans),
        "matched_text": matched_text,
        "right_overlap_text": right_overlap_text,
        "right_remainder_text": right_remainder.lstrip(),
        "merged_text": merged_text,
        "match": {
            "score": round(candidate.score, 4),
            "ratio": round(candidate.ratio, 4),
            "coverage": round(candidate.coverage, 4),
            "matched_content_tokens": candidate.matched_content_tokens,
            "cut_char": cut_char,
            "high_confidence": candidate.score >= args.min_score and candidate.coverage >= args.min_coverage,
        },
    }

    output_path = args.output or default_output_path(args.left, args.right)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Anchor: {anchor_text}")
    print(f"Matched: {matched_text}")
    print(f"Score: {candidate.score:.4f} (ratio={candidate.ratio:.4f}, coverage={candidate.coverage:.4f})")
    print(f"Overlap prefix chars removed from right: {cut_char}")
    print(f"Saved merged result to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
