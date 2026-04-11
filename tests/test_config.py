from __future__ import annotations

import unittest

from backend.platform.config import Settings, build_engine_kwargs


class EngineConfigTest(unittest.TestCase):
    def test_postgres_engine_uses_pre_ping_recycle_and_keepalives(self):
        settings = Settings(
            DATABASE_URL="postgresql://user:pass@db.example.com:5432/app",
            DB_POOL_PRE_PING=True,
            DB_POOL_RECYCLE_SECONDS=900,
            DB_POOL_SIZE=7,
            DB_MAX_OVERFLOW=3,
            DB_CONNECT_TIMEOUT_SECONDS=12,
            DB_TCP_KEEPALIVES=True,
            DB_KEEPALIVES_IDLE_SECONDS=25,
            DB_KEEPALIVES_INTERVAL_SECONDS=9,
            DB_KEEPALIVES_COUNT=4,
        )

        kwargs = build_engine_kwargs(settings)

        self.assertEqual(kwargs["echo"], False)
        self.assertEqual(kwargs["pool_pre_ping"], True)
        self.assertEqual(kwargs["pool_recycle"], 900)
        self.assertEqual(kwargs["pool_size"], 7)
        self.assertEqual(kwargs["max_overflow"], 3)
        self.assertEqual(kwargs["pool_use_lifo"], True)
        self.assertEqual(
            kwargs["connect_args"],
            {
                "connect_timeout": 12,
                "keepalives": 1,
                "keepalives_idle": 25,
                "keepalives_interval": 9,
                "keepalives_count": 4,
            },
        )

    def test_non_postgres_engine_skips_pool_tuning(self):
        settings = Settings(DATABASE_URL="sqlite:///translations.db")

        kwargs = build_engine_kwargs(settings)

        self.assertEqual(kwargs, {"echo": False})


if __name__ == "__main__":
    unittest.main()
