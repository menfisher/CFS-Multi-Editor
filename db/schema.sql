CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  selected TEXT,
  google_contact_id TEXT,
  etag TEXT,
  last_updated TEXT,
  status TEXT,
  fields_text TEXT,
  meetings_text TEXT,
  group_membership TEXT,
  family_name TEXT,
  given_name TEXT,
  photo TEXT,
  photo_drive_file_id TEXT NOT NULL DEFAULT '',
  photo_sync_revision INTEGER NOT NULL DEFAULT 0,
  photo_needs_export INTEGER NOT NULL DEFAULT 0,
  shared_drive_revision INTEGER NOT NULL DEFAULT 0,
  shared_drive_needs_export INTEGER NOT NULL DEFAULT 0,
  shared_drive_bootstrapped INTEGER NOT NULL DEFAULT 0,
  birthday TEXT,
  notes TEXT,
  mtg_home_elder_flag TEXT,
  meets_in_state TEXT NOT NULL DEFAULT '',
  do_not_print INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS relationships (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id INTEGER NOT NULL,
  position INTEGER NOT NULL,
  relation_type TEXT,
  relation_value TEXT,
  FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS phones (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id INTEGER NOT NULL,
  position INTEGER NOT NULL,
  phone_type TEXT,
  phone_value TEXT,
  FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS addresses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id INTEGER NOT NULL,
  position INTEGER NOT NULL,
  address_type TEXT,
  street_address TEXT,
  extended_address TEXT,
  city TEXT,
  region TEXT,
  postal_code TEXT,
  formatted_address TEXT,
  coordinates TEXT,
  FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS emails (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id INTEGER NOT NULL,
  position INTEGER NOT NULL,
  email_type TEXT,
  email_value TEXT,
  FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS custom_fields (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id INTEGER NOT NULL,
  position INTEGER NOT NULL,
  field_type TEXT,
  field_value TEXT,
  FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS meeting_data_rows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sheet_row_number INTEGER NOT NULL,
  field_name TEXT,
  meeting_name TEXT,
  format_code TEXT,
  col_d TEXT,
  col_e TEXT,
  col_f TEXT,
  col_g TEXT,
  col_h TEXT,
  col_i TEXT,
  col_j TEXT,
  col_k TEXT,
  col_l TEXT,
  col_m TEXT,
  col_n TEXT,
  col_o TEXT,
  col_p TEXT,
  col_q TEXT,
  col_r TEXT,
  col_s TEXT,
  col_t TEXT,
  col_u TEXT,
  col_v TEXT,
  col_w TEXT,
  col_x TEXT,
  col_y TEXT,
  col_z TEXT,
  col_aa TEXT,
  col_ab TEXT,
  col_ac TEXT,
  col_ad TEXT,
  col_ae TEXT
);

CREATE TABLE IF NOT EXISTS google_sync_accounts (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  account_email TEXT NOT NULL DEFAULT '',
  remember_preferred_account INTEGER NOT NULL DEFAULT 0,
  account_status TEXT NOT NULL DEFAULT 'not_connected',
  google_user_id TEXT NOT NULL DEFAULT '',
  google_display_name TEXT NOT NULL DEFAULT '',
  access_token TEXT NOT NULL DEFAULT '',
  refresh_token TEXT NOT NULL DEFAULT '',
  token_expiry TEXT NOT NULL DEFAULT '',
  scopes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS google_sync_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  source_of_truth TEXT NOT NULL DEFAULT 'app',
  sync_enabled INTEGER NOT NULL DEFAULT 0,
  pending_upload_count INTEGER NOT NULL DEFAULT 0,
  upload_in_progress INTEGER NOT NULL DEFAULT 0,
  upload_phase TEXT NOT NULL DEFAULT '',
  upload_total_count INTEGER NOT NULL DEFAULT 0,
  upload_processed_count INTEGER NOT NULL DEFAULT 0,
  upload_current_contact TEXT NOT NULL DEFAULT '',
  upload_started_at TEXT NOT NULL DEFAULT '',
  last_import_at TEXT NOT NULL DEFAULT '',
  last_import_account_email TEXT NOT NULL DEFAULT '',
  last_upload_at TEXT NOT NULL DEFAULT '',
  last_drive_export_at TEXT NOT NULL DEFAULT '',
  last_sync_error TEXT NOT NULL DEFAULT '',
  needs_drive_export INTEGER NOT NULL DEFAULT 0,
  book_layouts_needs_drive_export INTEGER NOT NULL DEFAULT 0,
  meetingdata_needs_drive_export INTEGER NOT NULL DEFAULT 0,
  contacts_manifest_needs_drive_export INTEGER NOT NULL DEFAULT 0,
  settings_sync_revision INTEGER NOT NULL DEFAULT 0,
  book_layouts_sync_revision INTEGER NOT NULL DEFAULT 0,
  contacts_sync_revision INTEGER NOT NULL DEFAULT 0,
  meetingdata_sync_revision INTEGER NOT NULL DEFAULT 0,
  signin_sync_in_progress INTEGER NOT NULL DEFAULT 0,
  signin_sync_phase TEXT NOT NULL DEFAULT '',
  signin_sync_error TEXT NOT NULL DEFAULT '',
  signin_sync_updated_at TEXT NOT NULL DEFAULT '',
  signin_sync_total_count INTEGER NOT NULL DEFAULT 0,
  signin_sync_processed_count INTEGER NOT NULL DEFAULT 0,
  signin_sync_current_item TEXT NOT NULL DEFAULT '',
  drive_root_folder_id TEXT NOT NULL DEFAULT '',
  drive_app_revisions_folder_id TEXT NOT NULL DEFAULT '',
  drive_app_revisions_file_id TEXT NOT NULL DEFAULT '',
  multi_editor_enabled INTEGER NOT NULL DEFAULT 0,
  multi_editor_account_locked INTEGER NOT NULL DEFAULT 0,
  editor_lock_timeout_minutes INTEGER NOT NULL DEFAULT 60,
  editor_name TEXT NOT NULL DEFAULT '',
  editor_session_id TEXT NOT NULL DEFAULT '',
  editor_mode TEXT NOT NULL DEFAULT 'normal',
  editor_lock_owner_name TEXT NOT NULL DEFAULT '',
  editor_lock_expires_at TEXT NOT NULL DEFAULT '',
  pending_editor_name TEXT NOT NULL DEFAULT '',
  editor_secret_hash TEXT NOT NULL DEFAULT '',
  editor_secret_salt TEXT NOT NULL DEFAULT '',
  address_book_pdf_share_enabled INTEGER NOT NULL DEFAULT 0,
  address_book_pdf_share_folder_id TEXT NOT NULL DEFAULT '',
  address_book_pdf_share_folder_name TEXT NOT NULL DEFAULT '',
  public_web_url TEXT NOT NULL DEFAULT '',
  share_web_api_key TEXT NOT NULL DEFAULT '',
  share_sync_last_message TEXT NOT NULL DEFAULT '',
  import_warning_acknowledged_at TEXT NOT NULL DEFAULT '',
  pending_oauth_state TEXT NOT NULL DEFAULT '',
  pending_oauth_state_created_at TEXT NOT NULL DEFAULT '',
  bootstrap_status TEXT NOT NULL DEFAULT 'idle',
  bootstrap_total_contacts INTEGER NOT NULL DEFAULT 0,
  bootstrap_processed_contacts INTEGER NOT NULL DEFAULT 0,
  bootstrap_last_contact_id INTEGER NOT NULL DEFAULT 0,
  bootstrap_started_at TEXT NOT NULL DEFAULT '',
  bootstrap_updated_at TEXT NOT NULL DEFAULT '',
  bootstrap_error TEXT NOT NULL DEFAULT '',
  needs_upload_reminder INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS google_sync_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id INTEGER,
  google_contact_id TEXT NOT NULL DEFAULT '',
  operation TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  last_error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS contact_assignment_options (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  parent_value TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (kind, value)
);

CREATE TABLE IF NOT EXISTS contact_edit_log_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id TEXT NOT NULL,
  batch_created_at TEXT NOT NULL,
  batch_row_index INTEGER NOT NULL,
  editor_initials TEXT NOT NULL,
  contact_id INTEGER NOT NULL,
  contact_name TEXT NOT NULL,
  label_name TEXT NOT NULL,
  original_text TEXT NOT NULL DEFAULT '',
  edited_text TEXT NOT NULL DEFAULT '',
  FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sort_order INTEGER NOT NULL,
  date_display TEXT NOT NULL DEFAULT '',
  version_text TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS address_book_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  margin_left_in REAL NOT NULL DEFAULT 0.35,
  margin_right_in REAL NOT NULL DEFAULT 0.35,
  margin_top_in REAL NOT NULL DEFAULT 0.35,
  margin_bottom_in REAL NOT NULL DEFAULT 0.35,
  font_family TEXT NOT NULL DEFAULT 'Arial',
  base_font_size_pt REAL NOT NULL DEFAULT 10,
  base_font_bold INTEGER NOT NULL DEFAULT 0,
  base_font_italic INTEGER NOT NULL DEFAULT 0,
  base_font_underline INTEGER NOT NULL DEFAULT 0,
  line_height REAL NOT NULL DEFAULT 1.2,
  preview_scale REAL NOT NULL DEFAULT 0.7,
  page_column_count INTEGER NOT NULL DEFAULT 1,
  include_bible_study_union_info INTEGER NOT NULL DEFAULT 1,
  two_column_gap_ch REAL NOT NULL DEFAULT 6,
  column_width_in REAL NOT NULL DEFAULT 4,
  address_align TEXT NOT NULL DEFAULT 'right',
  address_map_provider TEXT NOT NULL DEFAULT 'google',
  address_italic INTEGER NOT NULL DEFAULT 1,
  title_font_family TEXT NOT NULL DEFAULT 'Arial',
  title_font_size_pt REAL NOT NULL DEFAULT 13,
  title_font_bold INTEGER NOT NULL DEFAULT 1,
  title_font_italic INTEGER NOT NULL DEFAULT 0,
  title_font_underline INTEGER NOT NULL DEFAULT 0,
  title_align TEXT NOT NULL DEFAULT 'center',
  meeting_name_font_family TEXT NOT NULL DEFAULT 'Arial',
  meeting_name_font_size_pt REAL NOT NULL DEFAULT 11,
  meeting_name_font_bold INTEGER NOT NULL DEFAULT 1,
  meeting_name_font_italic INTEGER NOT NULL DEFAULT 0,
  meeting_name_font_underline INTEGER NOT NULL DEFAULT 0,
  meeting_name_align TEXT NOT NULL DEFAULT 'center',
  bible_study_font_size_pt REAL NOT NULL DEFAULT 8.5,
  bible_study_union_align TEXT NOT NULL DEFAULT 'center',
  separator_lines INTEGER NOT NULL DEFAULT 1,
  separator_vertical_lines INTEGER NOT NULL DEFAULT 1,
  manual_palette_items INTEGER NOT NULL DEFAULT 0,
  print_order_mode TEXT NOT NULL DEFAULT 'alphabetical',
  print_order_json TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS field_list_templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  is_default INTEGER NOT NULL DEFAULT 0,
  settings_json TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS field_list_template_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  template_id INTEGER NOT NULL,
  item_type TEXT NOT NULL,
  label_number INTEGER NOT NULL DEFAULT 0,
  x_in REAL NOT NULL DEFAULT 0,
  y_in REAL NOT NULL DEFAULT 0,
  width_in REAL NOT NULL DEFAULT 1,
  height_in REAL NOT NULL DEFAULT 0.4,
  font_size_pt REAL NOT NULL DEFAULT 11,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (template_id) REFERENCES field_list_templates(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_contacts_name
ON contacts (family_name, given_name);

CREATE INDEX IF NOT EXISTS idx_relationships_contact
ON relationships (contact_id, position);

CREATE INDEX IF NOT EXISTS idx_phones_contact
ON phones (contact_id, position);

CREATE INDEX IF NOT EXISTS idx_addresses_contact
ON addresses (contact_id, position);

CREATE INDEX IF NOT EXISTS idx_meeting_data_field_meeting
ON meeting_data_rows (field_name, meeting_name);

CREATE INDEX IF NOT EXISTS idx_google_sync_queue_status
ON google_sync_queue (status, created_at);

CREATE INDEX IF NOT EXISTS idx_contact_assignment_options_kind_value
ON contact_assignment_options (kind, value);

CREATE INDEX IF NOT EXISTS idx_contact_edit_log_entries_batch
ON contact_edit_log_entries (batch_created_at DESC, batch_row_index ASC, id ASC);

CREATE INDEX IF NOT EXISTS idx_app_revisions_sort_order
ON app_revisions (sort_order, id);

CREATE INDEX IF NOT EXISTS idx_field_list_templates_name
ON field_list_templates (name);

CREATE INDEX IF NOT EXISTS idx_field_list_template_items_template
ON field_list_template_items (template_id, sort_order, id);

CREATE TABLE IF NOT EXISTS share_recipients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  display_name TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL,
  invite_sent_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(email)
);

CREATE INDEX IF NOT EXISTS idx_share_recipients_email
ON share_recipients (email);

CREATE TABLE IF NOT EXISTS field_end_lists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  field_name TEXT NOT NULL,
  list_name TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  UNIQUE (field_name, list_name)
);

CREATE INDEX IF NOT EXISTS idx_field_end_lists_field_sort
ON field_end_lists (field_name, sort_order, id);
