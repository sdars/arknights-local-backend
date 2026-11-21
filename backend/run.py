import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("ARK_HOST", "127.0.0.1"),
        port=int(os.environ.get("ARK_PORT", "9443")),
        log_level="info",
    )

