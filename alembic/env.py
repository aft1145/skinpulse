from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from skinpulse.db.models import Base

config = context.config

# Only apply the ini file's logging config when Alembic is driven from its own CLI.
# `config.cmd_opts` is only set by alembic.config.CommandLine.main() (the CLI entry
# point); `upgrade_db()` drives Alembic through its Python API with a bare `Config(...)`,
# so `cmd_opts` stays None there. Skip fileConfig() in that case: even with
# disable_existing_loggers=False it still reconfigures the root logger's handlers/level
# from the ini file, which is only appropriate for a standalone `alembic upgrade` CLI run.
# When it does run (CLI case), never let it disable existing loggers - fileConfig()
# defaults to disable_existing_loggers=True, which would permanently disable every
# skinpulse.* module logger already created at import time (they short-circuit before
# propagation from then on, even after later logging setup).
if config.config_file_name is not None and config.cmd_opts is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata,
                      literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}),
                                     prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
