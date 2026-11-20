"""
background_apps.py - spots well-known background programs that use network, disk or CPU while you game.

It only SUGGESTS. Nothing is closed unless you tick it and confirm, and closing works like clicking
the X (the program can be opened again any time). Windows essentials are never offered.
"""
from typing import Dict, List

KNOWN: Dict[str, tuple] = {
    "onedrive.exe": ("OneDrive", "Cloud sync uses your network and disk in the background."),
    "dropbox.exe": ("Dropbox", "Cloud sync uses your network and disk in the background."),
    "googledrivefs.exe": ("Google Drive", "Cloud sync uses your network and disk in the background."),
    "spotify.exe": ("Spotify", "Music streaming uses network and memory."),
    "discord.exe": ("Discord", "Uses memory and some CPU. Keep it if you're using voice chat."),
    "teams.exe": ("Microsoft Teams", "Heavy on memory and CPU."),
    "ms-teams.exe": ("Microsoft Teams", "Heavy on memory and CPU."),
    "slack.exe": ("Slack", "Uses memory and network."),
    "skype.exe": ("Skype", "Uses memory and network."),
    "whatsapp.exe": ("WhatsApp", "Uses memory and network."),
    "telegram.exe": ("Telegram", "Uses memory and network."),
    "utorrent.exe": ("uTorrent", "Torrents can use all of your bandwidth and wreck your ping."),
    "qbittorrent.exe": ("qBittorrent", "Torrents can use all of your bandwidth and wreck your ping."),
    "bittorrent.exe": ("BitTorrent", "Torrents can use all of your bandwidth and wreck your ping."),
    "epicgameslauncher.exe": ("Epic Games Launcher", "Can download updates in the background."),
    "ccxprocess.exe": ("Adobe Creative Cloud", "Background helper that uses memory."),
    "adobeupdateservice.exe": ("Adobe updater", "Background updater."),
}


def find_background_apps(rows: List[Dict]) -> List[Dict]:
    """From grouped process rows (see core.monitor), pick the known background apps that are running."""
    found = []
    for row in rows:
        known = KNOWN.get(row["name"].lower())
        if known is None or row.get("protected"):
            continue
        found.append({"exe": row["name"], "label": known[0], "why": known[1], "cpu": row["cpu"],
                      "mem_mb": row["mem_mb"], "pids": list(row["pids"]), "count": row["count"]})
    return sorted(found, key=lambda a: a["mem_mb"], reverse=True)
