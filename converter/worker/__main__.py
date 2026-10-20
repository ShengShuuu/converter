"""Deferred: a standalone polling worker process.

The API process runs process_conversion (converter.worker.runner) in its single-thread executor, one batch at a
time, page by page; a future polling loop would claim 'queued' conversions and call the same function unchanged.
"""
