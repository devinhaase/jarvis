"""
network_tools.py — Phase 4 Network team: local-subnet host discovery, latency, bandwidth,
and (where available) router-level WAN stats via UPnP/IGD.

Everything here operates from wherever the Jarvis server host actually sits on the network
— same "location matters" reasoning REQUIRES_CAPABILITY already applies to check_ssl_cert
etc. No credentials, no router login, nothing stored: UPnP/IGD (when a router supports and
allows it) is queried anonymously over the LAN, same trust model as DHCP.

Live-tested against Devin's real home network while building this (Phase 4): UPnP/IGD
discovery got zero SSDP responses in two separate attempts (broad ssdp:all query, bound to
the real LAN interface) — most likely UPnP is disabled on the router, a common default on
newer consumer/ISP hardware, or Windows Firewall is dropping the inbound multicast reply to
this process. check_wan_status() therefore returns a clear "gateway not found" result on
this machine specifically, not an exception — same graceful-degradation convention as
nmap/whois/msfvenom elsewhere in this codebase for a tool whose external dependency isn't
present. ping_sweep/arp_table_snapshot/bandwidth_sample/check_latency don't depend on router
cooperation at all and are fully live-verified working.
"""

import os
import re
import time
import socket
import struct
import subprocess
import ipaddress
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

from security_hardening import run_hardened

_ARP_LINE_RE = re.compile(r'^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F]{2}(?:-[0-9a-fA-F]{2}){5})\s+(\w+)', re.MULTILINE)


# ---------------------------------------------------------------------------
# Local subnet detection — prefer the real LAN adapter (has a default gateway) over
# virtual/loopback adapters (WSL, Hyper-V, VPN TAP) that also show up in `ipconfig`.
# ---------------------------------------------------------------------------

def _detect_lan_subnet():
    """Returns (local_ip, network) for the adapter that actually has a default gateway —
    on a machine with several virtual adapters (WSL, VirtualBox, VPN), that's the only
    reliable signal for "this is the real LAN", not just picking the first IPv4 found."""
    try:
        out = run_hardened(["ipconfig"], timeout=10).stdout or ""
    except Exception as e:
        return None, None, f"could not run ipconfig: {e}"

    blocks = re.split(r'\r?\n\r?\n', out)
    for block in blocks:
        ip_match = re.search(r'IPv4 Address[.\s]*:\s*([\d.]+)', block)
        mask_match = re.search(r'Subnet Mask[.\s]*:\s*([\d.]+)', block)
        gw_match = re.search(r'Default Gateway[.\s]*:\s*([\d.]+)', block)
        if ip_match and mask_match and gw_match and gw_match.group(1).strip():
            try:
                iface = ipaddress.IPv4Interface(f"{ip_match.group(1)}/{mask_match.group(1)}")
                return ip_match.group(1), iface.network, gw_match.group(1)
            except ValueError:
                continue
    return None, None, None


def _ping_once(host: str, timeout_ms: int = 800) -> bool:
    try:
        result = run_hardened(["ping", "-n", "1", "-w", str(timeout_ms), host], timeout=timeout_ms / 1000 + 3)
        return result.returncode == 0 and "TTL=" in (result.stdout or "").upper()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# ping_sweep
# ---------------------------------------------------------------------------

def ping_sweep(subnet: str = None, max_hosts: int = 254) -> dict:
    """ICMP ping sweep of the local subnet. Auto-detects the real LAN /24 (the adapter with
    a default gateway) if `subnet` isn't given. Returns which hosts responded — a live
    device inventory signal, complementary to arp_table_snapshot's ARP-cache view (a host
    can be ARP-known but currently offline, or vice versa briefly after it joins)."""
    if subnet:
        try:
            network = ipaddress.ip_network(subnet, strict=False)
        except ValueError as e:
            return {"error": f"'{subnet}' isn't a valid subnet (expected e.g. 192.168.1.0/24): {e}"}
    else:
        local_ip, network, gateway = _detect_lan_subnet()
        if network is None:
            return {"error": "Could not auto-detect the local LAN subnet (no adapter with a default "
                              "gateway found). Pass subnet explicitly, e.g. '192.168.1.0/24'."}

    hosts = list(network.hosts())
    if len(hosts) > max_hosts:
        return {"error": f"Subnet {network} has {len(hosts)} addresses — larger than max_hosts={max_hosts}. "
                          f"Pass a narrower subnet or raise max_hosts explicitly."}

    alive = []
    with ThreadPoolExecutor(max_workers=32) as pool:
        futures = {pool.submit(_ping_once, str(h)): str(h) for h in hosts}
        for future in as_completed(futures):
            host = futures[future]
            try:
                if future.result():
                    alive.append(host)
            except Exception:
                pass

    alive.sort(key=lambda ip: tuple(int(p) for p in ip.split(".")))
    return {
        "subnet": str(network), "hosts_scanned": len(hosts),
        "hosts_alive": alive, "alive_count": len(alive),
    }


