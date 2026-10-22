import os

import yaml

UA = "jobwatch/0.1"
AMQP_URL = os.environ.get("AMQP_URL", "amqp://guest:guest@localhost/")


def load() -> dict:
    # re-read every cycle: edit config.yaml and the next poll picks it up, no restart
    with open(os.environ.get("CONFIG_PATH", "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)
