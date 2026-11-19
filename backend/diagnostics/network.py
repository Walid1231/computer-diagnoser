"""
Network Diagnostics Module
Checks connectivity, active connections, and network interfaces.
"""

import re
import socket

import psutil

from diagnostics.utils import format_bytes
from diagnostics.winutil import IS_WINDOWS, run_hidden

_format_bytes = format_bytes

# A hostname or IP only: blocks option injection ("-f"), spaces, shell metacharacters.
_HOST_RE = re.compile(r"^(?!-)[A-Za-z0-9.:\-]{1,253}$")


def get_network_info():
    """Get network interface information."""
    interfaces = []
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    io_counters = psutil.net_io_counters(pernic=True)

    for iface, addr_list in addrs.items():
        iface_info = {
            "name": iface,
            "is_up": stats[iface].isup if iface in stats else False,
            "speed_mbps": stats[iface].speed if iface in stats else 0,
            "addresses": [],
            "bytes_sent": 0,
            "bytes_recv": 0,
        }

        for addr in addr_list:
            if addr.family == socket.AF_INET:
                iface_info["addresses"].append({
                    "type": "IPv4",
                    "address": addr.address,
                    "netmask": addr.netmask,
                })
            elif addr.family == socket.AF_INET6:
                iface_info["addresses"].append({
                    "type": "IPv6",
                    "address": addr.address,
                })

        if iface in io_counters:
            iface_info["bytes_sent"] = io_counters[iface].bytes_sent
            iface_info["bytes_recv"] = io_counters[iface].bytes_recv
            iface_info["bytes_sent_display"] = format_bytes(io_counters[iface].bytes_sent)
            iface_info["bytes_recv_display"] = format_bytes(io_counters[iface].bytes_recv)

        interfaces.append(iface_info)

    # Connected adapters first.
    interfaces.sort(key=lambda i: (not i["is_up"], i["name"].lower()))
    return interfaces


def get_active_connections(top_n: int = 50):
    """Active network connections, established ones first.

    The old version returned the first 20 connections in arbitrary order (mostly idle
    listeners) and looked each process name up one by one; this builds one pid->name
    map and sorts by usefulness.
    """
    try:
        raw = psutil.net_connections(kind='inet')
    except (psutil.AccessDenied, OSError):
        return []

    names = {}
    for proc in psutil.process_iter(['pid', 'name']):
        names[proc.info['pid']] = proc.info['name']

    connections = []
    for conn in raw:
        connections.append({
            "family": "IPv4" if conn.family == socket.AF_INET else "IPv6",
            "type": "TCP" if conn.type == socket.SOCK_STREAM else "UDP",
            "local_addr": f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else "",
            "remote_addr": f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "",
            "status": conn.status,
            "pid": conn.pid,
            "process": names.get(conn.pid, "Unknown") if conn.pid else "System",
        })

    def rank(c):
        if c["status"] == "ESTABLISHED":
            return 0
        return 1 if c["remote_addr"] else 2

    connections.sort(key=lambda c: (rank(c), c["process"].lower()))
    return connections[:top_n]


def ping_test(host: str = "8.8.8.8", count: int = 4):
    """Run a ping test to check connectivity."""
    host = (host or "").strip()
    if not _HOST_RE.match(host):
        return {"host": host, "success": False, "output": "Invalid host name or IP address."}

    count = max(1, min(int(count), 10))
    if IS_WINDOWS:
        command = ["ping", "-n", str(count), "-w", "2000", host]
    else:
        command = ["ping", "-c", str(count), "-W", "2", host]

    proc = run_hidden(command, timeout=count * 3 + 5)
    if proc is None:
        return {"host": host, "success": False, "output": "Ping timed out or is not available."}
    output = proc.stdout or proc.stderr or ""
    # Windows ping exits 0 even for "Destination host unreachable" replies from the
    # local gateway, so a real reply (it contains "TTL=") is required for success.
    return {
        "host": host,
        "success": proc.returncode == 0 and "ttl=" in output.lower(),
        "output": output,
    }
