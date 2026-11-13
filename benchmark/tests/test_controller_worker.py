"""The benchmark selects the product Turn provider and pins its pending topology."""
import pytest

from benchmark.runtime.codex import Execution
from benchmark.runtime.worker import pending_turn


@pytest.mark.parametrize("original", [False, True])
def test_recovery_pins_original_topology(tmp_path, original):
    marker = tmp_path / "pending.json"
    def execution(dual):
        return Execution(mode="turn", controller_worker=dual, validation_command=["check"])

    first = pending_turn(marker, "first", execution(original))
    with pytest.raises(ValueError, match="original worker topology"):
        pending_turn(marker, "retry", execution(not original))
    assert pending_turn(marker, "retry", execution(original)) == first
