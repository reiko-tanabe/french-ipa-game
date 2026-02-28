#!/usr/bin/env python3
# Legacy WhisperX alignment workflow retained for future use.
import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path


DATA_PATH = Path("data/ipa_words.json")
DEFAULT_OUTPUT_PATH = Path("data/ipa_words.whisperx.updated.json")


@dataclass
class SplitSpec:
    audio_file: str
    start_index: int
    end_index: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate per-word timestamps for ipa_words.json using WhisperX."
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=DATA_PATH,
        help="Path to ipa_words.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Output JSON path. Original data is not modified.",
    )
    parser.add_argument(
        "--split",
        action="append",
        required=True,
        help=(
            "Audio/file to record range mapping in the form "
            "'audio/COA1_API_P1.mp3:0:49'. Index range is inclusive and 0-based."
        ),
    )
    parser.add_argument(
        "--language",
        default="fr",
        help="WhisperX language code. Default: fr",
    )
    parser.add_argument(
        "--model",
        default="large-v2",
        help="WhisperX ASR model name. Default: large-v2",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        help="WhisperX device. Example: cpu, cuda",
    )
    parser.add_argument(
        "--compute-type",
        default="int8",
        help="WhisperX compute type. Example: int8, float16",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="WhisperX transcription batch size.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing audio start/end values.",
    )
    return parser.parse_args()


def load_json_payload(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must be a JSON array")
    return payload


def extract_records(payload):
    data = payload
    while isinstance(data, list) and data and isinstance(data[0], list):
        data = data[0]
    if not isinstance(data, list):
        raise ValueError("Expected a flat array or nested single array")
    return data


def parse_split(raw: str) -> SplitSpec:
    audio_file, start_text, end_text = raw.rsplit(":", 2)
    start_index = int(start_text)
    end_index = int(end_text)
    if start_index < 0 or end_index < start_index:
      raise ValueError(f"Invalid split range: {raw}")
    return SplitSpec(audio_file=audio_file, start_index=start_index, end_index=end_index)


def normalize_word(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z]+", "", text)
    return text.lower()


def import_whisperx():
    try:
        import whisperx  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "whisperx is not installed. Install it first, e.g. `pip install whisperx`.\n"
            "Reference: https://github.com/m-bain/whisperX"
        ) from exc
    return whisperx


def build_word_timeline(segments):
    timeline = []
    for segment in segments:
        for word in segment.get("words", []):
            token = word.get("word", "").strip()
            start = word.get("start")
            end = word.get("end")
            normalized = normalize_word(token)
            if not normalized:
                continue
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                continue
            timeline.append(
                {
                    "raw": token,
                    "normalized": normalized,
                    "start": round(float(start), 2),
                    "end": round(float(end), 2),
                }
            )
    return timeline


def assign_split_words(records, split: SplitSpec, timeline, overwrite: bool):
    timeline_index = 0
    assigned = 0
    unresolved = []

    for record_index in range(split.start_index, split.end_index + 1):
        record = records[record_index]
        expected = normalize_word(record.get("word", ""))
        if not expected:
            unresolved.append((record_index, record.get("word", ""), "empty_expected_word"))
            continue

        audio_meta = record.get("audio")
        if (
            not overwrite
            and isinstance(audio_meta, dict)
            and isinstance(audio_meta.get("start"), (int, float))
            and isinstance(audio_meta.get("end"), (int, float))
        ):
            continue

        found = None
        while timeline_index < len(timeline):
            candidate = timeline[timeline_index]
            timeline_index += 1
            if candidate["normalized"] == expected:
                found = candidate
                break

        if not found:
            unresolved.append((record_index, record.get("word", ""), "not_found_in_transcript"))
            continue

        if not isinstance(record.get("audio"), dict):
            record["audio"] = {}
        record["audio"]["file"] = split.audio_file
        record["audio"]["start"] = found["start"]
        record["audio"]["end"] = found["end"]
        assigned += 1

    return assigned, unresolved


def transcribe_and_align(whisperx, audio_file: str, args: argparse.Namespace):
    model = whisperx.load_model(
        args.model,
        args.device,
        compute_type=args.compute_type,
        language=args.language,
    )
    audio = whisperx.load_audio(audio_file)
    result = model.transcribe(audio, batch_size=args.batch_size, language=args.language)
    model_a, metadata = whisperx.load_align_model(
        language_code=args.language,
        device=args.device,
    )
    aligned = whisperx.align(
        result["segments"],
        model_a,
        metadata,
        audio,
        args.device,
        return_char_alignments=False,
    )
    return aligned


def main() -> int:
    args = parse_args()
    payload = load_json_payload(args.data)
    records = extract_records(payload)
    splits = [parse_split(raw) for raw in args.split]

    total_records = len(records)
    covered = set()
    for split in splits:
        if split.end_index >= total_records:
            raise SystemExit(
                f"Split {split.audio_file}:{split.start_index}:{split.end_index} "
                f"exceeds record count {total_records}"
            )
        for idx in range(split.start_index, split.end_index + 1):
            if idx in covered:
                raise SystemExit(f"Record index {idx} is covered by multiple splits")
            covered.add(idx)

    whisperx = import_whisperx()

    total_assigned = 0
    total_unresolved = []

    for split in splits:
        print(
            f"Processing {split.audio_file} for records "
            f"{split.start_index}..{split.end_index}",
            file=sys.stderr,
        )
        aligned = transcribe_and_align(whisperx, split.audio_file, args)
        timeline = build_word_timeline(aligned.get("segments", []))
        assigned, unresolved = assign_split_words(records, split, timeline, args.overwrite)
        total_assigned += assigned
        total_unresolved.extend(unresolved)
        print(
            f"  timeline words={len(timeline)} assigned={assigned} unresolved={len(unresolved)}",
            file=sys.stderr,
        )

    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote: {args.output}")
    print(f"Assigned timestamps: {total_assigned}")
    print(f"Unresolved items: {len(total_unresolved)}")
    if total_unresolved:
        print("First unresolved items:")
        for index, word, reason in total_unresolved[:20]:
            print(f"- record[{index}] {word}: {reason}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
