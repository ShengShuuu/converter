-- Paths are stored relative to the data dir so the data volume can be moved or remounted.
-- AUTOINCREMENT: a deleted row's id is never reused, so a stale tab/bookmark cannot reach a different record.
CREATE TABLE templates(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  file_path TEXT NOT NULL, sheet_name TEXT NOT NULL,
  headers_json TEXT NOT NULL, column_map_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
-- v1 (single page per upload), replaced by conversions + pages for multi-page batches:
-- CREATE TABLE conversions(
--   id INTEGER PRIMARY KEY AUTOINCREMENT,
--   original_filename TEXT NOT NULL,
--   source_path TEXT NOT NULL,
--   status TEXT NOT NULL CHECK (status IN ('queued','processing','done','not_sales_order','failed')),
--   error TEXT,
--   profile_name TEXT, classifier_score REAL, rotated_by INTEGER,
--   page_width INTEGER, page_height INTEGER,
--   extracted_order_json TEXT,
--   order_json TEXT,
--   confirmed_fields_json TEXT NOT NULL DEFAULT '[]',
--   template_id INTEGER REFERENCES templates(id) ON DELETE SET NULL,
--   created_at TEXT NOT NULL, updated_at TEXT NOT NULL
-- );
-- PRAGMA user_version = 1;
CREATE TABLE conversions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  original_filename TEXT NOT NULL,
  source_path TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued','processing','done','failed')),
  error TEXT,
  -- NULL until the worker has counted the pages; set together with the page rows.
  page_count INTEGER,
  template_id INTEGER REFERENCES templates(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
-- One row per page: a page is one order attempt. 'skipped' is the user's verdict, 'not_sales_order' the classifier's.
CREATE TABLE pages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversion_id INTEGER NOT NULL REFERENCES conversions(id) ON DELETE CASCADE,
  page_number INTEGER NOT NULL CHECK (page_number >= 1),
  status TEXT NOT NULL CHECK (status IN ('queued','processing','done','not_sales_order','failed','skipped')),
  error TEXT,
  profile_name TEXT, classifier_score REAL, rotated_by INTEGER,
  page_width INTEGER, page_height INTEGER,
  extracted_order_json TEXT,
  order_json TEXT,
  confirmed_fields_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE (conversion_id, page_number)
);
PRAGMA user_version = 2;
