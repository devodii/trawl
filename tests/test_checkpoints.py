from trawl.db import checkpoints


class FakePool:
    def __init__(self, row=None):
        self.row = row
        self.calls = []

    async def fetchrow(self, *args):
        self.calls.append(args)
        return self.row

    async def execute(self, *args):
        self.calls.append(args)


async def test_a_missing_checkpoint_is_none_and_a_done_one_reports_done():
    assert await checkpoints.get(FakePool(), "job", "key") is None

    saved = await checkpoints.get(
        FakePool({"status": "done", "cursor": {"page": 3}, "attempts": 1}), "job", "key"
    )

    assert saved and saved.done and saved.cursor == {"page": 3}


async def test_putting_a_checkpoint_sends_the_cursor_and_error():
    pool = FakePool()

    await checkpoints.put(pool, "job", "key", "failed", {"page": 2}, "boom")

    assert pool.calls[0][1:] == ("job", "key", "failed", {"page": 2}, "boom")
