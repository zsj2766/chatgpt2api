from __future__ import annotations

import argparse
import uvicorn
from api import create_app

app = create_app()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run("main:app", reload=args.reload, port=args.port, access_log=False, log_level="info")
