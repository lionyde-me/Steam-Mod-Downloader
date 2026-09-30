# RimWorld Workshop Mod Downloader

A small desktop GUI tool that downloads RimWorld Steam Workshop mods via SteamCMD,
with automatic dependency resolution and a saved mod list.

## Features

- Paste Workshop links or raw IDs (one per line) and add them to a persistent list.
- Optional automatic dependency resolution: queries Steam's public Web API for each
  mod's "required items" and adds those too, recursively.
- Saved list persists to `saved_links.json` next to the script.
- "Download All" or "Download Selected" (multi-select the list first).
- Each mod is downloaded via its own SteamCMD invocation and immediately copied into
  your RimWorld `Mods` folder, to avoid SteamCMD's workshop-content cleanup deleting
  items downloaded earlier in the same session.
- SteamCMD path and Mods folder path are saved to `config.json`.

## Requirements

- Python 3.8+
- [SteamCMD](https://developer.valvesoftware.com/wiki/SteamCMD) installed somewhere
  on your machine
- No third-party Python packages — standard library only

## Usage

1. Run `python rimworld_mod_downloader.py`.
2. Set your SteamCMD executable path and your RimWorld `Mods` folder path (e.g.
   `...\Steam\steamapps\common\RimWorld\Mods`), then click Save on each.
3. Paste Workshop links into the text box and click "Add to List". Leave "Resolve &
   include dependencies automatically" checked if you want required mods pulled in too.
4. Select specific mods in the list (Ctrl/Shift+click) and use "Download Selected",
   or use "Download All" to download everything saved.
5. Watch the log panel for per-item progress and a final success/failure summary.

## Files this script creates

| File | Purpose |
|---|---|
| `config.json` | Saved SteamCMD path and Mods folder path |
| `saved_links.json` | Your saved list of mod IDs/titles |

## Known limitations

- Dependency detection relies on Steam's own "required items" data for a Workshop
  item. Mods that only mention a dependency in their text description (rather than
  declaring it as an official required item) won't be auto-detected.
- This automates SteamCMD; it doesn't replace checking mod compatibility, load order,
  or version requirements for RimWorld itself.

## AI disclosure

This script and this README were generated with AI assistance, at the requester's
direction, through the following conversation:

- **AI model:** Claude Sonnet 5 (Anthropic)
- **Interface / agent software:** Claude.ai chat interface (web/mobile), not an
  autonomous coding agent — each change was made in response to a specific request
  in the conversation, and no code was run or tested against a live SteamCMD/RimWorld
  installation by the AI.
- **Subscription tier:** not visible to the assistant and not recorded here — check
  your own Anthropic account/billing settings if this needs to be stated precisely.
- **Date generated:** conversation dated around September 29, 2026.

If you redistribute this script, consider keeping this section updated or replacing
it with your own disclosure statement, since exact requirements for AI-usage
disclosure vary by platform (e.g. Steam Workshop, GitHub) and jurisdiction.
