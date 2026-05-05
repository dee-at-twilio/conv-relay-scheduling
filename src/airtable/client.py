from __future__ import annotations
import logging
import time
import airtable

from src.config import config

logger = logging.getLogger(__name__)

# Airtable allows 5 requests/second per base
_MIN_INTERVAL = 0.2


class AirtableClient:
    def __init__(self):
        self._at = airtable.Airtable(config.airtable_base_id, config.airtable_api_key)
        self._last_call: float = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        self._last_call = time.monotonic()

    def get_all(self, table: str, filter_by_formula: str | None = None) -> list[dict]:
        self._throttle()
        kwargs = {}
        if filter_by_formula:
            kwargs["filter_by_formula"] = filter_by_formula
        logger.info("airtable GET table=%s formula=%s", table, filter_by_formula)
        try:
            response = self._at.get(table, **kwargs)
            records = response.get("records", [])
            logger.info("airtable GET table=%s → %d record(s)", table, len(records))
            return records
        except Exception as e:
            logger.error("airtable GET failed table=%s formula=%s error=%s", table, filter_by_formula, e)
            raise

    def create_record(self, table: str, fields: dict) -> dict:
        self._throttle()
        logger.info("airtable CREATE table=%s fields=%s", table, fields)
        try:
            record = self._at.create(table, fields)
            logger.info("airtable CREATE table=%s → id=%s", table, record.get("id"))
            return record
        except Exception as e:
            logger.error("airtable CREATE failed table=%s fields=%s error=%s", table, fields, e)
            raise

    def update_record(self, table: str, record_id: str, fields: dict) -> dict:
        self._throttle()
        logger.info("airtable UPDATE table=%s id=%s fields=%s", table, record_id, fields)
        try:
            record = self._at.update(table, record_id, fields)
            logger.info("airtable UPDATE table=%s id=%s → ok", table, record_id)
            return record
        except Exception as e:
            logger.error("airtable UPDATE failed table=%s id=%s fields=%s error=%s", table, record_id, fields, e)
            raise


airtable_client = AirtableClient()
