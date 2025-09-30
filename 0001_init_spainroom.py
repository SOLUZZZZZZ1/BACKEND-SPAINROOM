"""Initial SpainRoom schema (rooms, contracts, uploads, leads, kyc, remesas, reservas, contacto, franquicia)

Revision ID: 0001_init_spainroom
Revises: 
Create Date: 2025-09-30 13:05:32
"""
from alembic import op
import sqlalchemy as sa

revision = '0001_init_spainroom'
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    # rooms
    op.create_table('rooms',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('code', sa.String(length=32), nullable=True),
        sa.Column('direccion', sa.String(length=240), nullable=True),
        sa.Column('ciudad', sa.String(length=120), nullable=True),
        sa.Column('provincia', sa.String(length=120), nullable=True),
        sa.Column('m2', sa.Integer(), nullable=True),
        sa.Column('precio', sa.Integer(), nullable=True),
        sa.Column('estado', sa.String(length=32), nullable=True),
        sa.Column('notas', sa.Text(), nullable=True),
        sa.Column('published', sa.Boolean(), nullable=False, server_default=sa.text('FALSE')),
        sa.Column('images_json', sa.JSON(), nullable=True),
        sa.UniqueConstraint('code', name='uq_rooms_code')
    )

    # reservas
    op.create_table('reservas',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('room_id', sa.Integer(), index=True, nullable=False),
        sa.Column('nombre', sa.String(length=200), nullable=False),
        sa.Column('email', sa.String(length=200), nullable=True),
        sa.Column('telefono', sa.String(length=40), nullable=True),
        sa.Column('start_date', sa.Date(), index=True, nullable=False),
        sa.Column('end_date', sa.Date(), index=True, nullable=False),
        sa.Column('status', sa.String(length=16), index=True, nullable=True, server_default='pending'),
        sa.Column('notas', sa.Text(), nullable=True),
        sa.Column('meta_json', sa.JSON(), nullable=True)
    )

    # uploads
    op.create_table('uploads',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('subject_id', sa.String(length=128), index=True, nullable=True),
        sa.Column('category', sa.String(length=64), nullable=False),
        sa.Column('path', sa.String(length=300), nullable=False),
        sa.Column('mime', sa.String(length=80), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=True),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('sha256', sa.String(length=64), nullable=True)
    )

    # contracts + items
    op.create_table('contracts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('ref', sa.String(length=16), nullable=False),
        sa.Column('owner_id', sa.String(length=64), nullable=True),
        sa.Column('tenant_id', sa.String(length=64), nullable=True),
        sa.Column('franchisee_id', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=True, server_default='draft'),
        sa.Column('meta_json', sa.JSON(), nullable=True),
        sa.UniqueConstraint('ref', name='uq_contracts_ref')
    )
    op.create_index('ix_contracts_ref', 'contracts', ['ref'])

    op.create_table('contract_items',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('contract_id', sa.Integer(), sa.ForeignKey('contracts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sub_ref', sa.String(length=24), nullable=False),
        sa.Column('room_id', sa.Integer(), sa.ForeignKey('rooms.id', ondelete='CASCADE'), nullable=False),
        sa.Column('owner_id', sa.String(length=64), nullable=True),
        sa.Column('franchisee_id', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=True, server_default='draft'),
        sa.Column('split_owner', sa.Float(), nullable=True, server_default='0.80'),
        sa.Column('split_franchisee', sa.Float(), nullable=True, server_default='0.20'),
        sa.Column('meta_json', sa.JSON(), nullable=True),
        sa.UniqueConstraint('contract_id', 'sub_ref', name='uq_contract_subref')
    )
    op.create_index('ix_contract_items_subref', 'contract_items', ['sub_ref'])

    # leads
    op.create_table('leads',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('source', sa.String(length=32), nullable=True, server_default='voice'),
        sa.Column('provincia', sa.String(length=120), index=True, nullable=True),
        sa.Column('municipio', sa.String(length=180), index=True, nullable=True),
        sa.Column('nombre', sa.String(length=200), nullable=False),
        sa.Column('telefono', sa.String(length=40), nullable=False),
        sa.Column('email', sa.String(length=200), nullable=True),
        sa.Column('assigned_to', sa.String(length=120), index=True, nullable=True),
        sa.Column('status', sa.String(length=16), index=True, nullable=True, server_default='new'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('meta_json', sa.JSON(), nullable=True)
    )

    # contact_messages
    op.create_table('contact_messages',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('tipo', sa.String(length=64), index=True, nullable=False),
        sa.Column('nombre', sa.String(length=200), nullable=False),
        sa.Column('email', sa.String(length=200), nullable=False),
        sa.Column('telefono', sa.String(length=32), nullable=True),
        sa.Column('mensaje', sa.Text(), nullable=False),
        sa.Column('zona', sa.String(length=200), nullable=True),
        sa.Column('via', sa.String(length=64), nullable=True, server_default='web_contact_form'),
        sa.Column('meta_json', sa.JSON(), nullable=True)
    )

    # kyc_sessions
    op.create_table('kyc_sessions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('phone', sa.String(length=32), index=True, nullable=False),
        sa.Column('token', sa.String(length=64), unique=True, index=True, nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False, server_default='veriff'),
        sa.Column('provider_id', sa.String(length=64), index=True, nullable=True),
        sa.Column('verification_url', sa.String(length=400), nullable=True),
        sa.Column('state', sa.String(length=24), nullable=False, server_default='pending'),
        sa.Column('decision', sa.String(length=24), nullable=True),
        sa.Column('reason', sa.String(length=120), nullable=True),
        sa.Column('selfie_path', sa.String(length=300), nullable=True),
        sa.Column('meta_json', sa.JSON(), nullable=True)
    )

    # remesas
    op.create_table('remesas',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('user_id', sa.Integer(), index=True, nullable=False),
        sa.Column('request_id', sa.String(length=64), unique=True, index=True, nullable=True),
        sa.Column('status', sa.String(length=16), index=True, nullable=True, server_default='created'),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('currency_from', sa.String(length=6), nullable=True, server_default='EUR'),
        sa.Column('currency_to', sa.String(length=6), nullable=True, server_default='EUR'),
        sa.Column('country_dest', sa.String(length=2), nullable=True),
        sa.Column('receiver_name', sa.String(length=160), nullable=True),
        sa.Column('meta_json', sa.JSON(), nullable=True),
    )
    op.create_index('ix_remesas_user_status', 'remesas', ['user_id','status'])

    # owner_checks
    op.create_table('owner_checks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('nombre', sa.String(length=200), nullable=False),
        sa.Column('telefono', sa.String(length=40), nullable=False),
        sa.Column('via', sa.String(length=32), nullable=False),
        sa.Column('numero', sa.String(length=80), nullable=True),
        sa.Column('refcat', sa.String(length=80), nullable=True),
        sa.Column('direccion', sa.String(length=240), nullable=True),
        sa.Column('cp', sa.String(length=12), nullable=True),
        sa.Column('municipio', sa.String(length=120), nullable=True),
        sa.Column('provincia', sa.String(length=120), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=True),
        sa.Column('raw', sa.JSON(), nullable=True),
        sa.Column('franchisee_id', sa.String(length=64), nullable=True),
    )

    # franchise applications & uploads
    op.create_table('franchise_applications',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('nombre', sa.String(length=200), nullable=False),
        sa.Column('telefono', sa.String(length=32), nullable=True),
        sa.Column('email', sa.String(length=200), nullable=True),
        sa.Column('zona', sa.String(length=200), nullable=True),
        sa.Column('mensaje', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=True, server_default='received'),
        sa.Column('app_key', sa.String(length=64), unique=True, index=True, nullable=True),
        sa.Column('meta_json', sa.JSON(), nullable=True),
    )

    op.create_table('franchise_uploads',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('app_key', sa.String(length=64), index=True, nullable=False),
        sa.Column('category', sa.String(length=64), nullable=False),
        sa.Column('path', sa.String(length=300), nullable=False),
        sa.Column('mime', sa.String(length=80), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=True),
        sa.Column('sha256', sa.String(length=64), nullable=True),
    )

    # --- Franquicia (avanzado): grupos y ocupación ---
    op.create_table('franquicia_slots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('provincia', sa.String(length=120), nullable=False, index=True),
        sa.Column('municipio', sa.String(length=180), nullable=False, index=True),
        sa.Column('nivel', sa.String(length=24), nullable=False),   # municipio|distrito
        sa.Column('distrito', sa.String(length=120), nullable=True, server_default=''),
        sa.Column('poblacion', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('slots', sa.Integer(), nullable=False, server_default='1'),
    )

    op.create_table('franquicia_ocupacion',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('provincia', sa.String(length=120), nullable=False, index=True),
        sa.Column('municipio', sa.String(length=180), nullable=False, index=True),
        sa.Column('nivel', sa.String(length=24), nullable=False),
        sa.Column('distrito', sa.String(length=120), nullable=True, server_default=''),
        sa.Column('slot_index', sa.Integer(), nullable=False),
        sa.Column('ocupado', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('ocupado_por', sa.String(length=120), nullable=True),
    )
    # Unique por plaza
    op.create_unique_constraint('uq_franq_slot', 'franquicia_ocupacion', ['provincia','municipio','nivel','distrito','slot_index'])

    # --- Franquicia (simple) para compatibilidad con routes_admin_franchise ---
    op.create_table('franchise_slots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('provincia', sa.String(length=120), nullable=False),
        sa.Column('municipio', sa.String(length=180), nullable=False),
        sa.Column('poblacion', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('plazas', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('ocupadas', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('libres', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('status', sa.String(length=16), nullable=True, server_default='free'),
        sa.Column('assigned_to', sa.String(length=120), nullable=True),
    )
    op.create_unique_constraint('uq_frslot_prov_mun', 'franchise_slots', ['provincia','municipio'])

def downgrade() -> None:
    op.drop_table('franchise_slots')
    op.drop_constraint('uq_franq_slot', 'franquicia_ocupacion', type_='unique')
    op.drop_table('franquicia_ocupacion')
    op.drop_table('franquicia_slots')
    op.drop_table('franchise_uploads')
    op.drop_table('franchise_applications')
    op.drop_table('owner_checks')
    op.drop_index('ix_remesas_user_status', table_name='remesas')
    op.drop_table('remesas')
    op.drop_table('kyc_sessions')
    op.drop_table('contact_messages')
    op.drop_table('leads')
    op.drop_index('ix_contract_items_subref', table_name='contract_items')
    op.drop_table('contract_items')
    op.drop_index('ix_contracts_ref', table_name='contracts')
    op.drop_table('contracts')
    op.drop_table('uploads')
    op.drop_table('reservas')
    op.drop_table('rooms')
