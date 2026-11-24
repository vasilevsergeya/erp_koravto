import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

from app.core.db import SessionLocal
from app.models import Customer, CustomerAvatar
from app.services.customer_avatars import refresh_one, remember_avatar


@pytest.mark.skipif(os.getenv("CRM_INTEGRATION_TEST") != "1", reason="Isolated Postgres required")
def test_image_is_cached_and_downloads_are_throttled():
    with SessionLocal() as db:
        c = Customer(name="Photo fixture")
        db.add(c)
        db.flush()
        remember_avatar(
            db,
            c.id,
            {"status": "inbound", "contact": {"avatarUri": "https://images.example.test/photo"}},
        )
        db.commit()
        owner = c.id
    data = b"\xff\xd8\xff" + b"test"
    with patch(
        "app.services.customer_avatars.download_attachment",
        new_callable=AsyncMock,
        return_value=data,
    ) as download:
        assert asyncio.run(refresh_one())
        assert not asyncio.run(refresh_one())
        download.assert_awaited_once()
    with SessionLocal() as db:
        image = db.get(CustomerAvatar, owner)
        assert image.photo == data and image.mime == "image/jpeg"
        assert (image.next_attempt_at - image.updated_at).days >= 6
