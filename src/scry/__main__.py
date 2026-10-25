#!/usr/bin/env python3

import sys


def run_scry():
    if sys.argv[1:2] and sys.argv[1].startswith("remote-"):
        from scry.remote.server import main

        sys.exit(main(sys.argv[1:]))

    from scry.scry import do_table_loop

    do_table_loop()


if __name__ == "__main__":
    run_scry()
