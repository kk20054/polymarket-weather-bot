#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility entrypoint for the full WeatherBet bot."""

from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("bot_v2.py")), run_name="__main__")
