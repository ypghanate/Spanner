"""Session: event drain, media merge, plan/step tracking, newest-wins slow-loop scheduling."""
from __future__ import annotations

import asyncio

import pytest

from backend.contracts import BrainResponse, MediaUpdate, RepairPlan
from backend.mocks import fixture_json
from backend.session import Session, SessionManager, UnknownSession
from tests.helpers import run


def _plan() -> RepairPlan:
    return RepairPlan.model_validate(fixture_json("repair_plan"))


def test_drain_events_empties_the_queue():
    s = Session(id="a")
    s.queue_event(BrainResponse(phase="diagnosing", say="one"))
    s.queue_event(BrainResponse(phase="diagnosing", say="two"))
    assert [e.say for e in s.drain_events()] == ["one", "two"]
    assert s.drain_events() == []


def test_media_merges_into_next_event():
    s = Session(id="a")
    s.queue_media(MediaUpdate(kind="illustration", step_index=1, url="/media/x.png"))
    s.queue_event(BrainResponse(phase="guiding", say="hi"))
    events = s.drain_events()
    assert len(events) == 1
    assert events[0].media[0].url == "/media/x.png"


def test_media_without_event_becomes_media_only_event():
    s = Session(id="a", phase="guiding")
    s.queue_media(MediaUpdate(kind="scan3d", url="/media/scan.glb"))
    events = s.drain_events()
    assert len(events) == 1
    assert events[0].phase == "guiding" and events[0].say == ""
    assert events[0].media[0].kind == "scan3d"
    assert s.drain_events() == []


def test_apply_tracks_phase_plan_and_step():
    s = Session(id="a")
    plan = _plan()
    s.apply(BrainResponse(phase="guiding", plan=plan, current_step=1, step_status="in_progress"))
    assert s.phase == "guiding" and s.plan is plan
    assert s.current_step_index == 0 and s.current_step().index == 1
    s.apply(BrainResponse(phase="guiding", current_step=3, step_status="in_progress"))
    assert s.current_step().index == 3
    assert s.plan is plan, "a response without a plan must not clear it"
    s.apply(BrainResponse(phase="done"))
    assert s.steps_done == len(plan.steps)


def test_steps_done_counts_completed_steps():
    s = Session(id="a")
    s.apply(BrainResponse(phase="guiding", plan=_plan(), current_step=1, step_status="in_progress"))
    assert s.steps_done == 0
    s.apply(BrainResponse(phase="guiding", current_step=2, step_status="in_progress"))
    s.apply(BrainResponse(phase="guiding", current_step=2, step_status="done"))
    assert s.steps_done == 2


def test_manager_create_get_unknown_latest():
    m = SessionManager()
    a, b = m.create("phone"), m.create("phone")
    assert m.get(a.id) is a and m.latest() is b and a.id != b.id
    with pytest.raises(UnknownSession):
        m.get("nope")


def test_slow_loop_runs_one_at_a_time_and_newest_pending_wins():
    seen: list[str] = []

    async def main():
        release = asyncio.Event()

        async def handler(session, job):
            seen.append(f"start-{job}")
            if job == "first":
                await release.wait()
            seen.append(f"end-{job}")

        s = Session(id="a", slow_handler=handler)
        s.offer_keyframe("first")
        await asyncio.sleep(0.01)
        assert s.slow_busy
        s.offer_keyframe("second")
        s.offer_keyframe("third")      # replaces "second"
        await asyncio.sleep(0.01)
        assert seen == ["start-first"], "a running slow loop must not be cancelled or doubled"
        release.set()
        await asyncio.sleep(0.05)
        assert not s.slow_busy

    run(main())
    assert seen == ["start-first", "end-first", "start-third", "end-third"]


def test_slow_loop_survives_a_failing_job():
    seen: list[str] = []

    async def main():
        async def handler(session, job):
            if job == "boom":
                raise RuntimeError("x")
            seen.append(job)

        s = Session(id="a", slow_handler=handler)
        s.offer_keyframe("boom")
        await asyncio.sleep(0.02)
        assert not s.slow_busy
        s.offer_keyframe("ok")
        await asyncio.sleep(0.02)

    run(main())
    assert seen == ["ok"]


def test_spawn_swallows_errors_and_timeouts():
    async def main():
        s = Session(id="a")

        async def bad():
            raise ValueError("x")

        async def slow():
            await asyncio.sleep(5)

        t1 = s.spawn("bad", bad())
        t2 = s.spawn("slow", slow(), timeout=0.05)
        await asyncio.gather(t1, t2)   # neither raises

    run(main())


def test_end_cancels_background_tasks():
    async def main():
        m = SessionManager()
        s = m.create()
        started = asyncio.Event()

        async def forever():
            started.set()
            await asyncio.sleep(60)

        s.spawn("forever", forever())
        await started.wait()
        await m.end(s.id)
        assert s.ended_at is not None and not s._tasks

    run(main())


# --- push to a live socket ---------------------------------------------------

class FakeConn:
    def __init__(self, accept=True):
        self.accept, self.pushed = accept, []

    def push(self, r):
        if self.accept:
            self.pushed.append(r)
        return self.accept


def test_queue_event_is_pushed_to_a_connected_socket_and_not_queued():
    s = Session(id="a")
    s.live_conn = FakeConn()
    s.queue_event(BrainResponse(phase="guiding", say="now"))
    assert [r.say for r in s.live_conn.pushed] == ["now"]
    assert s.drain_events() == []


def test_queue_event_falls_back_to_the_queue_when_the_socket_refuses():
    s = Session(id="a")
    s.live_conn = FakeConn(accept=False)               # closed or not yet running
    s.queue_event(BrainResponse(phase="guiding", say="later"))
    assert [e.say for e in s.drain_events()] == ["later"]


def test_pending_media_rides_a_pushed_event():
    s = Session(id="a")
    s.pending_media.append(MediaUpdate(kind="clip", step_index=1, url="/media/c.mp4"))
    s.live_conn = FakeConn()
    s.queue_event(BrainResponse(phase="guiding", say="hi"))
    assert s.live_conn.pushed[0].media[0].url == "/media/c.mp4" and s.pending_media == []


def test_queue_media_with_a_socket_is_pushed_at_once_as_a_media_only_event():
    s = Session(id="a", phase="guiding")
    s.live_conn = FakeConn()
    s.queue_media(MediaUpdate(kind="scan3d", url="/media/scan.glb"))
    (r,) = s.live_conn.pushed
    assert r.phase == "guiding" and r.say == "" and r.media[0].kind == "scan3d"
    assert s.pending_media == [] and s.drain_events() == []


def test_step_check_starts_empty_and_shows_in_the_state_dump():
    s = Session(id="a")
    assert s.last_step_check is None and s.state_dict()["last_step_check"] is None
    s.last_step_check = {"step_status": "done"}
    assert s.state_dict()["last_step_check"] == {"step_status": "done"}
