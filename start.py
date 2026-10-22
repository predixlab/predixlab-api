#!/usr/bin/env python3
"""
PredixLab API Startup Script

This script starts the FastAPI application with proper configuration.
"""

import uvicorn
import os
import sys
from pathlib import Path

# Add the app directory to Python path
app_dir = Path(__file__).parent / "app"
sys.path.insert(0, str(app_dir))

def main():
    """Main function to start the application"""
    # Get configuration from environment
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    debug = os.getenv("DEBUG", "False").lower() == "true"
    workers = int(os.getenv("WORKERS", "1"))
    
    # Configure uvicorn
    config = {
        "app": "app.main:app",
        "host": host,
        "port": port,
        "reload": debug,
        "log_level": "debug" if debug else "info",
        "access_log": True,
    }
    
    # Add workers for production
    if not debug and workers > 1:
        config["workers"] = workers
    
    print(f"Starting PredixLab API on {host}:{port}")
    print(f"Debug mode: {debug}")
    print(f"Workers: {workers if not debug else 1}")
    print("-" * 50)
    
    # Start the server
    uvicorn.run(**config)

if __name__ == "__main__":
    main()
