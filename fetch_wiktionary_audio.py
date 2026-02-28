#!/usr/bin/env python3
import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import unquote
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import Request, urlopen

DATA_PATH = Path("data/ipa_words.json")
AUDIO_DIR = Path("audio")
ATTR_PATH = Path("audio_attribution.json")
REVIEW_PATH = Path("needs_review_list.json")

# Wiktionary（英語版）にフランス語セクションの音声が載っていることが多い
WIKT_API = "https://en.wiktionary.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# upload.wikimedia.org の直リンクをHTMLから拾う（ogg/oga/mp3）
UPLOAD_RE = re.compile(r'https?://upload\.wikimedia\.org/[^\s"\']+\.(?:ogg|oga|mp3)', re.IGNORECASE)
# File:XXXX.ogg のリンクを拾う
FILE_TITLE_RE = re.compile(r'/(?:wiki|w)/File:([^"\']+\.(?:ogg|oga|mp3))', re.IGNORECASE)
USER_AGENT = "french-ipa-game-audio-fetcher/0.1 (local script; contact: none)"
REQUEST_DELAY_SEC = 1.5
RETRY_MAX = 4
BACKOFF_BASE_SEC = 3.0
RETRIABLE_HTTP_CODES = {429, 500, 502, 503, 504}


def http_get_text(url: str, params: dict, timeout: int = 30) -> str:
    query = urlencode(params)
    req = Request(f"{url}?{query}", headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as res:
        return res.read().decode("utf-8")


def http_get_json(url: str, params: dict, timeout: int = 30) -> dict:
    return json.loads(http_get_text(url, params, timeout=timeout))


def with_retry(action, what: str):
    last_error = None
    for attempt in range(1, RETRY_MAX + 1):
        try:
            return action()
        except HTTPError as e:
            last_error = e
            if e.code not in RETRIABLE_HTTP_CODES or attempt >= RETRY_MAX:
                raise
            wait_sec = BACKOFF_BASE_SEC * (2 ** (attempt - 1))
            print(f"[retry] {what}: HTTP {e.code}, wait {wait_sec:.1f}s")
            time.sleep(wait_sec)
        except URLError as e:
            last_error = e
            if attempt >= RETRY_MAX:
                raise
            wait_sec = BACKOFF_BASE_SEC * (2 ** (attempt - 1))
            print(f"[retry] {what}: network error, wait {wait_sec:.1f}s")
            time.sleep(wait_sec)
    if last_error:
        raise last_error
    raise RuntimeError(f"{what}: retry failed")


def mw_parse_html(page_title: str) -> str:
    # MediaWiki Action API: action=parse でHTMLを得る（parseモジュールの基本）:contentReference[oaicite:1]{index=1}
    params = {
        "action": "parse",
        "page": page_title,
        "prop": "text",
        "format": "json",
        "redirects": 1,
        "origin": "*",
    }
    data = with_retry(
        lambda: http_get_json(WIKT_API, params=params, timeout=30),
        f"mw_parse_html({page_title})",
    )
    if "error" in data:
        raise RuntimeError(f"parse error: {data['error']}")
    return data["parse"]["text"]["*"]


def commons_fileinfo(file_name: str) -> dict:
    # Commons: imageinfo + extmetadata でダウンロードURLとライセンス等を取る（Commons API）:contentReference[oaicite:2]{index=2}
    title = f"File:{file_name}"
    params = {
        "action": "query",
        "titles": title,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "format": "json",
        "origin": "*",
    }
    data = with_retry(
        lambda: http_get_json(COMMONS_API, params=params, timeout=30),
        f"commons_fileinfo({file_name})",
    )
    pages = data.get("query", {}).get("pages", {})
    page = next(iter(pages.values()), {})
    if "missing" in page:
        return {}
    ii = (page.get("imageinfo") or [{}])[0]
    return ii


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    def run():
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=60) as res:
            with open(dest, "wb") as f:
                while True:
                    chunk = res.read(1024 * 128)
                    if not chunk:
                        break
                    f.write(chunk)

    with_retry(run, f"download({dest.name})")


def find_audio_for_word(word: str) -> tuple[str | None, str | None]:
    """
    returns (download_url, commons_filename)
    """
    html = mw_parse_html(word)

    # 1) upload.wikimedia.org 直リンクを拾う
    m = UPLOAD_RE.search(html)
    if m:
        url = m.group(0)
        fname = unquote(url.split("/")[-1])
        return url, fname

    # 2) /wiki/File:XXXX.ogg を拾う → Commons API で url を取得
    m2 = FILE_TITLE_RE.search(html)
    if m2:
        file_name = unquote(m2.group(1))
        info = commons_fileinfo(file_name)
        url = info.get("url")
        if url:
            return url, file_name

    return None, None


