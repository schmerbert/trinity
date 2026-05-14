# Trinity — Personal Financial Intelligence
# Copyright (C) 2025 schmerbert
# Licensed under GNU GPL v3 — see LICENSE file for details

import sys
import os
import time
import logging
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eyes.scraper import run_eyes

# Log to file so it runs silently
LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trinity_eyes.log")
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [Eyes] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

SCAN_INTERVAL_HOURS = 4

def main():
    logging.info("Eyes started.")
    while True:
        try:
            logging.info("Running scan...")
            run_eyes()
            logging.info("Scan complete. Sleeping.")
        except Exception as e:
            logging.error(f"Scan failed: {e}")
        time.sleep(SCAN_INTERVAL_HOURS * 3600)

if __name__ == "__main__":
    main()