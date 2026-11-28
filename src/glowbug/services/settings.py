from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from glowbug.models.app_setting import AppSetting


async def get_setting(session: AsyncSession, key: str) -> str | None:
    return await session.scalar(select(AppSetting.value).where(AppSetting.key == key))


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    setting = await session.get(AppSetting, key)
    if setting is None:
        session.add(AppSetting(key=key, value=value))
    else:
        setting.value = value
    await session.flush()