# ---------------------------------------------------------------------------
# arp_table_snapshot
# ---------------------------------------------------------------------------

def arp_table_snapshot() -> dict:
    """Reads the local ARP table (`arp -a`) for a device inventory: IP + MAC pairs the OS
    has already resolved recently, dynamic entries only (skips static/multicast rows).
    Diffable across calls (by MAC, since DHCP can reassign an IP) to spot a new device."""
    try:
        out = run_hardened(["arp", "-a"], timeout=10).stdout or ""
    except Exception as e:
        return {"error": f"could not run arp -a: {e}"}

    devices = []
    for match in _ARP_LINE_RE.finditer(out):
        ip, mac, entry_type = match.groups()
        if entry_type.lower() != "dynamic":
            continue
        devices.append({"ip": ip, "mac": mac.lower()})

    devices.sort(key=lambda d: tuple(int(p) for p in d["ip"].split(".")))
    return {"device_count": len(devices), "devices": devices}


def diff_arp_snapshots(previous: dict, current: dict) -> dict:
    """Pure comparison helper (no I/O) — new/departed devices between two
    arp_table_snapshot() results, keyed by MAC since DHCP can reassign IPs between calls."""
    prev_macs = {d["mac"]: d for d in (previous or {}).get("devices", [])}
    cur_macs = {d["mac"]: d for d in (current or {}).get("devices", [])}
    new = [d for mac, d in cur_macs.items() if mac not in prev_macs]
    departed = [d for mac, d in prev_macs.items() if mac not in cur_macs]
    return {"new_devices": new, "departed_devices": departed}


# ---------------------------------------------------------------------------
# check_latency
# ---------------------------------------------------------------------------

def check_latency(host: str = "8.8.8.8", count: int = 4) -> dict:
    """Ping `host` `count` times, report min/avg/max latency (ms) and packet loss. Default
    target (8.8.8.8) tests upstream/internet reachability, not just the LAN — pass a LAN IP
    to test just the local hop instead."""
    count = max(1, min(count, 10))
    try:
        result = run_hardened(["ping", "-n", str(count), host], timeout=count * 2 + 5)
    except subprocess.TimeoutExpired:
        return {"error": f"ping to {host} timed out"}
    except Exception as e:
        return {"error": str(e)}

    out = result.stdout or ""
    times = [int(m) for m in re.findall(r'time[=<](\d+)ms', out, re.IGNORECASE)]
    loss_match = re.search(r'\((\d+)% loss\)', out)
    loss_pct = int(loss_match.group(1)) if loss_match else (100 if not times else 0)

    if not times:
        return {"host": host, "reachable": False, "packet_loss_pct": loss_pct, "raw": out.strip()[-400:]}

    return {
        "host": host, "reachable": True, "packet_loss_pct": loss_pct,
        "min_ms": min(times), "avg_ms": round(sum(times) / len(times), 1), "max_ms": max(times),
    }


# ---------------------------------------------------------------------------
# bandwidth_sample
# ---------------------------------------------------------------------------

