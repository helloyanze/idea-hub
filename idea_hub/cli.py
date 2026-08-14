import argparse
import json
import pathlib
from datetime import datetime

from . import db
from . import scheduler
from .config import load as load_config


def main(argv=None):
    parser = argparse.ArgumentParser(prog="idea_hub")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    tick_parser = subparsers.add_parser("tick")
    tick_parser.set_defaults(func=cmd_tick)

    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--dest-dir", default="backups")
    backup_parser.set_defaults(func=cmd_backup)

    args = parser.parse_args(argv)
    args.func(args)


def cmd_tick(args):
    config = load_config(args.config)
    conn = db.connect(config.db_path)
    try:
        db.init_schema(conn)
        result = scheduler.tick(conn, config)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        conn.close()


def cmd_backup(args):
    config = load_config(args.config)
    dest_dir = pathlib.Path(args.dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"idea-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    db.backup(config.db_path, str(dest))
    print(dest)


if __name__ == "__main__":
    main()
