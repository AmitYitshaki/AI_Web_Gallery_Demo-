from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")


import os
import uuid
import json
import logging
from functools import wraps
from io import BytesIO
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from flask import Flask, jsonify, redirect, render_template, request, url_for
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

import firebase_admin
from firebase_admin import auth, credentials, firestore
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from services.ai_generator import (
    ImageGenerationCreditsExhaustedError,
    ImageGenerationError,
    ImageGenerationService,
)


logger = logging.getLogger(__name__)


UPLOAD_DIR = BASE_DIR / "static" / "uploads"
UPLOAD_URL_PREFIX = "/static/uploads"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    ensure_upload_directory()
    initialize_firebase()

    @app.route("/")
    def index() -> Any:
        """Public landing page with Firebase Auth UI."""
        return render_template("index.html")

    @app.route("/dashboard")
    @login_required_page
    def dashboard() -> Any:
        """Protected user gallery page."""
        return render_template("dashboard.html")

    @app.route("/studio")
    @login_required_page
    def studio() -> Any:
        """Protected AI chat studio page."""
        return render_template("studio.html")

    @app.route("/api/session", methods=["POST"])
    def create_session() -> Any:
        """
        Verify a Firebase ID token and store it in a secure-ish HTTP-only cookie.

        The frontend calls this immediately after Firebase Auth signs the user in.
        Route protection then checks this cookie before rendering private pages.
        """
        data = request.get_json(silent=True) or {}
        id_token = data.get("idToken")

        if not id_token:
            return jsonify({"error": "Missing Firebase ID token."}), 400

        try:
            decoded_token = auth.verify_id_token(id_token)
        except Exception as error:
            logger.exception("Firebase ID token verification failed.")
            return jsonify({"error": f"Invalid Firebase ID token: {error}"}), 401

        response = jsonify({"uid": decoded_token["uid"]})
        response.set_cookie(
            "firebase_id_token",
            id_token,
            httponly=True,
            secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
            samesite="Lax",
            max_age=60 * 60,
        )
        return response

    @app.route("/api/session", methods=["DELETE"])
    def delete_session() -> Any:
        """Clear the local session cookie after frontend Firebase sign-out."""
        response = jsonify({"ok": True})
        response.delete_cookie("firebase_id_token")
        return response

    @app.route("/api/me", methods=["GET"])
    @login_required_api
    def current_user() -> Any:
        """Return the authenticated user's Firebase identity."""
        return jsonify({"uid": request.user["uid"], "email": request.user.get("email")})

    @app.route("/api/firebase-config", methods=["GET"])
    def firebase_config() -> Any:
        """Expose the public Firebase client config used by the browser SDK."""
        return jsonify(get_firebase_client_config())

    @app.route("/api/upload", methods=["POST"])
    @login_required_api
    def upload_image() -> Any:
        """Receive an original image upload and save it to local static storage."""
        uploaded_file = request.files.get("image")

        if not uploaded_file or uploaded_file.filename == "":
            return jsonify({"error": "Image file is required."}), 400

        try:
            image_url = save_uploaded_file(
                uploaded_file=uploaded_file,
                uid=request.user["uid"],
            )
        except ValueError as error:
            return jsonify({"error": str(error)}), 400

        return jsonify(
            {
                "imageUrl": image_url,
                "fileName": uploaded_file.filename,
            }
        )

    @app.route("/api/generate", methods=["POST"])
    @login_required_api
    def generate_image() -> Any:
        """
        Generate an AI image variation using Hugging Face.

        The generated URL is intentionally ephemeral. The backend does not write it
        to Firestore; the user must explicitly save it from the studio UI first.
        """
        data = request.get_json(silent=True) or {}
        image_url = data.get("imageUrl")
        prompt = data.get("prompt")

        if not image_url:
            return jsonify({"error": "imageUrl is required."}), 400

        if not prompt or not prompt.strip():
            return jsonify({"error": "prompt is required."}), 400

        try:
            source_image = read_local_upload(image_url)
            service = ImageGenerationService()
            generated_image = service.generate_from_image_bytes(
                source_image=source_image,
                prompt=prompt.strip(),
            )
            generated_url = save_generated_image(
                uid=request.user["uid"],
                image_bytes=generated_image.content,
                content_type=generated_image.content_type,
            )
            return jsonify(
                {
                    "imageUrl": generated_url,
                    "prompt": prompt.strip(),
                    "sourceImageUrl": image_url,
                }
            )
        except ImageGenerationCreditsExhaustedError as error:
            source_image = read_local_upload(image_url)
            fallback_image = create_demo_generated_image(
                source_image=source_image,
                prompt=prompt.strip(),
            )
            generated_url = save_generated_image(
                uid=request.user["uid"],
                image_bytes=fallback_image,
                content_type="image/png",
            )
            return jsonify(
                {
                    "imageUrl": generated_url,
                    "prompt": prompt.strip(),
                    "sourceImageUrl": image_url,
                    "isFallback": True,
                    "message": str(error),
                }
            )
        except ImageGenerationError as error:
            return jsonify({"error": str(error)}), error.status_code
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        except Exception:
            return jsonify({"error": "AI generation failed. Please try again."}), 500

    @app.route("/api/images/<image_id>", methods=["DELETE"])
    @login_required_api
    def delete_gallery_image(image_id: str) -> Any:
        """Delete one original image, its saved variations, and local files."""
        try:
            deleted_count = delete_original_image(
                uid=request.user["uid"],
                image_id=image_id,
            )
        except ValueError as error:
            return jsonify({"error": str(error)}), 404

        return jsonify({"ok": True, "deletedFiles": deleted_count})

    @app.errorhandler(404)
    def not_found(_error: Exception) -> Any:
        return jsonify({"error": "Not found."}), 404

    @app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(_error: RequestEntityTooLarge) -> Any:
        return jsonify({"error": "Image is too large. Please upload an image under 10 MB."}), 413

    return app


