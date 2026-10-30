"""Money value type."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Money:
    """An amount in minor units (cents) of one ISO 4217 currency."""

    cents: int
    currency: str

    def __post_init__(self):
        if len(self.currency) != 3 or not self.currency.isupper():
            raise ValueError(f"not an ISO 4217 code: {self.currency!r}")

    def __add__(self, other: "Money") -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        if other.currency != self.currency:
            raise ValueError(f"cannot add {other.currency} to {self.currency}")
        return Money(self.cents + other.cents, self.currency)

    def __str__(self) -> str:
        sign = "-" if self.cents < 0 else ""
        return f"{sign}{abs(self.cents) // 100}.{abs(self.cents) % 100:02d} {self.currency}"
