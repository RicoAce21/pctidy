# PC Tidy

Nine tabs: **Cleanup** (remove junk), **Startup** (trim what launches with Windows), **Monitor** (live CPU / RAM / disk / temperature), **Display** (refresh rate check), **Storage** (where your disk space went), **Network** (fix connection problems), **Ping** (test your connection), **Performance** (power modes), **Log** (before/after recordings).

## Setup
1. Python 3.10+ from python.org (tick "Add python.exe to PATH" on the first screen).
2. In this folder's terminal: `pip install -r requirements.txt`   (psutil was added in this version)
3. Run: `python main.py`

Safe text-only junk scan (deletes nothing): `python scan_cli.py`

## Cleanup tab
Scan -> Preview -> tick -> Clean selected -> confirm. Nothing is ticked by default.
- Temp/cache junk is deleted permanently (it rebuilds itself). Old installers go to the Recycle Bin.
- If you tick the Recycle Bin too, it is emptied FIRST, so installers sent there in the same run survive.
- `core/safety.py` refuses anything outside each category's own folders, plus Documents/Desktop/Pictures/etc.
- Every clean is logged to `%APPDATA%\PCTidy\actions.log`.

- **Game shader caches** (DirectX / NVIDIA / AMD / Intel): rebuilt automatically, but games may stutter briefly
  on their first launch afterwards. Best used when a game started stuttering after an update. Close games first.

## Startup tab
Lists everything that launches at boot, with a *guess* of what's safe to turn off.
- The switch is the same one Task Manager's Startup tab uses. Nothing is deleted; flip it back any time.
- Takes effect at the next restart.
- "All users" items need administrator rights to change. Open your terminal as administrator
  (right-click -> Run as administrator) and run `python main.py` from there.

## Monitor tab
Updates every 2 seconds, only while the tab is open.
- **Temperature:** Windows doesn't give programs a reliable CPU temperature, so:
  1. Install **LibreHardwareMonitor** (free), run it as administrator.
  2. In it: Options -> Remote Web Server -> Run.
  3. PC Tidy picks it up automatically, no restart needed. (Shows "n/a" until then.)
  If it still shows "n/a", the line under the graph says why:
  - "Can't reach LibreHardwareMonitor": it isn't open, or Remote Web Server isn't switched on.
  - "running but isn't reporting any temperatures": close it and reopen it with Run as administrator.
  - "Something is answering on port 8085": another program is using that port.
  To check by hand, open http://127.0.0.1:8085/data.json in a browser: you should see a wall of text.
- "End" asks a program to close, like clicking X. Windows essentials and this app are "Locked".

## Display tab
Checks each screen's refresh rate against the best it supports at its current resolution.
- Read-only: it never changes a display setting. If a screen is running below its best, a button opens
  Windows' own display settings page, where you pick the higher rate yourself.
- Laptops can drop the rate on battery, so check while plugged in.

## Storage tab
Pick "My user folder", the whole drive (slow), or any folder. It scans once, then you can click through folders
instantly, or switch to "Biggest files" to see the largest files and how long since they changed.
- Read-only: it can't delete anything. "Show" opens the item in Explorer so you decide.
- Shortcut folders and online-only cloud files (OneDrive) are skipped so the numbers are honest.
- Folders Windows won't let the app read are counted and reported, not hidden.

## Network tab
Three fixes, safest first: flush DNS, renew IP address, reset Winsock. They use Windows' own `ipconfig` and
`netsh` commands and need administrator rights.
- If the app isn't running as administrator, pressing Run opens a popup with a **Run as administrator** button.
  Windows then shows its normal approval prompt and only that one fix runs elevated. Decline it and nothing changes.
- If you started the app as administrator already, fixes run straight away.
- Renewing the IP drops your connection for a few seconds. Resetting Winsock needs a restart.
- The Flush DNS card shows how many addresses are saved in the DNS cache, with a rough size estimate, and
  "was X, now Y" after a flush. The cache lives in memory (usually only kilobytes), and Windows doesn't report
  its real size, so the size is an estimate based on the count. The big space-savers are in the Cleanup tab.
- Try them in order and stop as soon as the problem is fixed.

## Ping tab
Times how long it takes to open a connection to two reference servers and eight cloud regions around the world
(about 10 tries each), and shows speed, steadiness (jitter) and lost tries.
- Read-only, needs no administrator rights, changes nothing.
- It then checks your setup (Wi-Fi vs cable, Wi-Fi signal, any active VPN) and explains what could be hurting
  your ping in plain words, plus tips that really help and a note on the "ping booster" myths.
- Limits: it measures cloud regions, not a specific game's servers, and it can't shorten the distance to a server.
  The Wi-Fi signal reading only works on English-language Windows.

## Performance tab
Three modes built on Windows power plans: **Cool and quiet**, **Balanced**, **Performance**.
- Cool and quiet makes a COPY of Balanced named "PC Tidy - Cool and quiet" and caps the CPU's top speed
  (99% usually switches turbo boost off, which cuts heat and fan noise; lower is cooler and slower). Use the slider.
- Windows' own plans are never edited. The first time you change anything, your current plan is remembered, and
  **Undo** goes back to it and removes PC Tidy's own plans.
- If Windows needs administrator rights, a popup offers **Run as administrator** (Windows shows its approval prompt).
- Some laptop makers hide the High performance plan; if it can't be created, the app says so and changes nothing.
- Results vary by laptop. Check the Monitor tab's temperature before and after (needs the temperature setup).
- Background apps helper: lists well-known background apps (cloud sync, torrent clients, chat apps...) that can hurt
  ping and heat while gaming. Nothing closes unless you tick it and confirm.

## Monitor tab: "What's making it hot?"
Ranks apps by their share of the CPU's work over the last ~30 seconds. It is an estimate: Windows doesn't measure
heat per app, but the CPU's work is what heats the chip. Graphics (GPU) work isn't counted yet, and on laptops
with graphics built into the CPU chip, graphics work heats the same silicon.

## Log tab: before and after
1. Name a recording (e.g. "Before - Balanced") and press **Start recording**. Do a normal activity, then **Stop**.
2. Change ONE thing (a power mode, closing apps, cleaning the fans...), then record again with the SAME activity.
3. Tick the two recordings and press **Compare**: temperature, CPU speed, load, time spent above 85 °C, the busiest
   apps, a plain-language verdict, and a graph with both runs on top of each other.
- Needs LibreHardwareMonitor running for temperatures; otherwise it still records CPU speed, load and memory.
- It warns you when two recordings aren't a fair comparison (different lengths or different workloads).
- Recordings are saved in `%APPDATA%\PCTidy\sessions` and survive restarts, and a crash mid-recording keeps what was saved.
- It does not measure FPS (that needs a tool like PresentMon).

## Tests
`python -m unittest discover -s tests -v`

## Layout
- `main.py` - window + tabs
- `ui/` - one file per tab, plus `bg.py` (background threads so the window never freezes)
- `core/safety.py` - rules blocking risky deletes, action log
- `core/scanner.py`, `core/cleaner.py`, `core/recycle.py` - junk finding / deleting
- `core/startup.py` - startup entries and on/off switch
- `core/monitor.py` - system stats, processes, temperatures
