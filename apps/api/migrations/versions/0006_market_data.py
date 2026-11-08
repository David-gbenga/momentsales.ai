"""Phase 2b market data (ADR-0019): the shared market-data cache, UK category seed terms,
run demand output, and cost metering in GBP micros with spend per provider.

`market_data_cache` and `category_seed_terms` are global (no workspace_id, no RLS): they
hold only public market data and reference terms. The cache is append-only for the app.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-08
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Starting search terms per category hint of the UK retail moments (0005) and common run
# categories. Candidates for Google Trends only; every number shown comes from a provider.
SEED_TERMS: dict[str, list[str]] = {
    "electronics": [
        "wireless earbuds",
        "smart watch",
        "portable charger",
        "bluetooth speaker",
        "ring light",
    ],
    "fashion": [
        "puffer jacket",
        "chunky knit cardigan",
        "thermal leggings",
        "faux fur slippers",
        "velvet scrunchies",
    ],
    "home": [
        "heated throw",
        "electric blanket",
        "scented candles",
        "draught excluder",
        "hot water bottle",
    ],
    "gifts": [
        "advent calendar",
        "secret santa gifts",
        "personalised mug",
        "stocking fillers",
        "gift hamper",
    ],
    "gadgets": ["mini projector", "digital photo frame", "hand warmer", "fidget toy", "smart plug"],
    "decorations": [
        "fairy lights",
        "christmas wreath",
        "tree ornaments",
        "led candles",
        "christmas garland",
    ],
    "partywear": [
        "sequin dress",
        "christmas jumper",
        "party headbands",
        "glitter makeup",
        "festive earrings",
    ],
    "winter accessories": [
        "christmas jumper",
        "knitted beanie",
        "touchscreen gloves",
        "cashmere scarf",
        "earmuffs",
    ],
    "warm clothing": [
        "thermal socks",
        "fleece hoodie",
        "heated gilet",
        "thermal base layer",
        "slipper boots",
    ],
    "fitness": ["resistance bands", "yoga mat", "kettlebell", "skipping rope", "foam roller"],
    "organisation": [
        "drawer organiser",
        "label maker",
        "storage boxes",
        "desk organiser",
        "meal planner",
    ],
    "jewellery": [
        "initial necklace",
        "birthstone ring",
        "charm bracelet",
        "pearl earrings",
        "jewellery box",
    ],
    "chocolate": [
        "chocolate hamper",
        "hot chocolate bombs",
        "personalised chocolate",
        "chocolate gift box",
        "vegan chocolate",
    ],
    "beauty": ["skincare gift set", "lip oil", "hair styler", "bath bombs", "nail kit"],
    "garden": ["bird feeder", "solar lights", "raised planter", "garden kneeler", "seed kit"],
    "kids crafts": ["slime kit", "colouring set", "craft kit", "bead kit", "paint by numbers"],
    "grooming": ["beard kit", "electric shaver", "hair clippers", "shaving set", "wash bag"],
    "outdoor": [
        "camping chair",
        "insulated flask",
        "head torch",
        "hiking socks",
        "waterproof jacket",
    ],
    "travel": ["packing cubes", "neck pillow", "travel adapter", "toiletry bag", "luggage scale"],
    "swimwear": ["swimsuit", "bikini", "swim shorts", "beach cover up", "swim goggles"],
    "stationery": ["bullet journal", "gel pens", "academic planner", "sticky notes", "pencil case"],
    "bags": ["tote bag", "crossbody bag", "backpack", "laptop bag", "bum bag"],
    "lunchboxes": ["bento box", "insulated lunch bag", "food flask", "water bottle", "snack pots"],
    "costumes": [
        "halloween costume",
        "witch hat",
        "skeleton costume",
        "face paint",
        "kids costume",
    ],
    "sweets": ["pick and mix", "retro sweets", "sweet hamper", "fudge", "candy cane"],
}

EXTRA_SQL = """
REVOKE UPDATE, DELETE, TRUNCATE ON market_data_cache FROM app_rw;
UPDATE research_runs SET cost_micros = cost_minor * 10000;
UPDATE agent_steps SET cost_micros = cost_minor * 10000;
"""


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.add_column(
        "research_runs",
        sa.Column("demand", postgresql.JSONB(none_as_null=True, astext_type=sa.Text())),
    )
    op.add_column(
        "research_runs",
        sa.Column("cost_micros", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "research_runs",
        sa.Column(
            "cost_by_provider",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "agent_steps",
        sa.Column("cost_micros", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.create_table(
        "category_seed_terms",
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("category", sa.String(length=60), nullable=False),
        sa.Column("term", sa.String(length=100), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_category_seed_terms")),
        sa.UniqueConstraint("country", "category", "term", name="uq_category_seed_terms"),
    )
    op.create_index("ix_category_seed_terms_lookup", "category_seed_terms", ["country", "category"])
    op.create_table(
        "market_data_cache",
        sa.Column("data_type", sa.String(length=40), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("iso_week", sa.String(length=8), nullable=False),
        sa.Column("query_key", sa.String(length=64), nullable=False),
        sa.Column("query", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_data_cache")),
    )
    op.create_index(
        "ix_market_data_cache_lookup",
        "market_data_cache",
        ["data_type", "country", "query_key", "fetched_at"],
    )
    op.execute(EXTRA_SQL)
    table = sa.table(
        "category_seed_terms",
        sa.column("id", sa.UUID()),
        sa.column("country", sa.String()),
        sa.column("category", sa.String()),
        sa.column("term", sa.String()),
        sa.column("active", sa.Boolean()),
    )
    op.bulk_insert(
        table,
        [
            {"id": uuid.uuid4(), "country": "GB", "category": c, "term": t, "active": True}
            for c, terms in SEED_TERMS.items()
            for t in terms
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_market_data_cache_lookup", table_name="market_data_cache")
    op.drop_table("market_data_cache")
    op.drop_index("ix_category_seed_terms_lookup", table_name="category_seed_terms")
    op.drop_table("category_seed_terms")
    op.drop_column("agent_steps", "cost_micros")
    op.drop_column("research_runs", "cost_by_provider")
    op.drop_column("research_runs", "cost_micros")
    op.drop_column("research_runs", "demand")
