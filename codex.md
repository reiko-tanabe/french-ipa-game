# Codex Agent Prompt File
# Project: French IPA Pronunciation Learning Web App

## Project Objective

Create and maintain a browser-based French IPA learning web app.

This is an educational tool.

## Current Main Specification

The app is currently implemented in a single file:

- `index.html`

Primary dataset currently used by the app:

- `data/ipa_words.apkg_audio.updated.json`

Fallback source dataset:

- `data/ipa_words.json`

## Current App Structure

The app has 3 main views:

1. Home page
   - lets the user choose between:
   - `Mode tableau`
   - `Mode jeu`

2. Table view
   - displays a phonetic table generated from JSON data
   - current `group` values in JSON should not be treated as the table layout source
   - words are grouped for display by predefined IPA sections in the UI
   - IPA labels are display-only
   - clicking a word plays audio

3. Game view
   - displays one word at a time
   - user hears the word by clicking it
   - user selects one IPA symbol from 4 choices
   - includes learning features such as:
   - review priority
   - weak-sound tracking
   - close-sound comparison
   - mastery by consecutive correct answers
   - study-set selection

## Language Requirement

All visible UI text should be in French unless the user explicitly requests otherwise.

## Audio Rules

Audio playback priority:

1. local file in record `audio.file`
2. `speechSynthesis` with `fr-FR`

If a local audio file exists, prefer it.

If `speechSynthesis` is used, prefer `fr-FR`.

## Dataset Rules

Each item may contain fields such as:

- `group`
- `ipa`
- `word`
- `bold`
- `note`
- `needs_review`
- `audio`

If `bold` is present and found in `word`, that substring should be emphasized in the UI.

Do not invent dataset entries.

Do not reorder dataset items unless explicitly requested.

## Table View Rules

The table should be generated from dataset content.

The current table layout is based on predefined IPA section groupings in `index.html`, not on the JSON `group` field.

Click behavior:

- click word: play audio
- click IPA label: no audio

## Game Rules

The game should:

- show one word at a time
- validate one IPA choice
- show immediate feedback
- display the incorrect-answer image when the answer is wrong

Choice generation:

- use predefined IPA comparison groups
- if the target group has fewer than 4 symbols, supplement from other IPA values

## Current Related Files

- `index.html`
- `data/ipa_words.json`
- `data/ipa_words.apkg_audio.updated.json`
- `audio_attribution.json`
- `extract_apkg_audio.py`
- `apply_apkg_audio_matches.py`
- `validate.py`

## Files No Longer Used

These are no longer part of the active workflow:

- `fetch_wiktionary_audio.py`
- `generate_whisperx_timestamps.py`

Do not reintroduce them unless explicitly requested.

## Working Rules

If a required detail is missing or ambiguous, ask the user before making a structural change.

Prefer minimal, targeted edits over broad rewrites unless the user requests a redesign.

Preserve existing working behavior unless the user asks for a change.
