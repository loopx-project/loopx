"""Run uninstrumented primary measurements before separate auxiliary probes."""
import json
from pathlib import Path

import auxiliary_probe
import session_driver


if __name__ == "__main__":
    primary = session_driver.main(["--run"])
    auxiliary = auxiliary_probe.main(["--run"])
    if auxiliary != 0:
        raise RuntimeError(f"Auxiliary runtime probes failed with status {auxiliary}")
    Path(__file__).with_name("qualification-run-result.json").write_text(
        json.dumps({"primary": primary, "auxiliary": auxiliary}, indent=2) + "\n")