def initialize_firebase() -> None:
    """
    Initialize Firebase Admin once.

    Supported configuration:
    - FIREBASE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
    - FIREBASE_SERVICE_ACCOUNT_PATH=/absolute/path/to/service-account.json
    - or a local firebase_config.json file in the project root
    """
    if firebase_admin._apps:
        return

    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    service_account_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "firebase_config.json")
    service_account_path_was_configured = bool(os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH"))

    if service_account_json:
        try:
            service_account_info = json.loads(service_account_json)
        except json.JSONDecodeError as error:
            raise RuntimeError("FIREBASE_SERVICE_ACCOUNT_JSON is not valid JSON.") from error

        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred)
        return

    if os.path.exists(service_account_path):
        cred = credentials.Certificate(service_account_path)
        firebase_admin.initialize_app(cred)
        return

    if service_account_path_was_configured:
        raise RuntimeError(f"Firebase service account file was not found at {service_account_path}.")

    # Application Default Credentials are useful in deployed Google environments.
    firebase_admin.initialize_app()


def get_firestore_client() -> Any:
    """Return a Firestore client for future backend-side data operations."""
    return firestore.client()


def ensure_upload_directory() -> None:
    """Create local upload storage if this is the first app startup."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def save_uploaded_file(uploaded_file: FileStorage, uid: str) -> str:
    """Validate and save a user-uploaded original image locally."""
    original_name = secure_filename(uploaded_file.filename or "")
    extension = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""

    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("Only JPG, PNG, and WEBP images are supported.")

    if uploaded_file.mimetype and not uploaded_file.mimetype.startswith("image/"):
        raise ValueError("Uploaded file must be an image.")

    filename = f"original_{uid}_{uuid.uuid4().hex}.{extension}"
    destination = UPLOAD_DIR / filename
    uploaded_file.save(destination)

    return f"{UPLOAD_URL_PREFIX}/{filename}"


def read_local_upload(image_url: str) -> bytes:
    """Read a previously uploaded local image URL from static/uploads."""
    file_path = local_upload_path_from_url(image_url)

    if not file_path.is_file():
        raise ValueError("Source image could not be found.")

    if file_path.stat().st_size > MAX_UPLOAD_BYTES:
        raise ValueError("Source image is too large. Please upload an image under 10 MB.")

    return file_path.read_bytes()


def local_upload_path_from_url(image_url: str) -> Path:
    """Resolve a static upload URL to a safe absolute filesystem path."""
    parsed_path = unquote(urlparse(image_url).path)

    if not parsed_path.startswith(f"{UPLOAD_URL_PREFIX}/"):
        raise ValueError("Only locally uploaded images can be used.")

    filename = parsed_path.removeprefix(f"{UPLOAD_URL_PREFIX}/")
    file_path = (UPLOAD_DIR / filename).resolve()
    upload_root = UPLOAD_DIR.resolve()

    if upload_root not in file_path.parents:
        raise ValueError("Invalid upload path.")

    return file_path


def save_generated_image(uid: str, image_bytes: bytes, content_type: str) -> str:
    """Save generated image bytes locally and return a static URL."""
    extension = CONTENT_TYPE_EXTENSIONS.get(content_type, "png")
    filename = f"generated_{uid}_{uuid.uuid4().hex}.{extension}"
    destination = UPLOAD_DIR / filename
    destination.write_bytes(image_bytes)

    return f"{UPLOAD_URL_PREFIX}/{filename}"


def create_demo_generated_image(source_image: bytes, prompt: str) -> bytes:
    """Create a local demo image when hosted AI credits are unavailable."""
    with Image.open(BytesIO(source_image)) as image:
        image = image.convert("RGB")
        image.thumbnail((1024, 1024))

        canvas = Image.new("RGB", (1024, 1024), (18, 24, 38))
        x = (canvas.width - image.width) // 2
        y = (canvas.height - image.height) // 2

        edited = ImageEnhance.Color(image).enhance(1.35)
        edited = ImageEnhance.Contrast(edited).enhance(1.18)
        edited = edited.filter(ImageFilter.SHARPEN)
        canvas.paste(edited, (x, y))

        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw.rectangle((0, 0, canvas.width, 132), fill=(20, 21, 33, 215))
        draw.rectangle((0, canvas.height - 190, canvas.width, canvas.height), fill=(20, 21, 33, 225))
        draw.line((0, 132, canvas.width, 132), fill=(56, 189, 248, 160), width=3)

        title_font = ImageFont.load_default()
        body_font = ImageFont.load_default()
        draw.text((34, 32), "Demo AI Variation", fill=(246, 247, 255, 255), font=title_font)
        draw.text((34, 72), "Hugging Face credits are depleted. This local fallback keeps the demo flow working.", fill=(174, 181, 207, 255), font=body_font)

        wrapped_prompt = wrap_text(f"Prompt: {prompt}", max_chars=96)
        draw.text((34, canvas.height - 150), wrapped_prompt, fill=(246, 247, 255, 255), font=body_font)

        final_image = Image.alpha_composite(canvas.convert("RGBA"), overlay)
        buffer = BytesIO()
        final_image.save(buffer, format="PNG")
        return buffer.getvalue()


def wrap_text(text: str, max_chars: int) -> str:
    """Wrap plain text for the generated fallback image annotation."""
    words = text.split()
    lines = []
    current_line = []

    for word in words:
        candidate = " ".join([*current_line, word])
        if len(candidate) > max_chars and current_line:
            lines.append(" ".join(current_line))
            current_line = [word]
        else:
            current_line.append(word)

    if current_line:
        lines.append(" ".join(current_line))

    return "\n".join(lines[:4])


def delete_original_image(uid: str, image_id: str) -> int:
    """Delete an original image document, variation docs, and local image files."""
    db = get_firestore_client()
    image_ref = db.collection("users").document(uid).collection("original_images").document(image_id)
    image_snapshot = image_ref.get()

    if not image_snapshot.exists:
        raise ValueError("Gallery image was not found.")

    file_urls = []
    image_data = image_snapshot.to_dict() or {}
    if image_data.get("url"):
        file_urls.append(image_data["url"])

    variations_ref = image_ref.collection("processed_variations")
    variation_snapshots = list(variations_ref.stream())

    for variation_snapshot in variation_snapshots:
        variation_data = variation_snapshot.to_dict() or {}
        if variation_data.get("url"):
            file_urls.append(variation_data["url"])

    deleted_files = 0
    for file_url in file_urls:
        if delete_local_upload_file(file_url):
            deleted_files += 1

    for variation_snapshot in variation_snapshots:
        variation_snapshot.reference.delete()

    image_ref.delete()
    return deleted_files


def delete_local_upload_file(image_url: str) -> bool:
    """Delete a local upload file if it exists; ignore already-missing files."""
    try:
        file_path = local_upload_path_from_url(image_url)
    except ValueError:
        return False

    if not file_path.exists():
        return False

    file_path.unlink()
    return True


def get_firebase_client_config() -> dict[str, str]:
    """Read Firebase web app settings from environment variables."""
    return {
        "apiKey": os.getenv("FIREBASE_API_KEY", ""),
        "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN", ""),
        "projectId": os.getenv("FIREBASE_PROJECT_ID", ""),
        "messagingSenderId": os.getenv("FIREBASE_MESSAGING_SENDER_ID", ""),
        "appId": os.getenv("FIREBASE_APP_ID", ""),
    }


def verify_request_token() -> dict[str, Any] | None:
    """
    Verify auth from either the Authorization header or session cookie.

    API calls should send: Authorization: Bearer <Firebase ID token>
    Protected page requests use the HTTP-only cookie created by /api/session.
    """
    auth_header = request.headers.get("Authorization", "")
    token = None

    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
    else:
        token = request.cookies.get("firebase_id_token")

    if not token:
        return None

    try:
        return auth.verify_id_token(token)
    except Exception:
        return None


def login_required_page(view_func: Callable[..., Any]) -> Callable[..., Any]:
    """Redirect unauthenticated browser requests back to the landing page."""

    @wraps(view_func)
    def wrapped_view(*args: Any, **kwargs: Any) -> Any:
        decoded_token = verify_request_token()

        if not decoded_token:
            return redirect(url_for("index"))

        request.user = decoded_token
        return view_func(*args, **kwargs)

    return wrapped_view


def login_required_api(view_func: Callable[..., Any]) -> Callable[..., Any]:
    """Reject unauthenticated API calls with a JSON 401 response."""

    @wraps(view_func)
    def wrapped_view(*args: Any, **kwargs: Any) -> Any:
        decoded_token = verify_request_token()

        if not decoded_token:
            return jsonify({"error": "Authentication required."}), 401

        request.user = decoded_token
        return view_func(*args, **kwargs)

    return wrapped_view


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.getenv("FLASK_RUN_HOST", "127.0.0.1"),
        port=int(os.getenv("FLASK_RUN_PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "true").lower() == "true",
    )
