"""
ping.py - how fast does your connection reach servers around the world, and what might be hurting it?

READ-ONLY and needs no administrator rights. It times how long it takes to open a connection to
each server (the same handshake a website does), then looks at your adapters (Wi-Fi / cable / VPN)
to suggest what could be hurting your ping.

What this can NOT do: shorten the physical distance to a game server. Most of a high ping is
distance. This finds the part that IS in your control.
"""
import re
import socket
import subprocess
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

SAMPLES = 10
TIMEOUT = 2.0
GAP = 0.12
GOOD_MS, OK_MS = 50.0, 100.0
JITTER_WARN = 20.0
LOSS_WARN = 1.0
WIFI_WEAK = 60


@dataclass(frozen=True)
class Target:
    id: str
    name: str
    kind: str          # "reference" = general health check, "region" = a cloud data-centre region
    host: str
    port: int = 443


TARGETS = [
    Target("cloudflare", "Cloudflare (nearby reference)", "reference", "1.1.1.1"),
    Target("google", "Google (nearby reference)", "reference", "8.8.8.8"),
    Target("mumbai", "Mumbai", "region", "ec2.ap-south-1.amazonaws.com"),
    Target("singapore", "Singapore", "region", "ec2.ap-southeast-1.amazonaws.com"),
    Target("tokyo", "Tokyo", "region", "ec2.ap-northeast-1.amazonaws.com"),
    Target("sydney", "Sydney", "region", "ec2.ap-southeast-2.amazonaws.com"),
    Target("frankfurt", "Frankfurt", "region", "ec2.eu-central-1.amazonaws.com"),
    Target("london", "London", "region", "ec2.eu-west-2.amazonaws.com"),
    Target("virginia", "US East (Virginia)", "region", "ec2.us-east-1.amazonaws.com"),
    Target("saopaulo", "Sao Paulo", "region", "ec2.sa-east-1.amazonaws.com"),
]


# ---------------------------------------------------------------- measuring
@dataclass
class PingStats:
    sent: int
    received: int
    loss_pct: float
    avg: Optional[float]
    low: Optional[float]
    high: Optional[float]
    jitter: Optional[float]      # how much the time bounces around between tries

    @property
    def reachable(self) -> bool:
        return self.received > 0


@dataclass
class PingResult:
    target: Target
    stats: PingStats
    error: str = ""


def summarize(samples: List[Optional[float]]) -> PingStats:
    good = [s for s in samples if s is not None]
    sent = len(samples)
    if not good:
        return PingStats(sent, 0, 100.0 if sent else 0.0, None, None, None, None)
    jitter = (sum(abs(b - a) for a, b in zip(good, good[1:])) / (len(good) - 1)) if len(good) > 1 else 0.0
    return PingStats(sent, len(good), 100.0 * (sent - len(good)) / sent, sum(good) / len(good),
                     min(good), max(good), jitter)


def measure(address, samples=SAMPLES, timeout=TIMEOUT, gap=GAP, cancel: Optional[threading.Event] = None,
            connect=socket.create_connection, clock=time.perf_counter, sleep=time.sleep) -> List[Optional[float]]:
    """Time several connections. None = that try failed or timed out."""
    out: List[Optional[float]] = []
    for i in range(samples):
        if cancel is not None and cancel.is_set():
            break
        start = clock()
        try:
            conn = connect(address, timeout=timeout)
            elapsed = (clock() - start) * 1000.0
            conn.close()
            out.append(elapsed)
        except OSError:
            out.append(None)
        if i < samples - 1:
            sleep(gap)
    return out


def resolve_ipv4(host: str, port: int) -> str:
    return socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]


def run_all(targets=TARGETS, samples=SAMPLES, cancel: Optional[threading.Event] = None,
            progress: Optional[Callable[[PingResult], None]] = None, connect=socket.create_connection,
            resolve=resolve_ipv4, sleep=time.sleep, workers=6) -> List[PingResult]:
    """Test every target (several at once) and return results in the original order."""
    def one(target: Target) -> PingResult:
        try:
            ip = resolve(target.host, target.port)      # look the address up first so DNS time isn't counted
        except OSError:
            result = PingResult(target, summarize([]), "Couldn't look up this server's address.")
        else:
            result = PingResult(target, summarize(measure((ip, target.port), samples, cancel=cancel,
                                                          connect=connect, sleep=sleep)))
        if progress:
            progress(result)
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, targets))


