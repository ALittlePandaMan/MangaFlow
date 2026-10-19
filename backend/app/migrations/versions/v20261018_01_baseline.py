"""Adopt legacy schema without replacing existing project data."""
import sqlalchemy as sa
from alembic import op

from app.migrations.baseline import metadata

revision = "20261018_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for table in metadata.sorted_tables:
        if table.name == "task_execution_runs":
            continue
        if table.name not in existing:
            table.create(bind)
    columns = {item["name"] for item in sa.inspect(bind).get_columns("text_regions")}
    # These four columns were added by the previous unversioned startup DDL.
    additions = (
        ("visible", sa.Boolean(), sa.true(), None),
        ("translated_polygon", sa.JSON(), sa.text("'[]'"), "polygon"),
        ("translated_bbox", sa.JSON(), sa.text("'[]'"), "bbox"),
        ("perspective_warp", sa.Boolean(), sa.false(), None),
    )
    for name, kind, default, original in additions:
        if name not in columns:
            op.add_column("text_regions", sa.Column(name, kind, nullable=False, server_default=default))
            if original:
                op.execute(sa.text(f"UPDATE text_regions SET {name} = {original}"))
    for table in metadata.sorted_tables:
        if table.name == "task_execution_runs":
            continue
        actual = {item["name"] for item in sa.inspect(bind).get_columns(table.name)}
        if missing := set(table.columns.keys()) - actual:
            raise ValueError(f"Unsupported legacy schema: {table.name} missing {sorted(missing)}")
        for index in table.indexes:
            index.create(bind, checkfirst=True)


def downgrade():
    raise RuntimeError("Destructive schema downgrades are disabled; restore a verified backup instead")
