import modal
import os

# 1. Define the Modal App
app = modal.App("questionnaire-ai-backend")

# 2. Build the container image
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libpq-dev", "gcc")
    .pip_install_from_requirements("requirements.txt")
    .add_local_dir("backend", remote_path="/root/backend")
    .add_local_file("alembic.ini", remote_path="/root/alembic.ini")
)

# 3. Reference the secret containing environment variables
secrets = [modal.Secret.from_name("questionnaire-secrets")]

# 4. Expose the FastAPI app via ASGI
@app.function(
    image=image,
    secrets=secrets,
    region="ap-south",  # Deployed in Asia-Pacific South (India)
    scaledown_window=300,  # keep warm for 5 minutes
)
@modal.asgi_app()
def fastapi_app():
    import sys
    backend_path = "/root/backend"
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
    
    from app.main import app as web_app
    return web_app
