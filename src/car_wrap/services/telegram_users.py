"""Privacy-minimal Telegram audience tracking and daily report metrics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from car_wrap.db.models import DailyStatsDelivery, GenerationJob, TelegramUser


async def record_telegram_user(session: AsyncSession, telegram_user_id: int) -> None:
    """Record a private user without storing profile data or message contents."""

    now = datetime.now(UTC)
    statement = insert(TelegramUser).values(
        telegram_user_id=telegram_user_id,
        first_seen_at=now,
        last_seen_at=now,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[TelegramUser.telegram_user_id],
            set_={"last_seen_at": statement.excluded.last_seen_at},
        )
    )


@dataclass(frozen=True, slots=True)
class DailyStats:
    report_date: date
    total_users: int
    new_users: int
    active_users: int
    generation_users: int
    generations: int
    successful_generations: int

    def text(self) -> str:
        return (
            f"📊 Статистика CarWrap за {self.report_date:%d.%m.%Y}\n\n"
            f"Всего пользователей: {self.total_users}\n"  # noqa: RUF001
            f"Новых за сутки: {self.new_users}\n"
            f"Активных за сутки: {self.active_users}\n"
            f"Пользователей с генерацией: {self.generation_users}\n"  # noqa: RUF001
            f"Запущено генераций: {self.generations}\n"
            f"Успешно завершено: {self.successful_generations}"
        )


async def daily_stats(session: AsyncSession, report_date: date) -> DailyStats:
    start = datetime.combine(report_date, datetime.min.time(), tzinfo=UTC)
    end = start + timedelta(days=1)
    total_users = await session.scalar(select(func.count()).select_from(TelegramUser))
    new_users = await session.scalar(
        select(func.count()).select_from(TelegramUser).where(
            TelegramUser.first_seen_at >= start, TelegramUser.first_seen_at < end
        )
    )
    active_users = await session.scalar(
        select(func.count()).select_from(TelegramUser).where(
            TelegramUser.last_seen_at >= start, TelegramUser.last_seen_at < end
        )
    )
    generation_users = await session.scalar(
        select(func.count(func.distinct(GenerationJob.telegram_user_id))).where(
            GenerationJob.created_at >= start, GenerationJob.created_at < end
        )
    )
    generations = await session.scalar(
        select(func.count()).select_from(GenerationJob).where(
            GenerationJob.created_at >= start, GenerationJob.created_at < end
        )
    )
    successful_generations = await session.scalar(
        select(func.count()).select_from(GenerationJob).where(
            GenerationJob.created_at >= start,
            GenerationJob.created_at < end,
            GenerationJob.status == "succeeded",
        )
    )
    return DailyStats(
        report_date=report_date,
        total_users=int(total_users or 0),
        new_users=int(new_users or 0),
        active_users=int(active_users or 0),
        generation_users=int(generation_users or 0),
        generations=int(generations or 0),
        successful_generations=int(successful_generations or 0),
    )


async def report_was_sent(
    session: AsyncSession, report_date: date, telegram_user_id: int
) -> bool:
    value = await session.scalar(
        select(DailyStatsDelivery.telegram_user_id).where(
            DailyStatsDelivery.report_date
            == datetime.combine(report_date, datetime.min.time(), tzinfo=UTC),
            DailyStatsDelivery.telegram_user_id == telegram_user_id,
        )
    )
    return value is not None


async def mark_report_sent(
    session: AsyncSession, report_date: date, telegram_user_id: int
) -> None:
    session.add(
        DailyStatsDelivery(
            report_date=datetime.combine(report_date, datetime.min.time(), tzinfo=UTC),
            telegram_user_id=telegram_user_id,
            sent_at=datetime.now(UTC),
        )
    )
