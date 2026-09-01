import argparse


def main():
    parser = argparse.ArgumentParser(prog="fixapp")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="Start the server")
    sub.add_parser("migrate", help="Run migrations")
    export = sub.add_parser('export-data', help="Export data")
    export.add_argument("--out")
    args = parser.parse_args()
    print(args)


if __name__ == "__main__":
    main()
