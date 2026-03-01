# French IPA Game

French IPA learning app for browser use.

## Files

- `index.html`
  - main app
- `data/ipa_words.json`
  - main dataset
- `audio/`
  - local audio files
- `extract_apkg_audio.py`
  - audio extraction helper
- `apply_apkg_audio_matches.py`
  - apply extracted audio paths to dataset
- `validate.py`
  - dataset validator

## Features

- Home view
- Table view
- Game view
- Local audio playback with `audio.file`
- `speechSynthesis` fallback with `fr-FR`
- Learning set selection
- Review priority
- Weak-sound tracking
- Mastery by consecutive correct answers
- Progress reset from the home view

## Run

Use a local server.

```bash
python3 -m http.server 5500
```

Open:

```text
http://localhost:5500/index.html
```

## Share

This project can be shared as a static site with GitHub Pages.

Files added for publishing:

- `.github/workflows/deploy-pages.yml`
- `.nojekyll`

Repository:

- `https://github.com/reiko-tanabe/french-ipa-game`

Expected public URL after GitHub Pages is enabled:

```text
https://reiko-tanabe.github.io/french-ipa-game/
```

Setup:

1. Push the latest `main` branch.
2. In GitHub, open `Settings` -> `Pages`.
3. Set `Source` to `GitHub Actions`.
4. Wait for the `Deploy GitHub Pages` workflow to finish.

## Dataset

Each record may include:

- `group`
- `ipa`
- `word`
- `bold`
- `note`
- `needs_review`
- `audio`

If `bold` exists and is contained in `word`, that substring is emphasized in the UI.

## Notes

- The app currently loads `data/ipa_words.json`.
