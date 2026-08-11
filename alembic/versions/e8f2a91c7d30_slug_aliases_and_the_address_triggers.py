"""slug_aliases and the address triggers

ADR 0019: no rename may ever discard its old address, for ANY writer -
passes, decision scripts, or a raw psql session - so the contract lives in
the database as triggers (the a1c4e7b93f20 precedent), not in Python:

- BEFORE UPDATE of an address: taking over another row's retired address
  raises; re-acquiring your own discharges the alias; the old address is
  recorded as an alias in the same statement.
- BEFORE INSERT: a new row cannot mint a retired address - this is what
  makes the passes' in-memory collision caches safe rather than merely
  polite (a rename committed mid-run aborts loudly instead of being
  silently re-minted).
- BEFORE DELETE: a row cannot vanish with its address unless a forwarding
  alias already exists (the merge discipline) or the session set the
  `carmanac.allow_address_drop` escape reserved for our own bug artifacts
  (ADR 0004).

Hand-written: triggers and plpgsql are autogenerate blind spots. One
function per operation; kind, arc column and scope-ness arrive via TG_ARGV,
and the company scope is read through to_jsonb so one function serves
tables with and without a company_id column.

Revision ID: e8f2a91c7d30
Revises: b7d1a2c4e9f0
Create Date: 2026-08-10

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8f2a91c7d30"
down_revision: str | Sequence[str] | None = "b7d1a2c4e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# table -> (entity_kind, arc column, 'scoped'|'global'), mirroring
# ADDRESS_KINDS / COMPANY_SCOPED_KINDS / ALIAS_ARC_BY_KIND in
# carmanac/db/models/reconciliation.py.
ADDRESS_TABLES = (
    ("companies", "company", "company_id", "global"),
    ("models", "model", "model_id", "scoped"),
    ("model_lines", "model_line", "model_line_id", "scoped"),
    ("generations", "generation", "generation_id", "scoped"),
    ("configurations", "configuration", "configuration_id", "global"),
    ("engines", "engine", "engine_id", "global"),
    ("transmissions", "transmission", "transmission_id", "global"),
)

ARC_COLUMNS = (
    "company_id",
    "model_id",
    "model_line_id",
    "generation_id",
    "configuration_id",
    "engine_id",
    "transmission_id",
)

_RENAME_FN = """
CREATE OR REPLACE FUNCTION slug_alias_record_rename() RETURNS trigger AS $$
DECLARE
    kind text := TG_ARGV[0];
    arc_col text := TG_ARGV[1];
    scoped boolean := TG_ARGV[2] = 'scoped';
    -- Equality (or a bare IS NULL) so the lookup uses uq_slug_aliases_address.
    -- `IS NOT DISTINCT FROM $2` is a DistinctExpr, which is never an index
    -- qual, and this runs on every insert into every address table.
    scope_pred text := CASE WHEN scoped
        THEN 'scope_company_id = $2'
        ELSE 'scope_company_id IS NULL AND $2 IS NULL' END;
    old_scope integer;
    new_scope integer;
    hit_id bigint;
    hit_target bigint;
BEGIN
    IF scoped THEN
        old_scope := (to_jsonb(OLD) ->> 'company_id')::integer;
        new_scope := (to_jsonb(NEW) ->> 'company_id')::integer;
    END IF;
    -- Address unchanged (a no-op or unrelated-column update): write nothing.
    IF NEW.slug IS NOT DISTINCT FROM OLD.slug
       AND new_scope IS NOT DISTINCT FROM old_scope THEN
        RETURN NEW;
    END IF;

    EXECUTE format(
        'SELECT id, %I FROM slug_aliases'
        ' WHERE entity_kind = $1 AND %s AND slug = $3',
        arc_col, scope_pred)
    INTO hit_id, hit_target
    USING kind, new_scope, NEW.slug;
    IF hit_id IS NOT NULL THEN
        IF hit_target IS DISTINCT FROM NEW.id THEN
            RAISE EXCEPTION
                '% slug "%" is the retired address of another row '
                '(slug_aliases id %); it cannot be taken over (ADR 0019)',
                kind, NEW.slug, hit_id;
        END IF;
        -- The row re-acquires its own former address: a promise the entity
        -- itself re-fulfills is discharged, not broken.
        DELETE FROM slug_aliases WHERE id = hit_id;
    END IF;

    -- No ON CONFLICT: the old address can only already be aliased if someone
    -- wrote that row by hand (an abandoned merge forwarding), and silently
    -- re-pointing it would overwrite a recorded judgment with a statement
    -- that never mentioned it. Let the unique constraint raise.
    EXECUTE format(
        'INSERT INTO slug_aliases (entity_kind, scope_company_id, slug, %I, reason)'
        ' VALUES ($1, $2, $3, $4, ''rename'')',
        arc_col)
    USING kind, old_scope, OLD.slug, NEW.id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_MINT_FN = """
CREATE OR REPLACE FUNCTION slug_alias_block_mint() RETURNS trigger AS $$
DECLARE
    kind text := TG_ARGV[0];
    scoped boolean := TG_ARGV[2] = 'scoped';
    scope integer;
    hit bigint;
BEGIN
    IF scoped THEN
        scope := (to_jsonb(NEW) ->> 'company_id')::integer;
        SELECT id INTO hit FROM slug_aliases
         WHERE entity_kind = kind AND scope_company_id = scope AND slug = NEW.slug;
    ELSE
        SELECT id INTO hit FROM slug_aliases
         WHERE entity_kind = kind AND scope_company_id IS NULL AND slug = NEW.slug;
    END IF;
    IF hit IS NOT NULL THEN
        RAISE EXCEPTION
            'new % slug "%" is a retired address (slug_aliases id %); '
            'minting it would steal its history (ADR 0019)',
            kind, NEW.slug, hit;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_DROP_FN = """
