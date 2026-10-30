from session_driver import main
import auxiliary_probe

errors = []
try:
    result = main(["--cleanup-only"])
    errors.extend(result.get("errors", []))
except Exception as error:
    errors.append(type(error).__name__)
try:
    auxiliary_probe.main(["--cleanup-only"])
except Exception as error:
    errors.append(type(error).__name__)
if errors:
    raise RuntimeError(str(errors))
