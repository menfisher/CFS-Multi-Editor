# MeetingData v2 Notes

This file documents the intended normalized meeting layout model before the editing
UI is built.

## Why v2 exists

`meeting_data_rows` stores imported spreadsheet rows. That is useful for:

- import validation
- debugging
- one-time conversion

It is not a good long-term editing structure because:

- blank header rows inherit context implicitly
- D-AE are position columns, not semantic entities
- merged cells are not represented explicitly
- screen editing and print layout are mixed together

## v2 entities

### `meeting_sections_v2`

Represents one editable section such as:

- `AL North / Fayetteville, TN (ERICKSON)`

### `meeting_section_rows_v2`

Represents a logical row inside that section. Example row kinds:

- `title`
- `month`
- `group`
- `underlined`
- `content`

### `meeting_row_cells_v2`

Represents visible content blocks in a row using:

- `col_start`
- `col_span`
- `text_value`
- style flags

This is the main structure the browser editor should manipulate.

## How the browser editor should think

The editor should not expose raw `col_d` through `col_ae`.
Instead it should expose:

- section title
- ordered rows
- editable blocks inside each row
- row type
- style toggles
- drag/move/reorder behavior later

## Conversion rule

The current imported section renderer already infers:

- effective field
- effective meeting
- visible row order
- populated cells
- rough cell span

That inferred data can be converted into the v2 model as a first migration.

