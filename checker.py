#!/usr/bin/env python3
"""
Health checker for Tor/I2P radio stations

Runs via cron every 5 minutes. Uses smart scheduling to determine which
stations actually need checking:
- Online stations: recheck every 4 hours
- Failed stations: escalating rechecks at 5min, 15min, 1hr before confirming offline
- Dead stations (offline 12+ hours): check every 4 hours
"""
import asyncio
import sys
from datetime import datetime
from typing import Optional

try:
    import aiohttp
    from aiohttp_socks import ProxyConnector
except ImportError as e:
    print(f"Error: Missing required dependency - {e.name}")
    print("Please install dependencies with: pip install -r requirements.txt")
    sys.exit(1)

import database as db
from config import (
    TOR_SOCKS_PROXY,
    HEALTH_CHECK_TIMEOUT,
    RECHECK_INTERVALS_MINUTES
)

I2P_HTTP_PROXY = "http://127.0.0.1:4444"


async def check_station(
    session: aiohttp.ClientSession,
    station_id: str,
    stream_url: str,
    network: str
) -> bool:
    try:
        async with session.get(
            stream_url,
            timeout=aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT),
            allow_redirects=True
        ) as response:
            if response.status == 200:
                chunk = await response.content.read(1024)
                return len(chunk) > 0
            return response.status in (200, 302, 301)
    except asyncio.TimeoutError:
        print(f"  Timeout: {stream_url}")
        return False
    except Exception as e:
        print(f"  Error checking {stream_url}: {type(e).__name__}: {e}")
        return False


def _get_check_type(station: dict) -> str:
    """Get a label for the type of check being performed"""
    failures = station.get("consecutive_failures", 0) or 0
    health = station.get("health_status", "unknown")

    if failures == 0 and health in ("online", "unknown"):
        return "regular"
    elif failures > 0 and failures <= len(RECHECK_INTERVALS_MINUTES):
        return f"recheck #{failures}"
    elif health == "dead":
        return "dead-check"
    else:
        return "offline-check"


async def check_tor_stations(stations: list[dict]) -> dict[str, bool]:
    results = {}
    if not stations:
        return results

    print(f"\nChecking {len(stations)} Tor stations via {TOR_SOCKS_PROXY}...")

    try:
        connector = ProxyConnector.from_url(TOR_SOCKS_PROXY)
        async with aiohttp.ClientSession(connector=connector) as session:
            for station in stations:
                station_id = station["id"]
                stream_url = station["stream_url"]
                check_type = _get_check_type(station)

                print(f"  [{check_type}] {stream_url}")
                is_online = await check_station(session, station_id, stream_url, "tor")
                results[station_id] = is_online

                status = "✓ online" if is_online else "✗ offline"
                print(f"    {status}")
                await asyncio.sleep(1)
    except Exception as e:
        print(f"Error setting up Tor proxy: {e}")
        for station in stations:
            results[station["id"]] = False

    return results


async def check_i2p_stations(stations: list[dict]) -> dict[str, bool]:
    results = {}
    if not stations:
        return results

    print(f"\nChecking {len(stations)} I2P stations via {I2P_HTTP_PROXY}...")

    try:
        async with aiohttp.ClientSession() as session:
            for station in stations:
                station_id = station["id"]
                stream_url = station["stream_url"]
                check_type = _get_check_type(station)

                print(f"  [{check_type}] {stream_url}")

                try:
                    async with session.get(
                        stream_url,
                        proxy=I2P_HTTP_PROXY,
                        timeout=aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT),
                        allow_redirects=True
                    ) as response:
                        if response.status == 200:
                            chunk = await response.content.read(1024)
                            is_online = len(chunk) > 0
                        else:
                            is_online = response.status in (302, 301)
                except asyncio.TimeoutError:
                    print(f"    Timeout")
                    is_online = False
                except Exception as e:
                    print(f"    Error: {type(e).__name__}: {e}")
                    is_online = False

                results[station_id] = is_online
                status = "✓ online" if is_online else "✗ offline"
                print(f"    {status}")
                await asyncio.sleep(1)
    except Exception as e:
        print(f"Error with I2P checks: {e}")
        for station in stations:
            results[station["id"]] = False

    return results


async def run_health_checks():
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("=" * 60)
    print(f"Health check run - {timestamp}")
    print("=" * 60)

    # Get only stations that are due for a check
    all_stations = db.get_stations_due_for_check()

    if not all_stations:
        print("No stations due for check")
        return

    # Count check types for summary
    regular_checks = sum(1 for s in all_stations if (s.get("consecutive_failures") or 0) == 0)
    rechecks = sum(1 for s in all_stations if 0 < (s.get("consecutive_failures") or 0) <= len(RECHECK_INTERVALS_MINUTES))
    dead_checks = sum(1 for s in all_stations if s.get("health_status") == "dead")

    tor_stations = [s for s in all_stations if s["network"] == "tor"]
    i2p_stations = [s for s in all_stations if s["network"] == "i2p"]

    print(f"Stations due for check: {len(all_stations)}")
    print(f"  Tor: {len(tor_stations)}, I2P: {len(i2p_stations)}")
    print(f"  Regular: {regular_checks}, Rechecks: {rechecks}, Dead: {dead_checks}")

    tor_results = await check_tor_stations(tor_stations)
    i2p_results = await check_i2p_stations(i2p_stations)

    all_results = {**tor_results, **i2p_results}
    online_count = 0
    recovered_count = 0

    for station_id, is_online in all_results.items():
        # Check if this is a recovery (was failing, now online)
        station = next((s for s in all_stations if s["id"] == station_id), None)
        if station and is_online and (station.get("consecutive_failures") or 0) > 0:
            recovered_count += 1

        db.update_health_status(station_id, is_online)
        if is_online:
            online_count += 1

    db.set_last_health_check_time()

    print("\n" + "=" * 60)
    print(f"Health check complete")
    print(f"  Checked: {len(all_results)}")
    print(f"  Online: {online_count}, Offline: {len(all_results) - online_count}")
    if recovered_count > 0:
        print(f"  Recovered: {recovered_count} (were offline, now online)")
    print("=" * 60)


def main():
    if len(sys.argv) > 1:
        url = sys.argv[1]
        print(f"Single station check not implemented in this version")
        sys.exit(1)
    else:
        asyncio.run(run_health_checks())


if __name__ == "__main__":
    main()
