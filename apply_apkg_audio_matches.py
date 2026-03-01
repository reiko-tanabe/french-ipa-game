#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


DEFAULT_DATA = Path("data/ipa_words.json")
DEFAULT_MATCHES = Path("audio/apkg_audio_matches.json")
DEFAULT_OUTPUT = Path("data/ipa_words.json")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Apply extracted APKG audio matches to ipa_words.json."
    )
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="Path to ipa_words.json")
    parser.add_argument(
        "--matches",
        type=Path,
        default=DEFAULT_MATCHES,
        help="Path to audio/apkg_audio_matches.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output path for updated JSON",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing audio.file values if present",
    )
    return parser.parse_args()


def load_payload(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{path} must contain a JSON array")
    return payload


def extract_records(payload):
    data = payload
    while isinstance(data, list) and data and isinstance(data[0], list):
        data = data[0]
    if not isinstance(data, list):
        raise SystemExit("Expected a flat array or a nested single array in ipa_words.json")
    return data


def main():
    args = parse_args()
    payload = load_payload(args.data)
    records = extract_records(payload)
    matches_payload = json.loads(args.matches.read_text(encoding="utf-8"))

    by_word = {}
    for item in matches_payload.get("matched", []):
      word = item.get("word")
      audio_file = item.get("audio_file")
      if isinstance(word, str) and isinstance(audio_file, str):
            by_word[word] = audio_file

    applied = 0
    skipped = 0
    missing = 0

    for record in records:
        word = record.get("word")
        if word not in by_word:
            missing += 1
            continue

        audio = record.get("audio")
        if isinstance(audio, dict):
            if audio.get("file") and not args.overwrite:
                skipped += 1
                continue
        else:
            audio = {}
            record["audio"] = audio

        audio["file"] = by_word[word]
        applied += 1

    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote: {args.output}")
    print(f"Applied audio files: {applied}")
    print(f"Skipped existing audio: {skipped}")
    print(f"No match found: {missing}")


if __name__ == "__main__":
    main()
