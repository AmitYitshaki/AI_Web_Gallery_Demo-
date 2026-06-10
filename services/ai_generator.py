from io import BytesIO
import os
import time
from dataclasses import dataclass
from typing import Any, Final

import requests
from huggingface_hub import InferenceClient
from huggingface_hub.errors import HfHubHTTPError, InferenceTimeoutError
from PIL import Image, UnidentifiedImageError


DEFAULT_MODEL: Final[str] = "black-forest-labs/FLUX.1-Kontext-dev"
DEFAULT_PROVIDER: Final[str] = "fal-ai"
DEFAULT_TIMEOUT_SECONDS: Final[int] = 120
MAX_SOURCE_IMAGE_BYTES: Final[int] = 10 * 1024 * 1024
MAX_INFERENCE_IMAGE_EDGE: Final[int] = 1024
INFERENCE_IMAGE_QUALITY: Final[int] = 88
NETWORK_RETRY_COUNT: Final[int] = 2


class ImageGenerationError(Exception):
    """Base exception for expected image-generation failures."""

    status_code: int = 500

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class ImageGenerationConfigError(ImageGenerationError):
    """Raised when the service is missing required configuration."""

    status_code = 500


class ImageGenerationValidationError(ImageGenerationError):
    """Raised when user-provided input is invalid."""

    status_code = 400


class ImageGenerationUpstreamError(ImageGenerationError):
    """Raised when Hugging Face or image download calls fail."""

    status_code = 502


class ImageGenerationTimeoutError(ImageGenerationError):
    """Raised when an upstream request times out."""

    status_code = 504


class ImageGenerationCreditsExhaustedError(ImageGenerationError):
    """Raised when the Hugging Face account has no included credits left."""

    status_code = 402


@dataclass(frozen=True)
class GeneratedImage:
    """Generated image bytes returned by the inference provider."""

    content: bytes
    content_type: str


class ImageGenerationService:
    """Client for Hugging Face image-to-image generation."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.api_key = api_key or os.getenv("HUGGINGFACE_API_KEY")
        self.model = model or os.getenv("HUGGINGFACE_MODEL", DEFAULT_MODEL)
        self.provider = provider or os.getenv("HUGGINGFACE_PROVIDER", DEFAULT_PROVIDER)
        self.timeout_seconds = timeout_seconds

        if not self.api_key:
            raise ImageGenerationConfigError("Hugging Face API key is not configured.")

        if self.provider == "hf-inference":
            raise ImageGenerationConfigError(
                f"HUGGINGFACE_PROVIDER={self.provider} is not compatible with the configured image-to-image flow. "
                "Use HUGGINGFACE_PROVIDER=fal-ai with HUGGINGFACE_MODEL=black-forest-labs/FLUX.1-Kontext-dev."
            )

    def generate_from_image_bytes(self, source_image: bytes, prompt: str) -> GeneratedImage:
        """Call Hugging Face with local source image bytes and return image bytes."""
        cleaned_prompt = prompt.strip()

        if not source_image:
            raise ImageGenerationValidationError("Source image is required.")

        if not cleaned_prompt:
            raise ImageGenerationValidationError("prompt is required.")

        if len(source_image) > MAX_SOURCE_IMAGE_BYTES:
            raise ImageGenerationValidationError("Source image is too large. Please upload an image under 10 MB.")

        optimized_image = self._prepare_source_image(source_image)
        return self._call_hugging_face(source_image=optimized_image, prompt=cleaned_prompt)

    def _prepare_source_image(self, source_image: bytes) -> bytes:
        """Resize and compress the source image to keep provider uploads stable."""
        try:
            with Image.open(BytesIO(source_image)) as image:
                image = image.convert("RGB")
                image.thumbnail((MAX_INFERENCE_IMAGE_EDGE, MAX_INFERENCE_IMAGE_EDGE))

                buffer = BytesIO()
                image.save(buffer, format="JPEG", quality=INFERENCE_IMAGE_QUALITY, optimize=True)
                return buffer.getvalue()
        except UnidentifiedImageError as exc:
            raise ImageGenerationValidationError("Uploaded file is not a readable image.") from exc

    def _call_hugging_face(self, source_image: bytes, prompt: str) -> GeneratedImage:
        """Send the image-to-image request through Hugging Face Inference Providers."""
        client = InferenceClient(
            provider=self.provider,
            api_key=self.api_key,
            timeout=self.timeout_seconds,
        )

        for attempt in range(NETWORK_RETRY_COUNT + 1):
            try:
                output_image = client.image_to_image(
                    source_image,
                    prompt=prompt,
                    model=self.model,
                    num_inference_steps=20,
                    guidance_scale=7.5,
                )
                break
            except (TimeoutError, InferenceTimeoutError) as exc:
                if attempt < NETWORK_RETRY_COUNT:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise ImageGenerationTimeoutError("AI generation timed out. Please try again.") from exc
            except HfHubHTTPError as exc:
                raise self._map_hugging_face_http_error(exc) from exc
            except requests.RequestException as exc:
                if attempt < NETWORK_RETRY_COUNT:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                message = str(exc) or exc.__class__.__name__
                raise ImageGenerationUpstreamError(
                    f"Could not reach the AI generation service. Network detail: {message}"
                ) from exc
            except Exception as exc:
                message = str(exc) or exc.__class__.__name__
                raise ImageGenerationUpstreamError(f"AI generation failed. Detail: {message}") from exc

        return self._normalize_generated_image(output_image)

    def _normalize_generated_image(self, output_image: Any) -> GeneratedImage:
        """Convert Hugging Face output into PNG bytes for local storage."""
        if isinstance(output_image, bytes):
            return GeneratedImage(content=output_image, content_type="image/png")

        if hasattr(output_image, "save"):
            buffer = BytesIO()
            output_image.save(buffer, format="PNG")
            return GeneratedImage(content=buffer.getvalue(), content_type="image/png")

        raise ImageGenerationUpstreamError("Hugging Face returned an unsupported image response.")

    def _map_hugging_face_http_error(self, error: HfHubHTTPError) -> ImageGenerationError:
        """Map provider HTTP errors to safe API responses."""
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)
        message = str(error)

        if status_code == 402 or "depleted your monthly included credits" in message.lower():
            return ImageGenerationCreditsExhaustedError(
                "Hugging Face credits are depleted, so demo fallback output was used."
            )

        if status_code in {401, 403}:
            return ImageGenerationUpstreamError("Hugging Face rejected the API key.", status_code=500)

        if status_code == 400:
            return ImageGenerationValidationError(
                f"Hugging Face rejected the image or prompt. Detail: {self._safe_error_message(message)}"
            )

        if status_code == 503:
            return ImageGenerationUpstreamError(
                "The AI model is loading or temporarily unavailable. Please try again shortly.",
                status_code=503,
            )

        if status_code and status_code >= 500:
            return ImageGenerationUpstreamError(
                f"Hugging Face is currently unavailable. Detail: {self._safe_error_message(message)}"
            )

        if "provider mapping" in message.lower():
            return ImageGenerationConfigError("The configured Hugging Face model is not available for this provider.")

        return ImageGenerationUpstreamError(f"AI generation failed. Detail: {self._safe_error_message(message)}")

    def _safe_error_message(self, message: str) -> str:
        """Trim provider errors before sending them to the browser."""
        return " ".join(message.split())[:500] or "No provider detail returned."
