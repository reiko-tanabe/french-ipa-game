#!/usr/bin/env python3
import argparse
import html
import json
import re
import shutil
import sqlite3
import tempfile
import unicodedata
import zipfile
from pathlib import Path


DEFAULT_APKG = Path("audio/5000_most_frequently_used_French_words_v_60.apkg")
DEFAULT_DATA = Path("data/ipa_words.json")
DEFAULT_OUTPUT_DIR = Path("audio/apkg_extracted")
DEFAULT_REPORT = Path("audio/apkg_audio_matches.json")

SOUND_RE = re.compile(r"\[sound:([^\]]+)\]")
TAG_RE = re.compile(r"<[^>]+>")
ARTICLES = (
    "le ",
    "la ",
    "les ",
    "un ",
    "une ",
    "des ",
    "l'",
    "d'",
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract matching audio files from an Anki .apkg for ipa_words.json."
    )
    parser.add_argument("--apkg", type=Path, default=DEFAULT_APKG, help="Path to .apkg file")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="Path to ipa_words.json")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for extracted audio files",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help="JSON report path for matched audio mappings",
    )
    return parser.parse_args()


def normalize_text(text: str) -> str:
    text = html.unescape(str(text))
    text = TAG_RE.sub(" ", text)
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'")
    text = re.sub(r"[^a-z0-9' -]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for article in ARTICLES:
        if text.startswith(article):
            text = text[len(article) :].strip()
            break
    return text


def load_records(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    data = payload
    while isinstance(data, list) and data and isinstance(data[0], list):
        data = data[0]
    if not isinstance(data, list):
        raise SystemExit(f"{path} must contain a JSON array")
    return data


def load_models(conn: sqlite3.Connection):
    raw = conn.execute("select models from col").fetchone()[0]
    return json.loads(raw)


def extract_note_candidates(field_names, fields):
    candidates = set()
    audio_files = []
    for name, value in zip(field_names, fields):
        lowered_name = name.lower()
        if "audio" in lowered_name:
            audio_files.extend(SOUND_RE.findall(value))
        if lowered_name in {"word", "word with article"}:
            normalized = normalize_text(value)
            if normalized:
                candidates.add(normalized)
    for value in fields:
      for sound in SOUND_RE.findall(value):
            audio_files.append(sound)
    return candidates, audio_files


def choose_audio(field_names, fields):
    preferred_names = [
        "Parisian French Audio (Voice 1)",
        "audio",
        "audio 1",
    ]
    by_name = {name: value for name, value in zip(field_names, fields)}
    for preferred in preferred_names:
        value = by_name.get(preferred)
        if value:
            sounds = SOUND_RE.findall(value)
            if sounds:
                return sounds[0]
    for value in fields:
        sounds = SOUND_RE.findall(value)
        if sounds:
            return sounds[0]
    return None


def build_audio_index(conn: sqlite3.Connection):
    models = load_models(conn)
    index = {}
    query = "select mid, flds from notes"
    for mid, raw_fields in conn.execute(query):
        model = models.get(str(mid))
        if not model:
            continue
        field_names = [field["name"] for field in model.get("flds", [])]
        fields = raw_fields.split("\x1f")
        candidates, _ = extract_note_candidates(field_names, fields)
        chosen_audio = choose_audio(field_names, fields)
        if not candidates or not chosen_audio:
            continue
        entry = {
            "model": model.get("name", ""),
            "audio": chosen_audio,
            "fields": field_names,
        }
        for candidate in candidates:
            index.setdefault(candidate, []).append(entry)
    return index


def choose_best_entry(entries):
    priorities = [
        "5000 French Words 2.0 (F to E)",
        "5000 French Words 2.0 (E to F) C",
        "French aspirated h",
    ]
    def score(entry):
        try:
            return priorities.index(entry["model"])
        except ValueError:
            return len(priorities)
    return sorted(entries, key=score)[0]


def safe_filename(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized).strip("_")
    return normalized or "audio"


def main():
    args = parse_args()
    records = load_records(args.data)
    if not args.apkg.exists():
        raise SystemExit(f"APKG not found: {args.apkg}")

    matched = []
    unmatched = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        with zipfile.ZipFile(args.apkg) as zf:
            zf.extract("collection.anki2", tmp)
            media_map = json.loads(zf.read("media"))
            conn = sqlite3.connect(tmp / "collection.anki2")
            audio_index = build_audio_index(conn)

            args.output_dir.mkdir(parents=True, exist_ok=True)
            extracted_files = {}

            for record in records:
                word = str(record.get("word", "")).strip()
                normalized = normalize_text(word)
                entries = audio_index.get(normalized, [])
                if not entries:
                    unmatched.append(word)
                    continue

                entry = choose_best_entry(entries)
                media_name = entry["audio"]
                media_key = None
                for key, value in media_map.items():
                    if value == media_name:
                        media_key = key
                        break

                if media_key is None:
                    unmatched.append(word)
                    continue

                if media_name not in extracted_files:
                    suffix = Path(media_name).suffix or ".mp3"
                    dest_name = f"{safe_filename(word)}{suffix}"
                    dest = args.output_dir / dest_name
                    with zf.open(media_key) as source, open(dest, "wb") as target:
                        shutil.copyfileobj(source, target)
                    extracted_files[media_name] = dest.as_posix()

                matched.append(
                    {
                        "word": word,
                        "ipa": record.get("ipa", ""),
                        "audio_source": media_name,
                        "audio_file": extracted_files[media_name],
                        "model": entry["model"],
                    }
                )

    report = {
        "apkg": args.apkg.as_posix(),
        "matched_count": len(matched),
        "unmatched_count": len(unmatched),
        "matched": matched,
        "unmatched": unmatched,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote report: {args.report}")
    print(f"Matched: {len(matched)}")
    print(f"Unmatched: {len(unmatched)}")
    if unmatched:
        print("First unmatched words:")
        for word in unmatched[:20]:
            print(f"- {word}")


if __name__ == "__main__":
    main()