def sanitize_note(note: str) -> str:
    parts = [p.strip() for p in str(note).split("|") if p.strip()]
    return " | ".join(parts)


def add_note(item: dict, message: str) -> None:
    existing = sanitize_note(item.get("note", ""))
    if not existing:
        item["note"] = message
        return
    parts = [p.strip() for p in existing.split(" | ") if p.strip()]
    if message not in parts:
        parts.append(message)
    item["note"] = " | ".join(parts)


def remove_error_notes(item: dict) -> None:
    existing = sanitize_note(item.get("note", ""))
    if not existing:
        item["note"] = ""
        return
    keep = [
        p
        for p in existing.split(" | ")
        if not (p.startswith("audio_fetch_error:") or p.startswith("download_error:") or p == "no_wiktionary_audio_found")
    ]
    item["note"] = " | ".join(keep)


def load_data_array() -> list[dict]:
    raw_data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    data = raw_data
    while isinstance(data, list) and data and isinstance(data[0], list):
        data = data[0]
    if not isinstance(data, list):
        raise SystemExit("data/ipa_words.json must be a JSON array.")
    return data


def load_attribution() -> dict:
    if not ATTR_PATH.exists():
        return {}
    try:
        loaded = json.loads(ATTR_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch French audio from Wiktionary/Commons.")
    parser.add_argument(
        "--max-items",
        type=int,
        default=0,
        help="Maximum number of words to process in this run (0 = no limit).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=REQUEST_DELAY_SEC,
        help="Sleep seconds between words to reduce rate-limit pressure.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not DATA_PATH.exists():
        raise SystemExit(f"Not found: {DATA_PATH}")

    AUDIO_DIR.mkdir(exist_ok=True)

    data = load_data_array()
    attribution = load_attribution()
    updated = 0
    missing = 0
    processed = 0

    for i, item in enumerate(data):
        if args.max_items > 0 and processed >= args.max_items:
            break

        word = item.get("word", "").strip()
        if not word:
            continue

        # 既に audio があり、ファイルも存在するならスキップ
        if "audio" in item and item["audio"]:
            existing = Path(item["audio"])
            if existing.exists():
                continue

        processed += 1
        print(f"[{processed}] {word}")

        try:
            url, fname = find_audio_for_word(word)
        except Exception as e:
            item["needs_review"] = True
            add_note(item, f"audio_fetch_error: {e}")
            missing += 1
            time.sleep(args.delay)
            continue

        if not url or not fname:
            item["needs_review"] = True
            add_note(item, "no_wiktionary_audio_found")
            missing += 1
            time.sleep(args.delay)
            continue

        dest = AUDIO_DIR / fname
        try:
            download(url, dest)
        except Exception as e:
            item["needs_review"] = True
            add_note(item, f"download_error: {e}")
            missing += 1
            time.sleep(args.delay)
            continue

        # jsonにローカルパスを保存
        item["audio"] = str(dest.as_posix())
        item["needs_review"] = False
        remove_error_notes(item)
        updated += 1

        # 可能ならCommonsのライセンス情報も保存（後でクレジット表示に使う）
        info = commons_fileinfo(fname) or {}
        ext = (info.get("extmetadata") or {})
        attribution[word] = {
            "file": fname,
            "source_url": info.get("descriptionurl", ""),
            "download_url": info.get("url", ""),
            "license": (ext.get("LicenseShortName") or {}).get("value", ""),
            "license_url": (ext.get("LicenseUrl") or {}).get("value", ""),
            "artist": (ext.get("Artist") or {}).get("value", ""),
            "attribution_required": bool((ext.get("AttributionRequired") or {}).get("value", "").strip()),
        }

        # 過負荷回避（礼儀として少し待つ）
        time.sleep(args.delay)

    # 保存
    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    ATTR_PATH.write_text(json.dumps(attribution, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== DONE ===")
        # needs_review リスト作成
    review_list = [
        {
            "word": item.get("word", ""),
            "ipa": item.get("ipa", ""),
            "group": item.get("group", ""),
            "note": item.get("note", "")
        }
        for item in data
        if item.get("needs_review") is True
    ]

    # ファイル保存
    REVIEW_PATH.write_text(
        json.dumps(review_list, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # コンソール表示
    print("\n=== NEEDS REVIEW LIST ===")
    print(f"Count: {len(review_list)}")

    for r in review_list:
        print(f"- {r['word']} ({r['ipa']}) : {r['note']}")

    print(f"\nSaved: {REVIEW_PATH}")
    print(f"Processed in this run: {processed}")
    print(f"Updated audio: {updated}")
    print(f"Missing / needs_review: {missing}")
    print(f"Wrote: {DATA_PATH}")
    print(f"Wrote: {ATTR_PATH}")


if __name__ == "__main__":
    main()
