"""Batch jobs.

Every job is a CLI command (`python -m app.jobs <command>`) rather than a timer
inside the API process (Phase 2 plan, decision 2). Two consequences that matter:
an operator can run any job by hand exactly as the scheduler runs it, and scheduling
does not duplicate itself when the API scales to more than one replica (§16.4).
"""
