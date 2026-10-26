"""The program's version, shown in About and at the top of the log.

Release builds are made from a `v<version>` tag, and the CI build refuses a
tag that does not match this - so the installer and the program can never
claim different versions.
"""

__version__ = "2.2.2"
