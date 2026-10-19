"""Apply Alembic migrations from code. The api calls `upgrade_head()` at start; it is idempotent."""

import logging

from alembic import command
from alembic.config import Config

from llmtrano.settings import Settings

log = logging.getLogger(__name__)


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", "llmtrano.db:migrations")
    # ConfigParser treats % as interpolation; escape it in case the password contains one.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade_head(database_url: str | None = None) -> None:
    url = database_url or Settings().database_url
    log.info("migrate action=upgrade target=head")
    command.upgrade(alembic_config(url), "head")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    upgrade_head()