def rate(stats: PingStats) -> str:
    if stats.sent == 0:
        return "Not tested"
    if not stats.reachable:
        return "Unreachable"
    if stats.avg < GOOD_MS:
        return "Great"
    return "OK" if stats.avg < OK_MS else "Poor"


# ---------------------------------------------------------------- what's your setup like?
VPN_WORDS = ("vpn", "proton", "nord", "express", "wireguard", "wintun", "tap-windows", "openvpn", "tunnel",
             "mullvad", "surfshark", "warp", "tailscale", "zerotier", "hamachi", "cisco anyconnect", "pia")
VIRTUAL_WORDS = ("loopback", "vethernet", "virtualbox", "vmware", "vmnet", "hyper-v", "wsl", "docker",
                 "bluetooth", "local area connection*", "isatap", "teredo", "pseudo")


def classify_adapter(name: str) -> str:
    low = name.lower()
    if any(w in low for w in VIRTUAL_WORDS):
        return "virtual"
    if any(w in low for w in VPN_WORDS):
        return "vpn"
    if any(w in low for w in ("wi-fi", "wifi", "wireless", "wlan")):
        return "wifi"
    if "ethernet" in low or low.startswith("eth") or low.startswith("en"):
        return "ethernet"
    return "other"


@dataclass
class Adapter:
    name: str
    kind: str


@dataclass
class Environment:
    adapters: List[Adapter] = field(default_factory=list)     # only adapters that are up and have an address
    wifi_signal: Optional[int] = None                         # percent
    wifi_radio: Optional[str] = None

    @property
    def vpn_names(self) -> List[str]:
        return [a.name for a in self.adapters if a.kind == "vpn"]

    @property
    def using_wifi(self) -> bool:
        kinds = {a.kind for a in self.adapters}
        return "wifi" in kinds and "ethernet" not in kinds

    @property
    def using_ethernet(self) -> bool:
        return any(a.kind == "ethernet" for a in self.adapters)


def parse_wlan(text: str) -> Dict:
    """Read `netsh wlan show interfaces`. Only understands English Windows; otherwise returns nothing."""
    signal = re.search(r"^\s*Signal\s*:\s*(\d+)\s*%", text, re.MULTILINE)
    radio = re.search(r"^\s*Radio type\s*:\s*(.+)$", text, re.MULTILINE)
    connected = re.search(r"^\s*State\s*:\s*connected", text, re.MULTILINE | re.IGNORECASE)
    return {"signal": int(signal.group(1)) if signal and connected else None,
            "radio": radio.group(1).strip() if radio and connected else None}


def get_environment() -> Environment:
    env = Environment()
    try:
        import psutil
        stats, addrs = psutil.net_if_stats(), psutil.net_if_addrs()
        for name, st in stats.items():
            ipv4 = [a.address for a in addrs.get(name, []) if a.family == socket.AF_INET]
            usable = [a for a in ipv4 if not a.startswith(("127.", "169.254."))]
            if st.isup and usable:
                kind = classify_adapter(name)
                if kind != "virtual":
                    env.adapters.append(Adapter(name, kind))
    except Exception:
        return env
    if sys.platform == "win32" and any(a.kind == "wifi" for a in env.adapters):
        try:
            out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True,
                                 errors="replace", timeout=8, creationflags=0x08000000).stdout
            info = parse_wlan(out)
            env.wifi_signal, env.wifi_radio = info["signal"], info["radio"]
        except (OSError, subprocess.TimeoutExpired):
            pass
    return env


# ---------------------------------------------------------------- turning numbers into advice
@dataclass
class Finding:
    level: str          # "good" | "info" | "warn" | "bad"
    title: str
    detail: str


@dataclass
class Diagnosis:
    verdict: Finding
    findings: List[Finding]