def bandwidth_sample(interface: str = None, duration_seconds: int = 3) -> dict:
    """Samples this machine's own network interface byte counters via psutil over a short
    window and reports an approximate throughput. This measures traffic through the Jarvis
    server host itself, not whole-network bandwidth — for that, check_wan_status()'s router
    counters (where reachable) are the more accurate source."""
    try:
        import psutil
    except ImportError:
        return {"error": "psutil not installed — pip install psutil"}

    duration_seconds = max(1, min(duration_seconds, 15))
    counters = psutil.net_io_counters(pernic=True)
    if interface and interface not in counters:
        return {"error": f"interface '{interface}' not found. Available: {list(counters.keys())}"}

    if not interface:
        # Default to whichever non-loopback interface has seen the most traffic so far —
        # a reasonable proxy for "the one actually in use" without asking the user to know
        # their own adapter name.
        candidates = {k: v for k, v in counters.items() if "loopback" not in k.lower()}
        if not candidates:
            return {"error": "no non-loopback interfaces found"}
        interface = max(candidates, key=lambda k: candidates[k].bytes_sent + candidates[k].bytes_recv)

    before = psutil.net_io_counters(pernic=True)[interface]
    time.sleep(duration_seconds)
    after = psutil.net_io_counters(pernic=True)[interface]

    sent_bps = (after.bytes_sent - before.bytes_sent) / duration_seconds
    recv_bps = (after.bytes_recv - before.bytes_recv) / duration_seconds
    return {
        "interface": interface, "duration_seconds": duration_seconds,
        "sent_kbps": round(sent_bps * 8 / 1000, 1), "recv_kbps": round(recv_bps * 8 / 1000, 1),
        "total_bytes_sent": after.bytes_sent, "total_bytes_recv": after.bytes_recv,
    }


# ---------------------------------------------------------------------------
# check_wan_status — UPnP/IGD, best-effort, documented as flaky by network design not bug
# ---------------------------------------------------------------------------

_SSDP_ADDR = ("239.255.255.250", 1900)
_SSDP_REQUEST = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    "MAN: \"ssdp:discover\"\r\n"
    "MX: 3\r\n"
    "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n"
    "\r\n"
).encode()


