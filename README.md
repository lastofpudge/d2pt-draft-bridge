# D2PT Draft Bridge

Windows bridge that reads Dota 2 draft picks and fills empty hero slots on the Dota2ProTracker Drafter page.

The bridge reads hero portraits from the Dota window and serves the current draft to the Chrome extension on `127.0.0.1:8765`. It does not use
Game State Integration (GSI), launch the Dota Draft Assist desktop app, read game memory, or send input to Dota.

## What's In The Folder

- `bridge.py` runs the local HTTP service and screen reader.
- `bridge_runtime/` contains the Python environment, capture/recognition modules, and portrait library required by the bridge. It is a reduced
  runtime, not the full Dota Draft Assist project.
- The Chrome extension shows D2PT's active-side top picks in a compact page overlay. Clicking a row copies the hero name; it does not select a hero
  in Dota.
- `extension/` is the unpacked Chrome extension.
- `start-bridge.bat` starts the bridge directly.
- `start-bridge-minimized.bat` starts the bridge in a minimized window.
- `send-draft.ps1` sends hero picks manually, without screen recognition.

## Start

1. Run `start-bridge-minimized.bat` and leave the bridge window open. To keep the console visible, run `start-bridge.bat` instead.
2. In Chrome, open `chrome://extensions`, enable Developer mode, select **Load unpacked**, and choose the `extension` folder.
3. Open https://dota2protracker.com/drafter and refresh the page.
4. Keep Dota visible during hero selection. Fullscreen is supported when the screen reader reports that it is capturing Dota.
5. The Chrome overlay shows the active Radiant/Dire side's five highest-ranked D2PT picks. Click a name to copy it, then select the hero manually in Dota.

To send picks manually:

```powershell
.\send-draft.ps1 -Radiant 'Axe','Lina' -Dire 'Pudge','Rubick'
```

## Check The Bridge

- `http://127.0.0.1:8765/health` should return `{"ok":true,"service":"D2PT Draft Bridge"}`.
- `http://127.0.0.1:8765/draft` shows the current draft feed.
- If screen recognition reports a missing portrait library, restore `bridge_runtime/assets/portraits/library.npz` from the original Dota Draft Assist
  files. The reduced runtime does not include portrait sources or the builder.
- After changing extension code, reload the extension at `chrome://extensions`, then refresh the Drafter page.

## Limitations

D2PT can change its page structure, so visually verify picks after updates. Screen recognition requires the included Windows Python environment and
portrait library. If either is removed or damaged, restore the runtime files from a backup or the original Dota Draft Assist project.

## Code Attribution

The capture and portrait-recognition runtime is adapted from [Dota Draft Assist](https://github.com/bijankle/Dota_Draft_Assist).
