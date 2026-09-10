"""Windows-friendly development server for the toolkit REST API.

The production container starts nginx and gunicorn from start_server.sh. That is
the recommended path for parity, but it is Linux-oriented. This helper starts
the same Flask app directly for local educational demos on Windows.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

from absl import flags

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

try:
  from dotenv import load_dotenv

  load_dotenv(REPO_ROOT / ".env")
except ImportError:
  pass

from src import rest_server


def main() -> None:
  default_config = (
      "config.demo.yaml"
      if (REPO_ROOT / "config.demo.yaml").exists()
      else "src/config.yaml"
  )
  config_file = os.environ.get("TOOLKIT_CONFIG_FILE", default_config)
  port = int(os.environ.get("TOOLKIT_DEV_PORT", "8088"))

  if not flags.FLAGS.is_parsed():
    flags.FLAGS([
        sys.argv[0],
        f"--config_file={config_file}",
    ])

  rest_server.flask_app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
  main()
