"""One lightweight local-time scheduler; no chat event or proactive messaging."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


class DailyScheduler:
    def __init__(self, run, enabled, hour=15, minute=0, clock=None, sleep=asyncio.sleep):
        if type(hour) is not int or not 0 <= hour <= 23:
            raise ValueError("auto_hour must be 0-23")
        if type(minute) is not int or not 0 <= minute <= 59:
            raise ValueError("auto_minute must be 0-59")
        self.run, self.enabled = run, enabled
        self.hour, self.minute = hour, minute
        self.clock = clock or (lambda: datetime.now(SHANGHAI))
        self.sleep = sleep
        self.attempted_day = None

    async def tick(self):
        current = self.clock()
        if current.tzinfo is None:
            raise ValueError("scheduler clock must be timezone-aware")
        current = current.astimezone(SHANGHAI)
        day = current.date().isoformat()
        if (
            self.enabled()
            and (current.hour, current.minute) >= (self.hour, self.minute)
            and self.attempted_day != day
        ):
            record, message = await self.run(day)
            # Busy/cooldown are retried next minute, never a second paid attempt.
            # A durable duplicate claim is terminal for the day as well.
            if record is not None or (message and "今日自动探索已尝试" in message):
                self.attempted_day = day
            return record, message
        return None, None

    async def loop(self):
        while True:
            await self.tick()
            await self.sleep(60)
