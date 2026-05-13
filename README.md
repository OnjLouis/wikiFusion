# WikiFusion

WikiFusion is an NVDA add-on that combines Wikipedia and Wiktionary lookup in one dialog, with smart routing, a results tree, optional media playback, and optional lighter sources.

## What's New In 1.5

- Adds optional `Uncyclopedia` and `Urban Dictionary` sources in Wiki Fusion settings.
- Keeps `Wikipedia` and `Wiktionary` as the top two sections in the results tree.
- Preserves smart exact-match priority:
  single words prefer dictionary-style matches first, phrases prefer encyclopedia-style matches first.
- Hides the media pane until the loaded result actually has media.
- Marks loaded tree items with `[media]` after media has been confirmed.

## Features

- Unified search across Wikipedia and Wiktionary.
- Smart routing:
  single words prefer Wiktionary, phrases prefer Wikipedia.
- Results shown in a tree with:
  `Wikipedia`, `Wiktionary`, then optional `Uncyclopedia` and `Urban Dictionary`.
- Optional internal playback for pronunciation and other audio.
- `ffmpeg` support for decoding formats such as OGG and OPUS when available on `PATH`.
- Copy behavior tuned by source:
  Wikipedia and Uncyclopedia copy title plus URL, Wiktionary and Urban Dictionary copy the term.

## Keyboard Shortcuts

- `NVDA+Alt+I`: open WikiFusion
- `Enter` in the search box: search
- `Enter` on a result: load selected item
- `Ctrl+Enter` on a result: open the selected item in a browser
- `Ctrl+C` on a result: copy text based on source
- `F1`: open the add-on help

## Media

- The media pane only appears when the currently loaded result has media.
- `Enter`: play or stop the selected media item
- `Space`: pause or resume playback
- `Ctrl+Enter`: download the selected media item

## Settings

- Wikipedia language code
- Wiktionary language code
- Include Uncyclopedia results
- Include Urban Dictionary results
- Maximum matches
- Maximum definitions
- Sounds on or off

## Install

1. Download [wikiFusion.nvda-addon](https://github.com/OnjLouis/wikiFusion/releases/download/v1.5.1/wikiFusion.nvda-addon).
2. In NVDA, open Add-on Manager and choose Install.
3. Select the downloaded file and restart NVDA when prompted.

## Source And Package

- Source snapshot: [`source/`](./source/)
- Latest packaged add-on: [`wikiFusion.nvda-addon`](./wikiFusion.nvda-addon)
- Latest release: [v1.5.1](https://github.com/OnjLouis/wikiFusion/releases/tag/v1.5.1)