def _discover_igd_location(timeout_seconds: float = 4.0):
    """SSDP multicast discovery for an InternetGatewayDevice. Returns the device
    description XML URL, or None if nothing responded within the timeout — a router with
    UPnP disabled (or a firewall dropping the multicast reply) looks identical to "no
    router" from here, which is why check_wan_status()'s error message names both possible
    causes rather than picking one."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout_seconds)
    try:
        sock.sendto(_SSDP_REQUEST, _SSDP_ADDR)
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                data, _addr = sock.recvfrom(4096)
            except socket.timeout:
                break
            text = data.decode(errors="replace")
            loc_match = re.search(r'LOCATION:\s*(\S+)', text, re.IGNORECASE)
            if loc_match:
                return loc_match.group(1).strip()
    finally:
        sock.close()
    return None


def _fetch_wan_control_url(description_url: str):
    """Parses a UPnP device description XML for the WANIPConnection (or WANPPPConnection)
    service's control URL — stdlib-only XML parsing, no new dependency, same convention
    file_ingest.py already uses for stdlib-first parsing."""
    import urllib.request
    with urllib.request.urlopen(description_url, timeout=5) as resp:
        xml_text = resp.read()

    ns = {"u": "urn:schemas-upnp-org:device-1-0"}
    root = ET.fromstring(xml_text)
    base_match = re.match(r'(https?://[^/]+)', description_url)
    base_url = base_match.group(1) if base_match else ""

    for service in root.iter("{urn:schemas-upnp-org:device-1-0}service"):
        service_type = service.findtext("u:serviceType", default="", namespaces=ns) or ""
        if "WANIPConnection" in service_type or "WANPPPConnection" in service_type:
            control_url = service.findtext("u:controlURL", default="", namespaces=ns)
            if control_url:
                return base_url + control_url, service_type
    return None, None


def check_wan_status() -> dict:
    """Queries the router's UPnP/IGD service (if enabled and reachable) for the external
    IP and WAN byte counters. Zero credentials — UPnP is anonymous by design, same trust
    model as DHCP. Returns a clear, non-exceptional result if UPnP isn't reachable (see
    module docstring — this is the common case on this project's own dev network)."""
    location = _discover_igd_location()
    if not location:
        return {
            "wan_reachable": False,
            "reason": "No UPnP/IGD gateway responded to SSDP discovery. Either UPnP is "
                      "disabled on the router (common default on newer hardware — check "
                      "Settings > Advanced > UPnP), or the reply is being blocked by a local "
                      "firewall. Local host-based tools (ping_sweep, arp_table_snapshot, "
                      "bandwidth_sample) don't depend on this and still work.",
        }

    try:
        control_url, service_type = _fetch_wan_control_url(location)
    except Exception as e:
        return {"wan_reachable": False, "reason": f"found a gateway at {location} but couldn't read its "
                                                    f"device description: {e}"}
    if not control_url:
        return {"wan_reachable": False, "reason": f"gateway at {location} doesn't expose a WAN IP/PPP "
                                                    f"connection service"}

    import urllib.request

    def _soap_call(action: str):
        body = (
            f'<?xml version="1.0"?>'
            f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            f'<s:Body><u:{action} xmlns:u="{service_type}"/></s:Body></s:Envelope>'
        ).encode()
        req = urllib.request.Request(control_url, data=body, headers={
            "Content-Type": "text/xml; charset=\"utf-8\"",
            "SOAPAction": f'"{service_type}#{action}"',
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            return ET.fromstring(resp.read())

    result = {"wan_reachable": True, "gateway_description_url": location}
    try:
        ip_root = _soap_call("GetExternalIPAddress")
        ip_el = ip_root.find(".//NewExternalIPAddress")
        result["external_ip"] = ip_el.text if ip_el is not None else None
    except Exception as e:
        result["external_ip_error"] = str(e)

    try:
        sent_root = _soap_call("GetTotalBytesSent")
        sent_el = sent_root.find(".//NewTotalBytesSent")
        result["wan_bytes_sent"] = int(sent_el.text) if sent_el is not None else None
    except Exception as e:
        result["wan_bytes_sent_error"] = str(e)

    try:
        recv_root = _soap_call("GetTotalBytesReceived")
        recv_el = recv_root.find(".//NewTotalBytesReceived")
        result["wan_bytes_received"] = int(recv_el.text) if recv_el is not None else None
    except Exception as e:
        result["wan_bytes_received_error"] = str(e)

    return result


# ---------------------------------------------------------------------------
# diagnose_connectivity — composes the above into one synthesized read, suggests fixes,
# never changes live config (that stays a human/Tier-4 decision, and there's no tool here
# that touches router settings at all — UPnP as used above is read-only queries).
# ---------------------------------------------------------------------------

def diagnose_connectivity(target: str = None) -> dict:
    """Runs latency + ARP inventory + WAN status together and synthesizes a plain-language
    diagnosis with suggested next steps. Diagnosis and suggestions only — this never
    executes a fix; anything that would (restarting a service, changing a config) is a
    separate, explicitly-tiered tool a human approves, not implied by this one's output."""
    findings = []
    suggestions = []

    local_latency = check_latency(host=target or "8.8.8.8", count=4)
    arp = arp_table_snapshot()
    wan = check_wan_status()

    if local_latency.get("reachable") is False:
        findings.append(f"No response from {local_latency.get('host')} — packet loss "
                         f"{local_latency.get('packet_loss_pct', '?')}%.")
        suggestions.append("Check the physical connection (cable/Wi-Fi) and whether other "
                            "devices on the same network can reach the internet.")
    elif local_latency.get("reachable"):
        avg = local_latency.get("avg_ms")
        findings.append(f"{local_latency['host']} reachable, avg latency {avg}ms, "
                         f"{local_latency.get('packet_loss_pct', 0)}% loss.")
        if avg and avg > 150:
            suggestions.append("Latency is high for a typical broadband connection — worth "
                                "checking for congestion (bandwidth_sample) or a saturated Wi-Fi channel.")

    if "error" not in arp:
        findings.append(f"{arp['device_count']} device(s) currently in the local ARP table.")
    else:
        findings.append(f"Could not read local ARP table: {arp['error']}")

    if wan.get("wan_reachable"):
        findings.append(f"Router UPnP reachable — external IP {wan.get('external_ip', '?')}.")
    else:
        findings.append(f"Router-level WAN stats unavailable: {wan.get('reason', 'unknown')}")

    return {
        "target": target or "8.8.8.8 (default upstream check)",
        "findings": findings,
        "suggestions": suggestions or ["No obvious problem detected from these signals."],
        "raw": {"latency": local_latency, "arp": arp, "wan": wan},
    }
