import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

FORMULA_KEY = "pricing.mobis_usd_formula"
DEFAULT_FORMULA = "0.00123 * W + 6.15 * EXP(-W / 10000)"
_NUMBER = r"([0-9]{1,12}(?:[.,][0-9]{1,12})?)"
_FORMULA = re.compile(
    rf"{_NUMBER}\s*\*\s*W\s*\+\s*{_NUMBER}\s*\*\s*EXP\s*"
    rf"\(\s*-\s*W\s*/\s*{_NUMBER}\s*\)",
    re.IGNORECASE | re.ASCII,
)


@dataclass(frozen=True)
class SaleFormula:
    rate: Decimal
    premium: Decimal
    scale: Decimal

    @classmethod
    def parse(cls, expression: str) -> "SaleFormula":
        match = _FORMULA.fullmatch(expression.strip())
        if not match:
            raise ValueError("Используйте форму A * W + B * EXP(-W / C).")
        rate, premium, scale = (Decimal(n.replace(",", ".")) for n in match.groups())
        if rate <= 0 or scale <= 0:
            raise ValueError("Коэффициент при W и делитель экспоненты должны быть больше нуля.")
        return cls(rate, premium, scale)

    def sale_usd(self, won: int | Decimal | None) -> str | None:
        if won is None or won <= 0:
            return None
        with localcontext() as context:
            context.prec = 50
            w = Decimal(won)
            value = self.rate * w + self.premium * (-w / self.scale).exp()
            return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