TIPS = [
    "Pick the closest server region in the game's settings. Distance is the biggest part of ping, and nothing else helps as much.",
    "Use a network cable instead of Wi-Fi if you can. It mostly fixes sudden lag spikes.",
    "Turn off any VPN while gaming (unless you need it for the game to work in your region).",
    "Pause big downloads, cloud sync (OneDrive, Dropbox), game updates and video streaming while you play. The Monitor tab shows what's busy.",
    "On Wi-Fi, sit closer to the router, or use the 5 GHz network if there is one. On a shared network (hostel, dorm), evenings are busiest.",
    "Restart your router now and then. If it's still bad, the cause is probably your internet provider.",
]
MYTHS = ("'Ping booster' registry tweaks, changing your DNS server and flushing DNS do very little for in-game "
         "ping. DNS only affects how fast a website's name is looked up, not the speed of a game connection.")


def diagnose(results: List[PingResult], env: Environment) -> Diagnosis:
    reachable = [r for r in results if r.stats.reachable]
    if not reachable:
        return Diagnosis(Finding("bad", "No connection detected",
                                 "Couldn't reach any server. Check you're connected to the internet, then test again."), [])
    refs = [r for r in reachable if r.target.kind == "reference"]
    base = min(refs or reachable, key=lambda r: r.stats.avg)
    s = base.stats
    findings: List[Finding] = []

    if s.avg < GOOD_MS and s.jitter < JITTER_WARN and s.loss_pct < LOSS_WARN:
        verdict = Finding("good", "Your connection looks healthy",
                          f"Nearby servers answer in about {s.avg:.0f} ms and steadily. A high ping in a game is "
                          f"probably distance to the game's server, not a problem on your side.")
    elif s.loss_pct >= 5 or s.avg >= OK_MS:
        verdict = Finding("bad", "Your connection is struggling",
                          f"Even nearby servers take about {s.avg:.0f} ms" + (f" and {s.loss_pct:.0f}% of tries were lost" if s.loss_pct else "")
                          + ". See the likely causes below.")
    else:
        verdict = Finding("info", "Usable, but not ideal", f"Nearby servers answer in about {s.avg:.0f} ms. See what might be improved below.")

    for name in env.vpn_names:
        findings.append(Finding("warn", f"A VPN looks active ({name})",
                                "A VPN sends your traffic the long way round and usually adds delay. If you're gaming, "
                                "try turning it off and testing again to see the difference."))
    unsteady = s.jitter >= JITTER_WARN or s.loss_pct >= LOSS_WARN
    if env.using_wifi:
        if env.wifi_signal is not None and env.wifi_signal < WIFI_WEAK:
            findings.append(Finding("warn", f"Weak Wi-Fi signal ({env.wifi_signal}%)",
                                    "A weak signal causes slow, unsteady connections. Move closer to the router or switch to the 5 GHz network."))
        elif unsteady:
            findings.append(Finding("warn", "Wi-Fi may be causing the unsteadiness",
                                    "Wi-Fi is the most common cause of sudden lag spikes. A network cable usually fixes it."))
    if s.jitter >= JITTER_WARN:
        findings.append(Finding("warn", f"Unsteady connection (varies by about {s.jitter:.0f} ms)",
                                "The delay jumps around, which feels like random lag spikes even when the average looks fine."))
    if s.loss_pct >= LOSS_WARN:
        findings.append(Finding("warn", f"{s.loss_pct:.0f}% of connection tries were lost",
                                "Lost data makes games stutter or rubber-band. Common causes: weak Wi-Fi, a busy shared network, or your provider."))
    if s.avg >= OK_MS:
        findings.append(Finding("warn", "Slow even to nearby servers",
                                "That points at your own connection (Wi-Fi, router, or provider) rather than game distance."))
    regions = [r for r in reachable if r.target.kind == "region"]
    if regions and s.avg < OK_MS:
        nearest = min(regions, key=lambda r: r.stats.avg)
        findings.append(Finding("info", f"Closest game-cloud region: {nearest.target.name} ({nearest.stats.avg:.0f} ms)",
                                "If a game lets you choose a server region, pick the closest one. Far regions are slow because of distance, which no setting can fix."))
    return Diagnosis(verdict, findings)
