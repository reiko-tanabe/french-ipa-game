import json
from pathlib import Path

DATA_PATH = Path("data/ipa_words.json")

def main():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    errors = []
    warns = []

    for i, item in enumerate(data):
        # 必須キー
        for k in ["group", "ipa", "word", "bold", "note", "needs_review"]:
            if k not in item:
                errors.append((i, "missing_key", k, item))
                continue

        word = item.get("word", "")
        bold = item.get("bold", "")
        ipa  = item.get("ipa", "")
        group = item.get("group", "")

        # bold が word に含まれるか（空文字は要レビュー扱い）
        if bold == "":
            warns.append((i, "bold_empty", word, ipa, group))
        elif bold not in word:
            errors.append((i, "bold_not_in_word", word, bold, ipa, group))

        # needs_review の整合（bold空なら true を推奨）
        if bold == "" and item.get("needs_review") is False:
            warns.append((i, "needs_review_should_be_true", word, ipa, group))

    print("=== VALIDATION RESULT ===")
    print(f"Total records: {len(data)}")
    print(f"Errors: {len(errors)}")
    print(f"Warnings: {len(warns)}")

    if errors:
        print("\n--- Errors ---")
        for e in errors[:200]:
            print(e)

    if warns:
        print("\n--- Warnings ---")
        for w in warns[:200]:
            print(w)

if __name__ == "__main__":
    main()