# Real QuickBooks exports (R5-10)

Empty until someone with a QuickBooks Online sandbox company fills it. Before the Atlanta trial:

1. In the sandbox company, record a few invoices and payments (a partial one, one with a memo
   naming the pay app, one from a customer with no open pay app).
2. Run **Reports > Transaction List by Date** and **Reports > Deposit Detail**, export each to CSV
   (and, if you can, to Excel), and save them here as `transaction-list-by-date.csv` and
   `deposit-detail.csv` (`.xlsx` beside them).
3. With the sandbox connected (`QBO_ENV=sandbox`), save the raw answers of
   `SELECT * FROM Payment` and `SELECT * FROM Invoice` from the Accounting API as
   `payments.json` and `invoices.json` (the whole reply, `{"QueryResponse": {...}}`).
4. Scrub names, emails and addresses (keep the column names, dates and amounts' shapes).

`tests/test_qbo_csv.py` (`RealExportTests`) and `tests/test_qbo.py` (`RealShapeTests`) read
whatever is here and skip while the folder holds only this note.
