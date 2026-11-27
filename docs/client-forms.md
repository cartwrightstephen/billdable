# The client's own forms

Nov 27, 2026 · `gcforms.py`, `pdf_forms.py`, `static/js/views/gcforms.js`

## Summary

Many general contractors send back a pay app that isn't on their own form (Siteline comparison, item 1: Siteline's edge is a library of 23,000 GC pay app and waiver forms set up by its staff). Duracash now takes a client's blank form once, matches its fields to Duracash's values, and fills it every month. Duracash's team can do the setup for a customer at first; the template carries a note that says so.

## How it works

| Step | Where | What happens |
| --- | --- | --- |
| Upload | Client playbook, a client's card, Their own forms | The blank PDF for one form type: pay app cover, continuation sheet, or one of the four lien waivers. Read before the company's lock and stored like any upload. |
| Match | Match values | A fillable form lists its fields; each gets a Duracash value (guessed from its name first, marked Guessed). A continuation line value takes the row. Fixed text and Always ticked are offered too. A form with no fields gets values placed by position. |
| Check | Preview with sample values; with a grid | The form with made-up values, and a half-inch grid numbered in inches to read positions off. Matched fields are outlined on the grid preview. |
| Send | Pay app page, Review & send | The card On (client)'s own forms links the pay app filled in and lists what stops it. Approve & send is refused while a problem stands, if the client's emails carry their form. |
| Email | `outbox.py` | Their form, Duracash's PDF, or both (the client's setting; both by default). |
| Waivers | Pay app page | Each waiver form the client has, filled in for this pay app, to print and sign. Never emailed unsigned. |

## Placing values on a form with no fields

Rendering a PDF page in the browser takes a PDF library, which the app doesn't load. So the page is not drawn in the browser. The person types where each value goes, in inches from the top left corner of the page as it shows (a turned page is measured as it shows), and opens the grid preview: the form, the values stamped on it, and a grid every half inch numbered every inch. Read the next position off the grid, type it, save, preview again. Each value can be lined up from the left, ending at the position (for money columns), or centred.

## Versions

A new blank of the same type for the same client is a new version. The old one is kept under Older versions (its blank still opens) and stops being used. Matches for fields whose names are on the new version carry over; the rest are guessed again. The same file uploaded twice is refused. Up to 50 versions are kept per client and type.

## The checks before filling

- Every matched field gets a value, unless it is marked May be blank. An empty contract number, a missing period-to date or a client with no approver is named.
- A value longer than the field's own maximum length is a problem.
- A pay app with more continuation lines than the form has rows matched is refused rather than cut short. Rows below the last line are left blank on purpose.
- Money as `$1,234.56`, `1,234.56` or `1234.56`; dates as `11/27/2026`, `November 27, 2026` or `2026-11-27`; set on each template.

## What is written

The filled form is the client's original bytes, untouched, followed by an incremental update: the fields' new values (`/V`, and `/AS` for checkboxes), `/NeedAppearances true`, and a plain Helvetica appearance stream for each text widget, sized from the field's `/DA` (or shrunk to fit) and aligned by its `/Q`, so viewers that ignore `/NeedAppearances` still show the value. A form with XFA data alongside its fields has the XFA dropped in the update, so viewers read the fields. Values placed by position are a content stream added to the page, after the page's own drawing wrapped in `q`/`Q`. An update to a file indexed by a cross-reference stream is indexed by one too; a file read by scanning gets a complete new table.

## Limits: which PDFs won't work

- **Encrypted (locked) PDFs**: refused. The person is asked for an unlocked copy, or to print it to a new PDF.
- **Dynamic XFA forms** (LiveCycle Designer, "Please wait..." in other viewers): refused. A static XFA form with ordinary fields works.
- **Compression other than FlateDecode** on the parts read (the cross-reference, object streams): refused. Page content in other filters is fine, since it isn't read.
- **Rotated fields** (`/MK /R`): the value is set and `/NeedAppearances` asks the viewer to draw it, but Duracash's own appearance isn't turned.
- **Signature and push-button fields**: left for the person signing.
- **Comb fields**: filled as plain text, not spread across the boxes.
- **Characters outside Windows-1252** show as `?` in Duracash's appearance; the field's value keeps them (UTF-16), and viewers that redraw show them.
- **Nothing is flattened**: the filled form is still fillable.
- **Size and shape**: up to 15 MB, 500 pages, 3,000 fields, 30 MB of unpacked streams and 8 seconds of reading. A file past any of them is refused.
- **Retainage split** (G702 lines 5a and 5b) isn't a separate value; match retainage to date to one of them.
- **Clients merged or deleted** keep their forms in the record but no longer show them.

## Open questions

- Should a GC form that can't be filled hold the send even when the email carries both forms? Today it does.
- Pencil copies (drafts sent for review) carry the client's form too; an incomplete one fails that email with the reason. Should they always go on Duracash's PDF?
- Should Duracash keep a shared library of common GC forms across companies (Siteline's moat), with each company's copy matched once?

## Sources

- `Siteline comparison.md`, item 1
- PDF 32000-1:2008, sections 7.5 (file structure, incremental updates, cross-reference streams, object streams) and 12.7 (interactive forms)
