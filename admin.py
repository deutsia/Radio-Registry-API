#!/usr/bin/env python3
"""
Admin CLI for managing Radio Registry
"""
import argparse
import json
import sys
from pathlib import Path

import database as db


def cmd_list(args):
    """List stations"""
    stations = db.get_stations(
        network=args.network,
        status=args.status,
        online_only=False,
        limit=args.limit
    )
    
    if not stations:
        print("No stations found")
        return
    
    print(f"\n{'ID':<36} {'Network':<6} {'Online':<7} {'Name':<30}")
    print("-" * 85)
    
    for s in stations:
        online = "✓" if s["lastCheckOk"] else "✗"
        name = s["name"][:28] + ".." if len(s["name"]) > 30 else s["name"]
        print(f"{s['id']:<36} {s['network']:<6} {online:<7} {name:<30}")
    
    print(f"\nTotal: {len(stations)}")


def cmd_pending(args):
    """List pending stations"""
    stations = db.get_pending_stations()
    
    if not stations:
        print("No pending stations")
        return
    
    print(f"\n{'ID':<36} {'Network':<6} {'Name':<30} {'URL':<50}")
    print("-" * 130)
    
    for s in stations:
        name = s["name"][:28] + ".." if len(s["name"]) > 30 else s["name"]
        url = s["streamUrl"][:48] + ".." if len(s["streamUrl"]) > 50 else s["streamUrl"]
        print(f"{s['id']:<36} {s['network']:<6} {name:<30} {url:<50}")
    
    print(f"\nPending: {len(stations)}")


def cmd_approve(args):
    """Approve a station"""
    station = db.get_station_by_id(args.id)
    if not station:
        print(f"Station not found: {args.id}")
        sys.exit(1)
    
    if station["status"] != "pending":
        print(f"Station is not pending (status: {station['status']})")
        sys.exit(1)
    
    if db.approve_station(args.id):
        print(f"✓ Approved: {station['name']}")
    else:
        print("Failed to approve station")
        sys.exit(1)


def cmd_reject(args):
    """Reject a station"""
    station = db.get_station_by_id(args.id)
    if not station:
        print(f"Station not found: {args.id}")
        sys.exit(1)
    
    if station["status"] != "pending":
        print(f"Station is not pending (status: {station['status']})")
        sys.exit(1)
    
    if db.reject_station(args.id, args.reason):
        print(f"✗ Rejected: {station['name']}")
        if args.reason:
            print(f"  Reason: {args.reason}")
    else:
        print("Failed to reject station")
        sys.exit(1)


def cmd_delete(args):
    """Delete a station"""
    station = db.get_station_by_id(args.id)
    if not station:
        print(f"Station not found: {args.id}")
        sys.exit(1)
    
    if not args.force:
        confirm = input(f"Delete '{station['name']}'? [y/N] ")
        if confirm.lower() != 'y':
            print("Cancelled")
            return
    
    if db.delete_station(args.id):
        print(f"Deleted: {station['name']}")
    else:
        print("Failed to delete station")
        sys.exit(1)


def cmd_info(args):
    """Show station details"""
    station = db.get_station_by_id(args.id)
    if not station:
        print(f"Station not found: {args.id}")
        sys.exit(1)
    
    print(f"\n{'='*60}")
    print(f"Station: {station['name']}")
    print(f"{'='*60}")
    print(f"ID:          {station['id']}")
    print(f"Network:     {station['network']}")
    print(f"Status:      {station['status']}")
    print(f"Stream URL:  {station['streamUrl']}")
    print(f"Homepage:    {station['homepage'] or 'N/A'}")
    print(f"Genre:       {station['genre']}")
    print(f"Codec:       {station['codec'] or 'Unknown'}")
    print(f"Bitrate:     {station['bitrate'] or 'Unknown'} kbps")
    print(f"Country:     {station['country'] or 'Unknown'}")
    print(f"\nHealth:")
    print(f"  Online:    {'Yes' if station['lastCheckOk'] else 'No'}")
    print(f"  Last Check:{station['lastCheckTime'] or 'Never'}")
    print(f"  Checks:    {station['checkOkCount']}/{station['checkCount']} successful")
    print(f"\nTimestamps:")
    print(f"  Submitted: {station['submittedAt'] or 'N/A'}")
    print(f"  Approved:  {station['approvedAt'] or 'N/A'}")
    print(f"  Created:   {station['createdAt']}")
    print(f"  Updated:   {station['updatedAt']}")


def cmd_stats(args):
    """Show directory statistics"""
    stats = db.get_stats()
    
    print(f"\n{'='*40}")
    print("Radio Registry Statistics")
    print(f"{'='*40}")
    print(f"Total Stations:    {stats['total_stations']}")
    print(f"Online Stations:   {stats['online_stations']}")
    print(f"Tor Stations:      {stats['tor_stations']}")
    print(f"I2P Stations:      {stats['i2p_stations']}")
    print(f"Pending Review:    {stats['pending_submissions']}")
    print(f"Last Health Check: {stats['last_health_check'] or 'Never'}")


