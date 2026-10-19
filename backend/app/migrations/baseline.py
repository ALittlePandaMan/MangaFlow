"""Frozen schema at adoption of versioned migrations. Do not import live models."""
import sqlalchemy as sa

metadata = sa.MetaData()

sa.Table('model_configs', metadata,
    sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('provider', sa.String(length=100), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('encrypted_api_key', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint('kind', 'name', name='uq_model_kind_name'),
)
sa.Index('ix_model_configs_kind', metadata.tables['model_configs'].c['kind'], unique=False)

sa.Table('projects', metadata,
    sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('source_language', sa.String(length=32), nullable=False),
    sa.Column('target_language', sa.String(length=32), nullable=False),
    sa.Column('translation_context', sa.JSON(), nullable=False),
    sa.Column('settings', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
)

sa.Table('editor_history', metadata,
    sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
    sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
    sa.Column('image_id', sa.String(length=36), nullable=True),
    sa.Column('scope', sa.String(length=16), nullable=False),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('before', sa.JSON(), nullable=False),
    sa.Column('after', sa.JSON(), nullable=False),
    sa.Column('region_ids', sa.JSON(), nullable=False),
    sa.Column('applied', sa.Boolean(), nullable=False),
    sa.Column('pending', sa.Boolean(), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
)
sa.Index('ix_editor_history_image_id', metadata.tables['editor_history'].c['image_id'], unique=False)
sa.Index('ix_editor_history_project_id', metadata.tables['editor_history'].c['project_id'], unique=False)
sa.Index('ix_editor_history_task_id', metadata.tables['editor_history'].c['task_id'], unique=False)

sa.Table('image_pages', metadata,
    sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
    sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('original_path', sa.Text(), nullable=False),
    sa.Column('clean_path', sa.Text(), nullable=True),
    sa.Column('rendered_path', sa.Text(), nullable=True),
    sa.Column('text_layer_path', sa.Text(), nullable=True),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('order_index', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('current_stage', sa.String(length=32), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('metadata_json', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint('project_id', 'order_index', name='uq_page_order'),
)
sa.Index('ix_image_pages_project_id', metadata.tables['image_pages'].c['project_id'], unique=False)
sa.Index('ix_image_pages_status', metadata.tables['image_pages'].c['status'], unique=False)

sa.Table('text_regions', metadata,
    sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
    sa.Column('image_id', sa.String(length=36), sa.ForeignKey('image_pages.id', ondelete='CASCADE'), nullable=False),
    sa.Column('region_key', sa.String(length=24), nullable=False),
    sa.Column('polygon', sa.JSON(), nullable=False),
    sa.Column('bbox', sa.JSON(), nullable=False),
    sa.Column('translated_polygon', sa.JSON(), nullable=False),
    sa.Column('translated_bbox', sa.JSON(), nullable=False),
    sa.Column('pixel_mask_path', sa.Text(), nullable=True),
    sa.Column('source_text', sa.Text(), nullable=False),
    sa.Column('translated_text', sa.Text(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('orientation', sa.String(length=16), nullable=False),
    sa.Column('reading_order', sa.Integer(), nullable=False),
    sa.Column('panel_id', sa.String(length=64), nullable=True),
    sa.Column('bubble_id', sa.String(length=64), nullable=True),
    sa.Column('region_type', sa.String(length=32), nullable=False),
    sa.Column('font_size', sa.Float(), nullable=False),
    sa.Column('font_family', sa.String(length=200), nullable=False),
    sa.Column('font_weight', sa.Integer(), nullable=False),
    sa.Column('text_color', sa.String(length=16), nullable=False),
    sa.Column('stroke_color', sa.String(length=16), nullable=False),
    sa.Column('stroke_width', sa.Float(), nullable=False),
    sa.Column('alignment', sa.String(length=16), nullable=False),
    sa.Column('line_spacing', sa.Float(), nullable=False),
    sa.Column('character_spacing', sa.Float(), nullable=False),
    sa.Column('rotation', sa.Float(), nullable=False),
    sa.Column('perspective_warp', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    sa.Column('opacity', sa.Float(), nullable=False),
    sa.Column('locked', sa.Boolean(), nullable=False),
    sa.Column('visible', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    sa.Column('needs_review', sa.Boolean(), nullable=False),
    sa.Column('review_reasons', sa.JSON(), nullable=False),
    sa.Column('layout_warning', sa.Boolean(), nullable=False),
    sa.Column('layout_data', sa.JSON(), nullable=False),
    sa.Column('inpainted_path', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint('image_id', 'region_key', name='uq_region_key'),
)
sa.Index('ix_text_regions_image_id', metadata.tables['text_regions'].c['image_id'], unique=False)
sa.Index('ix_text_regions_needs_review', metadata.tables['text_regions'].c['needs_review'], unique=False)

sa.Table('processing_tasks', metadata,
    sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
    sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=True),
    sa.Column('image_id', sa.String(length=36), sa.ForeignKey('image_pages.id', ondelete='CASCADE'), nullable=True),
    sa.Column('region_id', sa.String(length=36), sa.ForeignKey('text_regions.id', ondelete='CASCADE'), nullable=True),
    sa.Column('task_type', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('progress', sa.Float(), nullable=False),
    sa.Column('current_stage', sa.String(length=32), nullable=True),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('pause_requested', sa.Boolean(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
)
sa.Index('ix_processing_tasks_image_id', metadata.tables['processing_tasks'].c['image_id'], unique=False)
sa.Index('ix_processing_tasks_project_id', metadata.tables['processing_tasks'].c['project_id'], unique=False)
sa.Index('ix_processing_tasks_region_id', metadata.tables['processing_tasks'].c['region_id'], unique=False)
sa.Index('ix_processing_tasks_status', metadata.tables['processing_tasks'].c['status'], unique=False)
sa.Index('ix_processing_tasks_task_type', metadata.tables['processing_tasks'].c['task_type'], unique=False)

sa.Table('region_revisions', metadata,
    sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
    sa.Column('region_id', sa.String(length=36), sa.ForeignKey('text_regions.id', ondelete='CASCADE'), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('snapshot', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
)
sa.Index('ix_region_revisions_region_id', metadata.tables['region_revisions'].c['region_id'], unique=False)

sa.Table('translations', metadata,
    sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
    sa.Column('region_id', sa.String(length=36), sa.ForeignKey('text_regions.id', ondelete='CASCADE'), nullable=False),
    sa.Column('source_text', sa.Text(), nullable=False),
    sa.Column('translated_text', sa.Text(), nullable=False),
    sa.Column('source_language', sa.String(length=32), nullable=False),
    sa.Column('target_language', sa.String(length=32), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('model_name', sa.String(length=128), nullable=False),
    sa.Column('is_current', sa.Boolean(), nullable=False),
    sa.Column('raw_response', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
)
sa.Index('ix_translations_region_id', metadata.tables['translations'].c['region_id'], unique=False)

sa.Table('task_execution_runs', metadata,
    sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
    sa.Column('task_id', sa.String(length=36), sa.ForeignKey('processing_tasks.id', ondelete='CASCADE'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('queue_seconds', sa.Float(), nullable=False),
    sa.Column('wall_seconds', sa.Float(), nullable=False),
    sa.Column('timings', sa.JSON(), nullable=False),
)
sa.Index('ix_task_execution_runs_task_id', metadata.tables['task_execution_runs'].c['task_id'], unique=False)
