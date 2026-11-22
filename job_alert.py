#!/usr/bin/env python3
"""Run the tech profile; edit search behavior in settings.py."""

if __name__ == "__main__":
    from job_alerts.runner import main
    raise SystemExit(main("tech"))
