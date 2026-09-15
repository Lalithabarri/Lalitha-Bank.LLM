"""evidence/ — EvidenceWriter, Redactor, screenshots.

Owns: the only structured-write path to disk (ARCHITECTURE §3, §10, D19).
Must never: write unredacted structured data. Redaction happens before disk, never after.

Implemented at ARCHITECTURE §15 step 9.
"""
