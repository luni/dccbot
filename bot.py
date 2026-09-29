#!env python3

import os

from dccbot.__main__ import main

if __name__ == "__main__":
    main(["--config", os.path.join(os.path.dirname(__file__), "config.json")])
