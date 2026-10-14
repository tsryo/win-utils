"""longpaths: find paths too long to copy between Windows disks."""

from .scanner import Finding, ScanResult, check, scan

__all__ = ["Finding", "ScanResult", "check", "scan"]
