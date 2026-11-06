"""Write a manual review CSV without losing the reviewer's work.

Rows are matched by a key column. On every regeneration:
- my_verdict and my_note are kept from the existing file,
- new rows are added with status "new",
- rows still in the review set get status "needs_review" (details refreshed),
- rows no longer in the review set are kept with status "no_longer_needs_review".
"""
import pandas as pd

REVIEWER_COLUMNS = ["my_verdict", "my_note"]


def write_review_file(new, path, key):
    detail_cols = [c for c in new.columns if c != key]
    if path.exists():
        # read as text so empty notes stay "" and keys like "NA" stay text
        old = pd.read_csv(path, dtype=str, keep_default_na=False)
        for col in REVIEWER_COLUMNS:
            if col not in old.columns:
                old[col] = ""
        old = old[[key] + [c for c in detail_cols if c in old.columns] + REVIEWER_COLUMNS]
        merged = new.merge(old, on=key, how="outer", suffixes=("", "_old"), indicator=True)
        dropped = merged["_merge"] == "right_only"
        for col in detail_cols:
            if col + "_old" in merged.columns:   # dropped rows keep their last known details
                merged[col] = merged[col].where(~dropped, merged[col + "_old"])
        merged["status"] = merged["_merge"].map({
            "both": "needs_review", "left_only": "new", "right_only": "no_longer_needs_review"})
    else:
        merged = new.assign(status="new", my_verdict="", my_note="")
    for col in REVIEWER_COLUMNS:
        merged[col] = merged[col].fillna("")
    out = merged[[key] + detail_cols + ["status"] + REVIEWER_COLUMNS]
    path.parent.mkdir(exist_ok=True)
    out.to_csv(path, index=False)
    return out


def update_verdicts(path, key, edits):
    # Save the reviewer's my_verdict / my_note for the given keys. The file is re-read
    # at save time; every other row and column stays exactly as it is on disk.
    current = pd.read_csv(path, dtype=str, keep_default_na=False)
    edits = edits[[key] + REVIEWER_COLUMNS].fillna("").astype(str).set_index(key)
    unknown = set(edits.index) - set(current[key])
    if unknown:
        raise ValueError(f"keys not in {path.name}: {sorted(unknown)[:5]}")
    current = current.set_index(key)
    current.update(edits)                  # only these keys, only the reviewer columns
    current.reset_index().to_csv(path, index=False)
    return len(edits)