CREATE OR REPLACE FUNCTION slug_alias_block_drop() RETURNS trigger AS $$
DECLARE
    kind text := TG_ARGV[0];
    arc_col text := TG_ARGV[1];
    scoped boolean := TG_ARGV[2] = 'scoped';
    scope_pred text := CASE WHEN scoped
        THEN 'scope_company_id = $2'
        ELSE 'scope_company_id IS NULL AND $2 IS NULL' END;
    scope integer;
    forwarded boolean;
BEGIN
    IF current_setting('carmanac.allow_address_drop', true) = 'on' THEN
        RETURN OLD;
    END IF;
    IF scoped THEN
        scope := (to_jsonb(OLD) ->> 'company_id')::integer;
    END IF;
    -- The row may go only once its own address forwards to a DIFFERENT row
    -- (the merge discipline). Aliases targeting this row are separately
    -- pinned by the arc FKs.
    EXECUTE format(
        'SELECT EXISTS (SELECT 1 FROM slug_aliases'
        ' WHERE entity_kind = $1 AND %s AND slug = $3'
        '   AND %I IS DISTINCT FROM $4)',
        scope_pred, arc_col)
    INTO forwarded
    USING kind, scope, OLD.slug, OLD.id;
    IF NOT forwarded THEN
        RAISE EXCEPTION
            'deleting % "%" would drop its address: alias it to a successor '
            'first, or SET LOCAL carmanac.allow_address_drop = ''on'' for '
            'our own bug artifacts (ADR 0019, ADR 0004)',
            kind, OLD.slug;
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;
"""

# Row-level triggers do not fire on TRUNCATE, and `TRUNCATE <address table>
# CASCADE` reaches slug_aliases through the arc FKs - so without this the one
# table nothing can recompute is one statement away from empty, silently.
_TRUNCATE_FN = """
CREATE OR REPLACE FUNCTION slug_alias_block_truncate() RETURNS trigger AS $$
BEGIN
    IF current_setting('carmanac.allow_address_drop', true) = 'on' THEN
        RETURN NULL;
    END IF;
    RAISE EXCEPTION
        'TRUNCATE would discard recorded address history, which no pass can '
        'recompute; SET LOCAL carmanac.allow_address_drop = ''on'' if that is '
        'really intended (ADR 0019, ADR 0004)';
