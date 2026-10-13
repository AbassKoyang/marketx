from io import BytesIO

import cloudinary.uploader
from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError


MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


def upload_market_image(*, market, uploaded_file):
    if uploaded_file.size > MAX_IMAGE_BYTES:
        raise ValidationError("Market images must be 5MB or smaller.")

    header = uploaded_file.read()
    uploaded_file.seek(0)
    try:
        image = Image.open(BytesIO(header))
        image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationError("Upload must be a valid image file.") from exc

    if image.format not in ALLOWED_FORMATS:
        raise ValidationError("Only JPEG, PNG, and WEBP images are allowed.")

    if not all([
        getattr(settings, "CLOUDINARY_CLOUD_NAME", ""),
        getattr(settings, "CLOUDINARY_API_KEY", ""),
        getattr(settings, "CLOUDINARY_API_SECRET", ""),
    ]):
        raise ValidationError("Cloudinary is not configured.")

    result = cloudinary.uploader.upload(
        uploaded_file,
        folder="marketx/markets",
        public_id=f"market-{market.id}",
        overwrite=True,
        resource_type="image",
        allowed_formats=["jpg", "jpeg", "png", "webp"],
    )
    market.image_url = result["secure_url"]
    market.image_public_id = result["public_id"]
    market.save(update_fields=["image_url", "image_public_id", "updated_at"])
    return market
