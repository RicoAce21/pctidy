"""
Quick text-only scan: prints what WOULD be cleaned. Deletes nothing.
Run it with:  python scan_cli.py
"""
import time

from core.scanner import get_categories, scan_category, format_size

total = 0
start = time.time()
for cat in get_categories():
    print(f"Scanning {cat.name}...", end=" ", flush=True)
    t0 = time.time()
    r = scan_category(cat)
    total += r.total_size
    print(f"{r.item_count} items, {format_size(r.total_size)}  ({time.time() - t0:.1f}s)")
print("-" * 60)
print(f"Total reclaimable: {format_size(total)}  (took {time.time() - start:.1f}s)")
