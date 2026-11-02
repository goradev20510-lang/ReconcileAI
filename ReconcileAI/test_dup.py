import sys; sys.path.insert(0, '.')
from validation.duplicate_detection import detect_duplicate_transaction, _extract_invoice_refs, _narration_similarity

txn21 = {'transaction_id': 'TXN-021', 'utr': 'SBI279368618081',   'narration': 'NEFT/MERIDIAN/INV-019', 'credit_amount': 59000, 'transaction_date': '2026-11-08'}
txn22 = {'transaction_id': 'TXN-022', 'utr': 'KOTAK103944899549', 'narration': 'NEFT/MERIDIAN/INV-020', 'credit_amount': 59000, 'transaction_date': '2026-11-09'}

sim = _narration_similarity('neft/meridian/inv-019', 'neft/meridian/inv-020')
refs21 = _extract_invoice_refs('neft/meridian/inv-019')
refs22 = _extract_invoice_refs('neft/meridian/inv-020')
print(f"Similarity: {sim:.3f}")
print(f"Refs TXN-021: {refs21}")
print(f"Refs TXN-022: {refs22}")
print(f"Disjoint (different invoices): {refs21.isdisjoint(refs22)}")
print()

result = detect_duplicate_transaction(txn21, existing_transactions=[txn22])
print(f"TXN-021 is_duplicate: {result['is_duplicate']}  reason: {result['reason']}")

result2 = detect_duplicate_transaction(txn22, existing_transactions=[txn21])
print(f"TXN-022 is_duplicate: {result2['is_duplicate']}  reason: {result2['reason']}")
