Integrations Cookbook
=====================

This is a grab-bag of ways to wire my--father-mother into common tools listed on the Pieces integrations page. All methods use the local CLI or HTTP API (`serve` on `127.0.0.1:8765`).

General API endpoints
- Recent/filters: `GET /recent?limit=30&app=Terminal&tag=project&pins_only=true`
- Search: `GET /search?q=auth%20token&limit=20`
- Semantic search: `GET /semantic_search?q=api%20keys&limit=20`
- Context bundle: `GET /context?limit=30&app=Slack&hours=8`
- Clip by id: `GET /clip?id=123`
- Copy/pin: `POST /pin {"id":123,"pinned":true}`
- Dropper: `POST /dropper {"url": "...", "title": "...", "selection": "...", "html": "...", "app": "browser-extension"}`

CLI shortcuts
- `scripts/mfm-fetch.sh` — fetch by latest/id/query/semantic, print or copy.
- `scripts/mfm-fzf.sh` — fzf palette.
- `scripts/mfm-rofi.sh` — rofi palette.
- `scripts/extension-dropper/` — Chrome MV3 sample to send active tab to `/dropper`.

JetBrains Plugin (External Tool)
1) Settings → Tools → External Tools → “+”.
2) Name: `mfm fetch`.
3) Program: `/bin/zsh`
4) Parameters: `-lc "/Users/4jp/Workspace/my--father-mother/scripts/mfm-fetch.sh --query \"$SELECTION\" --semantic --copy"`
5) Working dir: `/Users/4jp/Workspace/my--father-mother`
Bind a keymap to the external tool; selects text → fetches semantic neighbors → copies to clipboard.

VS Code Extension (Task/Keybinding)
- Sample task (tasks.json):
```jsonc
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "mfm semantic fetch",
      "type": "shell",
      "command": "${workspaceFolder}/my--father-mother/scripts/mfm-fetch.sh",
      "args": ["--query", "${selectedText}", "--semantic", "--copy"],
      "problemMatcher": []
    }
  ]
}
```
Bind to a key with `workbench.action.tasks.runTask` args `"mfm semantic fetch"`.

Visual Studio (Windows) quick action
- Add a Tools → External Tool that runs PowerShell:
```
powershell -NoProfile -Command "Invoke-RestMethod http://127.0.0.1:8765/recent?limit=1 | ConvertTo-Json"
```
(For actual copy/paste, mirror `mfm-fetch.sh` logic in PowerShell; this repo stays mac-first.)

Sublime Plugin (build system)
- Tools → Build System → New Build System, content:
```json
{
  "cmd": ["/bin/zsh", "-lc", "/Users/4jp/Workspace/my--father-mother/scripts/mfm-fetch.sh --query \"$TM_SELECTED_TEXT\" --semantic --copy"],
  "shell": true,
  "selector": "text"
}
```
Run “Build” to copy semantic hits for the selection.

VS Code Extension (full)
- Use the above task; for a richer extension, point a custom command at the HTTP API (`/search`/`/context`) and populate a quick-pick—no extra server changes needed.

Pieces CLI
- Already provided: `python3 main.py ...` (see README for commands).

Desktop App (menubar)
- Use SwiftBar/BitBar plugin: place `scripts/mfm-swiftbar.1s.sh` in your plugins dir to get status, pause/resume, recent copy/pin from the menubar.

Obsidian Plugin (Templater hotkey)
- Create a Templater script:
```js
const {request} = require('obsidian');
const res = await request({url: 'http://127.0.0.1:8765/context?limit=10'});
const data = JSON.parse(res);
return data.items.map(i => `- #${i.id} [${i.source_app}] ${i.title || i.window_title || ''}`).join('\n');
```
Bind to a command/hotkey to paste recent context into a note.

JupyterLab Extension (cell helper)
- Add a Python helper in your notebook:
```python
import requests, textwrap
items = requests.get("http://127.0.0.1:8765/context", params={"limit": 10, "app": "Terminal"}).json()["items"]
for it in items:
    print(f"#{it['id']} [{it['source_app']}] {textwrap.shorten(it['content'], 120)}")
```
Wrap this in a JupyterLab command or a notebook cell for quick recall.

Raycast Extension (Script Command)
- Create `~/.config/raycast/scripts/mfm-recent.sh`:
```bash
#!/bin/zsh
json=$(curl -s "http://127.0.0.1:8765/recent?limit=1")
content=$(echo "$json" | /usr/bin/python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["items"][0]["content"])')
echo "$content" | pbcopy
echo "Copied latest clip"
```
Make executable, refresh Raycast scripts.

Web Extension - Chrome
- Sample MV3 extension lives in `scripts/extension-dropper/` (manifest + worker). Load as unpacked and it posts the active tab (title/url/selection/html) to `/dropper`.

Notes
- All integrations assume the API is running: `python3 main.py serve --port 8765`.
- Adjust `/Users/4jp/Workspace/my--father-mother` paths for your environment.
- For Windows-specific tooling (Visual Studio), mirror the REST/CLI flow in PowerShell; core app remains macOS-first.
