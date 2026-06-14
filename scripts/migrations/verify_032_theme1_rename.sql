-- Verify R10.1 Theme 1 (migration 032) — the 5 vol/indicator table renames.
--
-- Run AFTER `alembic -c src/persistence/alembic.ini upgrade head` against the
-- live DB. Proves : (1) the 5 new names exist, (2) the 5 old names are gone,
-- (3) row counts survived the rename (ALTER TABLE … RENAME preserves data),
-- (4) the alembic head is parked on 032.
--
-- Usage (from a psql session on the fxvol DB) :
--   \i scripts/migrations/verify_032_theme1_rename.sql

\echo '== new names present (expect 5 rows) =='
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
    'vol_surface_history',
    'regime_snapshot_history',
    'event_calendar',
    'pca_surface_snapshot_history',
    'pca_signal_history'
  )
ORDER BY table_name;

\echo '== old names gone (expect 0 rows) =='
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
    'vol_surface_snapshot',
    'regime_feature_snapshot',
    'macro_event',
    'surface_snapshots_hourly',
    'pca_projection_snapshot'
  )
ORDER BY table_name;

\echo '== data preserved (row counts on the renamed tables) =='
SELECT 'vol_surface_history'          AS table_name, count(*) FROM vol_surface_history
UNION ALL SELECT 'regime_snapshot_history',      count(*) FROM regime_snapshot_history
UNION ALL SELECT 'event_calendar',               count(*) FROM event_calendar
UNION ALL SELECT 'pca_surface_snapshot_history',  count(*) FROM pca_surface_snapshot_history
UNION ALL SELECT 'pca_signal_history',           count(*) FROM pca_signal_history;

\echo '== alembic head (expect 032_rename_vol_indicator_tables) =='
SELECT version_num FROM alembic_version;
