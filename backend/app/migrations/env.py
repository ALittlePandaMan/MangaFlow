from alembic import context

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Use app.core.migrations.upgrade_database with an explicit engine")
context.configure(connection=connection, transactional_ddl=True, compare_type=True)
with context.begin_transaction():
    context.run_migrations()
