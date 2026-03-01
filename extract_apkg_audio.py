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
TOKEN_SPLIT_RE = re.compile(r"[,;/|]+")
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
DIRECT_WORD_FIELDS = {
    "word",
    "word with article",
    "word with declinations",
    "1",
}
REFERENCE_ONLY_FIELDS = {
    "sample words",
    "sample words (table)",
}


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
    parser.add_argument(
        "--compare-only",
        action="store_true",
        help="Only compare ipa_words.json against the .apkg and write a report without extracting audio",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Allow partial matches during extraction. Keep this off unless the compare report has been reviewed.",
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


def add_candidate(candidate_map, text, source, priority):
    normalized = normalize_text(text)
    if not normalized:
        return
    existing = candidate_map.get(normalized)
    if existing is None or priority < existing["priority"]:
        candidate_map[normalized] = {"source": source, "priority": priority}


def extract_text_fragments(value: str):
    plain = html.unescape(TAG_RE.sub(" ", str(value)))
    fragments = {plain}
    for part in TOKEN_SPLIT_RE.split(plain):
        part = part.strip()
        if part:
            fragments.add(part)
    for line in plain.splitlines():
        line = line.strip()
        if line:
            fragments.add(line)
    return fragments


def extract_note_candidates(field_names, fields):
    direct_map = {}
    reference_map = {}
    audio_files = []
    fallback_text_fields = {
        "front",
        "back",
        "2",
        "3",
        "4",
        "translation",
        "meaning",
        "example sentences",
        "example sentences without translation",
    }

    for name, value in zip(field_names, fields):
        lowered_name = name.lower()
        if "audio" in lowered_name:
            audio_files.extend(SOUND_RE.findall(value))

        fragments = extract_text_fragments(value)
        if lowered_name in DIRECT_WORD_FIELDS:
            for fragment in fragments:
                add_candidate(direct_map, fragment, lowered_name, 0)
        elif lowered_name in REFERENCE_ONLY_FIELDS:
            for fragment in fragments:
                add_candidate(reference_map, fragment, lowered_name, 0)
        elif lowered_name in fallback_text_fields:
            for fragment in fragments:
                add_candidate(reference_map, fragment, lowered_name, 1)

    for value in fields:
        for sound in SOUND_RE.findall(value):
            audio_files.append(sound)

    return direct_map, reference_map, audio_files


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
    direct_index = {}
    reference_index = {}
    query = "select mid, flds from notes"
    for mid, raw_fields in conn.execute(query):
        model = models.get(str(mid))
        if not model:
            continue
        field_names = [field["name"] for field in model.get("flds", [])]
        fields = raw_fields.split("\x1f")
        direct_candidates, reference_candidates, _ = extract_note_candidates(field_names, fields)
        chosen_audio = choose_audio(field_names, fields)

        for candidate, candidate_meta in reference_candidates.items():
            entry = {
                "model": model.get("name", ""),
                "audio": chosen_audio,
                "fields": field_names,
                "candidate": candidate,
                "candidate_source": candidate_meta["source"],
                "candidate_priority": candidate_meta["priority"],
            }
            reference_index.setdefault(candidate, []).append(entry)

        if not direct_candidates or not chosen_audio:
            continue
        for candidate, candidate_meta in direct_candidates.items():
            entry = {
                "model": model.get("name", ""),
                "audio": chosen_audio,
                "fields": field_names,
                "candidate": candidate,
                "candidate_source": candidate_meta["source"],
                "candidate_priority": candidate_meta["priority"],
            }
            direct_index.setdefault(candidate, []).append(entry)
    return direct_index, reference_index


def choose_best_entry(entries):
    priorities = [
        "5000 French Words 2.0 (F to E)",
        "5000 French Words 2.0 (E to F) C",
        "French aspirated h",
    ]
    def score(entry):
        try:
            model_score = priorities.index(entry["model"])
        except ValueError:
            model_score = len(priorities)
        return (entry.get("candidate_priority", 99), model_score)
    return sorted(entries, key=score)[0]


def safe_filename(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized).strip("_")
    return normalized or "audio"


def find_entries(audio_index, word: str):
    normalized = normalize_text(word)
    exact_entries = audio_index.get(normalized, [])
    if exact_entries:
        return exact_entries, "exact"

    partial_entries = []
    word_tokens = [token for token in normalized.split() if len(token) >= 4]
    for candidate, entries in audio_index.items():
        if normalized and normalized in candidate:
            partial_entries.extend(entries)
            continue
        if candidate and candidate in normalized:
            partial_entries.extend(entries)
            continue
        if word_tokens and any(token in candidate for token in word_tokens):
            partial_entries.extend(entries)

    deduped = []
    seen = set()
    for entry in partial_entries:
        key = (entry["audio"], entry["model"], entry["candidate"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    if deduped:
        return deduped, "partial"
    return [], "unmatched"


def build_compare_item(record, entries, match_type):
    best = choose_best_entry(entries)
    return {
        "word": str(record.get("word", "")).strip(),
        "ipa": record.get("ipa", ""),
        "match_type": match_type,
        "candidate": best.get("candidate", ""),
        "candidate_source": best.get("candidate_source", ""),
        "audio_source": best.get("audio", ""),
        "model": best.get("model", ""),
        "alternatives": [
            {
                "candidate": entry.get("candidate", ""),
                "candidate_source": entry.get("candidate_source", ""),
                "audio_source": entry.get("audio", ""),
                "model": entry.get("model", ""),
            }
            for entry in entries[:5]
        ],
    }


def build_reference_item(record, entries):
    return build_compare_item(record, entries, "reference_only")


def main():
    args = parse_args()
    records = load_records(args.data)
    if not args.apkg.exists():
        raise SystemExit(f"APKG not found: {args.apkg}")

    matched = []
    unmatched = []
    partial_candidates = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        with zipfile.ZipFile(args.apkg) as zf:
            zf.extract("collection.anki2", tmp)
            media_map = json.loads(zf.read("media"))
            conn = sqlite3.connect(tmp / "collection.anki2")
            audio_index, reference_index = build_audio_index(conn)

            args.output_dir.mkdir(parents=True, exist_ok=True)
            extracted_files = {}

            for record in records:
                word = str(record.get("word", "")).strip()
                entries, match_type = find_entries(audio_index, word)
                if not entries:
                    reference_entries, _ = find_entries(reference_index, word)
                    if reference_entries:
                        partial_candidates.append(build_reference_item(record, reference_entries))
                    unmatched.append({"word": word, "ipa": record.get("ipa", "")})
                    continue

                if match_type == "partial" and not args.allow_partial:
                    partial_candidates.append(build_compare_item(record, entries, match_type))
                    unmatched.append(
                        {
                            "word": word,
                            "ipa": record.get("ipa", ""),
                            "reason": "partial_match_requires_review",
                        }
                    )
                    continue

                entry = choose_best_entry(entries)
                media_name = entry["audio"]
                media_key = None
                for key, value in media_map.items():
                    if value == media_name:
                        media_key = key
                        break

                if media_key is None:
                    unmatched.append(
                        {
                            "word": word,
                            "ipa": record.get("ipa", ""),
                            "match_type": match_type,
                            "candidate": entry.get("candidate", ""),
                            "candidate_source": entry.get("candidate_source", ""),
                            "reason": "media_missing",
                        }
                    )
                    continue

                if not args.compare_only and media_name not in extracted_files:
                    suffix = Path(media_name).suffix or ".mp3"
                    dest_name = f"{safe_filename(word)}{suffix}"
                    dest = args.output_dir / dest_name
                    with zf.open(media_key) as source, open(dest, "wb") as target:
                        shutil.copyfileobj(source, target)
                    extracted_files[media_name] = dest.as_posix()

                audio_file = extracted_files.get(media_name)
                if args.compare_only:
                    suffix = Path(media_name).suffix or ".mp3"
                    audio_file = (args.output_dir / f"{safe_filename(word)}{suffix}").as_posix()

                matched.append(
                    {
                        "word": word,
                        "ipa": record.get("ipa", ""),
                        "match_type": match_type,
                        "candidate": entry.get("candidate", ""),
                        "candidate_source": entry.get("candidate_source", ""),
                        "audio_source": media_name,
                        "audio_file": audio_file,
                        "model": entry["model"],
                    }
                )

    report = {
        "apkg": args.apkg.as_posix(),
        "matched_count": len(matched),
        "unmatched_count": len(unmatched),
        "matched": matched,
        "unmatched": unmatched,
        "partial_candidates_count": len(partial_candidates),
        "partial_candidates": partial_candidates,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote report: {args.report}")
    print(f"Matched: {len(matched)}")
    print(f"Unmatched: {len(unmatched)}")
    if partial_candidates:
        print(f"Partial candidates needing review: {len(partial_candidates)}")
    if unmatched:
        print("First unmatched words:")
        for item in unmatched[:20]:
            print(f"- {item['word']}")


if __name__ == "__main__":
    main()