END;
$$ LANGUAGE plpgsql;
"""

def upgrade() -> None:
    op.create_table(
        "slug_aliases",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("entity_kind", sa.Text(), nullable=False),
        sa.Column("scope_company_id", sa.Integer(), nullable=True),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("model_id", sa.Integer(), nullable=True),
        sa.Column("model_line_id", sa.Integer(), nullable=True),
        sa.Column("generation_id", sa.Integer(), nullable=True),
        sa.Column("configuration_id", sa.Integer(), nullable=True),
        sa.Column("engine_id", sa.Integer(), nullable=True),
        sa.Column("transmission_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "entity_kind IN ('company', 'model', 'model_line', 'generation', "
            "'configuration', 'engine', 'transmission')",
            name=op.f("ck_slug_aliases_entity_kind_valid"),
        ),
        sa.CheckConstraint(
            "reason IN ('rename', 'merge')",
            name=op.f("ck_slug_aliases_reason_valid"),
        ),
        sa.CheckConstraint(
            "(entity_kind IN ('model', 'model_line', 'generation')) = "
            "(scope_company_id IS NOT NULL)",
            name=op.f("ck_slug_aliases_scope_matches_kind"),
        ),
        sa.CheckConstraint(
            "num_nonnulls(company_id, model_id, model_line_id, generation_id, "
            "configuration_id, engine_id, transmission_id) = 1",
            name=op.f("ck_slug_aliases_exactly_one_target"),
        ),
        sa.CheckConstraint(
            "CASE entity_kind "
            "WHEN 'company' THEN company_id IS NOT NULL "
            "WHEN 'model' THEN model_id IS NOT NULL "
            "WHEN 'model_line' THEN model_line_id IS NOT NULL "
            "WHEN 'generation' THEN generation_id IS NOT NULL "
            "WHEN 'configuration' THEN configuration_id IS NOT NULL "
            "WHEN 'engine' THEN engine_id IS NOT NULL "
            "WHEN 'transmission' THEN transmission_id IS NOT NULL "
            "END",
            name=op.f("ck_slug_aliases_target_matches_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["scope_company_id"],
            ["companies.id"],
            name="fk_slug_aliases_scope_company_id_companies",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_slug_aliases_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["model_id"], ["models.id"], name=op.f("fk_slug_aliases_model_id_models")
        ),
        sa.ForeignKeyConstraint(
            ["model_line_id"],
            ["model_lines.id"],
            name=op.f("fk_slug_aliases_model_line_id_model_lines"),
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["generations.id"],
            name=op.f("fk_slug_aliases_generation_id_generations"),
        ),
        sa.ForeignKeyConstraint(
            ["configuration_id"],
            ["configurations.id"],
            name=op.f("fk_slug_aliases_configuration_id_configurations"),
        ),
        sa.ForeignKeyConstraint(
            ["engine_id"], ["engines.id"], name=op.f("fk_slug_aliases_engine_id_engines")
        ),
        sa.ForeignKeyConstraint(
            ["transmission_id"],
            ["transmissions.id"],
            name=op.f("fk_slug_aliases_transmission_id_transmissions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_slug_aliases")),
    )
    op.create_unique_constraint(
        "uq_slug_aliases_address",
        "slug_aliases",
        ["entity_kind", "scope_company_id", "slug"],
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(op.f("idx_slug_aliases_scope_company_id"), "slug_aliases", ["scope_company_id"])
    for col in ARC_COLUMNS:
        op.create_index(
            f"idx_slug_aliases_{col}",
            "slug_aliases",
            [col],
            postgresql_where=sa.text(f"{col} IS NOT NULL"),
        )

    op.execute(_RENAME_FN)
    op.execute(_MINT_FN)
    op.execute(_DROP_FN)
    op.execute(_TRUNCATE_FN)
    op.execute(
        """
        CREATE TRIGGER trg_slug_aliases_block_truncate
        BEFORE TRUNCATE ON slug_aliases
        FOR EACH STATEMENT
        EXECUTE FUNCTION slug_alias_block_truncate();
        """
    )
    for table, kind, arc_col, scope in ADDRESS_TABLES:
        update_of = "slug, company_id" if scope == "scoped" else "slug"
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_slug_alias_rename
            BEFORE UPDATE OF {update_of} ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION slug_alias_record_rename('{kind}', '{arc_col}', '{scope}');
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_slug_alias_mint_guard
            BEFORE INSERT ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION slug_alias_block_mint('{kind}', '{arc_col}', '{scope}');
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_slug_alias_drop_guard
            BEFORE DELETE ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION slug_alias_block_drop('{kind}', '{arc_col}', '{scope}');
            """
        )


def downgrade() -> None:
    # Refuse rather than discard (the 76cb287dd71c posture): alias rows are
    # recorded address history that nothing can recompute. The empty-table
    # round trip stays legal.
    bind = op.get_bind()
    aliases = bind.execute(sa.text("SELECT count(*) FROM slug_aliases")).scalar()
    if aliases:
        raise RuntimeError(
            f"cannot downgrade: {aliases} slug aliases would be discarded and "
            "no pass can recompute recorded address history; resolve them first"
        )

    for table, _, _, _ in ADDRESS_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_slug_alias_rename ON {table};")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_slug_alias_mint_guard ON {table};")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_slug_alias_drop_guard ON {table};")
    op.execute("DROP TRIGGER IF EXISTS trg_slug_aliases_block_truncate ON slug_aliases;")
    op.execute("DROP FUNCTION IF EXISTS slug_alias_block_truncate();")
    op.execute("DROP FUNCTION IF EXISTS slug_alias_record_rename();")
    op.execute("DROP FUNCTION IF EXISTS slug_alias_block_mint();")
    op.execute("DROP FUNCTION IF EXISTS slug_alias_block_drop();")

    for col in reversed(ARC_COLUMNS):
        op.drop_index(f"idx_slug_aliases_{col}", table_name="slug_aliases")
    op.drop_index(op.f("idx_slug_aliases_scope_company_id"), table_name="slug_aliases")
    op.drop_constraint("uq_slug_aliases_address", "slug_aliases")
    op.drop_table("slug_aliases")
