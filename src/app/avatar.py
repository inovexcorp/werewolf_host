import base64
import io
import os

from PIL import Image, ImageDraw

from app.config import settings

ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}
DEFAULT_AVATAR_FILENAME = "default.png"


def ensure_avatar_dir(avatar_dir: str | None = None) -> None:
    """Create avatar directory and generate default avatar if missing."""
    d = avatar_dir or settings.avatar_dir
    os.makedirs(d, exist_ok=True)

    default_path = os.path.join(d, DEFAULT_AVATAR_FILENAME)
    if not os.path.exists(default_path):
        _generate_default_avatar(default_path)


def default_avatar_path() -> str:
    return f"{settings.avatar_dir}/{DEFAULT_AVATAR_FILENAME}"


def process_avatar(base64_data: str, team_name: str) -> str:
    """Validate, resize, and save an avatar image. Returns the relative file path."""
    try:
        raw = base64.b64decode(base64_data, validate=True)
    except Exception as exc:
        raise ValueError("Invalid base64 data") from exc

    if len(raw) > settings.avatar_max_upload_bytes:
        max_bytes = settings.avatar_max_upload_bytes
        raise ValueError(f"Avatar too large: {len(raw)} bytes (max {max_bytes})")

    try:
        img = Image.open(io.BytesIO(raw))
    except Exception as exc:
        raise ValueError("Could not decode image") from exc

    if img.format not in ALLOWED_FORMATS:
        raise ValueError(
            f"Unsupported image format: {img.format}. Allowed: {ALLOWED_FORMATS}"
        )

    max_px = settings.avatar_max_size_px
    img.thumbnail((max_px, max_px), Image.LANCZOS)

    # Convert to RGBA then save as PNG
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")

    safe_name = team_name.lower().replace(" ", "_")
    filename = f"agent_{safe_name}.png"
    filepath = os.path.join(settings.avatar_dir, filename)

    img.save(filepath, format="PNG")
    return f"{settings.avatar_dir}/{filename}"


def _generate_default_avatar(path: str) -> None:
    """Generate a simple placeholder avatar (gray circle with silhouette)."""
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background circle
    draw.ellipse([8, 8, size - 8, size - 8], fill=(100, 100, 120, 255))

    # Simple head (circle)
    cx, cy = size // 2, size // 2 - 20
    r = 35
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(180, 180, 195, 255))

    # Simple body (arc/ellipse)
    bx, by = size // 2, size // 2 + 55
    bw, bh = 55, 40
    draw.ellipse([bx - bw, by - bh, bx + bw, by + bh], fill=(180, 180, 195, 255))

    img.save(path, format="PNG")
