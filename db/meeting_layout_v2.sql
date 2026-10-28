-- Normalized MeetingData schema for the app-owned editor model.
-- This does not replace the legacy `meeting_data_rows` import table yet.
-- It is the target structure for editing inside the new framework.

CREATE TABLE IF NOT EXISTS meeting_sections_v2 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  field_name TEXT NOT NULL,
  meeting_name TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  source_legacy_start_row INTEGER,
  source_legacy_end_row INTEGER,
  notes TEXT,
  UNIQUE(field_name, meeting_name)
);

CREATE TABLE IF NOT EXISTS meeting_section_name_options (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(kind, value)
);

CREATE TABLE IF NOT EXISTS meeting_section_rows_v2 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  section_id INTEGER NOT NULL,
  row_order INTEGER NOT NULL,
  row_kind TEXT NOT NULL DEFAULT 'content',
  group_with_table_below INTEGER NOT NULL DEFAULT 0,
  no_meeting_association INTEGER NOT NULL DEFAULT 0,
  print_new_page INTEGER NOT NULL DEFAULT 0,
  format_code TEXT,
  label_text TEXT,
  notes TEXT,
  FOREIGN KEY (section_id) REFERENCES meeting_sections_v2(id) ON DELETE CASCADE,
  UNIQUE(section_id, row_order)
);

CREATE TABLE IF NOT EXISTS meeting_row_cells_v2 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  row_id INTEGER NOT NULL,
  col_start INTEGER NOT NULL,
  col_span INTEGER NOT NULL DEFAULT 1,
  text_value TEXT NOT NULL DEFAULT '',
  text_align TEXT NOT NULL DEFAULT 'left',
  font_scale REAL NOT NULL DEFAULT 1.0,
  is_bold INTEGER NOT NULL DEFAULT 0,
  is_italic INTEGER NOT NULL DEFAULT 0,
  is_underlined INTEGER NOT NULL DEFAULT 0,
  is_title INTEGER NOT NULL DEFAULT 0,
  stack_under_cell_id INTEGER,
  flow_min_lines INTEGER,
  FOREIGN KEY (row_id) REFERENCES meeting_section_rows_v2(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS meeting_row_flow_items_v2 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  row_id INTEGER NOT NULL,
  column_index INTEGER NOT NULL,
  item_order INTEGER NOT NULL,
  text_value TEXT NOT NULL DEFAULT '',
  text_align TEXT NOT NULL DEFAULT 'left',
  font_scale REAL NOT NULL DEFAULT 1.0,
  is_bold INTEGER NOT NULL DEFAULT 0,
  is_italic INTEGER NOT NULL DEFAULT 0,
  is_underlined INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (row_id) REFERENCES meeting_section_rows_v2(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS meeting_layout_presets_v2 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  section_id INTEGER NOT NULL,
  viewport_name TEXT NOT NULL DEFAULT 'screen',
  column_count INTEGER NOT NULL DEFAULT 28,
  column_pixel_width INTEGER NOT NULL DEFAULT 16,
  row_gap_px INTEGER NOT NULL DEFAULT 6,
  cell_gap_px INTEGER NOT NULL DEFAULT 6,
  FOREIGN KEY (section_id) REFERENCES meeting_sections_v2(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS book_layout_presets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  book_title TEXT NOT NULL DEFAULT 'Address Book',
  trim_width_in REAL NOT NULL,
  trim_height_in REAL NOT NULL,
  margin_left_in REAL NOT NULL,
  margin_right_in REAL NOT NULL,
  margin_top_in REAL NOT NULL DEFAULT 0.14,
  margin_bottom_in REAL NOT NULL DEFAULT 0.14,
  font_family TEXT NOT NULL DEFAULT 'Arial',
  base_font_size_pt REAL NOT NULL DEFAULT 7.0,
  title_font_size_pt REAL NOT NULL DEFAULT 12.0,
  toc_font_size_pt REAL NOT NULL DEFAULT 10.0,
  line_height REAL NOT NULL DEFAULT 1.2,
  meeting_table_column_count INTEGER NOT NULL DEFAULT 28,
  meeting_table_column_gap_px INTEGER NOT NULL DEFAULT 6,
  screen_preview_scale REAL NOT NULL DEFAULT 220.0,
  contacts_title_bar_color TEXT NOT NULL DEFAULT '#e3eff3',
  contacts_primary_row_color TEXT NOT NULL DEFAULT '#f8f3e3',
  contacts_secondary_row_color TEXT NOT NULL DEFAULT '#edf5f7',
  contacts_highlight_row_color TEXT NOT NULL DEFAULT '#ffe3a1',
  is_default INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meeting_section_layout_v2 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  section_id INTEGER NOT NULL UNIQUE,
  book_layout_preset_id INTEGER NOT NULL,
  usable_width_in REAL,
  column_count INTEGER NOT NULL DEFAULT 28,
  column_width_px_preview REAL,
  column_gap_px INTEGER NOT NULL DEFAULT 6,
  screen_preview_scale REAL NOT NULL DEFAULT 220.0,
  font_family TEXT NOT NULL DEFAULT '',
  base_font_size_pt REAL NOT NULL DEFAULT 0,
  line_height REAL NOT NULL DEFAULT 0,
  snap_to_grid INTEGER NOT NULL DEFAULT 1,
  show_column_guides INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY (section_id) REFERENCES meeting_sections_v2(id) ON DELETE CASCADE,
  FOREIGN KEY (book_layout_preset_id) REFERENCES book_layout_presets(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS meeting_edit_log_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id TEXT NOT NULL,
  batch_created_at TEXT NOT NULL,
  batch_row_index INTEGER NOT NULL,
  editor_initials TEXT NOT NULL,
  section_id INTEGER NOT NULL,
  field_name TEXT NOT NULL,
  meeting_name TEXT NOT NULL,
  row_id INTEGER NOT NULL,
  row_number INTEGER NOT NULL,
  original_text TEXT NOT NULL DEFAULT '',
  edited_text TEXT NOT NULL DEFAULT '',
  FOREIGN KEY (section_id) REFERENCES meeting_sections_v2(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_meeting_sections_v2_field_meeting
ON meeting_sections_v2(field_name, meeting_name);

CREATE INDEX IF NOT EXISTS idx_meeting_section_name_options_kind_value
ON meeting_section_name_options(kind, value);

CREATE INDEX IF NOT EXISTS idx_meeting_section_rows_v2_section
ON meeting_section_rows_v2(section_id, row_order);

CREATE INDEX IF NOT EXISTS idx_meeting_row_cells_v2_row
ON meeting_row_cells_v2(row_id, col_start);

CREATE INDEX IF NOT EXISTS idx_meeting_row_flow_items_v2_row
ON meeting_row_flow_items_v2(row_id, column_index, item_order);

CREATE INDEX IF NOT EXISTS idx_book_layout_presets_default
ON book_layout_presets(is_default);

CREATE INDEX IF NOT EXISTS idx_meeting_edit_log_entries_batch
ON meeting_edit_log_entries(batch_created_at DESC, batch_row_index ASC, id ASC);
