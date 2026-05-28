"""
Network Diagnostics Module
Checks connectivity, active connections, and network interfaces.
"""

import psutil
import socket
import subprocess
import platform


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
            iface_info["bytes_sent_display"] = _format_bytes(io_counters[iface].bytes_sent)
            iface_info["bytes_recv_display"] = _format_bytes(io_counters[iface].bytes_recv)

        interfaces.append(iface_info)

    return interfaces


def get_active_connections(top_n: int = 20):
    """Get active network connections."""
    connections = []
    for conn in psutil.net_connections(kind='inet'):
        try:
            c = {
                "family": "IPv4" if conn.family == socket.AF_INET else "IPv6",
                "type": "TCP" if conn.type == socket.SOCK_STREAM else "UDP",
                "local_addr": f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else "",
                "remote_addr": f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "",
                "status": conn.status,
                "pid": conn.pid,
            }
            # Try to get process name
            if conn.pid:
                try:
                    c["process"] = psutil.Process(conn.pid).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    c["process"] = "Unknown"
            connections.append(c)
        except (psutil.AccessDenied, OSError):
            continue

    return connections[:top_n]


def ping_test(host: str = "8.8.8.8", count: int = 4):
    """Run a ping test to check connectivity."""
    try:
        param = "-n" if platform.system().lower() == "windows" else "-c"
        result = subprocess.run(
            ["ping", param, str(count), host],
            capture_output=True, text=True, timeout=15
        )
        return {
            "host": host,
            "success": result.returncode == 0,
            "output": result.stdout,
        }
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {
            "host": host,
            "success": False,
            "output": str(e),
        }


def _format_bytes(size: int) -> str:
    """Convert bytes to a human-readable format."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"
