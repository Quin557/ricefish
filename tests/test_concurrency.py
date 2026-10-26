"""并发闸门测试：上限、同会话互斥、队列上限、超时。"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ricefish.core.concurrency import Busy, ConcurrencyGate


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_limit(self):
        gate = ConcurrencyGate(max_parallel=2, max_queue=10, acquire_timeout=2)
        entered: list[str] = []
        release = asyncio.Event()

        async def worker(name: str):
            async with gate.slot(name):
                entered.append(name)
                await release.wait()

        tasks = [asyncio.create_task(worker(f"g{i}")) for i in range(2)]
        await asyncio.sleep(0.05)
        self.assertEqual(len(entered), 2)

        third = asyncio.create_task(worker("g3"))
        await asyncio.sleep(0.05)
        self.assertEqual(len(entered), 2)  # 第三个还在排队
        self.assertEqual(gate.waiting_count, 1)

        release.set()
        await asyncio.gather(*tasks, third)
        self.assertEqual(len(entered), 3)
        self.assertEqual(gate.active_count, 0)

    async def test_same_scope_rejected(self):
        gate = ConcurrencyGate(max_parallel=4, max_queue=10, acquire_timeout=2)
        release = asyncio.Event()

        async def hold():
            async with gate.slot("group:1"):
                await release.wait()

        task = asyncio.create_task(hold())
        await asyncio.sleep(0.05)
        with self.assertRaises(Busy) as ctx:
            async with gate.slot("group:1"):
                pass
        self.assertEqual(ctx.exception.reason, "same_scope")
        release.set()
        await task

    async def test_queue_full(self):
        gate = ConcurrencyGate(max_parallel=1, max_queue=0, acquire_timeout=2)
        release = asyncio.Event()

        async def hold():
            async with gate.slot("a"):
                await release.wait()

        task = asyncio.create_task(hold())
        await asyncio.sleep(0.05)
        with self.assertRaises(Busy) as ctx:
            async with gate.slot("b"):
                pass
        self.assertEqual(ctx.exception.reason, "queue_full")
        release.set()
        await task

    async def test_acquire_timeout(self):
        gate = ConcurrencyGate(max_parallel=1, max_queue=5, acquire_timeout=0.15)
        release = asyncio.Event()

        async def hold():
            async with gate.slot("a"):
                await release.wait()

        task = asyncio.create_task(hold())
        await asyncio.sleep(0.05)
        with self.assertRaises(Busy) as ctx:
            async with gate.slot("b"):
                pass
        self.assertEqual(ctx.exception.reason, "timeout")
        release.set()
        await task

    async def test_release_on_exception(self):
        gate = ConcurrencyGate(max_parallel=1, max_queue=2, acquire_timeout=1)
        with self.assertRaises(RuntimeError):
            async with gate.slot("a"):
                raise RuntimeError("boom")
        self.assertEqual(gate.active_count, 0)
        async with gate.slot("b"):
            self.assertEqual(gate.active_count, 1)

    async def test_snapshot(self):
        gate = ConcurrencyGate(max_parallel=3, max_queue=5, acquire_timeout=1)
        async with gate.slot("x"):
            snap = gate.snapshot()
            self.assertEqual(snap["active"], 1)
            self.assertEqual(snap["max_parallel"], 3)
            self.assertIn("x", snap["scopes"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