def cmd_import(args):
    """Import stations from JSON file"""
    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)
    
    with open(path) as f:
        data = json.load(f)
    
    # Handle both array and object with "stations" key
    if isinstance(data, dict) and "stations" in data:
        stations = data["stations"]
    elif isinstance(data, list):
        stations = data
    else:
        print("Invalid JSON format. Expected array or object with 'stations' key.")
        sys.exit(1)
    
    imported = db.import_stations_from_json(
        stations,
        network=args.network,
        auto_approve=args.approve
    )
    
    print(f"Imported {imported} stations")


def cmd_export(args):
    """Export stations to JSON"""
    stations = db.export_stations_to_json(network=args.network)
    
    output = {
        "stations": stations,
        "total": len(stations)
    }
    
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(output, f, indent=2)
        print(f"Exported {len(stations)} stations to {args.output}")
    else:
        print(json.dumps(output, indent=2))


def cmd_add(args):
    """Manually add a station"""
    # Detect network from URL
    if ".onion" in args.url:
        network = "tor"
    elif ".i2p" in args.url:
        network = "i2p"
    else:
        print("URL must be .onion or .i2p")
        sys.exit(1)
    
    # Check for duplicates
    existing = db.get_station_by_url(args.url)
    if existing:
        print(f"Station already exists: {existing['name']} (status: {existing['status']})")
        sys.exit(1)
    
    station_id = db.create_station(
        name=args.name,
        stream_url=args.url,
        network=network,
        homepage=args.homepage,
        genre=args.genre or "Other",
        codec=args.codec,
        bitrate=args.bitrate,
        status="approved" if args.approve else "pending"
    )
    
    status = "approved" if args.approve else "pending"
    print(f"Added station: {args.name} ({status})")
    print(f"ID: {station_id}")


def main():
    parser = argparse.ArgumentParser(
        description="Radio Registry Admin CLI"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # list command
    list_parser = subparsers.add_parser("list", help="List stations")
    list_parser.add_argument("--network", "-n", choices=["tor", "i2p"])
    list_parser.add_argument("--status", "-s", default="approved",
                            choices=["approved", "pending", "rejected"])
    list_parser.add_argument("--limit", "-l", type=int, default=50)
    list_parser.set_defaults(func=cmd_list)
    
    # pending command
    pending_parser = subparsers.add_parser("pending", help="List pending stations")
    pending_parser.set_defaults(func=cmd_pending)
    
    # approve command
    approve_parser = subparsers.add_parser("approve", help="Approve a station")
    approve_parser.add_argument("id", help="Station ID")
    approve_parser.set_defaults(func=cmd_approve)
    
    # reject command
    reject_parser = subparsers.add_parser("reject", help="Reject a station")
    reject_parser.add_argument("id", help="Station ID")
    reject_parser.add_argument("--reason", "-r", help="Rejection reason")
    reject_parser.set_defaults(func=cmd_reject)
    
    # delete command
    delete_parser = subparsers.add_parser("delete", help="Delete a station")
    delete_parser.add_argument("id", help="Station ID")
    delete_parser.add_argument("--force", "-f", action="store_true")
    delete_parser.set_defaults(func=cmd_delete)
    
    # info command
    info_parser = subparsers.add_parser("info", help="Show station details")
    info_parser.add_argument("id", help="Station ID")
    info_parser.set_defaults(func=cmd_info)
    
    # stats command
    stats_parser = subparsers.add_parser("stats", help="Show statistics")
    stats_parser.set_defaults(func=cmd_stats)
    
    # import command
    import_parser = subparsers.add_parser("import", help="Import from JSON")
    import_parser.add_argument("file", help="JSON file path")
    import_parser.add_argument("--network", "-n", required=True,
                              choices=["tor", "i2p"])
    import_parser.add_argument("--approve", "-a", action="store_true",
                              help="Auto-approve imported stations")
    import_parser.set_defaults(func=cmd_import)
    
    # export command
    export_parser = subparsers.add_parser("export", help="Export to JSON")
    export_parser.add_argument("--network", "-n", choices=["tor", "i2p"])
    export_parser.add_argument("--output", "-o", help="Output file")
    export_parser.set_defaults(func=cmd_export)
    
    # add command
    add_parser = subparsers.add_parser("add", help="Add a station")
    add_parser.add_argument("--name", "-n", required=True)
    add_parser.add_argument("--url", "-u", required=True, help="Stream URL")
    add_parser.add_argument("--homepage", "-p")
    add_parser.add_argument("--genre", "-g")
    add_parser.add_argument("--codec", "-c")
    add_parser.add_argument("--bitrate", "-b", type=int)
    add_parser.add_argument("--approve", "-a", action="store_true",
                           help="Auto-approve")
    add_parser.set_defaults(func=cmd_add)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    args.func(args)


if __name__ == "__main__":
    main()
