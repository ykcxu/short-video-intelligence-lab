import json

from .config import default_config
from .orchestrator import Orchestrator


def main() -> None:
    config = default_config()
    orchestrator = Orchestrator(config)
    result = orchestrator.bootstrap()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
