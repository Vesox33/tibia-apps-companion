"""Tibia Apps Companion – rezerwacje respów (tibialoot.com) + Hunt Uploader (hunt-analyser.com).
Uruchom: python main.py  (albo uruchom.bat)"""
import sys

if sys.version_info < (3, 10):
    sys.exit("Wymagany Python 3.10 lub nowszy.")

import paths

paths.setup()   # przed importem modułów – folder danych

from app import main

if __name__ == "__main__":
    main()
