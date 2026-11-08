import pytest

from target_ai.executor import Executor, ExecutorError


class Client:
    def __init__(self, validate_only):
        self.validate_only = validate_only


async def test_probe_refuses_a_client_that_is_not_validate_only(db, now):
    class Clock:
        def now(self):
            return now

    executor = Executor(db, lambda validate_only: Client(validate_only), None, Clock(), None)

    with pytest.raises(ExecutorError, match="validate_only"):
        await executor.probe_ig_access("act_1", "p1", "ig1", gateway=Client(False))
