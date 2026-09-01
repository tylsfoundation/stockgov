"""
Main FastAPI application entrypoint.
"""
from fastapi import FastAPI

app = FastAPI(
    title="Congressional Stock Trading API",
    description="API for Congressional stock trading analysis",
    version="0.1.0"
)


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"message": "Congressional Stock Trading API - Under Development"}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
