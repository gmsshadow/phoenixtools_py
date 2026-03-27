from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PhoenixOrder:
    parameters: list[object]

    def __str__(self) -> str:
        return f"Order={','.join(str(p) for p in self.parameters)};"

    @staticmethod
    def _bool(v: bool) -> str:
        return "True" if v else "False"

    @staticmethod
    def _str(v: object) -> str:
        return f"\"{v}\""

    @classmethod
    def navigation_hazard_status(cls, active: bool = True) -> "PhoenixOrder":
        return cls([5230, 0, cls._bool(active)])

    @classmethod
    def move_to_planet(cls, star_system_id: int, cbody_id: int) -> "PhoenixOrder":
        return cls([3130, 1, star_system_id, cbody_id])

    @classmethod
    def move_to_quad(cls, quad: int, ring: int) -> "PhoenixOrder":
        return cls([3000, 0, quad, ring])

    @classmethod
    def move_to_random_jump_quad(cls) -> "PhoenixOrder":
        # keep same behavior as Rails: random quad, ring 10
        import random

        return cls.move_to_quad(random.choice([1, 2, 3, 4]), 10)

    @classmethod
    def jump(cls, star_system_id: int) -> "PhoenixOrder":
        return cls([3080, 1, star_system_id])

    @classmethod
    def buy(cls, starbase_id: int, item_id: int, quantity: int, install: bool = False, private: bool = False) -> "PhoenixOrder":
        return cls([2040, 0, starbase_id, item_id, quantity, cls._bool(install), cls._bool(private)])

    @classmethod
    def sell(cls, starbase_id: int, item_id: int, quantity: int, private: bool = False) -> "PhoenixOrder":
        return cls([2030, 0, starbase_id, item_id, quantity, cls._bool(private)])

    @classmethod
    def wait_for_tus(cls, tus: int = 300, exact: bool = False) -> "PhoenixOrder":
        return cls([2520, 0, tus, cls._bool(exact)])

    @classmethod
    def gpi_row(cls, row: int, start_x: int, end_x: int, ore_type: int = 0) -> "PhoenixOrder":
        return cls([2500, 0, ore_type, row, start_x, end_x])

