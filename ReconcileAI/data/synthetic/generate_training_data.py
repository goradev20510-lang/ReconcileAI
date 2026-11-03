"""
ReconcileAI – Labelled Training Data Generator
Generates many independent reconciliation "batches" (customers, open invoices,
bank credits, extracted remittances) together with ground-truth labels, for
calibrating the matching weights and decision thresholds.

Unlike the demo dataset (generate_dataset.py, one fixed 30-transaction batch),
this produces thousands of transactions with realistic noise:

  • look-alike customer names (Apex Solutions vs Apex Systems)
  • bank-truncated / squashed / abbreviated payer names
  • several narration layouts (slash, hyphen, UTR-first, "BY TRANSFER")
  • invoice refs in clean and messy formats (INV-0012, INV0012, INV 0012, 0012)
  • recurring invoices with identical amounts (true ambiguity)
  • partially-paid invoices, TDS at section rates, bank charges, overpayments,
    bulk and split payments, wrong references, unknown payers, non-AR credits

Each transaction label has:
  true_invoice_ids : invoices this money really belongs to ([] = none)
  expected_outcome : "matched" | "review" | "unmatched"  (ideal system outcome)
  auto_ok          : auto-matching to a true invoice is also acceptable

Run:
  python -m data.synthetic.generate_training_data               # 400 batches
  python -m data.synthetic.generate_training_data --batches 1000 --seed 7
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "training" / "batches.jsonl"
BASE_DATE = date(2026, 4, 1)

# ── Customer name universe ────────────────────────────────────────────────────
_STEMS = [
    "Apex", "Bharat", "Nexus", "Sterling", "Orbit", "Prime", "Zeta", "Meridian",
    "Vantage", "Innovatech", "Cascade", "Pinnacle", "Shree", "Sai", "Ganesh",
    "Lakshmi", "Krishna", "Omega", "Delta", "Sigma", "Aurora", "Horizon",
    "Summit", "Evergreen", "Silverline", "Bluestone", "Redwood", "Sunrise",
    "Trident", "Vertex", "Quantum", "Fusion", "Infinity", "Galaxy", "Navkar",
    "Mahalaxmi", "Kaveri", "Ganga", "Himalaya", "Sahyadri", "Deccan", "Coastal",
]
_INDUSTRY = [
    "Technologies", "Solutions", "Systems", "Enterprises", "Infotech", "Logistics",
    "Healthcare", "Retail", "Software", "Infrastructure", "Analytics", "Traders",
    "Industries", "Pharma", "Textiles", "Engineering", "Consultants", "Exports",
    "Foods", "Motors", "Polymers", "Chemicals", "Builders", "Media",
]
_SUFFIX = ["Pvt Ltd", "Private Limited", "Ltd", "Limited", "LLP", "", "& Co", "Corporation"]
_STATE_CODES = ["27", "29", "07", "33", "19", "24", "06", "36", "21", "22", "32", "09", "10", "16"]
_BANKS = ["AXIS", "HDFC", "SBIN", "ICIC", "KKBK", "YESB", "IDFB", "PUNB"]

_TDS_SECTIONS = {"194C": 0.02, "194J": 0.10, "194H": 0.05, "194I": 0.10}

# Scenario mix (relative weights). Roughly mirrors a mid-size Indian AR desk.
SCENARIOS: dict[str, float] = {
    "exact_ref":              22,
    "exact_ref_outstanding":   4,
    "ref_only_no_payer":       5,
    "ref_messy_format":        6,
    "name_amount_unique":     10,
    "name_amount_ambiguous":   4,
    "lookalike_customer":      4,
    "remittance_only":         5,
    "tds_with_remittance":     7,
    "tds_no_remittance":       3,
    "bank_charges":            4,
    "partial_with_ref":        4,
    "split_payment":           3,
    "bulk_payment":            3,
    "bulk_with_remittance":    2,
    "amount_discrepancy":      3,
    "overpayment":             2,
    "wrong_ref":               3,
    "unknown_payer":           3,
    "unknown_payer_amount_hit": 1,
    "non_ar_credit":           2,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _gstin(rng: random.Random, letter: str) -> str:
    return (f"{rng.choice(_STATE_CODES)}AABC{letter.upper()}"
            f"{rng.randint(1000, 9999)}{rng.choice('ABCDEFGH')}{rng.randint(1, 9)}Z{rng.randint(0, 9)}")


def _make_customers(rng: random.Random, n: int) -> list[dict]:
    """Sample n customers; deliberately include look-alike pairs sharing a stem."""
    names: set[str] = set()
    customers = []
    stems = rng.sample(_STEMS, k=min(len(_STEMS), max(4, n // 2 + 2)))
    while len(customers) < n:
        stem = rng.choice(stems)  # reusing stems creates look-alikes naturally
        name = " ".join(p for p in (stem, rng.choice(_INDUSTRY), rng.choice(_SUFFIX)) if p)
        if name in names:
            continue
        names.add(name)
        idx = len(customers) + 1
        customers.append({
            "customer_id": f"CUST{idx:03d}",
            "name": name,
            "gstin": _gstin(rng, stem[0]) if rng.random() < 0.85 else None,
        })
    return customers


def payer_variant(rng: random.Random, name: str) -> str:
    """How a payer name shows up in a bank narration."""
    words = name.replace("&", "").split()
    core = [w for w in words if w.lower().strip(".") not in
            {"pvt", "private", "ltd", "limited", "llp", "co", "corporation"}]
    style = rng.random()
    if style < 0.25:
        v = name.upper()
    elif style < 0.45:
        v = "".join(core).upper()                    # XYZTECHNOLOGIES
    elif style < 0.60:
        v = " ".join(core).upper()                   # XYZ TECHNOLOGIES
    elif style < 0.72:
        v = (core[0] + " " + core[1][:4]).upper() if len(core) > 1 else core[0].upper()  # APEX SOLU
    elif style < 0.82:
        v = name.upper().replace(" ", "")[: rng.randint(10, 18)]  # bank truncation
    elif style < 0.90:
        v = core[0].upper()                          # first word only (weak)
    else:
        # one-character typo
        s = list(" ".join(core).upper())
        i = rng.randrange(len(s))
        s[i] = rng.choice("ABCDEFGHIKLMNOPRSTU") if s[i] != " " else s[i]
        v = "".join(s)
    return v.strip()


def _ref_clean(inv_id: str, rng: random.Random) -> str:
    num = inv_id.split("-")[1]
    return rng.choice([inv_id, inv_id, f"INV{num}", f"INV/{num}"])


def _ref_messy(inv_id: str, rng: random.Random) -> str:
    num = inv_id.split("-")[1]
    return rng.choice([f"INV {num}", f"INV{int(num)}", num, f"BILL {num}", f"INV-{num}", f"inv-{num}"])


def _utr(rng: random.Random) -> str:
    return f"{rng.choice(_BANKS)}{rng.choice('NR')}{rng.randint(10**10, 10**11 - 1)}"


def narration(rng: random.Random, payer: str | None, ref: str | None, utr: str,
              mode: str | None = None) -> str:
    mode = mode or rng.choices(["NEFT", "RTGS", "IMPS", "UPI"], [55, 25, 15, 5])[0]
    tail = ref or rng.choice(["PAYMENT", "SETTLEMENT", "OCT PAYMENT", "AGAINST BILLS", "PYMT", ""])
    layout = rng.random()
    if payer is None:
        parts = [mode, utr[-12:], tail] if layout < 0.5 else [mode, tail]
        return "/".join(p for p in parts if p)
    if layout < 0.60:
        parts = [mode, payer, tail]                       # NEFT/PAYER/REF
        return "/".join(p for p in parts if p)
    if layout < 0.75:
        return "/".join(p for p in [mode, utr, payer, tail] if p)      # UTR first
    if layout < 0.90:
        return "-".join(p for p in [mode, utr[:4] + "0001234", payer, tail] if p)  # hyphenated
    return "BY TRANSFER-" + "-".join(p for p in [mode, payer, tail] if p)


# ── Batch generator ───────────────────────────────────────────────────────────

class _Batch:
    def __init__(self, rng: random.Random, batch_no: int):
        self.rng = rng
        self.batch_id = f"B{batch_no:05d}"
        self.customers = _make_customers(rng, rng.randint(10, 24))
        self.invoices: list[dict] = []
        self.transactions: list[dict] = []
        self.remittances: list[dict] = []
        self.labels: list[dict] = []
        self.used: set[str] = set()
        self._inv_n = rng.randint(1, 900)
        self._txn_n = 1
        self.start = BASE_DATE + timedelta(days=rng.randint(0, 180))
        self._make_invoices()

    # invoices -------------------------------------------------------------
    def _amount(self) -> tuple[float, float, float]:
        rng = self.rng
        taxable = rng.choice([
            rng.randrange(5_000, 50_000, 500),
            rng.randrange(50_000, 300_000, 1000),
            rng.choice([50000, 75000, 100000, 120000, 150000, 200000]),
            rng.randrange(300_000, 2_000_000, 5000),
        ])
        gst_rate = rng.choices([0.18, 0.12, 0.05, 0.28], [70, 12, 10, 8])[0]
        gst = round(taxable * gst_rate)
        return float(taxable), float(gst), float(taxable + gst)

    def _make_invoices(self) -> None:
        rng = self.rng
        for cust in self.customers:
            k = rng.choices([1, 2, 3, 4, 5], [15, 30, 30, 15, 10])[0]
            recurring = rng.random() < 0.25          # monthly retainer → same amount
            base = self._amount()
            for _ in range(k):
                taxable, gst, total = base if recurring else self._amount()
                inv_date = self.start + timedelta(days=rng.randint(0, 45))
                status, outstanding = "open", total
                if rng.random() < 0.08:              # previously part-paid
                    outstanding = round(total * rng.choice([0.4, 0.5, 0.75]), 2)
                    status = "partially_paid"
                self.invoices.append({
                    "invoice_id": f"INV-{self._inv_n:04d}",
                    "customer_id": cust["customer_id"],
                    "customer_name": cust["name"],
                    "customer_gstin": cust["gstin"],
                    "invoice_date": inv_date.isoformat(),
                    "due_date": (inv_date + timedelta(days=rng.choice([15, 30, 45, 60]))).isoformat(),
                    "taxable_amount": taxable,
                    "gst_amount": gst,
                    "total_amount": total,
                    "outstanding_amount": outstanding,
                    "currency": "INR",
                    "status": status,
                })
                self._inv_n += 1

    # utilities --------------------------------------------------------------
    def cust(self, cid: str) -> dict:
        return next(c for c in self.customers if c["customer_id"] == cid)

    def free_invoices(self, pred=lambda inv: True) -> list[dict]:
        return [i for i in self.invoices if i["invoice_id"] not in self.used and pred(i)]

    def unique_amount(self, inv: dict) -> bool:
        return sum(1 for i in self.invoices
                   if abs(i["outstanding_amount"] - inv["outstanding_amount"]) < 1) == 1

    def txn_date(self, inv: dict | None) -> str:
        rng = self.rng
        if inv is None:
            return (self.start + timedelta(days=rng.randint(10, 90))).isoformat()
        due = date.fromisoformat(inv["due_date"])
        off = rng.choices([rng.randint(-10, 7), rng.randint(8, 30), rng.randint(31, 90)], [70, 22, 8])[0]
        return (due + timedelta(days=off)).isoformat()

    def add_txn(self, narr: str, credit: float, utr: str, inv: dict | None, *,
                scenario: str, true_ids: list[str], expected: str, auto_ok: bool,
                remittance: dict | None = None) -> None:
        tid = f"{self.batch_id}-T{self._txn_n:03d}"
        self._txn_n += 1
        self.transactions.append({
            "transaction_id": tid, "utr": utr, "narration": narr,
            "credit_amount": round(float(credit), 2), "debit_amount": 0.0,
            "transaction_date": self.txn_date(inv), "currency": "INR", "status": "unmatched",
        })
        if remittance:
            self.remittances.append({"utr": utr, "currency": "INR", **remittance})
        self.labels.append({
            "transaction_id": tid, "scenario": scenario, "true_invoice_ids": true_ids,
            "expected_outcome": expected, "auto_ok": auto_ok,
        })
        self.used.update(true_ids)

    # scenarios ---------------------------------------------------------------
    def build(self, n_txns: int) -> dict:
        names, weights = zip(*SCENARIOS.items())
        attempts = 0
        while len(self.transactions) < n_txns and attempts < n_txns * 6:
            attempts += 1
            sc = self.rng.choices(names, weights)[0]
            getattr(self, "_sc_" + sc)(sc)
        return {
            "batch_id": self.batch_id,
            "customers": self.customers,
            "invoices": self.invoices,
            "transactions": self.transactions,
            "remittances": self.remittances,
            "labels": self.labels,
        }

    def _pick(self, pred=lambda i: True) -> dict | None:
        pool = self.free_invoices(pred)
        return self.rng.choice(pool) if pool else None

    def _simple(self, sc, *, ref_fn, with_payer=True, amount=None, expected="matched",
                auto_ok=True, pred=lambda i: True):
        inv = self._pick(pred)
        if not inv:
            return
        utr = _utr(self.rng)
        payer = payer_variant(self.rng, inv["customer_name"]) if with_payer else None
        ref = ref_fn(inv["invoice_id"], self.rng) if ref_fn else None
        amt = inv["outstanding_amount"] if amount is None else amount(inv)
        self.add_txn(narration(self.rng, payer, ref, utr), amt, utr, inv, scenario=sc,
                     true_ids=[inv["invoice_id"]], expected=expected, auto_ok=auto_ok)

    def _sc_exact_ref(self, sc):
        self._simple(sc, ref_fn=_ref_clean, pred=lambda i: i["status"] == "open")

    def _sc_exact_ref_outstanding(self, sc):
        self._simple(sc, ref_fn=_ref_clean, pred=lambda i: i["status"] == "partially_paid")

    def _sc_ref_only_no_payer(self, sc):
        self._simple(sc, ref_fn=_ref_clean, with_payer=False)

    def _sc_ref_messy_format(self, sc):
        # Reference present but in a format the regex may not catch; customer + amount still there.
        self._simple(sc, ref_fn=_ref_messy, expected="matched", auto_ok=True)

    def _sc_name_amount_unique(self, sc):
        # No reference; payer name + exact amount, amount unique in the ledger.
        self._simple(sc, ref_fn=None, expected="review", auto_ok=True, pred=self.unique_amount)

    def _sc_name_amount_ambiguous(self, sc):
        # Customer has ≥2 open invoices with the same amount; no reference.
        groups: dict[tuple, list[dict]] = {}
        for inv in self.free_invoices():
            groups.setdefault((inv["customer_id"], inv["outstanding_amount"]), []).append(inv)
        dupes = [g for g in groups.values() if len(g) >= 2]
        if not dupes:
            return
        g = self.rng.choice(dupes)
        utr = _utr(self.rng)
        payer = payer_variant(self.rng, g[0]["customer_name"])
        # Any of the identical invoices is a legitimate allocation, but a human should pick.
        self.add_txn(narration(self.rng, payer, None, utr), g[0]["outstanding_amount"], utr, g[0],
                     scenario=sc, true_ids=[i["invoice_id"] for i in g], expected="review", auto_ok=False)

    def _sc_lookalike_customer(self, sc):
        # Payer shares a stem with another customer that also has open invoices.
        stems: dict[str, list[dict]] = {}
        for c in self.customers:
            stems.setdefault(c["name"].split()[0], []).append(c)
        pairs = [cs for cs in stems.values() if len(cs) >= 2]
        if not pairs:
            return
        cs = self.rng.choice(pairs)
        target = self.rng.choice(cs)
        inv = self._pick(lambda i: i["customer_id"] == target["customer_id"])
        if not inv:
            return
        with_ref = self.rng.random() < 0.5
        utr = _utr(self.rng)
        ref = _ref_clean(inv["invoice_id"], self.rng) if with_ref else None
        self.add_txn(narration(self.rng, payer_variant(self.rng, target["name"]), ref, utr),
                     inv["outstanding_amount"], utr, inv, scenario=sc + ("_ref" if with_ref else "_noref"),
                     true_ids=[inv["invoice_id"]], expected="matched" if with_ref else "review",
                     auto_ok=True)

    def _sc_remittance_only(self, sc):
        # Bank narration is useless; remittance email (found by UTR) names the invoice.
        inv = self._pick()
        if not inv:
            return
        utr = _utr(self.rng)
        rem = {"customer_name": inv["customer_name"], "invoice_references": [inv["invoice_id"]],
               "payment_amount": inv["outstanding_amount"], "tds_amount": 0.0,
               "payment_date": self.txn_date(inv)}
        self.add_txn(narration(self.rng, None, None, utr, mode="NEFT"), inv["outstanding_amount"], utr, inv,
                     scenario=sc, true_ids=[inv["invoice_id"]], expected="matched", auto_ok=True,
                     remittance=rem)

    def _tds(self, inv: dict) -> tuple[str, float]:
        sec = self.rng.choice(list(_TDS_SECTIONS))
        return sec, round(inv["taxable_amount"] * _TDS_SECTIONS[sec], 2) if self.rng.random() < 0.5 \
            else round(inv["total_amount"] * _TDS_SECTIONS[sec], 2)

    def _sc_tds_with_remittance(self, sc):
        inv = self._pick(lambda i: i["status"] == "open")
        if not inv:
            return
        sec, tds = self._tds(inv)
        credit = round(inv["total_amount"] - tds, 2)
        utr = _utr(self.rng)
        ref = _ref_clean(inv["invoice_id"], self.rng) if self.rng.random() < 0.6 else None
        rem = {"customer_name": inv["customer_name"], "invoice_references": [inv["invoice_id"]],
               "payment_amount": credit, "tds_amount": tds, "tds_section": sec,
               "payment_date": self.txn_date(inv)}
        # TDS without a certificate must always be reviewed (policy in validate_allocation).
        self.add_txn(narration(self.rng, payer_variant(self.rng, inv["customer_name"]), ref, utr),
                     credit, utr, inv, scenario=sc, true_ids=[inv["invoice_id"]],
                     expected="review", auto_ok=False, remittance=rem)

    def _sc_tds_no_remittance(self, sc):
        inv = self._pick(lambda i: i["status"] == "open")
        if not inv:
            return
        _, tds = self._tds(inv)
        utr = _utr(self.rng)
        self.add_txn(narration(self.rng, payer_variant(self.rng, inv["customer_name"]),
                               _ref_clean(inv["invoice_id"], self.rng), utr),
                     inv["total_amount"] - tds, utr, inv, scenario=sc, true_ids=[inv["invoice_id"]],
                     expected="review", auto_ok=False)

    def _sc_bank_charges(self, sc):
        # Payer's bank deducted a small fee (< 0.5 % tolerance).
        self._simple(sc, ref_fn=_ref_clean, expected="matched", auto_ok=True,
                     amount=lambda i: i["outstanding_amount"] - self.rng.choice([5.9, 11.8, 17.7, 23.6, 29.5]),
                     pred=lambda i: i["outstanding_amount"] > 20_000)

    def _sc_partial_with_ref(self, sc):
        # Partial payments are a business fact; auto-applying them with an exact ref is fine.
        self._simple(sc, ref_fn=_ref_clean, expected="review", auto_ok=True,
                     amount=lambda i: round(i["outstanding_amount"] * self.rng.choice([0.25, 0.4, 0.5, 0.6]), 2),
                     pred=lambda i: i["status"] == "open")

    def _sc_split_payment(self, sc):
        inv = self._pick(lambda i: i["status"] == "open")
        if not inv:
            return
        first = round(inv["total_amount"] * self.rng.choice([0.3, 0.5, 0.7]), 2)
        payer = payer_variant(self.rng, inv["customer_name"])
        for part, amt in (("PART1", first), ("PART2", round(inv["total_amount"] - first, 2))):
            utr = _utr(self.rng)
            ref = f"{_ref_clean(inv['invoice_id'], self.rng)}/{part}"
            self.add_txn(narration(self.rng, payer, ref, utr), amt, utr, inv, scenario=sc,
                         true_ids=[inv["invoice_id"]], expected="review", auto_ok=True)

    def _bulk(self, sc, remit: bool):
        by_cust: dict[str, list[dict]] = {}
        for inv in self.free_invoices(lambda i: i["status"] == "open"):
            by_cust.setdefault(inv["customer_id"], []).append(inv)
        options = [v for v in by_cust.values() if len(v) >= 2]
        if not options:
            return
        invs = self.rng.sample(self.rng.choice(options), 2)
        total = sum(i["total_amount"] for i in invs)
        utr = _utr(self.rng)
        rem = None
        if remit:
            rem = {"customer_name": invs[0]["customer_name"],
                   "invoice_references": [i["invoice_id"] for i in invs],
                   "payment_amount": total, "tds_amount": 0.0, "payment_date": self.txn_date(invs[0])}
        self.add_txn(narration(self.rng, payer_variant(self.rng, invs[0]["customer_name"]), "BULK", utr),
                     total, utr, invs[0], scenario=sc, true_ids=[i["invoice_id"] for i in invs],
                     expected="review", auto_ok=False, remittance=rem)

    def _sc_bulk_payment(self, sc):
        self._bulk(sc, remit=False)

    def _sc_bulk_with_remittance(self, sc):
        self._bulk(sc, remit=True)

    def _sc_amount_discrepancy(self, sc):
        self._simple(sc, ref_fn=_ref_clean, expected="review", auto_ok=False,
                     amount=lambda i: round(i["outstanding_amount"] * self.rng.choice([0.9, 0.95, 0.97, 0.98]), 2),
                     pred=lambda i: i["status"] == "open")

    def _sc_overpayment(self, sc):
        self._simple(sc, ref_fn=_ref_clean, expected="review", auto_ok=False,
                     # always beyond the ±0.5 % tolerance, otherwise it is just rounding
                     amount=lambda i: i["outstanding_amount"] + max(self.rng.choice([1000, 5000, 10000]),
                                                                    round(i["outstanding_amount"] * 0.01)))

    def _sc_wrong_ref(self, sc):
        # Narration cites someone else's invoice; payer + amount point to the right one.
        inv = self._pick(self.unique_amount)
        if not inv:
            return
        others = [i for i in self.invoices if i["customer_id"] != inv["customer_id"]]
        wrong = self.rng.choice(others)["invoice_id"] if others and self.rng.random() < 0.7 else "INV-9999"
        utr = _utr(self.rng)
        self.add_txn(narration(self.rng, payer_variant(self.rng, inv["customer_name"]), wrong, utr),
                     inv["outstanding_amount"], utr, inv, scenario=sc, true_ids=[inv["invoice_id"]],
                     expected="review", auto_ok=False)

    def _sc_unknown_payer(self, sc):
        utr = _utr(self.rng)
        payer = self.rng.choice(["RAMESH KUMAR", "UNKNOWNPAYER", "M S TRADERS", "CASH DEP", "ABC CORP"])
        amt = float(self.rng.randrange(1_000, 400_000, 250)) + self.rng.choice([0, 0.5])
        self.add_txn(narration(self.rng, payer, None, utr), amt, utr, None, scenario=sc,
                     true_ids=[], expected="unmatched", auto_ok=False)

    def _sc_unknown_payer_amount_hit(self, sc):
        # Unknown payer whose amount coincidentally equals some open invoice.
        inv = self.rng.choice(self.invoices)
        utr = _utr(self.rng)
        self.add_txn(narration(self.rng, "RAJESH ENTERPRISES", None, utr), inv["outstanding_amount"], utr, None,
                     scenario=sc, true_ids=[], expected="unmatched", auto_ok=False)

    def _sc_non_ar_credit(self, sc):
        utr = _utr(self.rng)
        narr = self.rng.choice(["INT.PD:01-04-2026 TO 30-06-2026", "REV/CHG/REFUND", "GST REFUND/CBIC",
                                "FD MATURITY PROCEEDS", "INSURANCE CLAIM SETTLEMENT"])
        self.add_txn(narr, float(self.rng.randrange(100, 80_000, 7)), utr, None, scenario=sc,
                     true_ids=[], expected="unmatched", auto_ok=False)


def generate(n_batches: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    batches = []
    for b in range(n_batches):
        batch = _Batch(random.Random(rng.random()), b + 1)
        n_txns = min(len(batch.invoices), rng.randint(15, 45))
        batches.append(batch.build(n_txns))
    return batches


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batches", type=int, default=400)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args()

    batches = generate(args.batches, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for b in batches:
            f.write(json.dumps(b, separators=(",", ":")) + "\n")

    n_txn = sum(len(b["transactions"]) for b in batches)
    n_inv = sum(len(b["invoices"]) for b in batches)
    by_sc: dict[str, int] = {}
    for b in batches:
        for lab in b["labels"]:
            by_sc[lab["scenario"]] = by_sc.get(lab["scenario"], 0) + 1
    print(f"✅ {len(batches)} batches → {args.out}")
    print(f"   invoices: {n_inv:,}   transactions: {n_txn:,}   "
          f"remittances: {sum(len(b['remittances']) for b in batches):,}")
    for sc, n in sorted(by_sc.items(), key=lambda kv: -kv[1]):
        print(f"   {sc:<28} {n:>6}")


if __name__ == "__main__":
    main()
