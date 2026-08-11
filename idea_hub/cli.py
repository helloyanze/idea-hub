# idea_hub/cli.py
import argparse, sys
from idea_hub import db, collectors, models

def _conn(args):
    c = db.connect(args.db)
    db.init_schema(c)
    return c

def cmd_collect(args):
    conn = _conn(args)
    res = collectors.collect_all(conn)
    print(f"collected={res['collected']}")
    for e in res["errors"]:
        print(f"ERROR: {e}", file=sys.stderr)

def main():
    p = argparse.ArgumentParser(prog="idea_hub")
    p.add_argument("--db", default="data/idea.db")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect").set_defaults(func=cmd_collect)
    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
