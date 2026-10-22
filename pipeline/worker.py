"""Entry point of the model container (AWS Batch or docker run): fit one species against the lakehouse.

    python -m pipeline.worker tettigonia-viridissima
"""
from __future__ import annotations

import json
import logging
import sys

from . import sdm

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = sdm.run_species(sys.argv[1])
    print(json.dumps({"species": result["species"], "metrics": result["metrics"]}))
