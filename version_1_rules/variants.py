"""The three portfolio variants tested in version 1 (all use the same 12-1 momentum ranks)."""
from alpha_finder.backtest.engine import BacktestConfig
from alpha_finder.backtest.tax import TaxConfig
from alpha_finder.research import TAX


def variants(tax: TaxConfig = TAX) -> dict[str, BacktestConfig]:
    return {
        "A": BacktestConfig("A: top-25 replaced monthly, tax-blind", sell_rank=None, equalize=True,
                            tax_gate=False, tax=tax),
        "B": BacktestConfig("B: rank buffer", sell_rank=60, tax_gate=False, tax=tax),
        "C": BacktestConfig("C: buffer + tax gate", sell_rank=60, tax_gate=True, tax=tax),
    }
