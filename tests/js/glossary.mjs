// The wording glossary (docs/improvement-plan.md section 4) as the tests check it: the plain terms
// the app writes and the old words they replace. Shared by terms.test.mjs (the app) and
// docs.test.mjs (the wireframes drawn from it, R6-11).

// The glossary's plain terms, as the page writes them.
export const NEW = [
  "Paperwork", "Open paperwork", "Paperwork for ", "Needs paperwork", "Get CO ", "I have this…", "Marked as in hand",
  "Send draft for their review", "Customer agreed to the draft", "Customer wants changes", "Customer settings",
  "Save to customer settings", "Customer sent a pay app back? Note why.", "Who pays you?", "Who signs off on your bills",
  "Email for pay apps", "Record a payment", "Payments received", "Finish job", "Ready to finish the job.", "<h2>Money</h2>",
  "Signed change orders", "Unsigned change orders", "Bill now (send waits for signature)", "Held back, still owed",
  "Delete draft", "Activity log", "Unpaid", "Closeout", "Waiting (", "Also email (CC)", "Paperwork needed with each pay app",
  "They send pay apps back when", "Days they usually take to pay", "Days between follow-ups", "Total done so far $",
  "Anything to note? (optional)", "sent by ", "Notes (kept on the job record)", "Recommended for owners.",
  "Homeowner: their email. GC: their accounts-payable address.",
];

// The words they replace. Each is a label, button, heading or sentence the page must not show;
// the trade terms stay only in help sentences (the Help page's "Paperwork (packet)" and the like).
export const OLD = [
  /\bPackets\b/, /Open packet/, /Clear \$?\{?\w*\}? ?blockers?/i, /Mark present/i, /Marked present/, /pencil copy\b(?!\))/i,
  /Payer agreed/, /Payer wants changes/, /Playbook|(the|to|payer) playbook/, /Payer sent a pay app back/, /Match payment/, /Payments matched/,
  /[Cc]lose for (the )?record/, /Closed for the record/, /Money file/i, /Approved COs/, /Pending COs/, /Put on next pay app/,
  /Retainage open/, /Void draft/, />Audit</, /"Audit"/, /"Collections"/, />Collections</, /File to job record/, /To file \(/,
  /Completed to date/, /Completion notes/, /Notes for the job record/, /Owners should turn this on/, /"AP email"/, /AP email \(/,
  /"Approver"/, />Approver</, /Approve send/, /Draft skipped/, /\bthe payer\b/, /\bPayer\b(?!s)/,
];
