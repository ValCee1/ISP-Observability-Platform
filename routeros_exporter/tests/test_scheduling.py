"""Unit tests for the per-router backup/topology retry backoff.

CONFIRMED 2026-09-14, live, against a hAP AC Lite: retrying a failed
backup every ~30s poll cycle is only safe for a genuinely transient miss.
A command that fails the same way every time (that router's `/export
file=...` hung the full timeout on every attempt, load or no load) kept a
hung API connection in flight almost continuously when retried that fast,
which exhausted the router's API connections badly enough to break its
routine PPP/system polling too. `_schedule_after_attempt` is the pure
scheduling decision `run()` uses to back off after a couple of consecutive
misses instead of hammering the router forever.
"""

from routeros_exporter.__main__ import BACKOFF_AFTER_FAILURES, _schedule_after_attempt


def test_success_always_advances_and_resets_streak():
    advance, streak = _schedule_after_attempt(streak=0, succeeded=True)
    assert (advance, streak) == (True, 0)

    # Even mid-streak, a success resets it - the fast-retry path is
    # available again immediately after the very next genuine success.
    advance, streak = _schedule_after_attempt(streak=5, succeeded=True)
    assert (advance, streak) == (True, 0)


def test_failures_retry_fast_until_the_backoff_threshold():
    streak = 0
    for _ in range(BACKOFF_AFTER_FAILURES - 1):
        advance, streak = _schedule_after_attempt(streak, succeeded=False)
        assert advance is False  # don't advance -> retries next 30s cycle


def test_persistent_failure_backs_off_after_the_threshold():
    streak = 0
    for _ in range(BACKOFF_AFTER_FAILURES):
        advance, streak = _schedule_after_attempt(streak, succeeded=False)
    assert advance is True  # stop hammering - push the clock a full interval out
    assert streak == BACKOFF_AFTER_FAILURES

    # And it stays backed off (still incrementing, still advancing) for as
    # long as it keeps failing - not just a one-time flip.
    advance, streak = _schedule_after_attempt(streak, succeeded=False)
    assert advance is True
    assert streak == BACKOFF_AFTER_FAILURES + 1


def test_a_late_success_recovers_the_fast_path():
    streak = BACKOFF_AFTER_FAILURES + 3  # deep in backoff
    advance, streak = _schedule_after_attempt(streak, succeeded=True)
    assert (advance, streak) == (True, 0)

    # Next failure after that recovery starts the count fresh, not backed
    # off immediately.
    advance, streak = _schedule_after_attempt(streak, succeeded=False)
    assert advance is (BACKOFF_AFTER_FAILURES <= 1)
