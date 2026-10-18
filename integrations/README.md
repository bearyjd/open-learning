# Future source adapters

The first milestone ingests public article URLs only. No Kobo or Libby account
integration is implemented. Future adapters should create the same document and
chunk records as URL ingestion and explicitly set the document coverage:
`full_text`, `excerpt`, `annotations`, `abstract`, or `metadata`.

Planned adapters: DRM-free EPUB; user-exported Kobo annotations linked to book
and chapter; Libby/DoD MWR exported notes and catalog/borrowing links; accessible
PDF text; Markdown/plain text; RSS; OpenAlex, Crossref, and arXiv. Each adapter
must retain source identifiers, author, original location, import timestamp,
coverage, and supporting passages. Imports must have byte and extraction limits.

Do not bypass DRM, depend on a private library API, or label metadata or highlights
as a complete book. Keep copyrighted source documents and personal exports out
of repository fixtures.
